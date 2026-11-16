# AI Interior Layout Workbench

对话式 AI 室内布局工作台：上传/绘制户型，用自然语言和直接操作完成家具布置。
AI Agent 输出**关系约束**，确定性求解器产出坐标，用户确认后落库。

> 当前状态：**M3 Agent 层完成**（2026-11-16；真模型冒烟待配置凭据后执行）。7 个 Agent 工具（查询/检索/新增/移动/
> 删除/更新/ask_user）、TurnManager（单场景 1 running + 1 queued）、OpenAI 兼容流式
> LLMAdapter、ContextBuilder/场景快照、五层校验（L0–L3）、pending overlay + 幽灵提案、
> 依赖链确认/拒绝/过期、WS turn 事件/取消/断线重连、G1 脚本化用例与 OpenAI 兼容假服务
> 已打通。M2 的 60 件 CC0 资产、拖放/吸附/旋转、服务端碰撞复核、手绘房间、门窗开口、
> 详情卡 + PointContext 保持兼容。界面视觉参照 aedifex / Pascal Editor（MIT），代码为
> 独立实现。
>
> 项目为独立自研实现；aedifex 等开源项目仅作**架构思想参考**，不复制源码。

## 技术栈

| 层 | 选择 |
|---|---|
| 前端 | React 19 · Vite 7 · R3F 9 · three · Zustand/Zundo · Tailwind 4 · shadcn 风格组件 · TypeScript |
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
    app/agent/  TurnManager、LLMAdapter、ContextBuilder、ToolRegistry、placement/rules
  web/        React + R3F 客户端
packages/
  contracts/  生成的 OpenAPI/WS/tools schema、rules.json、geometry-fixtures.json
e2e/          Playwright（后续里程碑）
assets/       GLB 模型与缩略图
fixtures/     内置户型、评测用例、LLM 录制脚本
docs/         ADR、PROTOCOL、CONVENTIONS、DEPENDENCIES
eval/reports/ 评测报告（指标唯一来源）
scripts/      export_contracts.py、smoke.mjs、资产构建/校验
```

## 快速开始

前置：Python 3.12（可用 `uv` 自动安装）、Node 24、pnpm 11、Git；Docker 可选。

```bash
# 1. 安装依赖
uv sync --directory apps/server
pnpm install

# 2. 配置 LLM（可选；不配置时对话回合会以 llm_error 失败）
cp .env.example .env
# 填 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL（GLM、Qwen、DeepSeek、OpenAI 均可）

# 3. 起前后端（web:5173，server:8000；server 启动时自动执行 Alembic 迁移）
pnpm dev

# 4. 冒烟（另开终端）
pnpm smoke
SMOKE_WEB=1 pnpm smoke   # 同时检查前端
```

- API 文档：<http://127.0.0.1:8000/api/docs>
- Web：<http://127.0.0.1:5173>
- 健康检查：`GET /api/health` → `{ "ok": true, "version": "0.1.0", "model": "" }`
- 首页会列出 4 个内置户型；打开后可 3D/俯视切换、从目录拖放家具（松开前显示
  绿色/红色 ghost）、拖拽移动、R 旋转 15°、方向键微调、删除、undo/redo。
- 家具面板支持分类/搜索/缩略图与 `POST /api/catalog/reload` 热加载；详情卡显示
  尺寸/位置/朝向/约束并生成 PointContext。
- 房间面板可手绘闭合房间、拖动顶点、选择墙段放置门/窗并调整 offset/width/height/
  sill/swing；改多边形后服务端重锚定开口并返回 warning toast。
- AI 面板可发送自然语言：模型先 `query_scene`/`search_catalog`，写工具生成青色幽灵
  提案；确认后按依赖顺序落库，可 undo；支持取消、ask_user 回答和断线重连。
- 带 `?perf=200` 时渲染 200 个代理盒并显示 FPS。

## 常用命令

| 命令 | 作用 |
|---|---|
| `pnpm dev` | concurrently 起 web + uvicorn（单 worker） |
| `pnpm test` | Vitest + pytest |
| `pnpm typecheck` | `tsc --noEmit` + mypy strict |
| `pnpm lint` | Biome + Ruff |
| `pnpm build` | 前端 production build |
| `pnpm contracts:gen` | 重新导出 schema 并生成 TS |
| `pnpm contracts:check` | 重新生成并检查改动/新增（CI 用） |
| `pnpm smoke` | 健康检查冒烟 |
| `uv run --directory apps/server pytest tests/test_agent_m3.py tests/test_agent_lifecycle.py -q` | G1/工具/提案链/回合生命周期回归 |
| `uv run --directory apps/server pytest tests/test_fake_openai_e2e.py -q` | 真 OpenAI SDK 路径 + 假 LLM 服务 |
| `pnpm check:web-assets` | 构建后校验 nginx 资产代理不与 Vite bundle 冲突 |
| `uv run --directory apps/server python ../../scripts/build_catalog_assets.py --check` | 校验 60 件资产/尺寸/缩略图/许可 |
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
- 资产路径限制在 `assets/` 白名单内；目录校验拒绝绝对路径、`..`、缺失文件与
  尺寸/三角面/缩略图漂移。

## 里程碑

| 阶段 | 内容 | 状态 |
|---|---|---|
| M0 | 脚手架、契约流水线、CI、Docker、ADR | ✅ 2026-11-16 |
| M1 | 场景内核、几何、REST/WS、渲染、undo、协议冻结 | ✅ 2026-11-16 |
| M2 | 目录、拖放、手绘、门窗、服务端复核、评测骨架、求解器 spike | ✅ 2026-11-16 |
| M3 | Agent 循环、7 工具、proposal 链、G1 | ✅ 2026-11-16 |
| M4 | 求解器、VLM、30 例评测、G2/G3 | 计划中 |
| M5 | 导出、第一人称、MCP、日照（可选） | 可选 |

## 文档

- [PRD](PRD.md) · [执行计划](EXECUTION-PLAN.md)
- [ADR-0001 服务端真相源](docs/adr/0001-server-source-of-truth.md)
- [ADR-0002 三层工具范式](docs/adr/0002-three-layer-tools.md)
- [ADR-0003 Python/FastAPI 选型](docs/adr/0003-python-fastapi.md)
- [ADR-0004 契约生成](docs/adr/0004-contract-generation.md)
- [ADR-0005 前端视觉语言](docs/adr/0005-frontend-visual-language.md)
- [PROTOCOL（M1 冻结 + M2 增量）](docs/PROTOCOL.md)
- [ADR-0008 求解器 spike](docs/adr/0008-solver-spike.md)
- [CONVENTIONS](docs/CONVENTIONS.md) · [DEPENDENCIES](docs/DEPENDENCIES.md)

## License

[MIT](LICENSE)