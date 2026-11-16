"""Async solver facade: CPU work off the event loop (PRD §5.5, M4-01).

The pure solver lives in :mod:`app.solver.engine`. This wrapper serialises the
scene into a plain payload and runs the search in a one-worker process pool so
a 5 s search never stalls WebSocket heartbeats. Tests and constrained
environments fall back to a thread.
"""

from __future__ import annotations

import asyncio
import multiprocessing
from collections.abc import Iterable
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from typing import Any

import structlog

from app.contracts.catalog import CatalogEntry
from app.contracts.layout import AutoLayoutResult
from app.contracts.rules import Rules
from app.contracts.scene import SceneDocument
from app.core.config import Settings
from app.scene.errors import SceneError
from app.solver.engine import SolveOptions, SolveSpec, solve_layout_payload

logger = structlog.get_logger(__name__)

PROCESS_POOL_GRACE_SECONDS = 3.0


class SolverService:
    """Runs deterministic layout solves without blocking the loop."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._use_process_pool = settings.environment != "test" and settings.solver_process_pool
        self._pool: ProcessPoolExecutor | None = None

    def _executor(self) -> ProcessPoolExecutor:
        if self._pool is None:
            self._pool = ProcessPoolExecutor(
                max_workers=2,
                mp_context=multiprocessing.get_context("spawn"),
            )
        return self._pool

    async def solve(
        self,
        document: SceneDocument,
        catalog: dict[str, CatalogEntry],
        rules: Rules,
        *,
        room_id: str,
        items: list[SolveSpec],
        options: SolveOptions | None = None,
    ) -> AutoLayoutResult:
        options = options or SolveOptions(time_budget_ms=rules.solver_time_budget)
        timeout = options.time_budget_ms / 1000.0 + PROCESS_POOL_GRACE_SECONDS
        payload = {
            "document": document.model_dump(mode="json", by_alias=True),
            "catalog": [entry.model_dump(mode="json", by_alias=True) for entry in catalog.values()],
            "rules": rules.model_dump(mode="json", by_alias=True),
            "roomId": room_id,
            "items": [
                {
                    "kindId": spec.kind_id,
                    "variantId": spec.variant_id,
                    "required": spec.required,
                    "constraint": spec.constraint.model_dump(mode="json", by_alias=True)
                    if spec.constraint is not None
                    else None,
                    "existingItemId": spec.existing_item_id,
                }
                for spec in items
            ],
            "options": {
                "allow_partial": options.allow_partial,
                "seed": options.seed,
                "time_budget_ms": options.time_budget_ms,
                "max_evals": options.max_evals,
                "replace_existing": options.replace_existing,
            },
        }
        loop = asyncio.get_running_loop()
        try:
            if self._use_process_pool:
                raw = await asyncio.wait_for(
                    loop.run_in_executor(self._executor(), solve_layout_payload, payload),
                    timeout=timeout,
                )
            else:
                raw = await asyncio.wait_for(
                    loop.run_in_executor(None, _solve_in_thread, payload),
                    timeout=timeout,
                )
        except TimeoutError as error:
            raise SceneError(
                "solver_timeout",
                "auto_layout exceeded its wall-clock budget.",
                "Reduce the number of required items or allowPartial.",
                {"roomId": room_id, "timeoutMs": int(timeout * 1000)},
            ) from error
        except BrokenProcessPool as error:  # pragma: no cover - worker crash
            logger.exception("solver.pool_broken", error=str(error))
            await self.shutdown()
            raw = await _run_fallback(payload, timeout, room_id)
        except Exception as error:  # pragma: no cover - process boundary failure
            logger.exception("solver.pool_failed", error=str(error))
            raw = await _run_fallback(payload, timeout, room_id)
        if not raw.get("ok", True):
            payload_error = raw.get("error") or {}
            raise SceneError(
                str(payload_error.get("code", "internal_error")),
                str(payload_error.get("message", "auto_layout failed.")),
                str(payload_error.get("suggest", "Retry once.")),
                payload_error.get("details") or {},
            )
        return AutoLayoutResult.model_validate(raw.get("result", raw))

    async def shutdown(self) -> None:
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)
            self._pool = None


def _solve_in_thread(payload: dict[str, Any]) -> dict[str, Any]:
    return solve_layout_payload(payload)


async def _run_fallback(payload: dict[str, Any], timeout: float, room_id: str) -> dict[str, Any]:
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_solve_in_thread, payload),
            timeout=timeout,
        )
    except TimeoutError as error:
        raise SceneError(
            "solver_timeout",
            "auto_layout exceeded its wall-clock budget.",
            "Reduce the number of required items or allowPartial.",
            {"roomId": room_id, "timeoutMs": int(timeout * 1000)},
        ) from error


def build_catalog_map(entries: Iterable[CatalogEntry]) -> dict[str, CatalogEntry]:
    return {entry.kind_id: entry for entry in entries}


__all__ = ["SolverService", "build_catalog_map"]
