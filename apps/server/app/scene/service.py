"""Scene service: versioned commits, undo/redo and pending proposal overlay."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta

from app.contracts.api import SceneWarning
from app.contracts.enums import Actor, ProposalStatus
from app.contracts.patch import PatchOp, ScenePatch
from app.contracts.proposal import Proposal
from app.contracts.rules import Rules
from app.contracts.scene import SceneDocument
from app.geometry.openings import GeometryWarning
from app.scene.document import CatalogMap, validate_document
from app.scene.errors import SceneError
from app.scene.patch import apply_ops, invert_resolved_ops

PROPOSAL_TTL = timedelta(minutes=15)


def _scene_warning(warning: GeometryWarning) -> SceneWarning:
    return SceneWarning(
        code=warning.code,
        message=warning.message,
        details=warning.details or None,
    )


@dataclass(frozen=True)
class CommitOutcome:
    """Internal commit result: public patch plus the inverse for persistence."""

    document: SceneDocument
    patch: ScenePatch
    inverse_ops: list[PatchOp]
    warnings: list[SceneWarning] = field(default_factory=list)
    already_confirmed: bool = False
    expired_proposals: list[str] = field(default_factory=list)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


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
        self._confirmed_outcomes: dict[str, CommitOutcome] = {}
        self._rejected: set[str] = set()
        self._idempotency: dict[str, CommitOutcome] = {}

    def update_catalog(self, catalog: CatalogMap | None) -> None:
        """Swap the catalog map after a hot reload."""
        self._catalog = catalog

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
        """Current pending proposals; expiry is explicit via ``expire_due``."""
        return [proposal.model_copy(deep=True) for proposal in self._pending.values()]

    def expire_due(self) -> list[str]:
        """Expire TTL/stale proposals and return every removed id (cascaded)."""
        expired = set(self._expire_ttl())
        expired.update(self._expire_stale_pending())
        return sorted(expired)

    def clone(self) -> SceneService:
        """Copy service state for persist-before-swap commits in the registry."""
        clone = SceneService(self._document, rules=self._rules, catalog=self._catalog)
        clone._undo = list(self._undo)
        clone._redo = list(self._redo)
        clone._pending = dict(self._pending)
        clone._confirmed = set(self._confirmed)
        clone._confirmed_outcomes = dict(self._confirmed_outcomes)
        clone._rejected = set(self._rejected)
        clone._idempotency = dict(self._idempotency)
        return clone

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
        for proposal_id in list(self._pending):
            expired.append(proposal_id)
            self._pending.pop(proposal_id, None)
        return expired

    def _expire_stale_pending(self) -> list[str]:
        """Expire proposals whose expected version can no longer be reached.

        Expiry cascades to dependants so the overlay never applies a chain with
        a missing prerequisite.
        """
        expired: set[str] = set()
        changed = True
        while changed:
            changed = False
            for proposal_id, proposal in list(self._pending.items()):
                if proposal_id in expired:
                    continue
                dependency_expired = any(
                    dependency in expired for dependency in proposal.depends_on
                )
                dependency_missing = any(
                    dependency not in self._pending and dependency not in self._confirmed
                    for dependency in proposal.depends_on
                )
                dependencies_confirmed = all(
                    dependency in self._confirmed for dependency in proposal.depends_on
                )
                required_version = proposal.base_version + len(proposal.depends_on)
                if (
                    dependency_expired
                    or dependency_missing
                    or (dependencies_confirmed and self._document.version != required_version)
                ):
                    expired.add(proposal_id)
                    changed = True
        for proposal_id in expired:
            self._pending.pop(proposal_id, None)
        return sorted(expired)

    def _expire_ttl(self) -> list[str]:
        now = _utc_now()
        expired: set[str] = set()
        for proposal_id, proposal in list(self._pending.items()):
            if now - _as_utc(proposal.created_at) <= PROPOSAL_TTL:
                continue
            expired.add(proposal_id)
            self._pending.pop(proposal_id, None)
        # TTL removal can orphan dependants; cascade in the same pass.
        expired.update(self._expire_stale_pending())
        return sorted(expired)

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
        geometry_warnings: list[GeometryWarning] = []
        working, resolved = apply_ops(
            self._document,
            ops,
            validate=True,
            rules=self._rules,
            catalog=self._catalog,
            warnings=geometry_warnings,
        )
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
        expired: list[str] = []
        if actor == Actor.USER:
            expired = self._expire_all("scene_changed")

        outcome = CommitOutcome(
            document=new_document,
            patch=patch,
            inverse_ops=inverse_ops,
            warnings=[_scene_warning(warning) for warning in geometry_warnings],
            expired_proposals=expired,
        )
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
        forward_ops, inverse_ops = self._undo[-1]
        geometry_warnings: list[GeometryWarning] = []
        working, resolved = apply_ops(
            self._document,
            inverse_ops,
            validate=True,
            reanchor=False,
            check_placement=False,
            rules=self._rules,
            catalog=self._catalog,
            warnings=geometry_warnings,
        )
        new_document, patch = self._advance(working, resolved)
        patch = patch.model_copy(update={"actor": Actor.USER})
        self._undo.pop()
        self._document = new_document
        self._redo.append((forward_ops, inverse_ops))
        expired = self._expire_all("scene_changed")
        return CommitOutcome(
            document=new_document,
            patch=patch,
            inverse_ops=invert_resolved_ops(resolved),
            warnings=[_scene_warning(warning) for warning in geometry_warnings],
            expired_proposals=expired,
        )

    def redo(self, *, expected_version: int) -> CommitOutcome:
        self._check_version(expected_version)
        if not self._redo:
            raise SceneError(
                "invalid_input",
                "There is no undone change to redo.",
                "Undo a change first.",
            )
        forward_ops, inverse_ops = self._redo[-1]
        geometry_warnings: list[GeometryWarning] = []
        working, resolved = apply_ops(
            self._document,
            forward_ops,
            validate=True,
            reanchor=False,
            check_placement=False,
            rules=self._rules,
            catalog=self._catalog,
            warnings=geometry_warnings,
        )
        new_document, patch = self._advance(working, resolved)
        patch = patch.model_copy(update={"actor": Actor.USER})
        self._redo.pop()
        self._document = new_document
        self._undo.append((forward_ops, inverse_ops))
        expired = self._expire_all("scene_changed")
        return CommitOutcome(
            document=new_document,
            patch=patch,
            inverse_ops=invert_resolved_ops(resolved),
            warnings=[_scene_warning(warning) for warning in geometry_warnings],
            expired_proposals=expired,
        )

    # ---- pending proposals (PRD §6.7) -----------------------------------

    def add_pending(self, proposal: Proposal) -> Proposal:
        self._expire_ttl()
        if proposal.scene_id != self._document.id:
            raise SceneError(
                "invalid_input",
                "Proposal belongs to a different scene.",
                "Use the current scene id.",
            )
        if (
            proposal.id in self._pending
            or proposal.id in self._confirmed
            or proposal.id in self._rejected
        ):
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
        depends_on = list(dict.fromkeys(proposal.depends_on))
        for dependency in depends_on:
            if dependency in self._confirmed:
                raise SceneError(
                    "proposal_expired",
                    f"Proposal dependency {dependency!r} is already confirmed.",
                    "A new proposal must depend on pending proposals from the same turn.",
                    {"proposalId": proposal.id, "dependency": dependency},
                )
            if dependency not in self._pending:
                raise SceneError(
                    "proposal_expired",
                    f"Proposal dependency {dependency!r} is missing or expired.",
                    "Regenerate the proposal chain.",
                    {"proposalId": proposal.id, "dependency": dependency},
                )
        stored = proposal.model_copy(
            deep=True,
            update={
                "status": ProposalStatus.PENDING,
                "depends_on": depends_on,
                "created_at": _as_utc(proposal.created_at),
            },
        )
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
        self._expire_ttl()
        self._expire_stale_pending()
        working = self._document.model_copy(deep=True)
        for proposal in self._ordered_pending():
            working, _ = apply_ops(
                working,
                proposal.ops,
                validate=False,
                rules=self._rules,
                catalog=self._catalog,
            )
        validate_document(working, rules=self._rules, catalog=self._catalog)
        return working

    def confirm_proposal(
        self,
        proposal_id: str,
        *,
        expected_version: int | None = None,
    ) -> CommitOutcome:
        cached = self._confirmed_outcomes.get(proposal_id)
        if cached is not None:
            return replace(cached, already_confirmed=True)
        pending_before = set(self._pending)
        ttl_expired = self._expire_ttl()
        stale_expired = self._expire_stale_pending()
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
            self._expire_stale_pending()
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
        self._confirmed_outcomes[proposal_id] = outcome
        # A confirmation from another turn invalidates that turn's siblings.
        other_turn_expired = [
            candidate_id
            for candidate_id, candidate in list(self._pending.items())
            if candidate.turn_id != proposal.turn_id
        ]
        for candidate_id in other_turn_expired:
            self._pending.pop(candidate_id, None)
        stale_after = self._expire_stale_pending()
        expired = sorted(
            {
                *(pending_before - set(self._pending) - {proposal_id}),
                *ttl_expired,
                *stale_expired,
                *other_turn_expired,
                *stale_after,
            }
        )
        return replace(outcome, expired_proposals=expired)

    def reject_proposal(self, proposal_id: str, reason: str | None = None) -> list[str]:
        ttl_expired = set(self._expire_ttl())
        proposal = self._pending.pop(proposal_id, None)
        if proposal is None:
            raise SceneError(
                "not_found",
                f"Pending proposal {proposal_id!r} does not exist.",
                "Query the turn for current proposal ids.",
            )
        self._rejected.add(proposal_id)
        expired: list[str] = []
        # Rejecting a proposal expires everything that depends on it.
        changed = True
        while changed:
            changed = False
            for candidate_id, candidate in list(self._pending.items()):
                if set(candidate.depends_on) & {proposal_id, *expired}:
                    expired.append(candidate_id)
                    self._pending.pop(candidate_id, None)
                    changed = True
        return sorted((set(expired) | ttl_expired) - {proposal_id})

    def expire_pending(self, reason: str = "scene_changed") -> list[str]:
        """Expire all pending proposals (external commit/undo)."""
        return self._expire_all(reason)

    def expire_turn(self, turn_id: str, reason: str = "turn_cancelled") -> list[str]:
        """Expire every pending proposal from ``turn_id`` and its dependants."""
        del reason
        expired: set[str] = set(self._expire_ttl())
        changed = True
        while changed:
            changed = False
            for proposal_id, proposal in list(self._pending.items()):
                if proposal.turn_id == turn_id or set(proposal.depends_on) & expired:
                    expired.add(proposal_id)
                    self._pending.pop(proposal_id, None)
                    changed = True
        return sorted(expired)
