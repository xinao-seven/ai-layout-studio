# AI 室内布局工作台 · 执行计划

| 项 | 内容 |
|---|---|
| 依据 | [PRD.md](D:/code/three/PRD.md) v1.2（2026-11-16） |
| 计划版本 | v1.0 |
| 团队假设 | 1 人全职（前端 + 后端 + Agent + 评测），5 天/周 |
| 核心预算 | 57 理想人日（约 12 周） |
| 日历排期 | 14 周（13 周核心含节假日 + 1 周加固缓冲） |
| 起始 | 2026-11-16（周一） |
| 核心目标完成 | 2027-02-19（周五，含缓冲） |
| 加餐 | M5 可选，W15 起 1–2 周 |
| 状态 | 待执行 |

> 说明：PRD 的 M0–M4 基线是 10.5 周。单人全栈要同时做 3D 编辑器、Agent、求解器、VLM 和评测，实际联调与踩坑会吃掉估算，因此本计划按 **13 周核心（含节假日）+ 1 周加固缓冲** 编排；若必须压到 10.5 周，按 §11 的裁剪顺序砍 P1/M5，保留 G1–G3 与 30 例评测。

---

## 0. 一页摘要

**建造顺序**

```
契约与规则 → 几何内核 → SceneService/版本 → REST/WS → 渲染与编辑
→ Agent 循环 → 工具/提案链 → 求解器 → VLM → 评测与指标 → 打包
```

**12 周核心路线**

| 周 | 主目标 | 周末 Gate |
|---|---|---|
| W1 | M0 脚手架 + M1 契约模型起步 | 前后端起得来、CI 绿、契约生成链路通 |
| W2–W4 | M1 内核 + 渲染 | 加载 JSON 场景、3D/俯视渲染、undo、WS 同步 |
| W4–W6 | M2 编辑交互 | 手绘房间 + 门窗、拖放/碰撞、手动布置完客厅 |
| W6–W9 | M3 Agent | G1 通过、7 工具、proposal 链、reconnect/cancel |
| W9–W13 | M4 求解器 + VLM + 评测 | G2/G3 通过、30 例评测、指标报告 |
| W14 | 缓冲 + 加固 + Demo | Docker 一键起、README/Demo、量化指标 |
| W15+ | M5 可选 | 导出、第一人称、MCP、户型图 VLM、日照 |

**成功标准（核心）**

- G1/G2/G3 脚本化 100% 通过；真模型 G1–G3 ≥90%（每例 3 次）。
- 评测集 ≥30 例，三 runner 同用例表；PR 跑 pytest unit/api-ws，nightly 跑 Playwright/live。
- `auto_layout` p95 ≤5s；典型两居 200 实例 60fps（p95）。
- VLM JSON 可解析率 100%（含 1 次重试）；几何复核误报率 ≤20%。
- 断线重连 ≤2s；`uvicorn --workers 1` 下单场景状态一致。
- Docker Compose 一键起；README 含架构图、Demo、指标表、运行说明。

**Top 5 风险与提前动作**

1. 协议在 M3 才返工 → W4 末冻结 PatchOp/WS/Proposal 状态机。
2. 求解器卡壳 → W6 做 2 天 spike，失败则降级为规则模板布局。
3. 跨语言契约漂移 → Pydantic 单一来源 + `contracts:check` 阻断。
4. Python CPU 阻塞事件循环 → 求解放进程池，W9 前压测。
5. 资产/缩略图拖慢 M2 → W2 就开始下载与规范化，先用线框盒占位。

---

## 1. 目标与验收

### 1.1 交付目标

| 维度 | 目标 |
|---|---|
| 产品 | 从户型输入到布置方案导出/导入的完整闭环 |
| 技术 | Python/FastAPI 服务端 Agent + React/R3F 编辑器 + Shapely 求解 + VLM 闭环 + 30 例评测 |
| 简历 | 四条亮点均有可演示产物和实测数字：Agent 架构 / VLM 闭环 / 人机协同 / 质量工程 |

### 1.2 Definition of Done（整个项目）

- [ ] G1「沙发 + 茶几」端到端通过：两个 proposal、依赖链确认、可 undo、无 refusal。
- [ ] G2「卧室整室布局」通过：`auto_layout` ≤5s、床尾朝南、`verify_layout` 零问题。
- [ ] G3「VLM 检查 + 局部重排」通过：JSON 可解析、几何复核、复检通过。
- [ ] 评测集 ≥30 例，脚本化 runner 100%；报告落 `eval/reports/`。
- [ ] 三 runner（pytest operation / pytest api-ws / Playwright ui）共享同一用例表。
- [ ] PR CI 绿：Ruff + mypy + pytest + typecheck + Biome + contracts diff。
- [ ] Docker Compose 一键起 web + server + SQLite volume。
- [ ] README、ADR、CONVENTIONS、CREDITS、LICENSE、Demo 视频齐全。
- [ ] 简历数字全部来自 `eval/reports/`，无估写。

### 1.3 关键指标

| 指标 | 目标 | 采集方式 |
|---|---|---|
| G1–G3 脚本化通过率 | 100% | `eval/reports` |
| G1–G3 真模型通过率 | ≥90%（3 次/例） | nightly live |
| 平均 turn 轮次 | ≤6 | turn usage |
| refusal 后自纠成功率 | ≥70% | 事件流分析 |
| `auto_layout` p95 | ≤5s | solver stats |
| 渲染 | 200 实例 60fps p95 | perf harness |
| VLM JSON 可解析率 | 100%（1 次重试） | VLM 日志 |
| VLM 误报率 | ≤20% | 几何复核 |
| 重连恢复 | ≤2s | WS 测试 |

---

## 2. 前提与约束

| 项 | 约束 |
|---|---|
| 人力 | 1 人全职；估算为理想工作日，含实现 + 测试 + 文档，不含等待模型/构建 |
| 技术栈 | Python 3.12 + uv + FastAPI + Pydantic v2 + SQLAlchemy 2.0 async + Shapely 2.x；React 19 + Vite + R3F + Zustand/Zundo |
| 部署 | 单用户自部署；`uvicorn --workers 1`；SQLite WAL；不做多客户端实时协同 |
| 模型 | OpenAI 兼容 LLM（GLM/Qwen/DeepSeek 可配）；VLM（GLM-4V/Qwen-VL 可配）；key 只在服务端 |
| 形状 | P0 凸多边形；正交 L 型 P1；任意凹形 `auto_layout` 拒绝 |
| 资产 | Kenney/Quaternius 同风格低模；单品 ≤1MB/15k tris；首屏 ≤20MB |
| 质量 | 几何/校验/求解核心模块行覆盖 ≥80%；refusal 码 100% 有用例 |
| 日历 | W6/W7 含圣诞/元旦，W13 含春节；按 3–4 个有效工作日估算，差额由 buffer 吸收 |

---

## 3. 执行原则

1. **契约先行**：任何工具/接口先改 Pydantic 模型 → 跑 `pnpm contracts` → 再写 Operation 和 UI。禁止前端手写重复接口。
2. **垂直切片**：M3 先打通「读工具 + add_item + ghost + confirm」一条链，再铺其余 6 个工具；不要按工具横向铺开。
3. **测试随任务走**：每个 Work Package 完成时，单测/契约测试同时完成；不留「最后补测试」。
4. **协议早冻结**：W4 末冻结 PatchOp、WS 信封、Proposal 状态机；M3 只实现不重构。
5. **Demo 驱动**：每个里程碑都有可演示脚本；拿不出 Demo 视为未完成。
6. **小步提交**：每个 WP 1–3 个 PR；PR 必须绿；架构决策写 ADR。
7. **先占位后替换**：资产、缩略图、VLM 桩、LLM 录制都允许先用占位，但接口和断言先定。
8. **每周留 buffer**：周计划按 4.5 天排，0.5 天用于修 CI/技术债/记录。

---

## 4. 里程碑预算与路线图

### 4.1 预算

| 阶段 | PRD 基线 | 执行预算 | 主要产出 | 退出 Gate |
|---|---|---|---|---|
| M0 | 0.5 周 | 4 d | 混合仓库、FastAPI、Vite/R3F、CI、Docker、契约链路 | 前后端起得来、CI 绿 |
| M1 | 2 周 | 13 d | Pydantic 模型、几何、SceneService、版本/patch、REST/WS、渲染、undo | 加载 fixture、3D/俯视、undo、协议冻结 |
| M2 | 2 周 | 11 d | 目录、拖放、碰撞、手绘、门窗、服务端复核、评测骨架、求解器 spike | 手动布置完一个客厅 |
| M3 | 2.5 周 | 15 d | TurnManager、LLMAdapter、7 工具、五层校验、proposal 链、G1 | G1 通过、refusal 全覆盖 |
| M4 | 2.5 周 | 14 d | 求解器、verify_layout、VLM 闭环、before/after、30 例评测 | G2/G3 通过、指标报告 |
| 缓冲 | — | 3 d | 联调、修缺陷、Demo、Docker 加固 | 可发布 |
| **核心合计** | **10.5 周（PRD 基线）** | **60 d（含 3 d 缓冲）** | — | — |
| M5 | 1–2 周 | 8 d | 导出、第一人称、MCP、户型图 VLM、日照 | 择做 |

### 4.2 日历排期（14 周）

| 周 | 日期 | 主里程碑 | 说明 |
|---|---|---|---|
| W1 | 11/16–11/20 | M0 + M1 起步 | 仓库/CI/Docker/契约链路；M1 模型开始 |
| W2 | 11/23–11/27 | M1 | 几何 + SceneService |
| W3 | 11/30–12/04 | M1 | 持久化 + REST + WS |
| W4 | 12/07–12/11 | M1 收尾 + M2 起步 | 渲染 + undo；**协议冻结 Gate** |
| W5 | 12/14–12/18 | M2 | 目录 + 拖放 + 碰撞 |
| W6 | 12/21–12/25 | M2 | 手绘/门窗；圣诞周，按 3.5 d 估 |
| W7 | 12/28–01/01 | M2 收尾 + M3 起步 | 服务端复核、spike；元旦周 |
| W8 | 01/04–01/08 | M3 | TurnManager/LLMAdapter/垂直切片 |
| W9 | 01/11–01/15 | M3 | 7 工具 + 五层校验；M4 求解器起步 |
| W10 | 01/18–01/22 | M3 收尾 | proposal 链 + WS + G1 |
| W11 | 01/25–01/29 | M4 | 求解器 + G2 |
| W12 | 02/01–02/05 | M4 | VLM + G3 + 评测 |
| W13 | 02/08–02/12 | M4 收尾 + 缓冲 | 春节周；M4-05/06 收尾，只做低风险修复 |
| W14 | 02/15–02/19 | 加固 + Demo | 指标、README、Demo、发布 |
| W15+ | — | M5 可选 | 导出/第一人称/MCP/户型图/日照 |

> 里程碑允许跨周重叠 2–3 天（例如 W4 后半段开始 M2）。如果某周被假期打断，优先保 Gate，宁可推迟 M5。

---

## 5. 工作分解（WBS）

> 估算单位：理想人日（d）。每个 WP 的验收都包含「实现 + 测试 + 契约/文档更新」。

### 5.1 M0 脚手架（4 d）

| ID | 工作包 | 主要任务 | 估算 | 依赖 | 产出 / 验收 |
|---|---|---|---|---|---|
| M0-01 | 仓库与规范 | `git init`；目录 `apps/server`、`apps/web`、`packages/contracts`、`e2e`、`assets`、`fixtures`、`docs`、`eval`；`.gitignore`、`.editorconfig`、MIT LICENSE | 0.5 | — | 目录齐、首次提交、README 占位 |
| M0-02 | Python 服务端 | `uv init --python 3.12`；FastAPI `/api/health`；pydantic-settings；structlog；Ruff/mypy/pytest 配置 | 0.5 | M0-01 | `uv run uvicorn` 起服务；`/api/health` 返回 `{ok, version}`；`ruff/mypy/pytest` 绿 |
| M0-03 | 前端脚手架 | pnpm workspace；Vite + React 19 + R3F 空画布；Zustand；Biome/tsconfig；Vitest | 0.5 | M0-01 | `pnpm --filter web dev` 出画布；`pnpm typecheck` 绿 |
| M0-04 | 数据库骨架 | SQLAlchemy 2.0 async + aiosqlite；Alembic 初始化；`data/` 目录；WAL/busy_timeout | 0.5 | M0-02 | `alembic upgrade head` 成功；空表可连接 |
| M0-05 | 契约流水线 | `scripts/export_contracts.py` 骨架；Pydantic → OpenAPI/JSON Schema；`packages/contracts` 生成物；orval/json-schema-to-typescript 配置 | 0.5 | M0-02 | 生成 `openapi.json`/`ws.schema.json`/TS 类型；`contracts:check` 可跑 |
| M0-06 | CI 与钩子 | GitHub Actions：server/web/contracts 三 job；pre-commit（ruff/format）；PR 模板 | 0.5 | M0-02/03/05 | PR 触发 CI；三 job 绿 |
| M0-07 | Docker | `apps/server/Dockerfile`（python:3.12-slim + uv）；`apps/web/Dockerfile`（node build + nginx）；Compose + volume + healthcheck | 0.5 | M0-02/03 | `docker compose up` 起 web+server；刷新持久化 |
| M0-08 | ADR/README/冒烟 | ADR-0001（服务端真相源）、0002（三层工具）、0003（Python/FastAPI 选型）、0004（契约生成）；CONVENTIONS；冒烟脚本 | 0.5 | M0-01..07 | `pnpm dev` 一键起；文档可读；冒烟通过 |

**M0 退出条件**

- [ ] `pnpm dev` 同时起 web（5173）与 server（8000）。
- [ ] `/api/health`、`/api/docs`（FastAPI OpenAPI）可访问。
- [ ] `uv run ruff check`、`uv run mypy`、`uv run pytest`、`pnpm typecheck` 全绿。
- [ ] `pnpm contracts:check` 可运行且无 diff。
- [ ] `docker compose up` 可用；SQLite 数据在 volume 中。
- [ ] ADR-0001..0004 已提交。

---

### 5.2 M1 内核 + 渲染（13 d）

| ID | 工作包 | 主要任务 | 估算 | 依赖 | 产出 / 验收 |
|---|---|---|---|---|---|
| M1-01 | Pydantic 模型与规则 | `SceneDocument/Room/Opening/Item/Catalog/Proposal/Turn/Patch/Refusal`；`rules.json`；camelCase alias；枚举；ID 前缀 | 1.5 | M0-05 | 模型可导出 OpenAPI/JSON Schema；`rules.json` 被 Python/TS 读取 |
| M1-02 | 几何内核 | 有向面积/简单多边形/点在内；派生 WallSegment/法线/内缩区；`shapely` 包装；OBB SAT；`geometry-fixtures.json` | 2.0 | M1-01 | pytest + Vitest 跑同一组 fixtures；边界用例覆盖 |
| M1-03 | SceneService + overlay | normalize/validate；confirmed + pending overlay；commit/inverse；版本号；`apply_patch` 语义 | 2.0 | M1-02 | 单测：加/删/改 item、房间、开口；版本单调；overlay 查询 |
| M1-04 | 持久化 | SQLAlchemy 模型（6 表）；Alembic 初始迁移；repository；保留 100 版 | 0.5 | M1-03 | 迁移测试；重启后场景/版本可恢复 |
| M1-05 | REST API | scenes CRUD、commit/undo/redo、catalog、export/import 占位；统一错误 envelope | 1.0 | M1-03/04 | httpx 测试覆盖 2xx/4xx/refusal；OpenAPI 文档可读 |
| M1-06 | WS Gateway | 信封 `{v,ch,type,seq,ts,payload}`；三通道；seq；心跳；resume 骨架 | 1.0 | M1-03 | ws 测试：连接、echo、seq 单调、断线重连补发/快照 |
| M1-07 | 契约生成 | 导出 OpenAPI/WS/tools schema；前端生成 TS；CI diff 检查 | 0.5 | M1-01/05/06 | `pnpm contracts` 一键生成；CI 阻断 drift |
| M1-08 | 渲染壳与场景 | R3F Canvas；相机/轨道/两光源/阴影；房间/墙/地板/开口渲染；GLB loader + 线框盒兜底 | 2.0 | M1-01/07 | 加载 fixture 场景；墙厚/开口位置正确；缺失 GLB 不崩 |
| M1-09 | 家具与俯视 | Item 渲染；pivot/rotation/frontAxis；正交俯视；Tab 切换；墙剖切/隐藏 0.15m | 1.0 | M1-08 | 3D/俯视一致；俯视不遮挡；家具朝向正确 |
| M1-10 | 客户端状态与 undo | Zustand store；snapshot/patch apply；Zundo 镜像；undo/redo 调服务端 | 1.0 | M1-05 | 手动加/移/删后 undo/redo；刷新从服务端重建 |
| M1-11 | 测试/夹具/协议冻结 | 3–5 个内置户型 JSON；几何/SceneService/WS 测试；协议文档；200 代理盒 perf harness | 0.5 | M1-01..10 | fixture schema 校验；协议冻结文档；perf 基线记录 |

**M1 退出条件**

- [ ] 首页可选 3–5 个内置户型，加载并渲染。
- [ ] 3D/俯视切换正常；墙/门窗/家具位置正确。
- [ ] 手动添加/移动/删除家具，undo/redo 正常。
- [ ] REST/WS 连通；断线重连能拿到快照或补发。
- [ ] 几何/SceneService 单测通过；几何 fixtures 双端对等。
- [ ] PatchOp/WS/Proposal 状态机文档冻结（W4 末 Gate）。

---

### 5.3 M2 编辑交互（11 d）

| ID | 工作包 | 主要任务 | 估算 | 依赖 | 产出 / 验收 |
|---|---|---|---|---|---|
| M2-01 | 目录资产与 API | 下载/规范化 Kenney/Quaternius GLB；缩略图；`catalog.json`；`GET /api/catalog` + reload；CREDITS | 2.0 | M1-08 | 30–60 件可加载；缩略图/尺寸/正面轴正确；缺失资产兜底 |
| M2-02 | 目录 UI | 分类、搜索、缩略图、拖拽源；选中态；详情摘要 | 1.0 | M2-01 | 搜索/分类可用；拖拽到画布触发放置 |
| M2-03 | 拖放/吸附/旋转/选择 | 地面射线放置；0.1m 网格；15° 旋转；选择高亮；删除；拖拽本地实时 | 2.0 | M1-09/10, M2-02 | 拖拽流畅；落位 snapping；一次拖拽一次 undo |
| M2-04 | 预检 + 服务端复核 | 客户端 OBB SAT + 房间边界红色警示；落位 commit；服务端 L2/L3；失败回滚 + toast | 1.5 | M2-03, M1-05 | 碰撞不可落位；服务端拒绝时 UI 回滚；refusal 可读 |
| M2-05 | 手绘房间 | 点击画墙中心线；0.1m 吸附；闭合；顶点编辑；简单多边形校验 | 1.5 | M1-02/09 | 画出闭合房间；非法 polygon 拒绝；顶点编辑可用 |
| M2-06 | 门窗开口 | 墙段选择；门/窗放置；offset/width/height/sill/swing；重锚定 | 1.0 | M2-05, M1-03 | 门窗贴墙；重叠/越界拒绝；polygon 编辑后重锚定有警告 |
| M2-07 | 详情卡 + PointContext | 显示 kind/variant/尺寸/位置/朝向/约束；点选反哺对话上下文 | 0.5 | M2-03 | 点选显示正确；上下文包含 id/room/transform |
| M2-08 | 评测骨架 + 求解器 spike | 用例 schema；operation runner；fixture loader；2 天 spike：候选位姿/门净空/成组可行性 | 1.5 | M1-11, M2-04 | `pytest` 可跑首批 5 例；spike 结论写入 ADR-0008 |

**M2 退出条件**

- [ ] 纯手动布置完一个客厅：沙发 + 茶几 + 电视柜 + 地毯，无碰撞。
- [ ] 拖拽碰撞有红色警示，落位被服务端拒绝时回滚。
- [ ] 手绘一个矩形房间并放门/窗，保存后刷新不丢。
- [ ] 详情卡可点选并显示属性；PointContext 可被后续对话使用。
- [ ] 评测 operation runner 可跑；求解器 spike 有结论。

---

### 5.4 M3 Agent 层（15 d）

| ID | 工作包 | 主要任务 | 估算 | 依赖 | 产出 / 验收 |
|---|---|---|---|---|---|
| M3-01 | TurnManager + LLMAdapter | 建 turn/队列/lock/cancel/waiting_user；`AsyncOpenAI` 流式；tool_calls 攒齐/去重；重试/超时；usage；provider 差异 | 2.0 | M1-05/06 | 单场景 1 running + 队列 1；取消生效；fake LLM 可回放 |
| M3-02 | ContextBuilder + ToolRegistry | system prompt；场景快照（含 north/wallLabels/pending）；目录摘要；token 预算；Pydantic → JSON Schema → tools；L0 | 1.5 | M3-01, M1-01 | 工具 schema 正确；上下文不超预算；快照含 pending |
| M3-03 | 垂直切片 | `query_scene`/`search_catalog`；`add_item` proposal；pending overlay；ghost；confirm；对话 UI + 工具过程 | 3.5 | M3-02 | 用户一句话 → 检索 → ghost → 确认落库；单测 + api-ws 测试 |
| M3-04 | 其余写工具 + ask_user | `move_item`/`remove_item`/`update_item`；`ask_user` 挂起/恢复；tool result 回灌 | 2.0 | M3-03 | 7 工具全部可用；ask_user 可恢复；refusal 详情回灌 |
| M3-05 | 五层校验 + refusal 注册表 | L1/L2/L3 接入；空间常量；一次返回多错；`unrealistic` ruleId；注册表用例 | 1.5 | M3-04, M2-04 | 每个 refusal 码至少 1 例；suggest 非空；L3 warning/strict |
| M3-06 | Proposal 链 | `dependsOn`；overlay 顺序；confirm 条件；reject/expire；TTL；外部提交过期；幂等 | 1.5 | M3-03 | G1 两个 proposal 可依次确认；过期/重复确认有测试 |
| M3-07 | WS 三通道 + 取消/重连 | turn 事件流；state patch；host 回调；seq/重连；cancel；重启恢复 | 1.5 | M3-01, M1-06 | 断线 2s 恢复；取消不脏场景；重启 turn 标 failed |
| M3-08 | G1 + 假 LLM + 回归 | 假 OpenAI 服务；G1 用例；refusal 覆盖；真模型冒烟（GLM/Qwen/DeepSeek 任一） | 1.5 | M3-06/07 | G1 脚本化通过；真模型可跑通一次；报告落盘 |

**M3 退出条件**

- [ ] G1 端到端通过：两个 proposal、依赖链、逐个确认、可 undo、无 refusal。
- [ ] 7 工具全部可用；每个写工具走 proposal；读工具不门控。
- [ ] refusal 注册表所有码有用例；`suggest` 非空。
- [ ] `turn.cancel` 生效；`waiting_user` 10 分钟超时；重连 ≤2s。
- [ ] fake LLM 可回放；真模型至少一家跑通。

---

### 5.5 M4 求解器 + VLM + 评测（14 d）

| ID | 工作包 | 主要任务 | 估算 | 依赖 | 产出 / 验收 |
|---|---|---|---|---|---|
| M4-01 | 求解器核心 | 候选位姿（沿墙/角落/近家具/网格）；硬约束；评分；beam search；确定性；进程池；time budget | 4.0 | M2-08, M3-05 | 凸多边形 20 件 ≤5s；同输入同输出；单测覆盖正常/无解/部分 |
| M4-02 | auto_layout/verify_layout + G2 | 工具接入 proposal；`verify_layout` 结构化 issues；L3 规则；G2 用例 | 2.0 | M4-01, M3-06 | G2 通过：床尾朝南、门净空、verify 0 问题 |
| M4-03 | 截图 + 标注俯视 | capture.request/response；正交固定相机；隐藏天花板；ID/legend；PNG 校验/缩放 | 1.5 | M1-09, M3-07 | 前端回传 ≤1MB PNG + legend；无浏览器返回 `capture_unavailable` |
| M4-04 | VLMService + G3 | OpenAI VLM；JSON schema；重试；ID 校验；几何复核（含主路径寻路）；映射约束；局部重排；2 轮上限 | 3.0 | M4-02/03 | G3 通过：JSON 可解析、误报过滤、复检通过 |
| M4-05 | before/after + 30 例评测 | 首轮写前/收尾截图；存储/清理；30 例分布；三 runner；CI PR/nightly | 2.5 | M3-08, M4-04 | ≥30 例；脚本化 100%；报告含 git sha/模型/环境 |
| M4-06 | 真模型/指标/性能 | live LLM/VLM；指标统计；200 实例 60fps；solver p95；报告生成 | 1.0 | M4-05 | 指标表；README 数字；性能达标或记录差距 |

**M4 退出条件**

- [ ] G2 通过：`auto_layout` 一次给出整室提案，床尾朝南，`verify_layout` 零问题，≤5s。
- [ ] G3 通过：VLM 返回 JSON，几何复核，局部重排，复检通过，≤2 轮。
- [ ] 30 例评测可一键跑；脚本化 100%；live 报告产出。
- [ ] VLM JSON 可解析率 100%（1 次重试）；误报率有统计。
- [ ] 200 实例 60fps p95；solver p95 记录。
- [ ] before/after 截图可用，失败不阻塞。

---

### 5.6 M5 加餐（可选，8 d）

| ID | 工作包 | 估算 | 验收 |
|---|---|---|---|
| M5-01 | 方案 JSON 导出/导入 + PNG | 1.5 | 导出含 schemaVersion；导入重映射 ID；再导入还原 |
| M5-02 | 第一人称漫游 | 1.5 | WASD + 碰撞；不影响编辑 |
| M5-03 | MCP server（5 工具） | 1.5 | 与 chat 共用 Pydantic 契约；对等测试 |
| M5-04 | 户型图照片 VLM 解析 | 1.5 | 5MB/JPEG/PNG；Pillow 校验；草案 + 手动修正 |
| M5-05 | 日照环境层 | 1.0 | north + 经纬度 + 时间轴阴影 |
| M5-06 | 文档/Demo/发布 | 1.0 | README、Demo、CREDITS、标签 |

---

## 6. 周计划与 Gate

> 每周按 4.5 天排任务，0.5 天留修 CI/技术债/记录。Gate 不通过不进入下一里程碑的主功能，先修 Gate。

| 周 | 工作包 | 周末 Gate | 交付物 |
|---|---|---|---|
| W1 | M0-01..08；M1-01 起步 | 前后端起、CI 绿、契约链路通 | 仓库、health、空画布、Docker、ADR |
| W2 | M1-01 收尾、M1-02、M1-03 起步 | 几何 fixtures 双端通过；SceneService 单测 | `rules.json`、几何库、SceneService |
| W3 | M1-03 收尾、M1-04、M1-05、M1-06 起步 | REST commit/undo + WS echo/seq | 6 表、REST、WS 骨架 |
| W4 | M1-06 收尾、M1-07、M1-08、M1-09 | 加载 fixture 渲染；3D/俯视；**协议冻结** | 渲染器、协议文档 |
| W5 | M1-10、M1-11；M2-01 起步 | undo/redo；200 代理盒基线；目录资产开始 | 客户端 store、fixtures、资产 |
| W6 | M2-01/02/03（圣诞周 3.5d） | 目录面板 + 拖放 + snapping | 30–60 件目录、拖放 |
| W7 | M2-04/05/06/07；M3-01 起步（元旦周 3.5d） | 碰撞/服务端复核、手绘、门窗；TurnManager 起步 | 手动编辑闭环 |
| W8 | M2-08；M3-01/02、M3-03 起步 | 手动客厅 Demo；solver spike；垂直切片起步 | 评测骨架、spike 结论 |
| W9 | M3-03 收尾；M3-04/05；M4-01 起步 | 垂直切片 + 7 工具 + refusal 全覆盖 | G1 半程、工具集 |
| W10 | M3-06/07/08；M4-01 | **G1 通过**；重连/取消；求解器核心起步 | G1 报告 |
| W11 | M4-01 收尾、M4-02 | 求解器核心 + **G2** | solver、G2 报告 |
| W12 | M4-03/04/05 起步 | 截图 + VLM + **G3** | G3 报告 |
| W13 | M4-05 收尾/06（春节周，缓冲） | 30 例、指标、性能 | 评测报告 |
| W14 | 缓冲 + 加固 + Demo | Docker、README、Demo、发布候选 | 发布候选 |
| W15+ | M5 可选 | 对应 FR | 加餐 |

**周 Gate 规则**

- 金线 Gate（G1/G2/G3）不通过：当周停止新功能，先修 Gate；必要时用 §11 裁剪。
- 每周五：跑全量 `pnpm test` + `pnpm typecheck` + `uv run pytest`；记录指标；更新 ADR/风险。
- 每两周：一次 5 分钟 Demo 录屏；对比上一版，确保可演示性不退步。

---

## 7. 关键路径与并行

### 7.1 关键路径

```
Pydantic 契约 → rules/geometry → SceneService/版本 → REST/WS
→ 客户端 store/渲染 → 手动编辑 → Agent 工具/提案链
→ 求解器 → VLM 截图/评审 → 评测/指标 → 打包
```

任一段延迟都会推迟 G1–G3。优先保关键路径，文档/资产/M5 让路。

### 7.2 可并行的低风险工作（碎片时间）

| 工作 | 何时做 | 说明 |
|---|---|---|
| 资产下载/规范化/缩略图 | W2–W5 | 不阻塞后端；先线框盒占位 |
| 内置户型 fixture | W3–W5 | 与场景 schema 同步 |
| 评测用例草稿 | W5 起 | 每个功能完成时补 2–3 例 |
| README/ADR/架构图 | 每个 WP 完成时 | 不攒到最后 |
| LLM 录制回放 | W7 起 | 每接一家 provider 录一组 |
| Demo 片段 | 每个 Gate | 5 分钟录屏，最后剪 |

### 7.3 依赖红线

- 未冻结协议不得开始 M3 的 WS/提案实现。
- 未完成 SceneService/overlay 不得做 ghost/confirm。
- 未完成 `verify_layout` 不得接 VLM 映射。
- 未完成几何 fixtures 双端对等不得宣称「客户端预检可靠」。

---

## 8. 测试与质量计划

### 8.1 测试金字塔

| 层 | 工具 | 覆盖 | 何时跑 |
|---|---|---|---|
| 几何/Operation 单测 | pytest | 多边形/墙/OBB/净空/求解/refusal | 每次 PR |
| 契约测试 | pytest + 生成物 diff | Pydantic → OpenAPI/JSON Schema → TS | 每次 PR |
| API/WS 集成 | pytest + TestClient/httpx + fake LLM | REST/WS/TurnManager/proposal | 每次 PR |
| 前端单测 | Vitest | store/patch/预检/组件纯逻辑 | 每次 PR |
| UI E2E | Playwright | 加载/拖拽/ghost/确认/undo | nightly / Gate |
| 真模型 | live LLM/VLM | G1–G3 回归 | nightly / 手动 |

### 8.2 覆盖率与硬门槛

- 几何、SceneService、校验、求解：行覆盖 ≥80%，关键 refusal 路径 100%。
- 每个 refusal 码至少 1 个用例；`suggest` 非空断言。
- 契约生成物零 diff 才能合并。
- 不允许跳过测试、`xfail` 无 issue、`TODO` 留在关键路径。
- 性能：W5 建基线；W12 对照；报告记录机器/构建。

### 8.3 测试数据

| 数据 | 数量 | 阶段 |
|---|---|---|
| 内置户型 fixture | 3–5 | M1 |
| 几何 fixtures | ≥20 向量 | M1 |
| 目录条目 | 30–60 | M2 |
| 评测用例 | ≥30 | M2 起草，M4 完成 |
| LLM 录制响应 | 每 provider 1 组 | M3 |
| VLM 桩响应 | 3–5 | M4 |
| 户型照片样例 | 3 | M5 |

### 8.4 PR Checklist

- [ ] Pydantic 模型/契约已更新；`pnpm contracts` 已跑；生成物已提交。
- [ ] Operation/校验/求解有新单测；refusal 码已注册并有用例。
- [ ] `uv run ruff check`、`uv run mypy`、`uv run pytest` 绿。
- [ ] `pnpm typecheck`、`pnpm test`、`pnpm build` 绿。
- [ ] 没有密钥/绝对路径/调试日志；资产在 allowlist。
- [ ] 架构决策已写 ADR；README/接口文档同步。
- [ ] UI 改动附截图/GIF；性能敏感改动附测量。

### 8.5 手动 Demo Checklist（每个 Gate）

- [ ] 冷启动 30s 内可用。
- [ ] G1/G2/G3 脚本按预期执行，无 console error。
- [ ] proposal 卡片、ghost、确认/拒绝、undo 可用。
- [ ] 断网 5s 重连，场景与对话恢复。
- [ ] 刷新页面，场景/对话/pending 恢复。
- [ ] 200 实例场景不卡顿；俯视/3D 切换正常。

---

## 9. 数据、资产与模型计划

### 9.1 资产

| 步骤 | 内容 | 时间 |
|---|---|---|
| 下载 | Kenney Furniture Kit、Quaternius、Poly Haven 少量 | W2 |
| 规范化 | 米制、Y-up、底部中心、front +Z、AABB；压缩/减面 | W2–W3 |
| 缩略图 | 256px，统一背景/角度 | W3–W4 |
| 目录 | `catalog.json`：kind/variant/size/clearance/rules/tags/license | W4 |
| 校验 | 脚本检查尺寸/三角面/路径/许可 | W4 |
| 替换占位 | 线框盒 → 正式 GLB，逐件替换 | W4–W6 |

### 9.2 模型与 Provider

| 项 | 计划 |
|---|---|
| LLM | 先接一家（GLM 或 DeepSeek）；`LLMAdapter` 记录 provider 差异；再补第二家 |
| VLM | GLM-4V 或 Qwen-VL；先用手动截图跑通，再接 capture |
| 录制 | 每 provider 录 1 组 G1–G3 SSE，供 `recorded` 模式 |
| 假服务 | `app/testing/fake_llm.py` 实现 OpenAI 兼容 SSE；PR 默认用它 |
| 成本 | 真模型只在 nightly/手动；每例 3 次；记录 token/耗时 |
| 安全 | key 只在 `.env`；`.env.example`；不提交；不在前端出现 |

### 9.3 评测节奏

- M2：operation runner + 5 例（几何/校验）。
- M3：G1 + 10 例（工具/refusal/proposal）。
- M4：30 例补齐（求解/VLM/多轮/断线/导入导出）。
- 每次 Gate：跑脚本化全量；live 只跑金线。
- W14：生成最终报告与 README 指标表。

---

## 10. 环境与命令

### 10.1 初始化（M0 参考）

```bash
# 仓库
git init
mkdir -p apps/server apps/web packages/contracts e2e assets/models assets/thumbnails \
  fixtures docs/adr eval/reports scripts

# 后端
cd apps/server
uv init --python 3.12
uv add fastapi "uvicorn[standard]" pydantic pydantic-settings \
  "sqlalchemy[asyncio]" aiosqlite alembic shapely numpy pillow openai httpx \
  structlog slowapi python-multipart
uv add --dev pytest pytest-asyncio pytest-cov ruff mypy

# 前端（仓库根）
pnpm create vite apps/web --template react-ts
pnpm --filter web add three @react-three/fiber @react-three/drei zustand zundo
pnpm --filter web add -D vitest @playwright/test typescript
pnpm add -Dw concurrently orval json-schema-to-typescript
```

### 10.2 常用命令

```bash
# 开发（根 package.json scripts）
pnpm dev            # concurrently: web + uvicorn --reload
pnpm test           # web vitest + server pytest
pnpm typecheck      # tsc --noEmit + mypy
pnpm contracts      # 导出 OpenAPI/JSON Schema + 生成 TS
pnpm contracts:check

# 后端
uv run ruff check .
uv run ruff format .
uv run mypy app
uv run pytest -q
uv run uvicorn app.main:app --reload --port 8000 --workers 1

# 数据库
uv run alembic upgrade head
uv run alembic revision --autogenerate -m "..."

# 评测
uv run pytest tests/eval -q
pnpm --filter e2e test

# Docker
docker compose up --build
```

### 10.3 环境变量

```bash
HOST=127.0.0.1
PORT=8000
DATA_DIR=./data
LLM_BASE_URL=
LLM_API_KEY=
LLM_MODEL=
VLM_BASE_URL=
VLM_API_KEY=
VLM_MODEL=
```

### 10.4 常见问题预案

| 问题 | 处理 |
|---|---|
| uv/Python 3.12 在 Windows 安装失败 | 用 Docker 开发容器；或 pyenv-win；锁定 Python 3.12 |
| Shapely 安装失败 | 升级 pip/uv；使用官方 wheel；Docker 复现 |
| SQLite `database is locked` | 确认 `--workers 1`；WAL + busy_timeout；短事务 |
| 前端 GLB 加载失败 | 检查路径白名单/CORS；线框盒兜底；资源压缩 |
| LLM 流式 tool_calls 解析异常 | 用 `LLMAdapter` + 录制回放；按 provider 写适配测试 |
| WS 重连丢事件 | 检查 seq/缓冲；不足则 snapshot + turn.status |

---

## 11. 风险、触发器与裁剪

### 11.1 风险表

| 风险 | 触发信号 | 影响 | 应对 |
|---|---|---|---|
| 协议返工 | W4 末 WS/Proposal 未冻结 | M3 全面延期 | 立即冻结最小协议：snapshot + patch + confirm；砍 replay 细化 |
| 求解器卡壳 | W8 spike 无可行解或 >8s | G2 失败 | 降级规则模板布局（床靠墙/衣柜靠墙/床头柜成组）+ 碰撞校验；beam search 降为 P1 |
| G1 不通过 | W11 Gate 失败 | 核心亮点缺失 | 停新功能；先修 overlay/dependsOn/refusal；砍 P1 |
| VLM 不稳定 | W13 JSON 解析 <90% | G3 失败 | 收紧 prompt/schema；用录制桩保证 CI；Demo 用真实调用一次 |
| Python 阻塞 | W9 压测 WS 卡顿 | 交互卡 | 求解放进程池；减少候选网格；几何放线程池 |
| 契约漂移 | CI diff 失败 | 前端字段错 | 回滚生成物；只改 Pydantic；重新生成 |
| 资产拖慢 | W6 目录不足 30 件 | M2 演示差 | 先用占位盒/少量精品；W8 前补齐 |
| LLM 限流/费用 | nightly 失败/超预算 | 评测中断 | 降频；只用录制；真模型每周一次 |
| 假期/生病 | 周 Gate 连续失败 | 整体延期 | 用 W13/W14 buffer；按 §11.2 裁剪 |
| 多标签页冲突 | 第二标签写入 | 状态不一致 | 明确只读/version_conflict；不做协同 |

### 11.2 裁剪顺序（从后往前砍）

1. **M5 全部**（FR-18/19/20/21/22）。
2. **FR-16 before/after 截图**（保留 VLM 截图，因为 G3 需要）。
3. **FR-12 会话历史持久化**（保留 scene_versions；对话可只存最近 turn）。
4. **FR-13 重连简化为 snapshot-only**（去掉事件补发细化）。
5. **nightly live 评测降频**（保留 30 例脚本化）。
6. **求解器降级为规则模板 + 局部碰撞**（保 G2 语义，缩搜索空间）。

**永不裁剪**：G1/G2/G3、五层校验与 refusal、proposal/confirm、服务端真相源、30 例脚本化评测、契约生成与 CI。

### 11.3 压缩到 10.5 周的方案

若必须按 PRD 基线交付：

- 砍 M5、FR-16、FR-12。
- M3 只做 G1 所需工具 + 1 个 move；其余工具标 P1。
- 求解器用规则模板 + 局部搜索，不做完整 beam search。
- VLM 只做单轮，不做 2 轮修复。
- 评测保 30 例脚本化，live 只跑 G1–G3 各 1 次。
- 风险：简历亮点缩水；需在 README 如实标注范围。

---

## 12. 交付物、指标与简历映射

### 12.1 交付物清单

- [ ] 可运行仓库（MIT LICENSE）。
- [ ] `README.md`：定位、架构图、Quickstart、Demo GIF、指标表、限制说明。
- [ ] `docs/adr/`：D1–D16 对应 ADR（至少 D1/D6/D9/D15 在 M0）。
- [ ] `docs/CONVENTIONS.md`：轴向/单位/角度/ID/命名/契约生成。
- [ ] `docs/DEPENDENCIES.md` + `assets/CREDITS.md`。
- [ ] `eval/reports/`：至少一份最终报告（JSON + 摘要）。
- [ ] Docker Compose 一键起。
- [ ] 2–3 分钟 Demo 视频 + G1/G2/G3 截图。
- [ ] 简历四条亮点与数字对照表。

### 12.2 简历映射

| 亮点 | 证据 |
|---|---|
| Agent 架构 | 服务端 Agent 循环、三层工具、9 工具、proposal 依赖链、五层校验；ADR D1–D16 |
| VLM 闭环 | 标注俯视图、结构化 JSON、几何复核、局部重排、最多 2 轮；G3 报告 |
| 人机协同 | ghost/confirm、点选上下文、undo/redo、before/after、断线恢复 |
| 质量工程 | 30 例评测、三 runner、契约生成、CI 分层、实测指标 |

### 12.3 指标表模板（README 用）

| 指标 | 结果 | 环境 |
|---|---|---|
| G1 脚本化 | 100% | pytest/api-ws |
| G2 求解 p95 | _ ms | 基准机 |
| G3 VLM JSON | 100% | 模型名/日期 |
| 评测集 | 30/30 | git sha |
| 平均 turn | _ | live |
| 自纠成功率 | _% | live |
| 渲染 | 60fps p95 | 200 实例 |
| 重连 | _s | Playwright |

> 所有数字由 `eval/reports/` 生成；禁止手填估算值。

---

## 13. 第一周逐日计划

| 日 | 任务 | 完成标志 |
|---|---|---|
| D1（11/16） | git init、目录、LICENSE、README 占位；uv 项目；FastAPI `/api/health`；pydantic-settings/logging | `uv run uvicorn` 起服务，health 200 |
| D2（11/17） | pnpm workspace；Vite + React + R3F 空画布；Biome/tsconfig/Vitest | `pnpm dev` 出画布；typecheck 绿 |
| D3（11/18） | SQLAlchemy/Alembic 骨架；SQLite WAL；6 表空迁移 | `alembic upgrade head` 成功 |
| D4（11/19） | `export_contracts.py` + Pydantic 示例模型；OpenAPI/JSON Schema/TS 生成；CI 三 job | `pnpm contracts` 生成；CI 绿 |
| D5（11/20） | Docker Compose；ADR-0001..0004；CONVENTIONS；周 Gate 冒烟 | `docker compose up`；文档提交；周复盘 |

**W1 周末必须回答**

- 前后端启动是否一条命令？
- 契约生成/检查是否可重复？
- Docker 是否持久化 SQLite？
- CI 是否在 PR 上真的跑？
- 下周 M1 的 geometry/SceneService 任务是否已拆到 0.5–1 天粒度？

---

## 14. 追踪与复盘

### 14.1 任务板

列：`Backlog / This Week / In Progress / Review / Done / Blocked`。

- 每个任务必须有：ID、验收、估算、依赖、关联 FR。
- 每天结束更新一次；Blocked 必须写原因和下一步。
- 每周五：关闭本周任务；未完成移到下周并重估；更新风险表。

### 14.2 每日节奏

- 开始：选 1 个 WP，确认验收与测试；跑一次基线测试。
- 结束：跑 `ruff/mypy/pytest` + `pnpm typecheck`；提交；记录遇到的问题。
- 等待 LLM/构建的碎片时间：补测试、缩略图、文档、评测用例。

### 14.3 每周复盘问题

1. 本周 Gate 是否通过？没有通过的原因是什么？
2. 哪个任务估算偏差 >50%？为什么？
3. 是否出现契约/协议返工？如何避免下周重复？
4. 关键路径是否被非关键任务挤占？
5. 下周是否需要裁剪？裁什么？

### 14.4 版本与提交

- 分支：`feat/m1-scene-service`、`feat/m3-proposal-chain`；小 PR。
- 提交：`feat(server): ...`、`fix(web): ...`、`docs(adr): ...`。
- 标签：`v0.1-m1`、`v0.2-m2`、`v0.3-g1`、`v0.4-g2`、`v0.5-g3`、`v1.0.0`。
- 每个 Gate 打 tag + 截图/报告，便于回溯和简历取证。

---

## 附录 A：里程碑交付物速查

| 阶段 | 代码 | 文档 | 数据 | Demo |
|---|---|---|---|---|
| M0 | 仓库/CI/Docker/health | ADR-0001..0004、CONVENTIONS | — | 空画布 + health |
| M1 | 模型/几何/SceneService/REST/WS/渲染/undo | 协议冻结文档 | 3–5 户型 fixture | 加载户型 + 撤销 |
| M2 | 目录/拖放/手绘/门窗/复核 | 资产 CREDITS | 30–60 目录、5 例评测 | 手动客厅 |
| M3 | Agent/7 工具/提案链/WS | refusal 注册表 | G1 用例、LLM 录制 | G1 |
| M4 | 求解器/VLM/评测 | 指标报告 | 30 例、VLM 桩 | G2/G3 |
| M5 | 导出/第一人称/MCP/照片/日照 | 发布说明 | 样例 | 加餐演示 |

## 附录 B：计划变更记录

| 日期 | 变更 | 原因 |
|---|---|---|
| 2026-11-16 | 初版 | 基于 PRD v1.2 |

## 附录 C：FR 追踪矩阵

| FR | 优先级 | 里程碑 | 主要 WP | 验证方式 |
|---|---|---|---|---|
| FR-01 内置户型 | P0 | M1 | M1-08/11 | fixture 加载 + schema 测试 |
| FR-02 手绘房间 | P0 | M2 | M2-05/06 | E2E + polygon 单测 |
| FR-03 三维渲染 | P0 | M1 | M1-08 | 视觉 + perf harness |
| FR-04 俯视模式 | P0 | M1 | M1-09 | E2E 截图 + 拖拽回归 |
| FR-05 目录面板 | P0 | M2 | M2-01/02 | API + UI 测试 |
| FR-06 手动编辑 | P0 | M2 | M2-03/04 | E2E + 服务端复核测试 |
| FR-07 撤销重做 | P0 | M1/M2 | M1-10 | pytest + api-ws |
| FR-08 详情卡 | P0 | M2 | M2-07 | E2E |
| FR-09 对话面板 | P0 | M3 | M3-03 | E2E + 事件流断言 |
| FR-10 Agent 写闭环 | P0 | M3 | M3-03/04/06 | G1 |
| FR-11 五层校验/refusal | P0 | M3 | M3-05 | refusal 注册表用例 |
| FR-12 会话持久化 | P1 | M3 | M3-07 | 重启/刷新测试 |
| FR-13 断线重连 | P1 | M3 | M3-07 | WS 断线测试 |
| FR-14 auto_layout | P0 | M4 | M4-01/02 | G2 |
| FR-15 VLM 闭环 | P0 | M4 | M4-03/04 | G3 |
| FR-16 before/after | P1 | M4 | M4-05 | E2E + 文件检查 |
| FR-17 评测/CI | P0 | M2–M4 | M2-08、M4-05 | CI + 报告 |
| FR-18 导出/导入 | P1 | M5 | M5-01 | pytest + 往返测试 |
| FR-19 户型图 VLM | P2 | M5 | M5-04 | 手动样例 |
| FR-20 第一人称 | P2 | M5 | M5-02 | E2E |
| FR-21 MCP | P2 | M5 | M5-03 | 契约对等测试 |
| FR-22 日照 | P2 | M5 | M5-05 | 视觉检查 |
| FR-23 Turn 生命周期 | P0 | M3 | M3-01/07 | pytest + WS |
| FR-24 协议一致性 | P0 | M1/M3 | M1-06/07、M3-07 | 契约测试 + CI diff |