# Dependencies

Versions are pinned by `apps/server/uv.lock` and `pnpm-lock.yaml`. This file
records the main direct dependencies and their licenses; update it whenever a
direct dependency is added.

## Server (Python 3.12)

| Package | Purpose | License |
|---|---|---|
| FastAPI | REST/WS API framework | MIT |
| Uvicorn | ASGI server | BSD-3-Clause |
| Pydantic / pydantic-settings | Contracts and settings | MIT |
| SQLAlchemy (asyncio) | ORM and async persistence | MIT |
| aiosqlite | Async SQLite driver | MIT |
| Alembic | Schema migrations | MIT |
| Shapely | Geometry (authoritative) | BSD-3-Clause |
| NumPy | Numerical solver support | BSD-3-Clause |
| Pillow | Screenshot/image validation | HPND |
| openai | OpenAI-compatible LLM/VLM client | Apache-2.0 |
| httpx | HTTP client and test transport | BSD-3-Clause |
| structlog | Structured logging | Apache-2.0 / MIT |
| slowapi | Rate limiting | MIT |
| python-multipart | Form uploads | Apache-2.0 |

Dev: pytest, pytest-asyncio, pytest-cov (MIT), Ruff (MIT), mypy (MIT),
types-jsonschema (Apache-2.0).

## Web (Node 24)

| Package | Purpose | License |
|---|---|---|
| React / React DOM | UI runtime | MIT |
| three | WebGL renderer | MIT |
| @react-three/fiber | React renderer for three | MIT |
| @react-three/drei | R3F helpers | MIT |
| Zustand | Client state | MIT |
| zod | Generated schema runtime | MIT |
| Vite | Build/dev server | MIT |
| Tailwind CSS | utility styling | MIT |
| Radix UI primitives | accessible tooltip/separator/slot | MIT |
| class-variance-authority / clsx / tailwind-merge | component variants + class merging | MIT |
| lucide-react | icon set | ISC |
| Zundo | undo/redo temporal middleware | MIT |
| @fontsource/barlow | Barlow UI typeface | OFL-1.1 |
| @fontsource/geist-mono | Geist Mono code typeface | OFL-1.1 |
| marked | Markdown rendering for agent replies | MIT |
| highlight.js | Code block highlighting | BSD-3-Clause |
| dompurify | Sanitizes rendered markdown before injection | MPL-2.0 OR Apache-2.0 |
| Vitest | Unit tests | MIT |
| TypeScript | Type checking | Apache-2.0 |
| Biome | Lint/format | MIT |
| orval | OpenAPI → zod generation | MIT |
| json-schema-to-typescript | JSON Schema → TS | MIT |
| concurrently | Root dev runner | MIT |

## Assets

Third-party furniture assets and their licenses are tracked separately in
[`assets/CREDITS.md`](../assets/CREDITS.md).