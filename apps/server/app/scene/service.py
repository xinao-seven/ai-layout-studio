"""Scene service: versioned commits, undo/redo and pending proposal overlay."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from app.contracts.enums import Actor, ProposalStatus
from app.contracts.patch import PatchOp, ScenePatch
from app.contracts.proposal import Proposal
from app.contracts.rules import Rules
from app.contracts.scene import SceneDocument
from app.scene.document import CatalogMap, validate_document
from app.scene.errors import SceneError
from app.scene.patch import apply_ops, invert_resolved_ops


@dataclass(frozen=True)
class CommitOutcome:
    """Internal commit result: public patch plus the inverse for persistence."""

    document: SceneDocument
    patch: ScenePatch
    inverse_ops: list[PatchOp]


def _utc_now() -> datetime:
    return datetime.now(UTC)


class SceneService:
    """Mutable in-memory scene state; callers serialise commits per scene."""

    def __init__(
        self,
        document: SceneDocument,
        *,
        rules: Rules | None = None,
        catalog: CatalogMap | None = None,
    ) -> None:
        validate_document(document, rules=rules, catalog=catalog)
        self._rules = rules
        self._catalog = catalog
        self._document = document.model_copy(deep=True)
        self._undo: list[tuple[list[PatchOp], list[PatchOp]]] = []
        self._redo: list[tuple[list[PatchOp], list[PatchOp]]] = []
        self._pending: dict[str, Proposal] = {}
        self._confirmed: set[str] = set()
        self._idempotency: dict[str, CommitOutcome] = {}

    @property
    def document(self) -> SceneDocument:
        return self._document.model_copy(deep=True)

    @property
    def version(self) -> int:
        return self._document.version

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    @property
    def pending_proposals(self) -> list[Proposal]:
        return [proposal.model_copy(deep=True) for proposal in self._pending.values()]

    def _check_version(self, expected_version: int) -> None:
        if expected_version != self._document.version:
            raise SceneError(
                "version_conflict",
                f"Expected version {expected_version}, current version is {self._document.version}.",
                "Fetch the latest scene snapshot and retry.",
                {"expectedVersion": expected_version, "currentVersion": self._document.version},
            )

    def _advance(
        self, working: SceneDocument, resolved: list[PatchOp]
    ) -> tuple[SceneDocument, ScenePatch]:
        new_version = self._document.version + 1
        new_document = working.model_copy(update={"version": new_version, "updated_at": _utc_now()})
        patch = ScenePatch(
            scene_id=new_document.id,
            from_version=self._document.version,
            to_version=new_version,
            ops=resolved,
        )
        return new_document, patch

    def _expire_all(self, reason: str) -> list[str]:
        expired: list[str] = []
        now = _utc_now()
        for proposal_id, proposal in list(self._pending.items()):
            expired.append(proposal_id)
            self._pending[proposal_id] = proposal.model_copy(
                update={
                    "status": ProposalStatus.EXPIRED,
                    "resolved_at": now,
                    "reason": reason,
                }
            )
        self._pending.clear()
        return expired

    def commit(
        self,
        ops: list[PatchOp],
        *,
        base_version: int,
        actor: Actor = Actor.USER,
        turn_id: str | None = None,
        proposal_id: str | None = None,
        client_mutation_id: str | None = None,
    ) -> CommitOutcome:
        """Apply a change set and produce version + 1."""
        if client_mutation_id is not None:
            cached = self._idempotency.get(client_mutation_id)
            if cached is not None:
                return cached

        self._check_version(base_version)
        working, resolved = apply_ops(self._document, ops, validate=True)
        new_document, patch = self._advance(working, resolved)
        patch = patch.model_copy(
            update={
                "actor": actor,
                "turn_id": turn_id,
                "proposal_id": proposal_id,
                "client_mutation_id": client_mutation_id,
            }
        )
        inverse_ops = invert_resolved_ops(resolved)

        self._document = new_document
        self._undo.append((resolved, inverse_ops))
        self._redo.clear()
        if actor == Actor.USER:
            self._expire_all("scene_changed")

        outcome = CommitOutcome(document=new_document, patch=patch, inverse_ops=inverse_ops)
        if client_mutation_id is not None:
            if len(self._idempotency) >= 100:
                self._idempotency.pop(next(iter(self._idempotency)))
            self._idempotency[client_mutation_id] = outcome
        return outcome

    def undo(self, *, expected_version: int) -> CommitOutcome:
        self._check_version(expected_version)
        if not self._undo:
            raise SceneError(
                "invalid_input",
                "There is no committed change to undo.",
                "Make a change first.",
            )
        forward_ops, inverse_ops = self._undo.pop()
        working, resolved = apply_ops(self._document, inverse_ops, validate=True)
        new_document, patch = self._advance(working, resolved)
        patch = patch.model_copy(update={"actor": Actor.USER})
        self._document = new_document
        self._redo.append((forward_ops, inverse_ops))
        self._expire_all("scene_changed")
        return CommitOutcome(
            document=new_document,
            patch=patch,
            inverse_ops=invert_resolved_ops(resolved),
        )

    def redo(self, *, expected_version: int) -> CommitOutcome:
        self._check_version(expected_version)
        if not self._redo:
            raise SceneError(
                "invalid_input",
                "There is no undone change to redo.",
                "Undo a change first.",
            )
        forward_ops, inverse_ops = self._redo.pop()
        working, resolved = apply_ops(self._document, forward_ops, validate=True)
        new_document, patch = self._advance(working, resolved)
        patch = patch.model_copy(update={"actor": Actor.USER})
        self._document = new_document
        self._undo.append((forward_ops, inverse_ops))
        self._expire_all("scene_changed")
        return CommitOutcome(
            document=new_document,
            patch=patch,
            inverse_ops=invert_resolved_ops(resolved),
        )

    # ---- pending proposals (PRD §6.7) -----------------------------------

    def add_pending(self, proposal: Proposal) -> Proposal:
        if proposal.scene_id != self._document.id:
            raise SceneError(
                "invalid_input",
                "Proposal belongs to a different scene.",
                "Use the current scene id.",
            )
        if proposal.id in self._pending or proposal.id in self._confirmed:
            raise SceneError(
                "invalid_input",
                f"Proposal {proposal.id!r} already exists.",
                "Mint a new proposal id.",
            )
        if proposal.status != ProposalStatus.PENDING:
            raise SceneError(
                "invalid_input",
                "Only pending proposals can be added.",
                "Create the proposal with status pending.",
            )
        if proposal.base_version != self._document.version:
            raise SceneError(
                "proposal_expired",
                f"Proposal baseVersion {proposal.base_version} != current version "
                f"{self._document.version}.",
                "Regenerate the proposal from the latest scene.",
                {"proposalId": proposal.id},
            )
        for dependency in proposal.depends_on:
            if dependency not in self._pending and dependency not in self._confirmed:
                raise SceneError(
                    "proposal_expired",
                    f"Proposal dependency {dependency!r} is missing or expired.",
                    "Regenerate the proposal chain.",
                    {"proposalId": proposal.id, "dependency": dependency},
                )
        stored = proposal.model_copy(deep=True, update={"status": ProposalStatus.PENDING})
        self._pending[proposal.id] = stored
        return stored.model_copy(deep=True)

    def _ordered_pending(self) -> list[Proposal]:
        ordered: list[Proposal] = []
        visiting: set[str] = set()
        visited: set[str] = set()
        pending = self._pending
        for proposal_id in pending:
            if proposal_id in visited:
                continue
            stack = [proposal_id]
            while stack:
                current_id = stack[-1]
                if current_id in visited:
                    stack.pop()
                    continue
                if current_id in visiting:
                    raise SceneError(
                        "proposal_expired",
                        "Proposal dependency cycle detected.",
                        "Regenerate the proposal chain.",
                    )
                visiting.add(current_id)
                proposal = pending.get(current_id)
                if proposal is None:
                    raise SceneError(
                        "proposal_expired",
                        f"Pending proposal {current_id!r} disappeared.",
                        "Regenerate the proposal chain.",
                    )
                unresolved = [
                    dependency
                    for dependency in proposal.depends_on
                    if dependency in pending and dependency not in visited
                ]
                if unresolved:
                    stack.extend(unresolved)
                    continue
                visiting.discard(current_id)
                visited.add(current_id)
                ordered.append(proposal)
                stack.pop()
        return ordered

    def overlay_document(self) -> SceneDocument:
        """Confirmed scene + all pending proposals, in dependency order."""
        working = self._document.model_copy(deep=True)
        for proposal in self._ordered_pending():
            working, _ = apply_ops(working, proposal.ops, validate=False)
        validate_document(working, rules=self._rules, catalog=self._catalog)
        return working

    def confirm_proposal(
        self,
        proposal_id: str,
        *,
        expected_version: int | None = None,
    ) -> CommitOutcome:
        proposal = self._pending.get(proposal_id)
        if proposal is None:
            raise SceneError(
                "not_found",
                f"Pending proposal {proposal_id!r} does not exist.",
                "Query the turn for current proposal ids.",
                {"proposalId": proposal_id},
            )
        if expected_version is not None:
            self._check_version(expected_version)
        for dependency in proposal.depends_on:
            if dependency not in self._confirmed:
                raise SceneError(
                    "proposal_expired",
                    f"Proposal {proposal_id!r} depends on unconfirmed {dependency!r}.",
                    "Confirm proposals in dependency order.",
                    {"proposalId": proposal_id, "dependency": dependency},
                )
        required_version = proposal.base_version + len(proposal.depends_on)
        if self._document.version != required_version:
            self._pending.pop(proposal_id, None)
            raise SceneError(
                "proposal_expired",
                f"Proposal {proposal_id!r} expected version {required_version}, "
                f"current version is {self._document.version}.",
                "Regenerate the proposal.",
                {"proposalId": proposal_id},
            )

        outcome = self.commit(
            proposal.ops,
            base_version=self._document.version,
            actor=Actor.AGENT,
            turn_id=proposal.turn_id,
            proposal_id=proposal.id,
        )
        self._pending.pop(proposal_id, None)
        self._confirmed.add(proposal_id)
        for candidate_id, candidate in list(self._pending.items()):
            if candidate.turn_id != proposal.turn_id:
                self._pending[candidate_id] = candidate.model_copy(
                    update={
                        "status": ProposalStatus.EXPIRED,
                        "resolved_at": _utc_now(),
                        "reason": "scene_changed",
                    }
                )
                self._pending.pop(candidate_id, None)
        return outcome

    def reject_proposal(self, proposal_id: str, reason: str | None = None) -> list[str]:
        proposal = self._pending.pop(proposal_id, None)
        if proposal is None:
            raise SceneError(
                "not_found",
                f"Pending proposal {proposal_id!r} does not exist.",
                "Query the turn for current proposal ids.",
            )
        resolved_at = _utc_now()
        expired: list[str] = []
        # Rejecting a proposal expires everything that depends on it.
        changed = True
        while changed:
            changed = False
            for candidate_id, candidate in list(self._pending.items()):
                if candidate_id == proposal_id:
                    continue
                if set(candidate.depends_on) & {proposal_id, *expired}:
                    expired.append(candidate_id)
                    self._pending[candidate_id] = candidate.model_copy(
                        update={
                            "status": ProposalStatus.EXPIRED,
                            "resolved_at": resolved_at,
                            "reason": f"dependency_rejected:{proposal_id}",
                        }
                    )
                    self._pending.pop(candidate_id, None)
                    changed = True
        return expired

    def expire_pending(self, reason: str = "scene_changed") -> list[str]:
        """Expire all pending proposals (external commit/undo)."""
        return self._expire_all(reason)
