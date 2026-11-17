# AI Layout Server

FastAPI service for AI Layout Studio: scene truth source,
version history, geometry validation, REST/WebSocket sync and (from M3) the
agent loop.

Quick start (from repository root):

```bash
uv run --directory apps/server python -m app --reload   # runs Alembic, then serves
curl http://127.0.0.1:8000/api/health
```

The dev/test app creates missing tables automatically; production starts after
the Alembic chain (`python -m app` runs it). SQLite uses WAL, foreign keys and a
5 s busy timeout. Exactly one uvicorn worker is required.

## REST surface (M3)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | liveness + configured model |
| GET | `/api/fixtures` | built-in floor plans |
| GET | `/api/catalog` | 60-item catalog metadata |
| GET/POST | `/api/scenes` | list / create (`fixtureId?`) |
| GET | `/api/scenes/{id}` | confirmed scene snapshot |
| POST | `/api/scenes/{id}/commit` | semantic PatchOps + baseVersion |
| POST | `/api/scenes/{id}/undo` · `/redo` | versioned undo/redo |
| POST | `/api/scenes/{id}/items` | server-minted manual add (free-space placement) |
| PATCH/DELETE | `/api/scenes/{id}/items/{itemId}` | manual move/rotate/delete |
| GET/POST | `/api/scenes/{id}/turns` | turn history / create an agent turn |
| GET | `/api/scenes/{id}/turns/{turnId}` | turn status, messages and usage |
| POST | `/api/turns/{turnId}/cancel` | cancel a running/queued turn |
| GET | `/api/scenes/{id}/proposals?status=` | proposal chain state |
| GET | `/api/scenes/{id}/export` | JSON export |
| POST | `/api/scenes/import` | import with ID remapping |
| WS | `/ws?sceneId=` | state/turn/host channels |

The seven M3 tools are `query_scene`, `search_catalog`, `add_item`, `move_item`,
`remove_item`, `update_item` and `ask_user`. Pydantic input models in
`app/agent/contracts.py` are the single source for `tools.schema.json` and L0
validation. Write tools create proposals; reads are ungated.

Errors use `{ "error": { code, message, suggest?, details? } }`. The frozen
protocol is documented in `docs/PROTOCOL.md`.

## Quality gates

```bash
uv run --directory apps/server ruff check .
uv run --directory apps/server ruff format --check .
uv run --directory apps/server mypy app
uv run --directory apps/server pytest -q
```

86 backend tests cover geometry fixtures, SceneService versions/undo, proposal
chains, repository retention, REST envelopes and WebSocket snapshot/patch/resume.