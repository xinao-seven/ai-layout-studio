# Conventions

Project-wide conventions for the AI interior layout workbench. PRD references
are to [PRD.md](../PRD.md) v1.2.

## Coordinate system (PRD §4.3)

- World axes: **+X east, −Z north, +Y up** (three.js right-handed).
- Floor coordinates are `Vec2 = [x, z]` in **meters**.
- Angles are **radians** in storage, **degrees** in UI and `rules.json`.
- `rotationY` rotates around +Y. Furniture front direction is
  `[sin(rotationY), cos(rotationY)]`; assets are authored with front `+Z`.
- `north` is a unit `Vec2`, default `[0, -1]`.

## Units and tolerances

- Lengths in meters, time in seconds, solver budgets in milliseconds.
- Geometry assertions use tolerance `1e-6`.
- Evaluation coordinate comparisons use tolerance `1e-3 m`.

## Rooms, walls, openings

- `Room.polygon` is the **wall centerline**, CCW (positive signed area), simple
  (no self-intersection), at least 3 points, area ≥ 1 m².
- `WallSegment.id` is `${roomId}:e${edgeIndex}`, derived on read — never stored.
- Opening `offset` is measured from the edge start along the centerline to the
  opening center; openings use `edgeIndex`, not free-floating wall references.
- Door/window re-anchoring rules and warnings (`opening_reanchored`,
  `opening_removed`) follow PRD §5.2.

## Identifiers and naming

- Server mints IDs with prefixes: `scene_`, `room_`, `op_`, `it_`, `prop_`,
  `turn_`, `seg_`.
- JSON over the wire is **camelCase**. Python internals are **snake_case**;
  Pydantic `alias_generator=to_camel` bridges the two.
- Enums are Python `StrEnum` / TypeScript string-literal unions; JSON values are
  identical and generated from Pydantic.
- Timestamps are ISO 8601 UTC with a timezone, e.g. `2026-11-16T12:00:00Z`.

## Contracts

Pydantic v2 is the single source of truth (PRD D6, §5.6):

```
Pydantic (apps/server/app/contracts)
  → scripts/export_contracts.py
  → packages/contracts/generated/{openapi.json, ws.schema.json}
  → orval (REST zod) + json-schema-to-typescript (WS types)
  → apps/web/src/contracts/generated/
```

- Never hand-write a second REST/WS schema on the frontend.
- Shared spatial constants live in `packages/contracts/rules.json`.
- `pnpm contracts:check` regenerates and fails when `git status --porcelain`
  reports modified or untracked artifacts; CI runs it.

## Testing

- Python: Ruff + mypy strict + pytest (`uv run --directory apps/server pytest`).
- Web: `tsc --noEmit`, Biome, Vitest; Playwright for the later `ui` runner.
- Every refusal code needs at least one case, with a non-empty `suggest`.
- Geometry/SceneService/validation/solver target ≥80% line coverage.
- Do not merge skipped tests, issue-less `xfail`s, or TODOs on the critical path.

## Git and reviews

- Branches: `feat/m1-scene-service`, `fix/...`, `docs/...`.
- Commits: `feat(server): ...`, `fix(web): ...`, `docs(adr): ...`, `test(...)`.
- Tags per gate: `v0.1-m1`, `v0.2-m2`, `v0.3-g1`, `v0.4-g2`, `v0.5-g3`, `v1.0.0`.
- Architectural decisions are recorded in `docs/adr/`; protocol changes require
  an ADR update before implementation.