# AI Layout Studio

> **Agent-driven 3D interior layout studio.** Draw or import a floor plan, place
> furniture by hand or by conversation. The LLM only chooses *semantic relations*
> ("against the south wall", "next to the bed") — a deterministic server-side
> core owns geometry, collision checks, versions and undo.
>
> - **Editor**: React + three.js canvas, catalog drag & drop, room drawing, doors
>   and windows, first-person walkthrough, JSON/PNG export.
> - **Agent**: streaming tool loop over 11 tools, ghost proposals confirmed by the
>   user, whole-room beam-search solver, VLM visual review.
> - **Agent UI**: resizable right-hand dock, token-level markdown replies with
>   thinking blocks and collapsible tool cards, per-turn reasoning level.
> - **Procedural assets**: the agent can *build* furniture — parametric families
>   and free box composition — into a reusable scene library.
> - **Contract-first**: Pydantic is the only schema source; REST/WS/TS types are
>   generated and drift-checked in CI.

对话式 AI 室内布局工作台：上传/绘制户型，用自然语言和直接操作完成家具布置。
AI Agent 输出**关系约束**，确定性求解器产出坐标，用户确认后落库。

> 当前状态：**M7 完成（2026-11-17）**。M1–M6 交付场景内核、目录与拖放、Agent 回合、
> 求解器 + VLM 闭环、导出/漫游/MCP/日照、参数化资产库；M7 重做 **Agent 界面**：
> 对话搬到右侧可拖拽 Dock（360–720px，可收起、双击复位、尺寸持久化，窄屏浮层），
> 助手回复改为逐 token 的 markdown 渲染（代码高亮/复制、表格、列表），
> 思考过程与工具调用改为可折叠卡片；服务端补齐 **reasoning 全链路**——
> `assistant.reasoning_delta` 流、`TurnMessage.reasoning` 持久化、
> `GET /api/agent/capabilities`，以及按 provider 自动映射的
> `thinkingLevel`（off/low/medium/high，`LLM_REASONING_STYLE` 可覆盖）。
>
> 项目为独立自研实现；aedifex / Pascal Editor（MIT）等开源项目仅作**架构与视觉参考**，
> 不复制源码。

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
  server/     FastAPI 服务端（REST/WS、Agent、几何、求解、VLM、资产生成、评测）
    app/agent/     TurnManager、LLMAdapter、ContextBuilder、ToolRegistry、placement/rules
    app/modelgen/  spec → 网格 → GLB 编译、参数化族（含自由图元组合）、缩略图
    app/scene/     权威场景文档、补丁、proposal 运行时
    app/solver/    beam search 求解、走道寻路、布局审计
    app/vlm/       截图审查（只输出受限 issue schema）
  web/        React + R3F 客户端
packages/
  contracts/  生成的 OpenAPI/WS/tools schema、rules.json、geometry-fixtures.json
e2e/          Playwright（后续里程碑）
assets/       GLB 模型与缩略图（60 件 CC0 资产）
fixtures/     内置户型、评测用例、LLM 录制脚本
docs/         ADR、PROTOCOL、CONVENTIONS、DEPENDENCIES、计划文档
eval/reports/ 评测报告（指标唯一来源）
scripts/      export_contracts.py、smoke.mjs、资产构建/校验
data/         运行时数据（SQLite、截图、生成资产库；已 gitignore）
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
# 推理挡位默认 off；LLM_REASONING_STYLE=auto 会按端点识别 provider，
# 自定义网关可显式设为 openai/deepseek/anthropic/qwen/glm/none。

# 3. 起前后端（web:5173，server:8000；server 启动时自动执行 Alembic 迁移）
pnpm dev

# 4. 冒烟（另开终端）
pnpm smoke
SMOKE_WEB=1 pnpm smoke   # 同时检查前端
```

- API 文档：<http://127.0.0.1:8000/api/docs>
- Web：<http://127.0.0.1:5173>
- 健康检查：`GET /api/health` → `{ "ok": true, "version": "0.1.0", "model": "" }`
- 首页会列出 6 个内置户型（含空白主卧 G2 场景与 G3 视觉检查场景）；打开后可 3D/俯视
  切换、从目录拖放家具（松开前显示绿色/红色 ghost）、拖拽移动、R 旋转 15°、方向键微调、
  删除、undo/redo。
- 家具面板支持分类/搜索/缩略图与 `POST /api/catalog/reload` 热加载；详情卡显示
  尺寸/位置/朝向/约束并生成 PointContext；选中后可拖**右下角手柄**或用侧栏步进器
  等比缩放（`0.4×–2.0×`，超过层高会被拒绝）。
- 房间面板可手绘闭合房间（默认直角 + 0.1m 网格吸附，Shift 自由绘制，实时段长标注）、
  拖动顶点（吸附到场景内**任意**房间的顶点坐标，0.25m 半径，跨房间共墙不再差一格）、
  拖动墙段平移整面墙（两端点同步移动，几何重合的共享墙在两个房间一起移动）、
  选择墙段放置门/窗并调整 offset/width/height/sill/swing；改多边形后服务端重锚定
  开口并返回 warning toast。
- AI 面板可发送自然语言：模型先 `query_scene`/`search_catalog`，写工具生成青色幽灵
  提案；"画一个 4×5 米的卧室"用 `add_room`（输出轮廓顶点，服务端 0.1m 吸附 + 直角/面积
  校验，虚线幽灵预览）；"做一个 2.4 米通顶书柜"用 `model_item` 现场生成资产；
  确认后按依赖顺序落库，可 undo；支持取消、ask_user 回答和断线重连。
- 整室布置用 `auto_layout`（床/衣柜/床头柜一次成提案）；"检查布局"用
  `verify_layout(visual=true)`：前端自动切正交俯视、给家具/门窗打 A1/D1/W1 标签并回传
  PNG，VLM 复核后生成局部重排提案。
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
| `pnpm eval` | 跑 30 例共享用例表并写 `eval/reports/YYYY-MM-DD-m4-eval.json` |
| `uv run --directory apps/server pytest tests/test_agent_m3.py tests/test_agent_lifecycle.py -q` | G1/工具/提案链/回合生命周期回归 |
| `uv run --directory apps/server pytest tests/test_solver_m4.py tests/test_m4_agent.py -q` | G2/G3、求解器、verify、VLM 与截图链路回归 |
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
| M4 | 求解器、VLM、30 例评测、G2/G3 | ✅ 2026-11-16 |
| M5 | 导出/导入、第一人称、MCP、户型图 VLM、日照 | ✅ 2026-11-16 |
| M6 | 参数化资产库、`model_item`+7 族、家具缩放、边缘吸附、整墙平移 | ✅ 2026-11-17 |

## M6：参数化资产与编辑器增强

### Agent 现场建模（`model_item`）

Agent 不再只能挑目录里的 60 件家具，而是按 **spec** 现场编译 GLB，再走完全相同的
proposal / ghost / 确认流程。工具数从 9 增至 **11**（`query_scene`、`search_catalog`、
`add_item`、`add_room`、`move_item`、`remove_item`、`update_item`、`model_item`、
`auto_layout`、`verify_layout`、`ask_user`）。

| 族 | 参数（范围，米） | 生成内容 |
|---|---|---|
| `bookshelf` | 宽 0.4–3.0 · 深 0.18–0.6 · 高 0.6–2.8 · 层板 1–8 · 门 0–4（可玻璃） | 柜体 + 层板 + 柜门 + 五金 |
| `wardrobe` | 宽 0.6–3.6 · 深 0.45–0.75 · 高 1.6–2.8 · 门 1–4 · 平开/推拉 · 挂衣杆 | 柜体 + 挂杆 + 门（推拉门错层） |
| `tv_stand` | 宽 0.8–2.6 · 深 0.3–0.55 · 高 0.3–0.75 · 抽屉 0–3 · 开放格 | 柜体 + 抽屉面板 + 把手 |
| `desk_l` | 主台 1.0–2.2 · 进深 0.5–0.8 · 回字 0.35–0.9 × 0.8–1.6 · 高 0.68–0.78 | L 形台面 + 桌腿 + 抽屉柜 |
| `counter_run` | 长 0.9–4.0 · 深 0.5–0.7 · 高 0.8–0.95 · 分段 1–4 · 水槽/灶台 | 踢脚 + 柜体 + 台面 + 嵌件 |
| `scale` | 已有 kindId · x/y/z 倍数 0.6–1.4 | 按比例缩放已有模型（限 storage/table/bed） |
| `primitives` | 1–24 个轴对齐盒子（尺寸/中心点，y=0 为地面） | 长尾简单物体，如晾衣架、鞋架、花架 |

约束：spec 哈希是资产身份（12 位），同 spec 永远编译出同样的字节；服务器自检
AABB 与声明尺寸一致、底面贴地、三角形/字节上限、整体尺寸包络；`primitives` 还要求
至少一个部件贴地且不得低于地面。生成的资产登记进 `data/assets/`，用户确认后从
`tmp/` 晋升到 `models/` 并永久保留，可通过 `search_catalog(source="library")` 复用。

### 协议与资产库

- `catalog.updated`（state 通道）：新资产在引用它的 `proposal.created` **之前**推送，
  保证前端画 ghost 时已拿到模型 URL。
- `/assets/library/{models,thumbnails,tmp}` 静态服务（manifest 不对外），Vite/nginx
  使用窄代理，`pnpm check:web-assets` 防止误配成宽代理遮蔽 bundle。
- 确认时在广播 `scene.patch` 之前完成晋升；被任一持久化场景引用的临时资产永不回收。

### 编辑器

- **家具缩放**：`Transform.scale` 是文档字段（默认 1.0），碰撞/净空/求解器全部按
  缩放后尺寸计算；范围 0.4–2.0，超过房间层高拒绝。
- **边缘吸附**：拖动家具时边缘在 0.25m 内吸附到墙面内缩线或相邻家具边缘，使
  「缝隙为 0 或 ≥0.6m」的走道规则可以被手工达成。
- **预检对齐**：客户端预检补齐走道窄缝与窗下净空，不再出现「ghost 绿色但提交被拒」。
- **整面墙平移**：拖动墙面平移墙线，两端点一起移动；几何重合的共享墙在两个房间
  同时移动；实时校验自相交、最小面积与家具出墙。

验证（2026-11-17）：后端 `pytest` 232 项、前端 `vitest` 109 项通过；`ruff`、`mypy`、
`tsc`、`biome` 全绿；生成的 GLB 经自检（尺寸/贴地/三角形数）后由 `/assets/library`
提供并被前端渲染。

## M5 加餐使用说明

- **导出/导入**：编辑器右上「导出 JSON / 导出 PNG」；首页「导入方案」上传导出文件，
  服务端重映射 ID 后新建场景（schemaVersion 校验，非法文档返回 `import_invalid`）。
- **第一人称漫游**：画布右上「漫游」→ 点击画面锁定指针，WASD/方向键移动、Shift 加速、
  ESC 退出锁定；门洞可通行、家具与墙有碰撞；漫游中编辑交互全部禁用。
- **日照环境层**：画布右上 ☀ 按钮 → 启用日照、拖时间轴（太阳时，12:00 = 太阳正午）、
  填经纬度（`scene.update` 提交，可 undo，随场景保存）。
- **MCP server**：`pnpm mcp`（等价 `uv run --directory apps/server python -m app.mcp`），
  stdio JSON-RPC 2.0。`tools/list` 暴露 `query_scene` / `search_catalog` / `add_item` /
  `move_item` / `remove_item`，schema 与 chat 同源；`tools/call` 参数加保留键
  `sceneId` 指定场景，写工具走同一套 L1–L3 校验并直接落库（无确认 UI）。
  Claude Desktop 等客户端示例配置见 [ADR-0010](docs/adr/0010-m5-additions.md)。
- **户型图照片**：首页「户型图照片」上传 ≤5MB JPEG/PNG → VLM 解析为未保存草案
  （无效房间降级为 warning）→ 弹窗内修正名称/类型 → 创建场景（服务端再次校验
  + ID 重映射）。需配置 `VLM_BASE_URL / VLM_API_KEY / VLM_MODEL`。

## 文档

- [PRD](PRD.md) · [执行计划](EXECUTION-PLAN.md)
- [ADR-0001 服务端真相源](docs/adr/0001-server-source-of-truth.md)
- [ADR-0002 三层工具范式](docs/adr/0002-three-layer-tools.md)
- [ADR-0003 Python/FastAPI 选型](docs/adr/0003-python-fastapi.md)
- [ADR-0004 契约生成](docs/adr/0004-contract-generation.md)
- [ADR-0005 前端视觉语言](docs/adr/0005-frontend-visual-language.md)
- [ADR-0008 求解器 spike](docs/adr/0008-solver-spike.md)
- [ADR-0009 生产求解器与 VLM 闭环](docs/adr/0009-solver-vlm-m4.md)
- [ADR-0010 M5 加餐的范围与折衷](docs/adr/0010-m5-additions.md)
- [ADR-0011 运行时参数化资产库](docs/adr/0011-runtime-asset-library.md)
- [PROTOCOL（含 M6 资产库与 catalog.updated）](docs/PROTOCOL.md)
- [CONVENTIONS](docs/CONVENTIONS.md) · [DEPENDENCIES](docs/DEPENDENCIES.md)

## M4 实测指标（来源：`eval/reports/2026-11-16-m4-eval.json`）

| 指标 | 实测 | 说明 |
|---|---|---|
| 共享用例通过率 | **30/30 (100%)** | operation 13 / solver 6 / verify 8 / VLM 3 |
| `auto_layout` p95 | **104 ms** | 4 次评测求解；20 件最坏用例 3.6 s（`m4-perf.json`），预算 5000 ms |
| `auto_layout` 平均候选评估 | 893 | 上限 50k；同输入同输出（单测） |
| G2 床尾朝南 | 通过 | `dot(front, north) < -0.99` + `verify_layout` 零问题 |
| G2 真模型 | 通过（`deepseek-flash`） | 4 轮、16.3 s、1 个 3-op 提案、0 refusal、确认后 verify 0 问题 |
| G3 视觉闭环 | 通过（脚本化 VLM） | 主路径阻断 → VLM → 几何复核 → 局部重排 → 复检通过；真实 VLM 未配置 |
| VLM JSON 解析率 | 100%（每例 1 次调用） | 脚本化 VLM 桩；真实 VLM 未配置 |
| VLM 误报过滤 | 1 条误报被几何复核拒绝，0 个未知 ID | 3 例脚本化 VLM 用例，样本较小 |
| before/after 截图 | 通过 | WS 回传 PNG → ≤1MB/1536 → `data/screenshots/` |
| 渲染 FPS | **120 / 120 / 120**（8 次采样） | `?perf=200`，5 房间 + 16 家具 + 200 代理盒，IAB WebGL2 |

## License

[MIT](LICENSE)