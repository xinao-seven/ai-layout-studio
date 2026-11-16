# AI Interior Layout Workbench

对话式 AI 室内布局工作台：上传/绘制户型，用自然语言和直接操作完成家具布置。
AI Agent 输出**关系约束**，确定性求解器产出坐标，用户确认后落库。

> 当前状态：**M0 脚手架完成**（2026-11-16）。仓库、服务端、前端、数据库骨架、
> 契约生成流水线、CI、Docker 与 ADR 已就绪；M1 起进入场景内核与渲染。
>
> 项目为独立自研实现；aedifex 等开源项目仅作**架构思想参考**，不复制源码。

## 技术栈

| 层 | 选择 |
|---|---|
| 前端 | React 19 · Vite · R3F 9 · three · Zustand/Zundo · TypeScript |
| 服务端 | Python 3.12 · FastAPI · Pydantic v2 · SQLAlchemy 2 async · SQLite |
| 几何/求解 | Shapely 2 · NumPy（服务端权威），客户端轻量 OBB 预检 |
| 契约 | Pydantic → OpenAPI 3.1 / JSON Schema → orval + json-schema-to-typescript |
| 质量 | Ruff · mypy strict · pytest · Biome · Vitest · GitHub Actions |

架构与需求的完整定义见 [PRD.md](PRD.md)，排期与工作包见
[EXECUTION-PLAN.md](EXECUTION-PLAN.md)。

## 仓库结构

```
apps/
  server/     FastAPI 服务端（REST/WS、Agent、几何、求解、VLM、评测）
  web/        React + R3F 客户端
packages/
  contracts/  生成的 OpenAPI/WS schema、rules.json
e2e/          Playwright（后续里程碑）
assets/       GLB 模型与缩略图（后续）
fixtures/     内置户型、评测用例、录制响应（后续）
docs/         ADR、CONVENTIONS、DEPENDENCIES
eval/reports/ 评测报告（指标唯一来源）
scripts/      export_contracts.py、smoke.mjs
```

## 快速开始

前置：Python 3.12（可用 `uv` 自动安装）、Node 24、pnpm 11、Git；Docker 可选。

```bash
# 1. 安装依赖
uv sync --directory apps/server
pnpm install

# 2. 起前后端（web:5173，server:8000）
pnpm dev

# 3. 冒烟（另开终端）
pnpm smoke
SMOKE_WEB=1 pnpm smoke   # 同时检查前端
```

- API 文档：<http://127.0.0.1:8000/api/docs>
- Web：<http://127.0.0.1:5173>
- 健康检查：`GET /api/health` → `{ "ok": true, "version": "0.1.0", "model": "" }`

## 常用命令

| 命令 | 作用 |
|---|---|
| `pnpm dev` | concurrently 起 web + uvicorn（单 worker） |
| `pnpm test` | Vitest + pytest |
| `pnpm typecheck` | `tsc --noEmit` + mypy strict |
| `pnpm lint` | Biome + Ruff |
| `pnpm build` | 前端 production build |
| `pnpm contracts:gen` | 重新导出 schema 并生成 TS |
| `pnpm contracts:check` | 重新生成并检查 git diff（CI 用） |
| `pnpm smoke` | 健康检查冒烟 |
| `uv run --directory apps/server alembic upgrade head` | 应用数据库迁移 |

## 契约流水线

Pydantic 是唯一来源，前端不手写接口：

```
apps/server/app/contracts/            # Pydantic v2（唯一可编辑来源）
  → scripts/export_contracts.py       # 确定性 JSON（排序键）
  → packages/contracts/generated/     # openapi.json、ws.schema.json（入库）
  → orval + json-schema-to-typescript
  → apps/web/src/contracts/generated/ # rest.zod.ts、ws.d.ts（入库）
```

生成物入库，CI 运行 `pnpm contracts:check`，有 diff 即失败。空间常量集中在
`packages/contracts/rules.json`，Python 与 TS 共用。

## 数据库

SQLite + WAL + `foreign_keys=ON` + `busy_timeout=5000`，单 worker 保证单写入语义。
M0 已建立 6 张空表迁移：`scenes`、`scene_versions`、`turns`、`turn_events`、
`proposals`、`eval_runs`。迁移测试覆盖 upgrade/downgrade。

## Docker

```bash
docker compose up --build
# web  → http://127.0.0.1:5173
# api  → http://127.0.0.1:8000/api/health
```

- 端口仅绑定 `127.0.0.1`；容器内服务绑定 `0.0.0.0`。
- 两个镜像均以非 root 用户运行（server UID 10001；web 使用 nginx-unprivileged）。
- SQLite 数据在命名卷 `app-data` 中持久化。
- 服务端启动时自动执行 Alembic 迁移，固定单 worker（多 worker 会启动失败）。

## 安全说明

- API 无账号鉴权，仅面向本地/受信网络；请勿直接暴露到公网。
- LLM/VLM key 只放在服务端 `.env`（参考 `.env.example`），不会进入前端构建。
- 场景 JSON 与截图在启用 AI/VLM 时会发送给第三方模型 provider；VLM 可关闭。
- 资产路径将限制在 `assets/` 白名单内，拒绝绝对路径与 `..`。

## 里程碑

| 阶段 | 内容 | 状态 |
|---|---|---|
| M0 | 脚手架、契约流水线、CI、Docker、ADR | ✅ 2026-11-16 |
| M1 | 场景内核、几何、REST/WS、渲染、undo、协议冻结 | ⏳ 下一步 |
| M2 | 目录、拖放、手绘、门窗、服务端复核 | 计划中 |
| M3 | Agent 循环、9 工具、proposal 链、G1 | 计划中 |
| M4 | 求解器、VLM、30 例评测、G2/G3 | 计划中 |
| M5 | 导出、第一人称、MCP、日照（可选） | 可选 |

## 文档

- [PRD](PRD.md) · [执行计划](EXECUTION-PLAN.md)
- [ADR-0001 服务端真相源](docs/adr/0001-server-source-of-truth.md)
- [ADR-0002 三层工具范式](docs/adr/0002-three-layer-tools.md)
- [ADR-0003 Python/FastAPI 选型](docs/adr/0003-python-fastapi.md)
- [ADR-0004 契约生成](docs/adr/0004-contract-generation.md)
- [CONVENTIONS](docs/CONVENTIONS.md) · [DEPENDENCIES](docs/DEPENDENCIES.md)

## License

[MIT](LICENSE)