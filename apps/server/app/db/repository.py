"""Async repository for scenes and append-only version history."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.contracts.scene import SceneDocument
from app.db.models import Scene, SceneVersion
from app.scene.errors import SceneError
from app.scene.service import CommitOutcome

KEEP_VERSIONS = 100


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
