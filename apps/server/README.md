# AI Layout Server

FastAPI service for the AI interior layout workbench.

Quick start (from repository root):

```bash
uv run --directory apps/server python -m app --reload
curl http://127.0.0.1:8000/api/health
```

Quality gates:

```bash
uv run --directory apps/server ruff check .
uv run --directory apps/server ruff format --check .
uv run --directory apps/server mypy app
uv run --directory apps/server pytest -q
```