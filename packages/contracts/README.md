# Shared contracts

This package contains artifacts shared by the Python server and the TypeScript
client.

## `rules.json`

Spatial constants from PRD §6.3. Keys are uppercase and unit-bearing by
convention:

- lengths and distances are **meters**
- angles are **degrees**
- time budgets are **milliseconds**

`ROOM_INSET` is derived as `WALL_THICKNESS / 2 + 0.02`; a unit test pins this
relationship so the three values cannot drift silently.

## `generated/`

Committed, deterministic build artifacts produced by the contract pipeline:

```bash
pnpm contracts:gen     # export OpenAPI + WS JSON Schema, then generate TS
pnpm contracts:check   # regenerate and fail on git diff
```

- `openapi.json` — FastAPI OpenAPI 3.1 document (REST)
- `ws.schema.json` — JSON Schema bundle for WebSocket messages

The only editable source is the Pydantic models in
`apps/server/app/contracts/`. Never hand-edit files in `generated/`.

Generated TypeScript lives in `apps/web/src/contracts/generated/`.