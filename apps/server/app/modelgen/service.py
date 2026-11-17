"""Service facade for parametric asset generation.

Owns the enabled flag, the model-path resolver used by the ``scale`` family, and
the promotion step that turns a temporary proposal asset into a permanent
library entry once the user confirms it.
"""

from __future__ import annotations

from pathlib import Path

import structlog
from pydantic import TypeAdapter, ValidationError

from app.catalog.library import AssetLibrary
from app.contracts.catalog import CatalogEntry
from app.contracts.proposal import Proposal
from app.modelgen.compile import CompiledAsset, CompileError, compile_spec
from app.modelgen.families import ModelPathResolver, SourceAsset
from app.modelgen.specs import FamilySpec, SpecBase
from app.scene.errors import SceneError

logger = structlog.get_logger(__name__)

SPEC_ADAPTER: TypeAdapter[SpecBase] = TypeAdapter(FamilySpec)


class ModelGenService:
    def __init__(
        self,
        library: AssetLibrary,
        *,
        enabled: bool = True,
        resolve_model_path: ModelPathResolver | None = None,
    ) -> None:
        self._library = library
        self._enabled = enabled
        self._resolve_model_path = resolve_model_path

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def library(self) -> AssetLibrary:
        return self._library

    def ensure(self, spec: SpecBase) -> CompiledAsset:
        """Compile (or reuse) the asset for ``spec``.

        Failures are split by cause: an infeasible but well-formed parameter
        combination is the user's problem (``invalid_input``), an unknown source
        kind is a lookup failure, and anything that trips the generator's own
        self-checks is a server bug (``internal_error``).
        """
        if not self._enabled:
            raise SceneError(
                "invalid_input",
                "Parametric asset generation is disabled on this server.",
                "Use a catalog item instead, or ask the operator to enable it.",
            )
        try:
            return compile_spec(
                spec,
                self._library,
                resolve_model_path=self._resolve_model_path,
            )
        except SceneError:
            raise
        except LookupError as error:
            raise SceneError(
                "unknown_kind",
                str(error),
                "Call search_catalog to get an existing kindId first.",
                {"family": getattr(spec, "family", "")},
            ) from error
        except CompileError as error:
            logger.error(
                "modelgen.selfcheck_failed",
                family=getattr(spec, "family", "?"),
                error=str(error),
            )
            raise SceneError(
                "internal_error",
                "The generated asset failed its self-check and was discarded.",
                "Retry with a different size; if it persists this is a server bug.",
                {"family": getattr(spec, "family", ""), "detail": str(error)},
            ) from error
        except (ValueError, OSError) as error:
            logger.info(
                "modelgen.compile_failed",
                family=getattr(spec, "family", "?"),
                error=str(error),
            )
            raise SceneError(
                "invalid_input",
                f"This parameter combination cannot be generated: {error}",
                "Adjust the dimensions, shelf or door counts and try again.",
                {"family": getattr(spec, "family", "")},
            ) from error

    def spec_for(self, kind_id: str) -> SpecBase | None:
        """Rehydrate the generator spec of a library asset."""
        payload = self._library.spec_for(kind_id)
        if payload is None:
            return None
        try:
            return SPEC_ADAPTER.validate_python(payload)
        except ValidationError:
            return None

    def release(self, kind_id: str) -> None:
        """Drop an unpromoted asset after its placement was refused."""
        record = self._library.get(kind_id)
        if record is None or record.promoted:
            return
        self._library.release(kind_id)

    def on_proposal_confirmed(self, proposal: Proposal) -> list[CatalogEntry]:
        """Promote every library asset a confirmed proposal introduces.

        Returns each updated entry so the caller can broadcast
        ``catalog.updated``; a proposal can reference several generated kinds
        (for example a whole-room ``auto_layout``), and hosts need all of them.
        """
        updated: list[CatalogEntry] = []
        for op in proposal.ops:
            if op.op != "item.add":
                continue
            kind_id = op.item.kind_id
            record = self._library.get(kind_id)
            if record is None or record.promoted:
                continue
            try:
                promoted = self._library.promote(kind_id)
            except OSError as error:  # pragma: no cover - disk failure
                logger.warning("modelgen.promote_failed", kind_id=kind_id, error=str(error))
                continue
            if not promoted:
                logger.info("modelgen.promote_failed", kind_id=kind_id)
                continue
            refreshed = self._library.get(kind_id)
            if refreshed is not None:
                updated.append(refreshed.entry)
        return updated


def build_model_path_resolver(
    catalog: object,
    library: AssetLibrary,
    assets_root: Path | None,
) -> ModelPathResolver:
    """Resolve a kindId to an on-disk GLB plus its catalog entry."""

    def resolve(kind_id: str) -> SourceAsset | None:
        record = library.get(kind_id)
        if record is not None:
            path = library.asset_path(record)
            return SourceAsset(path=path, entry=record.entry) if path else None
        get = getattr(catalog, "get", None)
        entry: CatalogEntry | None = get(kind_id) if callable(get) else None
        if entry is None or not entry.variants:
            return None
        model_path = entry.variants[0].model_path
        if assets_root is None or not model_path.startswith("/assets/"):
            return None
        relative = model_path[len("/assets/") :]
        candidate = (assets_root / relative).resolve()
        try:
            candidate.relative_to(assets_root.resolve())
        except ValueError:
            return None
        return SourceAsset(path=candidate, entry=entry) if candidate.is_file() else None

    return resolve