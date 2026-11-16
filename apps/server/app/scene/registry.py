"""Per-scene runtime registry with asyncio locks (PRD D12)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.contracts.enums import Actor, FacingToward, ProposalStatus
from app.contracts.ids import IdPrefix, new_id
from app.contracts.patch import PatchOp
from app.contracts.proposal import Proposal, Refusal
from app.contracts.scene import FacingTowardSpec, ItemConstraint, SceneDocument
from app.db.models import Scene
from app.db.repository import SceneRepository
from app.scene.document import CatalogMap
from app.scene.errors import SceneError
from app.scene.service import CommitOutcome, SceneService


@dataclass
class SceneRuntime:
    service: SceneService
    lock: asyncio.Lock


@dataclass
class ProposalBuild:
    """Result of a write-tool builder running under the scene lock."""

    ops: list[PatchOp] = field(default_factory=list)
    refusals: list[Refusal] = field(default_factory=list)
    document_after: SceneDocument | None = None


@dataclass(frozen=True)
class OverlayView:
    """Confirmed + pending overlay plus the ids that are still pending."""

    document: SceneDocument
    pending: list[Proposal]
    pending_item_ids: set[str]
    pending_proposal_ids: set[str]
    expired_ids: list[str] = field(default_factory=list)


def _constraint_item_refs(constraint: ItemConstraint | None) -> set[str]:
    if constraint is None:
        return set()
    refs: set[str] = set()
    if constraint.near is not None and constraint.near.item_id is not None:
        refs.add(constraint.near.item_id)
    facing = constraint.facing
    if (
        isinstance(facing, FacingTowardSpec)
        and facing.toward == FacingToward.ITEM
        and facing.id is not None
    ):
        refs.add(facing.id)
    return refs


class SceneRegistry:
    """Loads scenes on demand and serialises mutations per scene."""

    def __init__(
        self,
        repository: SceneRepository,
        *,
        catalog: CatalogMap | None = None,
    ) -> None:
        self._repository = repository
        self._catalog = catalog
        self._runtimes: dict[str, SceneRuntime] = {}
        self._load_lock = asyncio.Lock()

    def update_catalog(self, catalog: CatalogMap | None) -> None:
        self._catalog = catalog
        for runtime in self._runtimes.values():
            runtime.service.update_catalog(catalog)

    def validate_catalog(self, catalog: CatalogMap) -> None:
        """Reject a hot reload that would make a live scene invalid."""
        from app.scene.document import validate_document

        for runtime in self._runtimes.values():
            validate_document(runtime.service.document, catalog=catalog)

    async def list_scenes(self) -> Sequence[Scene]:
        return await self._repository.list_scenes()

    async def _runtime(self, scene_id: str) -> SceneRuntime:
        runtime = self._runtimes.get(scene_id)
        if runtime is not None:
            return runtime
        # Serialise lazy loads so two concurrent requests cannot build two
        # independent services (and therefore two undo stacks) for one scene.
        async with self._load_lock:
            runtime = self._runtimes.get(scene_id)
            if runtime is not None:
                return runtime
            document = await self._repository.get_scene(scene_id)
            if document is None:
                raise SceneError(
                    "not_found",
                    f"Scene {scene_id!r} does not exist.",
                    "List scenes and pick an existing id.",
                    {"sceneId": scene_id},
                )
            runtime = SceneRuntime(
                service=SceneService(document, catalog=self._catalog),
                lock=asyncio.Lock(),
            )
            self._runtimes[scene_id] = runtime
            return runtime

    async def create_scene(self, document: SceneDocument) -> SceneDocument:
        # Validate (including catalog refs) before inserting so a rejected
        # import cannot leave an orphan scene row behind.
        service = SceneService(document, catalog=self._catalog)
        await self._repository.create_scene(document)
        self._runtimes[document.id] = SceneRuntime(
            service=service,
            lock=asyncio.Lock(),
        )
        return document

    async def get_document(self, scene_id: str) -> SceneDocument:
        runtime = await self._runtime(scene_id)
        async with runtime.lock:
            return runtime.service.document

    async def overlay_document(self, scene_id: str) -> SceneDocument:
        runtime = await self._runtime(scene_id)
        async with runtime.lock:
            return runtime.service.overlay_document()

    async def commit(
        self,
        scene_id: str,
        ops: list[PatchOp],
        *,
        base_version: int,
        actor: Actor = Actor.USER,
        client_mutation_id: str | None = None,
    ) -> CommitOutcome:
        runtime = await self._runtime(scene_id)
        async with runtime.lock:
            candidate = runtime.service.clone()
            outcome = candidate.commit(
                ops,
                base_version=base_version,
                actor=actor,
                client_mutation_id=client_mutation_id,
            )
            # Idempotent duplicate commits return the cached outcome without
            # advancing the version; do not write a second version row.
            if outcome.document.version > runtime.service.version:
                await self._repository.save_commit(outcome)
            if outcome.expired_proposals:
                await self._repository.expire_pending_proposals(scene_id, outcome.expired_proposals)
            runtime.service = candidate
            return outcome

    async def commit_with(
        self,
        scene_id: str,
        build_ops: Callable[[SceneDocument], list[PatchOp]],
    ) -> CommitOutcome:
        """Build and commit ops while holding the scene lock (manual edits).

        The builder reads the current document under the same lock, so the
        commit cannot race another writer between read and write.
        """
        runtime = await self._runtime(scene_id)
        async with runtime.lock:
            candidate = runtime.service.clone()
            ops = build_ops(candidate.document)
            outcome = candidate.commit(
                ops,
                base_version=candidate.version,
                actor=Actor.USER,
            )
            await self._repository.save_commit(outcome)
            if outcome.expired_proposals:
                await self._repository.expire_pending_proposals(scene_id, outcome.expired_proposals)
            runtime.service = candidate
            return outcome

    async def undo(self, scene_id: str, *, expected_version: int) -> CommitOutcome:
        runtime = await self._runtime(scene_id)
        async with runtime.lock:
            candidate = runtime.service.clone()
            outcome = candidate.undo(expected_version=expected_version)
            await self._repository.save_commit(outcome)
            if outcome.expired_proposals:
                await self._repository.expire_pending_proposals(scene_id, outcome.expired_proposals)
            runtime.service = candidate
            return outcome

    async def redo(self, scene_id: str, *, expected_version: int) -> CommitOutcome:
        runtime = await self._runtime(scene_id)
        async with runtime.lock:
            candidate = runtime.service.clone()
            outcome = candidate.redo(expected_version=expected_version)
            await self._repository.save_commit(outcome)
            if outcome.expired_proposals:
                await self._repository.expire_pending_proposals(scene_id, outcome.expired_proposals)
            runtime.service = candidate
            return outcome

    async def add_pending(self, scene_id: str, proposal: Proposal) -> Proposal:
        runtime = await self._runtime(scene_id)
        async with runtime.lock:
            return runtime.service.add_pending(proposal)

    async def confirm_proposal(self, scene_id: str, proposal_id: str) -> CommitOutcome:
        runtime = await self._runtime(scene_id)
        async with runtime.lock:
            candidate = runtime.service.clone()
            outcome = candidate.confirm_proposal(proposal_id)
            if not outcome.already_confirmed:
                await self._repository.save_commit(outcome)
                await self._repository.update_proposal_status(proposal_id, ProposalStatus.CONFIRMED)
                if outcome.expired_proposals:
                    await self._repository.expire_pending_proposals(
                        scene_id, outcome.expired_proposals
                    )
                runtime.service = candidate
            return outcome

    async def reject_proposal(
        self, scene_id: str, proposal_id: str, reason: str | None = None
    ) -> list[str]:
        runtime = await self._runtime(scene_id)
        async with runtime.lock:
            candidate = runtime.service.clone()
            expired = candidate.reject_proposal(proposal_id, reason)
            await self._repository.update_proposal_status(
                proposal_id, ProposalStatus.REJECTED, reason=reason or "user_rejected"
            )
            if expired:
                await self._repository.expire_pending_proposals(
                    scene_id, expired, reason="dependency_rejected"
                )
            runtime.service = candidate
            return expired

    async def expire_turn_proposals(
        self, scene_id: str, turn_id: str, *, reason: str = "turn_cancelled"
    ) -> list[str]:
        runtime = await self._runtime(scene_id)
        async with runtime.lock:
            candidate = runtime.service.clone()
            expired = candidate.expire_turn(turn_id, reason)
            if expired:
                await self._repository.expire_pending_proposals(scene_id, expired, reason=reason)
            runtime.service = candidate
            return expired

    async def expire_due(self, scene_id: str) -> list[str]:
        """Expire TTL/stale proposals and persist the terminal statuses."""
        runtime = await self._runtime(scene_id)
        async with runtime.lock:
            expired = runtime.service.expire_due()
            if expired:
                await self._repository.expire_pending_proposals(
                    scene_id, expired, reason="proposal_expired"
                )
            return expired

    async def pending_proposals(self, scene_id: str) -> list[Proposal]:
        await self.expire_due(scene_id)
        runtime = await self._runtime(scene_id)
        async with runtime.lock:
            return runtime.service.pending_proposals

    async def get_proposal(self, proposal_id: str) -> Proposal | None:
        return await self._repository.get_proposal(proposal_id)

    async def list_proposals(self, scene_id: str) -> list[Proposal]:
        """Plain listing; callers that need expiry call ``expire_due`` first."""
        return list(await self._repository.list_proposals(scene_id))

    async def overlay_view(self, scene_id: str) -> OverlayView:
        """Return confirmed+pending overlay and mark which items are provisional."""
        runtime = await self._runtime(scene_id)
        async with runtime.lock:
            expired = runtime.service.expire_due()
            if expired:
                await self._repository.expire_pending_proposals(
                    scene_id, expired, reason="proposal_expired"
                )
            overlay = runtime.service.overlay_document()
            pending = runtime.service.pending_proposals
            confirmed_ids = {item.id for item in runtime.service.document.items}
            overlay_ids = {item.id for item in overlay.items}
            touched: set[str] = set()
            for proposal in pending:
                for op in proposal.ops:
                    if op.op == "item.add":
                        touched.add(op.item.id)
                    elif op.op in {"item.update", "item.remove"}:
                        touched.add(op.item_id)
            return OverlayView(
                document=overlay,
                pending=pending,
                pending_item_ids=(overlay_ids - confirmed_ids) | touched,
                pending_proposal_ids={proposal.id for proposal in pending},
                expired_ids=expired,
            )

    async def propose(
        self,
        scene_id: str,
        turn_id: str,
        builder: Callable[[SceneDocument, list[Proposal]], ProposalBuild],
    ) -> tuple[Proposal | None, list[Refusal]]:
        """Run a write-tool builder and persist a pending proposal atomically."""
        runtime = await self._runtime(scene_id)
        async with runtime.lock:
            expired = runtime.service.expire_due()
            if expired:
                await self._repository.expire_pending_proposals(
                    scene_id, expired, reason="proposal_expired"
                )
            overlay = runtime.service.overlay_document()
            pending = runtime.service.pending_proposals
            build = builder(overlay, pending)
            if build.refusals:
                return None, build.refusals
            if not build.ops:
                return None, [
                    Refusal(
                        code="no_space",
                        message="The tool produced no change to propose.",
                        suggest="Adjust the request or query the scene again.",
                    )
                ]
            # Never create a proposal that references or mutates an item owned
            # by another turn's pending proposal: that turn's confirmation
            # would be impossible because the dependency is not same-turn.
            existing_item_ids = {item.id for item in overlay.items}
            proposed_add_ids: set[str] = set()
            other_turn_items: set[str] = set()
            for candidate in pending:
                if candidate.turn_id == turn_id:
                    continue
                for op in candidate.ops:
                    if op.op == "item.add":
                        other_turn_items.add(op.item.id)
                    elif op.op in {"item.update", "item.remove"}:
                        other_turn_items.add(op.item_id)
            for op in build.ops:
                if op.op == "item.add":
                    if op.item.id in existing_item_ids or op.item.id in proposed_add_ids:
                        return None, [
                            Refusal(
                                code="invalid_input",
                                message=f"Item {op.item.id} already exists or is used twice.",
                                suggest="Regenerate the proposal with fresh item ids.",
                                details={"itemId": op.item.id},
                            )
                        ]
                    proposed_add_ids.add(op.item.id)
                elif op.op in {"item.update", "item.remove"} and op.item_id not in (
                    existing_item_ids
                ):
                    return None, [
                        Refusal(
                            code="not_found",
                            message=f"Item {op.item_id} no longer exists.",
                            suggest="Regenerate the proposal from the latest scene.",
                            details={"itemId": op.item_id},
                        )
                    ]
                if op.op in {"item.update", "item.remove"} and op.item_id in other_turn_items:
                    return None, [
                        Refusal(
                            code="proposal_expired",
                            message=(
                                f"Item {op.item_id} is still being changed by an earlier "
                                "pending proposal."
                            ),
                            suggest="Confirm or reject the earlier proposal, then retry.",
                            details={"itemId": op.item_id},
                        )
                    ]
                constraint: ItemConstraint | None = None
                if op.op == "item.add":
                    constraint = op.item.constraint
                elif op.op == "item.update":
                    constraint = op.after.constraint
                for referenced_id in _constraint_item_refs(constraint):
                    if referenced_id in other_turn_items:
                        return None, [
                            Refusal(
                                code="proposal_expired",
                                message=(
                                    f"Item {referenced_id} is still pending in an earlier turn."
                                ),
                                suggest=(
                                    "Confirm the earlier proposal first, or reference a "
                                    "confirmed item."
                                ),
                                details={"itemId": referenced_id},
                            )
                        ]
            proposal = Proposal(
                id=new_id(IdPrefix.PROPOSAL),
                scene_id=scene_id,
                turn_id=turn_id,
                base_version=runtime.service.version,
                # Same-turn dependencies only: other turns' pending proposals
                # are overlay context, and confirming this turn expires them.
                depends_on=[candidate.id for candidate in pending if candidate.turn_id == turn_id],
                ops=build.ops,
                status=ProposalStatus.PENDING,
                created_at=datetime.now(UTC),
            )
            next_service = runtime.service.clone()
            next_service.add_pending(proposal)
            await self._repository.save_proposal(proposal)
            runtime.service = next_service
            return proposal.model_copy(deep=True), []

    async def version(self, scene_id: str) -> int:
        runtime = await self._runtime(scene_id)
        async with runtime.lock:
            return runtime.service.version
