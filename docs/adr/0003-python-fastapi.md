# ADR-0003: Python 3.12 + FastAPI + Pydantic v2 for the backend

- Status: Accepted
- Date: 2026-11-16
- PRD: D15, §11.1
- Deciders: project owner
- Supersedes: the earlier (v1.0) Node/Fastify/zod backend choice

## Context

The heavy server-side work is geometry (Shapely), constraint solving
(NumPy/Shapely), agent orchestration (OpenAI-compatible SDK) and evaluation
(pytest). A TypeScript backend would force either duplicate geometry or awkward
Python subprocesses. The project is a single-user self-hosted app; there is no
requirement for multiple workers or horizontal scale.

## Decision

Use **Python 3.12 + FastAPI + Pydantic v2** on the server, with SQLAlchemy 2.0
async + aiosqlite + Alembic for persistence. Run with `uvicorn --workers 1`.
Use `uv` for dependency management. The frontend remains React 19 + Vite + R3F.

## Consequences

- Geometry, solver, agent and evaluation live in one language and one process.
- Cross-language contracts must be generated rather than shared as a zod package
  (ADR-0004).
- Single-writer SQLite and in-process asyncio locks require exactly one worker;
  multi-worker deployment would need a DB lease/outbox design (explicitly out of
  scope for this milestone).
- Python CPU-heavy solving must move to a process pool to keep WebSocket
  heartbeats alive (M4).
- CI runs Ruff, mypy strict (Pydantic plugin) and pytest; Docker pins Python
  3.12-slim.