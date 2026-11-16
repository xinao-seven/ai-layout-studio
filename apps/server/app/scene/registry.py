"""Per-scene runtime registry with asyncio locks (PRD D12)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from app.contracts.enums import Actor
from app.contracts.patch import PatchOp
from app.contracts.proposal import Proposal
from app.contracts.scene import SceneDocument
from app.db.models import Scene
from app.db.repository import SceneRepository
from app.scene.document import CatalogMap
from app.scene.errors import SceneError
from app.scene.service import CommitOutcome, SceneService


@dataclass
class SceneRuntime:
    service: SceneService
    lock: asyncio.Lock


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
        await self._repository.create_scene(document)
        self._runtimes[document.id] = SceneRuntime(
            service=SceneService(document, catalog=self._catalog),
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
            runtime.service = candidate
            return outcome

    async def undo(self, scene_id: str, *, expected_version: int) -> CommitOutcome:
        runtime = await self._runtime(scene_id)
        async with runtime.lock:
            candidate = runtime.service.clone()
            outcome = candidate.undo(expected_version=expected_version)
            await self._repository.save_commit(outcome)
            runtime.service = candidate
            return outcome

    async def redo(self, scene_id: str, *, expected_version: int) -> CommitOutcome:
        runtime = await self._runtime(scene_id)
        async with runtime.lock:
            candidate = runtime.service.clone()
            outcome = candidate.redo(expected_version=expected_version)
            await self._repository.save_commit(outcome)
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
                runtime.service = candidate
            return outcome

    async def reject_proposal(
        self, scene_id: str, proposal_id: str, reason: str | None = None
    ) -> list[str]:
        runtime = await self._runtime(scene_id)
        async with runtime.lock:
            return runtime.service.reject_proposal(proposal_id, reason)

    async def pending_proposals(self, scene_id: str) -> list[Proposal]:
        runtime = await self._runtime(scene_id)
        async with runtime.lock:
            return runtime.service.pending_proposals

    async def version(self, scene_id: str) -> int:
        runtime = await self._runtime(scene_id)
        async with runtime.lock:
            return runtime.service.version
