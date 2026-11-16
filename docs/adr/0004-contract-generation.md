# ADR-0004: Generate frontend contracts from Pydantic

- Status: Accepted
- Date: 2026-11-16
- PRD: D6, §5.6, §6.7
- Deciders: project owner

## Context

With a Python backend, the frontend can no longer import a shared zod package.
Hand-writing REST/WS types would drift from the server models, especially while
the agent protocol is evolving. CI must detect drift before merge.

## Decision

Pydantic v2 models in `apps/server/app/contracts/` are the only schema source.

- FastAPI generates **OpenAPI 3.1** at `packages/contracts/generated/openapi.json`.
- `scripts/export_contracts.py` also exports WebSocket JSON Schema (draft-07,
  `definitions`), because OpenAPI does not cover WebSocket messages.
- **orval** generates REST zod schemas into `apps/web/src/contracts/generated/`.
- **json-schema-to-typescript** generates WS TypeScript interfaces.
- Generated artifacts are committed. `pnpm contracts:check` regenerates and runs
  `scripts/check_contracts.mjs`, which fails when `git status --porcelain`
  reports modified **or untracked** artifacts; the CI `contracts` job runs it.
- Shared spatial constants live in `packages/contracts/rules.json` and are
  consumed by both languages.

## Consequences

- Changing a tool/API shape starts with a Pydantic edit, not a frontend type.
- The WS schema uses draft-07 `definitions` because json-schema-to-typescript
  resolves that reliably; the renderer output is deterministic (sorted keys).
- Generation adds a Node toolchain step to server contract changes; CI catches
  missing regeneration.
- The full turn/state/host discriminated union is added when the protocol is
  frozen at the end of M1; M0 ships the envelope only.