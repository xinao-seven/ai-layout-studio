"""Async repository for scenes and append-only version history."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.contracts.enums import MessageRole, ProposalStatus, TurnStatus
from app.contracts.proposal import Proposal, Turn, TurnMessage, TurnUsage
from app.contracts.scene import SceneDocument
from app.db.models import Proposal as ProposalRow
from app.db.models import Scene, SceneVersion
from app.db.models import Turn as TurnRow
from app.db.models import TurnEvent as TurnEventRow
from app.scene.errors import SceneError
from app.scene.service import CommitOutcome

KEEP_VERSIONS = 100
KEEP_TURN_EVENTS = 500
INCOMPLETE_TURN_STATUSES = (
    TurnStatus.QUEUED.value,
    TurnStatus.RUNNING.value,
    TurnStatus.WAITING_USER.value,
)


class SceneRepository:
    """Persistence boundary; all methods own their short transaction."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def list_scenes(self) -> Sequence[Scene]:
        async with self._session_factory() as session:
            result = await session.execute(select(Scene).order_by(Scene.updated_at.desc()))
            return list(result.scalars())

    async def get_scene_row(self, scene_id: str) -> Scene | None:
        async with self._session_factory() as session:
            return await session.get(Scene, scene_id)

    async def get_scene(self, scene_id: str) -> SceneDocument | None:
        row = await self.get_scene_row(scene_id)
        if row is None:
            return None
        try:
            return SceneDocument.model_validate(row.doc_json)
        except ValueError as error:
            raise SceneError(
                "invalid_input",
                f"Stored scene {scene_id!r} is corrupt.",
                "Restore from an exported document or a database backup.",
                {"error": str(error)},
            ) from error

    async def create_scene(self, document: SceneDocument) -> None:
        doc_json = document.model_dump(mode="json", by_alias=True)
        async with self._session_factory() as session, session.begin():
            session.add(
                Scene(
                    id=document.id,
                    name=document.name,
                    current_version=document.version,
                    doc_json=doc_json,
                    created_at=document.created_at,
                    updated_at=document.updated_at,
                )
            )
            session.add(
                SceneVersion(
                    scene_id=document.id,
                    version=document.version,
                    doc_json=doc_json,
                    patch_json=None,
                    inverse_json=None,
                    actor="system",
                    created_at=document.created_at,
                )
            )

    async def save_commit(self, outcome: CommitOutcome) -> None:
        document = outcome.document
        doc_json = document.model_dump(mode="json", by_alias=True)
        patch_json = outcome.patch.model_dump(mode="json", by_alias=True)
        inverse_json = [op.model_dump(mode="json", by_alias=True) for op in outcome.inverse_ops]
        async with self._session_factory() as session, session.begin():
            row = await session.get(Scene, document.id)
            if row is None:
                raise SceneError(
                    "not_found",
                    f"Scene {document.id!r} disappeared during commit.",
                    "Reload the scene list.",
                )
            row.name = document.name
            row.current_version = document.version
            row.doc_json = doc_json
            row.updated_at = document.updated_at
            session.add(
                SceneVersion(
                    scene_id=document.id,
                    version=document.version,
                    doc_json=doc_json,
                    patch_json=patch_json,
                    inverse_json=inverse_json,
                    actor=outcome.patch.actor.value,
                    turn_id=outcome.patch.turn_id,
                    created_at=document.updated_at,
                )
            )
            await self._prune_versions(session, document.id, document.version)

    async def _prune_versions(
        self, session: AsyncSession, scene_id: str, current_version: int
    ) -> None:
        cutoff = current_version - KEEP_VERSIONS + 1
        if cutoff <= 1:
            return
        await session.execute(
            delete(SceneVersion).where(
                SceneVersion.scene_id == scene_id,
                SceneVersion.version < cutoff,
            )
        )

    async def list_versions(self, scene_id: str) -> Sequence[SceneVersion]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(SceneVersion)
                .where(SceneVersion.scene_id == scene_id)
                .order_by(SceneVersion.version.desc())
            )
            return list(result.scalars())

    async def get_version(self, scene_id: str, version: int) -> SceneVersion | None:
        async with self._session_factory() as session:
            return await session.get(SceneVersion, (scene_id, version))

    async def delete_scene(self, scene_id: str) -> bool:
        async with self._session_factory() as session, session.begin():
            row = await session.get(Scene, scene_id)
            if row is None:
                return False
            await session.delete(row)
            return True

    # ---- turns and proposals (M3) ---------------------------------------

    async def create_turn(self, turn: Turn, user_message: str) -> None:
        async with self._session_factory() as session, session.begin():
            session.add(
                TurnRow(
                    id=turn.id,
                    scene_id=turn.scene_id,
                    status=turn.status.value,
                    user_message=user_message,
                    messages_json=[
                        message.model_dump(mode="json", by_alias=True) for message in turn.messages
                    ],
                    usage_json=turn.usage.model_dump(mode="json", by_alias=True),
                    created_at=turn.created_at,
                    ended_at=turn.ended_at,
                )
            )

    async def update_turn(self, turn: Turn, *, error: str | None = None) -> None:
        async with self._session_factory() as session, session.begin():
            row = await session.get(TurnRow, turn.id)
            if row is None:
                return
            row.status = turn.status.value
            row.messages_json = [
                message.model_dump(mode="json", by_alias=True) for message in turn.messages
            ]
            row.usage_json = turn.usage.model_dump(mode="json", by_alias=True)
            row.error = error
            row.ended_at = turn.ended_at

    async def append_turn_event(
        self,
        turn_id: str,
        seq: int,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        async with self._session_factory() as session, session.begin():
            session.add(
                TurnEventRow(
                    turn_id=turn_id,
                    seq=seq,
                    type=event_type,
                    payload_json=payload,
                )
            )
            if seq > KEEP_TURN_EVENTS:
                await session.execute(
                    delete(TurnEventRow).where(
                        TurnEventRow.turn_id == turn_id,
                        TurnEventRow.seq <= seq - KEEP_TURN_EVENTS,
                    )
                )

    async def get_turn(self, turn_id: str) -> Turn | None:
        async with self._session_factory() as session:
            row = await session.get(TurnRow, turn_id)
            return _turn_from_row(row) if row is not None else None

    async def list_turns(self, scene_id: str, *, limit: int = 50) -> Sequence[Turn]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(TurnRow)
                .where(TurnRow.scene_id == scene_id)
                .order_by(TurnRow.created_at.desc())
                .limit(limit)
            )
            return [_turn_from_row(row) for row in result.scalars()]

    async def update_turn_status(
        self,
        turn_id: str,
        status: TurnStatus,
        *,
        error: str | None = None,
    ) -> None:
        async with self._session_factory() as session, session.begin():
            await session.execute(
                update(TurnRow)
                .where(TurnRow.id == turn_id)
                .values(status=status.value, error=error, ended_at=datetime.now(UTC))
            )

    async def save_proposal(self, proposal: Proposal) -> None:
        async with self._session_factory() as session, session.begin():
            await session.merge(
                ProposalRow(
                    id=proposal.id,
                    scene_id=proposal.scene_id,
                    turn_id=proposal.turn_id,
                    base_version=proposal.base_version,
                    depends_on_json=list(proposal.depends_on),
                    ops_json=[op.model_dump(mode="json", by_alias=True) for op in proposal.ops],
                    status=proposal.status.value,
                    reason=proposal.reason,
                    created_at=proposal.created_at,
                    resolved_at=proposal.resolved_at,
                )
            )

    async def update_proposal_status(
        self,
        proposal_id: str,
        status: ProposalStatus,
        *,
        reason: str | None = None,
    ) -> None:
        async with self._session_factory() as session, session.begin():
            row = await session.get(ProposalRow, proposal_id)
            if row is None:
                return
            row.status = status.value
            row.reason = reason
            if status != ProposalStatus.PENDING:
                row.resolved_at = datetime.now(UTC)

    async def expire_pending_proposals(
        self,
        scene_id: str,
        proposal_ids: Sequence[str] | None = None,
        *,
        reason: str = "scene_changed",
    ) -> list[str]:
        async with self._session_factory() as session, session.begin():
            query = select(ProposalRow).where(
                ProposalRow.scene_id == scene_id,
                ProposalRow.status == ProposalStatus.PENDING.value,
            )
            if proposal_ids is not None:
                query = query.where(ProposalRow.id.in_(list(proposal_ids)))
            rows = list((await session.execute(query)).scalars())
            resolved_at = datetime.now(UTC)
            for row in rows:
                row.status = ProposalStatus.EXPIRED.value
                row.reason = reason
                row.resolved_at = resolved_at
            return [row.id for row in rows]

    async def pending_proposals(self, scene_id: str) -> Sequence[Proposal]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(ProposalRow)
                .where(
                    ProposalRow.scene_id == scene_id,
                    ProposalRow.status == ProposalStatus.PENDING.value,
                )
                .order_by(ProposalRow.created_at.asc())
            )
            return [_proposal_from_row(row) for row in result.scalars()]

    async def get_proposal(self, proposal_id: str) -> Proposal | None:
        async with self._session_factory() as session:
            row = await session.get(ProposalRow, proposal_id)
            return _proposal_from_row(row) if row is not None else None

    async def list_proposals(self, scene_id: str) -> Sequence[Proposal]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(ProposalRow)
                .where(ProposalRow.scene_id == scene_id)
                .order_by(ProposalRow.created_at.asc())
            )
            return [_proposal_from_row(row) for row in result.scalars()]

    async def recover_interrupted(self) -> tuple[int, int]:
        """Mark unfinished turns failed and pending proposals expired on boot."""
        now = datetime.now(UTC)
        async with self._session_factory() as session, session.begin():
            turns = list(
                (
                    await session.execute(
                        select(TurnRow).where(TurnRow.status.in_(INCOMPLETE_TURN_STATUSES))
                    )
                ).scalars()
            )
            for row in turns:
                row.status = TurnStatus.FAILED.value
                row.error = "server_restart"
                row.ended_at = now
            proposals = list(
                (
                    await session.execute(
                        select(ProposalRow).where(
                            ProposalRow.status == ProposalStatus.PENDING.value
                        )
                    )
                ).scalars()
            )
            for proposal_row in proposals:
                proposal_row.status = ProposalStatus.EXPIRED.value
                proposal_row.reason = "server_restart"
                proposal_row.resolved_at = now
            return len(turns), len(proposals)


def _turn_from_row(row: TurnRow) -> Turn:
    usage = TurnUsage.model_validate(row.usage_json or {})
    messages = (
        [TurnMessage.model_validate(message) for message in row.messages_json]
        if row.messages_json
        else [TurnMessage(role=MessageRole.USER, content=row.user_message)]
    )
    return Turn(
        id=row.id,
        scene_id=row.scene_id,
        status=TurnStatus(row.status),
        messages=messages,
        usage=usage,
        created_at=row.created_at,
        ended_at=row.ended_at,
    )


def _proposal_from_row(row: ProposalRow) -> Proposal:
    return Proposal.model_validate(
        {
            "id": row.id,
            "sceneId": row.scene_id,
            "turnId": row.turn_id,
            "baseVersion": row.base_version,
            "dependsOn": list(row.depends_on_json or []),
            "ops": list(row.ops_json),
            "status": row.status,
            "createdAt": row.created_at,
            "resolvedAt": row.resolved_at,
            "reason": row.reason,
        }
    )
