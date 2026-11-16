# ADR-0009: Production solver, capture pipeline and VLM closed loop (M4)

- Status: Accepted
- Date: 2026-11-16
- PRD: §6.5, §6.6, FR-14/15/16/17
- Code: `app/solver/`, `app/capture/`, `app/vlm/`, `apps/web/src/components/editor/CaptureOverlay.tsx`

## Context

M2's spike proved candidate generation, deterministic ordering and door
clearance. M4 had to turn it into a production `auto_layout`/`verify_layout`
pair, add a browser screenshot round-trip, and use a VLM without letting it
place furniture.

## Decisions

1. **Pure solver core + async facade.** `solver.engine.solve_layout` takes
   Pydantic document/catalog/rules and returns a result; it is deterministic and
   never mutates the scene. `SolverService` runs it in a one-worker
   `ProcessPoolExecutor` (thread fallback in tests) with a wall-clock budget.
2. **Beam search over deterministic candidates.** Wall-flush (0.25 m), corner,
   group-ring and 0.5 m grid candidates are deduplicated and sorted. Hard
   constraints are inset/collision/door/window/wardrobe-front/passage; scoring
   adds wall affinity, group distance, door margin and centrality. Ties break on
   a rounded coordinate signature.
3. **Convex-only P0.** Concave/L-shaped rooms return `unsupported_room_shape`
   instead of producing a misleading layout.
4. **Verify is a separate read path.** `verify_layout` returns every L2 error and
   L3 warning. It intentionally omits circulation so G3 can use a fixture that
   passes the rules but blocks a route.
5. **Capture is a host request/response, not a server browser.** The frontend
   renders a labelled orthographic view and returns PNG + legend over the
   existing WebSocket (`capture.request` / `capture.result`). The server never
   launches a browser; missing hosts degrade to `capture_unavailable`.
6. **VLM proposes semantics only.** The server builds the authoritative legend,
   validates ids, rechecks each issue with local geometry (circulation uses a
   grid BFS from doors to the room centre/windows), and maps confirmed issues to
   a local solver run. The result is a normal pending proposal.
7. **Screenshots are bounded and disposable.** Long edge ≤1536 px, ≤1 MB,
   stored under `data/screenshots/`, cleaned after 7 days; a failed capture never
   fails a turn.

## Consequences

- Solver and VLM are testable headlessly; CI uses scripted LLM/VLM and an
  in-process capture provider.
- All placements still require user confirmation through the proposal chain.
- Real VLM quality and 200-instance render p95 remain live/nightly metrics.