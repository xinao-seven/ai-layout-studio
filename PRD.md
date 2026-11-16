# AI 室内布局工作台 · 需求文档（PRD）

| 项 | 内容 |
|---|---|
| 版本 | v1.2（2026-11-16） |
| 状态 | 评审通过 → 待开发 |
| 项目性质 | 个人简历亮点项目（面向通用前端/全栈岗位） |
| 参照声明 | 架构思想参照开源项目 aedifex（MIT）的公开架构文档，**仅参考设计思想，不复制代码** |
| 变更 | v1.1 补齐数据模型、墙/开口约定、proposal 依赖链、undo 归属、指北与朝向、求解器/VLM/同步协议/评测规格；v1.2 后端技术栈改为 **Python 3.12 + FastAPI + Pydantic v2**，并补齐跨语言契约生成、Shapely 几何、Python 评测 runner 与并发约束 |

---

## 0. v1.1 / v1.2 修订摘要

| # | v1.0 的问题 | v1.1 的处理 |
|---|---|---|
| 1 | 工具数量 8/9/7 三处不一致 | 统一为 **9 个（M3 7 + M4 2）**，MVP 验收按 7 个 |
| 2 | 墙体/开口模型缺失，`wallSegmentRef` 不稳定 | 明确 polygon 为墙中心线，派生 `WallSegment`，开口用 `edgeIndex` + 重锚定规则 |
| 3 | 门/窗字段不足 | 补 `sillHeight`、`swing`、`hinge`、`opensTo`、`offsetFrom` |
| 4 | 多 proposal 同版本必然冲突，pending 家具无法被引用 | 增加 **pending overlay + dependsOn 依赖链**，外部提交统一过期 |
| 5 | undo 在客户端 Zundo 与服务端真相源之间漂移 | 服务端版本历史为权威，Zundo 只做镜像；undo 产生新版本 |
| 6 | 没有指北，"床要朝南"无法实现 | 增加 `north: Vec2`，定义 `facing = 家具正面朝向` |
| 7 | 求解器只有一句"退避搜索" | 补输入/输出、候选位姿、beam search、评分、失败码、确定性、形状边界 |
| 8 | VLM "只校验不定位"与"局部重排"矛盾 | 俯视图打 ID 标注，VLM 只回 ID + issue type，服务端几何复核后映射约束 |
| 9 | WS/REST/PatchOp 未定义 | 补语义 PatchOp、WS 信封与 seq、重连、REST 端点 |
| 10 | 评测"三 runner"未定义、缺假 LLM | 定义 operation / api-ws / ui 三 runner、脚本化 LLM、指标与 CI 分层 |
| 11 | 空间阈值全是形容词 | 常量集中：门净空 0.6m、主过道 0.8m、次过道 0.6m、窗洞前 0.05m 等 |
| 12 | 性能目标缺基准与测量方法 | 补基准机、production build、p50/p95、资产预算 |
| 13 | 安全只写了 key 不落前端 | 补绑定地址、Origin、限流、请求体上限、资产白名单、图片外发声明 |
| 14 | 排期偏乐观 | M2 末加求解器 spike、评测骨架前移、M3 先做垂直切片、P1 可裁 |
| 15 | 后端 Node/TS + Fastify + zod | 改为 **Python 3.12 + FastAPI + Pydantic v2**；Agent/几何/求解/VLM/评测全部 Python |
| 16 | 前后端契约无法再用同一份 zod 包 | Pydantic 单一来源 → OpenAPI 3.1 + JSON Schema → 前端生成 TS 类型/zod client；CI 校验生成物无 drift |
| 17 | 跨语言几何重复实现 | 客户端只做轻量预检（OBB SAT + 房间边界），服务端 Python/Shapely 为权威；共享几何 fixture 对等测试 |

---

## 1. 项目概述

### 1.1 一句话定位

一个对话式 AI 室内布局工作台：用户上传/绘制户型，用自然语言和直接操作（拖拽、点选）完成家具布置；AI Agent 以"输出约束、确定性求解器产出坐标"的方式生成布局提案，经用户确认后落库。

### 1.2 项目目标

1. **产品目标**：从"输入户型"到"导出布置成果"的完整闭环可用。
2. **技术目标**：完整自研实现一套 Agent 驱动的三维编辑器，包含：客户端 3D 编辑器、服务端 Agent 循环、三层工具范式、五层空间校验、布局约束求解器、VLM 视觉评审闭环、版本/提案同步协议、评测回归体系。
3. **简历目标**：支撑四条亮点——Agent 架构 / VLM 闭环 / 人机协同交互 / 质量工程；所有量化指标来自自建评测集实测。

### 1.3 目标用户

- 有布置需求的普通用户（租房/新房/改造），不想学专业软件。
- （隐性读者）面试官：通过演示与代码理解架构能力。

---

## 2. 范围界定

### 2.1 范围内（In Scope）

| 域 | 内容 |
|---|---|
| 户型输入 | 手绘房间（多边形墙中心线）、门窗开口放置、内置 3–5 个典型户型 JSON；（加分项）户型图照片 → VLM 解析多边形草案 |
| 三维场景 | R3F 渲染房间/墙/门窗/家具，俯视布局模式，第一人称漫游（P2） |
| 手动编辑 | 家具目录、拖放/旋转/吸附、选择与详情卡、撤销重做 |
| AI 布局 | 服务端 Agent 循环 + **9 个工具（M3 7 + M4 2）**，ghost 预览 + 确认制写入 |
| 空间校验 | OBB 碰撞、门净空、过道净宽、房间边界、不压窗、业务常识规则；手动与 Agent 共用同一套服务端校验 |
| 布局求解器 | 整室多家具约束求解（朝向/成组/净空/动线），P0 凸多边形 + 分区退避搜索 |
| VLM 评审 | 带 ID 标注的俯视渲染 → 视觉模型结构化评审 → 几何复核 → 意见转约束触发局部重排 |
| 人机协同 | 点选家具反哺对话（简化版 PointContext）、before/after 截图、多轮编辑式指令 |
| 成果输出 | 场景快照导出 PNG、布置方案 JSON 导入导出（含 schemaVersion 与 ID 重映射） |
| 同步协议 | REST + 单 WS 三通道；版本号 + patch；断线 seq 补发/快照兜底；pending proposal 恢复 |
| 质量工程 | 评测集（≥30 例）、断言最终场景状态、三 runner 同用例表、headless CI |

### 2.2 范围外（Out of Scope，明确不做）

多楼层/楼梯/屋顶/CSG 布尔运算、IFC 导入、插件体系、多语言 i18n、2D CAD 图纸、实时协同多人编辑、多标签页并发编辑（第二标签只读或版本冲突）、天花板建模、墙面挂件/吊灯等非落地家具、任意凹形房间的 `auto_layout`、日照/窗外视野环境层（列为 M5 可选）、text-to-3D 生成家具（仅作彩蛋演示）、移动端适配、用户账号体系（单用户自部署）、WebGPU（使用 WebGL2）。

---

## 3. 术语定义

| 术语 | 定义 |
|---|---|
| Scene / 场景文档 | 服务端持有的唯一真相源：房间、墙、开口、家具实例、版本号的集合 |
| Confirmed Scene | 已确认并落库的场景，只读快照 + 当前版本 |
| Pending Overlay | `Confirmed Scene + 所有 pending proposals` 的叠加视图；读工具与后续写工具校验都基于它 |
| Proposal / 提案 | AI 写操作产生的待确认变更包（PatchOp 列表），确认前不进入场景历史 |
| Proposal 依赖链 | 同一 turn 内后序 proposal 依赖前序 proposal；确认必须按依赖顺序 |
| Ghost / 鬼影 | Proposal 在客户端的半透明预览渲染，来自 pending overlay，不写入服务端场景 |
| 契约（Contract） | 工具的名称、描述、Pydantic 输入模型（导出 JSON Schema）的唯一定义 |
| Operation | 契约对应的 Python 纯函数：校验、求解、产出变更与 refusal |
| Refusal | 结构化拒绝：稳定错误码 + 人话原因 + 可行建议 |
| PatchOp | 语义化变更操作（item.add/remove/update、room.*、opening.*），带 before 以便生成 inverse |
| Turn | 一次用户消息触发的完整 Agent 循环（异步任务） |
| WallSegment | 由房间 polygon 边派生的墙段，ID 为 `${roomId}:e${edgeIndex}` |
| north / facing | `north` 是场景指北单位向量；`facing` 指家具**正面**朝向（床的正面是床尾方向） |
| frontAxis | 目录资产的正面轴约定，统一为 `+Z`；旋转后正面方向 = `[sin(yaw), cos(yaw)]` |
| Region | MVP 中等价于 `roomId`；工具输入统一使用 `roomId` |
| 评测集 | 断言最终场景状态的自动化任务用例集合 |
| 三 runner | `operation`（纯函数）、`api-ws`（服务端 + 脚本化 LLM）、`ui`（Playwright） |

---

## 4. 系统架构

### 4.1 总体形态

```
浏览器（渲染端 + 交互采集）
  React 19 + Vite + R3F + Zustand + Zundo
        ▲ │ REST：场景 CRUD / 目录 / turn 创建 / 导入导出
        │ ▼ WebSocket（一条连接，三通道复用）
        │   ① turn 事件流：token 增量 / tool_call 事件 / 进度 / 完成
        │   ② 状态同步：版本号 + patch（confirmed / proposal）
        │   ③ 宿主回调：confirm / reject / ask_user 回答 / capture 结果 / commit / undo
┌───────┴─────────────────────────────────┐
│ 服务端（Python 3.12 + FastAPI）           │
│  SyncGateway     WS 三通道 + seq + 重连   │
│  TurnManager     asyncio 单场景 1 running │
│                  + 队列 1                 │
│  Orchestrator    Agent 循环 + Guardrails  │
│  ToolRegistry    9 契约（Pydantic → JSON Schema）│
│  SceneService    confirmed + pending overlay │
│                  + 五层校验 + 版本历史      │
│  SolverService   auto_layout（Shapely）    │
│  VLMService      标注截图评审 + 几何复核    │
│  CatalogService  目录与资产下发             │
│  EvalRunner      pytest / Playwright 用例   │
│  存储：SQLite（SQLAlchemy 2.0 + Alembic）  │
│        scenes / scene_versions / turns /   │
│        turn_events / proposals / eval_runs │
└──────────────────────────────────────────┘
```

### 4.2 关键架构决策

| # | 决策 | 理由 |
|---|---|---|
| D1 | Agent 循环放服务端，场景真相源随迁 | 工具为服务端纯函数；评测可 headless；key 不落前端；浏览器只做渲染 |
| D2 | 三层工具范式：契约 / Operation / Executor | 换宿主只换执行器；chat 与（可选的极简）MCP 共用契约 |
| D3 | LLM 输出关系约束，不输出坐标 | 确定性求解器负责位置，可靠性优先 |
| D4 | 写操作两段式：proposal → confirm | AI 提案、人拍板；确认前可撤销可丢弃 |
| D5 | 状态同步用"版本号 + patch" | 断线重连先对版本再补快照 |
| D6 | 契约以 Pydantic v2 为唯一来源：FastAPI 生成 OpenAPI 3.1，脚本导出 WS/tool JSON Schema；前端生成 TS 类型与 zod client | 跨语言仍保持"一份契约"；CI 校验生成物无 drift |
| D7 | 墙模型：polygon 为墙中心线，派生 WallSegment；开口用 `edgeIndex` + 重锚定 | 保留手绘体验，同时让门窗有稳定引用 |
| D8 | Proposal 用 pending overlay + `dependsOn` 链 | 解决同 turn 多 proposal 的版本冲突与 pending 家具引用 |
| D9 | Undo 以服务端版本历史为权威，Zundo 做镜像 | 真相源一致；undo 是"应用 inverse patch 产生新版本" |
| D10 | 场景存 `north`，`facing` 表示家具正面朝向 | "床朝南"可计算、可测试；为日照层留接口 |
| D11 | 同步协议用语义 PatchOp + WS 信封（seq/重连） | 可校验、可逆、可补发；避免 RFC6902 的表达力问题 |
| D12 | 每场景同时 1 个 running turn，队列 1 | 成本与状态机可控；取消/超时语义清晰 |
| D13 | `auto_layout` P0 只支持凸多边形；正交 L 型 P1；任意凹形拒绝 | 与风险表一致，避免求解器无底洞 |
| D14 | VLM 只回 ID + issue type，服务端几何复核后映射约束 | 解决"不定位"与"局部重排"的矛盾；误报可统计 |
| D15 | 后端采用 Python 3.12 + FastAPI + Pydantic v2；几何用 Shapely，求解用自研 beam search | 与前端 TS 通过生成契约衔接；单用户部署下链路最短 |
| D16 | 客户端只做轻量几何预检，服务端 Python 为权威 | 拖拽要即时反馈；两端用共享几何 fixture 对等测试，冲突以服务端为准 |

### 4.3 全局约定

| 项 | 约定 |
|---|---|
| 单位 | 长度米、时间秒；UI 可显示度，存储角度用弧度 |
| 世界轴 | `+X 东、-Z 北、+Y 上`（three.js 右手系） |
| 地面坐标 | `Vec2 = [x, z]`，单位米 |
| 多边形 | 点序按有向面积 `Σ(x_i·z_{i+1} − x_{i+1}·z_i) > 0` 为 CCW；必须简单、无自交、≥3 点、面积 ≥1㎡ |
| 旋转 | `rotationY` 绕 +Y，弧度；正面方向 = `[sin(yaw), cos(yaw)]` |
| 指北 | `north: Vec2` 单位向量，默认 `[0, -1]`；罗盘朝向由 north 与正面方向点积/叉积计算 |
| 资产正面 | `frontAxis: '+Z'`；床的正面为床尾方向 |
| ID | 服务端铸造，前缀：`scene_ / room_ / op_ / it_ / prop_ / turn_ / seg_`；墙段 ID 为 `${roomId}:e${edgeIndex}` |
| JSON 命名 | API/WS JSON 用 camelCase；Python 内部字段用 snake_case，Pydantic `alias_generator=to_camel` 输出 camelCase |
| 时间 | ISO 8601 UTC，带时区（如 `2026-11-16T12:00:00Z`） |
| 枚举 | Python `StrEnum` / TS string literal union；JSON 值一致，由契约生成 |
| 数值比较 | 几何断言容差 `1e-6`；评测坐标比较容差 `1e-3 m` |

---

## 5. 数据模型

### 5.1 核心类型

> 本节用 TypeScript 语法表达字段契约，便于前端阅读；服务端以 Pydantic v2 为唯一实现。Python 内部字段用 snake_case，Pydantic 通过 `alias_generator=to_camel` 输出 camelCase JSON；前端类型由 OpenAPI/JSON Schema 生成，不手写重复接口。

```ts
type Vec2 = [number, number]        // [x, z]，米
type Vec3 = [number, number, number]

interface SceneDocument {
  schemaVersion: 1
  id: string
  name: string
  version: number                   // 服务端单调递增，undo 也产生新版本
  units: 'metric'
  north: Vec2                       // 单位向量，默认 [0, -1]
  defaults: {
    wallHeight: number              // 2.8
    wallThickness: number           // 0.12
    wallMaterial: string
  }
  rooms: Room[]
  openings: Opening[]
  items: Item[]
  createdAt: string
  updatedAt: string
}

interface Room {
  id: string
  name: string
  type: 'living' | 'bedroom' | 'kitchen' | 'bathroom' | 'study' | 'balcony' | 'other'
  polygon: Vec2[]                   // 墙中心线，CCW，简单多边形，>=3 点
  wallLabels?: Record<string, string> // key = edgeIndex，如 { "0": "电视墙" }
  wallHeight?: number               // 覆盖 defaults
  wallThickness?: number
  floorMaterial: string
}

interface Opening {
  id: string
  type: 'door' | 'window'
  roomId: string
  edgeIndex: number                 // polygon[i] -> polygon[(i+1)%n]
  offsetFrom: 'start'
  offset: number                    // 边起点到开口中心，米
  width: number
  height: number
  sillHeight?: number               // 窗，默认 0.9
  door?: {
    swing: 'single' | 'double' | 'sliding'
    hinge: 'start' | 'end'
    opensTo: 'in' | 'out'
  }
  style?: string
}

interface Item {
  id: string
  kindId: string
  variantId: string
  transform: {
    position: Vec3
    rotationY: number               // 弧度
    scale?: number
  }
  constraint?: ItemConstraint       // 保留 LLM 意图，可解释、可重排
  materialOverrides?: Record<string, string>
  source: 'manual' | 'agent'
}

interface ItemConstraint {
  roomId?: string
  anchor?: {
    type: 'wall' | 'corner' | 'window' | 'door' | 'center'
    wallId?: string
    wallLabel?: string
    openingId?: string
  }
  near?: { itemId?: string; openingId?: string; distance?: number }
  facing?: 'north' | 'east' | 'south' | 'west'
    | { toward: 'door' | 'window' | 'item'; id?: string }
  clearance?: { front?: number; sides?: number; back?: number }
  group?: string
}

interface CatalogEntry {
  kindId: string
  name: string
  category: 'sofa' | 'bed' | 'table' | 'storage' | 'lighting' | 'decor' | 'other'
  tags: string[]                    // 中英文关键词，供 search
  placement: 'floor'                // MVP 只做落地家具
  defaultVariantId: string
  variants: Array<{
    id: string
    sizeM: [number, number, number] // w, d, h；与 frontAxis 一致
    modelPath: string               // 相对 assets/models/
    thumbnailPath: string           // 相对 assets/thumbnails/
  }>
  frontAxis: '+Z'
  pivot: 'bottom-center'
  collision: { type: 'obb' }        // 由 AABB 生成
  clearance: { front?: number; sides?: number; back?: number }
  allowedRotations: number[]        // 默认 [0, 90, 180, 270]
  rules?: {
    mustAgainstWall?: boolean
    preferredWall?: 'north' | 'east' | 'south' | 'west'
    groupWith?: string[]            // kindId
    anchorPriority?: number         // 越大越先放置
    roomTypes?: string[]
  }
  license?: { name: string; url: string }
}
```

### 5.2 派生墙段与开口重锚定

**WallSegment**（不落库，读时生成）：

```ts
interface WallSegment {
  id: string                        // `${roomId}:e${edgeIndex}`
  roomId: string
  edgeIndex: number
  a: Vec2
  b: Vec2
  normal: Vec2                      // 指向房间内侧
  length: number
  thickness: number
  height: number
}
```

规则：

1. 墙中心线为 polygon 边；墙内表面 = 中心线向房间内侧偏移 `thickness/2`。
2. 开口 `offset` 从边起点沿中心线量到开口中心；必须满足 `width/2 ≤ offset ≤ length − width/2`，否则 `opening_off_wall`。
3. 同一墙段上的开口不得重叠，否则 `opening_overlap`。
4. polygon 顶点增删/移动后：
   - 按"最近边 + 最近点"重锚定开口；
   - 新位置距离原位置 ≤0.5m 时保留并返回 `opening_reanchored` 警告；
   - 找不到可锚定边或距离 >0.5m 时删除开口，并返回 `opening_removed` 警告（不得静默丢失）。
5. 共墙：两条共线且重叠的边视为同一面墙；渲染去重，开口切两侧，净空按两侧分别计算。MVP 若实现成本过高，可先限制"内置户型共墙处不放门"，但必须写进限制说明与测试。
6. 窗：`sillHeight + height ≤ wallHeight`，否则 `opening_off_wall`；窗洞体积为 `[sillHeight, sillHeight+height]`，其室内侧前 0.05m 禁放家具。

### 5.3 目录与资产规范

- GLB、米制、Y-up、底部中心锚点、正面 `+Z`；AABB 自动生成 OBB 碰撞体。
- 目录由 JSON 元数据驱动；M2 提供 `POST /api/catalog/reload` 热加载，失败保留旧目录并返回错误。
- 单品预算：≤1MB、≤15k 三角面；缩略图 256×256 PNG/WebP；首屏资产总量 ≤20MB。
- 资产路径必须落在 `assets/models/` 与 `assets/thumbnails/` 白名单内，拒绝绝对路径与 `..`。
- 缺失/加载失败的 GLB 用同尺寸线框盒兜底，并在详情卡标注 `asset_fallback`。
- `assets/CREDITS.md` 记录每个资产的名称、作者、许可证、来源 URL。

### 5.4 Proposal、Turn、Patch、Refusal

```ts
interface Proposal {
  id: string
  sceneId: string
  turnId: string
  baseVersion: number               // turn 开始时的 confirmed 版本
  dependsOn: string[]               // 同 turn 前序 proposal（跨 turn 引用会被拒绝）
  ops: PatchOp[]
  status: 'pending' | 'confirmed' | 'rejected' | 'expired'
  createdAt: string
  resolvedAt?: string
  reason?: string                   // 过期/拒绝原因，如 scene_changed
}

interface Turn {
  id: string
  sceneId: string
  status: 'queued' | 'running' | 'waiting_user' | 'done' | 'failed' | 'cancelled'
  messages: Array<{
    role: 'user' | 'assistant' | 'tool'
    content: string
    toolCalls?: unknown[]
    toolResult?: unknown
    screenshotBefore?: string
    screenshotAfter?: string
  }>
  usage: {
    iterations: number
    tokensIn: number
    tokensOut: number
    refusals: string[]
    wallClockMs: number
  }
  createdAt: string
  endedAt?: string
}

interface Refusal {
  code: string
  message: string
  suggest: string
  details?: Record<string, unknown>
}

type PatchOp =
  | { op: 'item.add'; item: Item }
  | { op: 'item.remove'; itemId: string; before: Item }
  | { op: 'item.update'; itemId: string; before: Partial<Item>; after: Partial<Item> }
  | { op: 'room.add'; room: Room }
  | { op: 'room.remove'; roomId: string; before: Room }
  | { op: 'room.update'; roomId: string; before: Partial<Room>; after: Partial<Room> }
  | { op: 'opening.add'; opening: Opening }
  | { op: 'opening.remove'; openingId: string; before: Opening }
  | { op: 'opening.update'; openingId: string; before: Partial<Opening>; after: Partial<Opening> }

interface ScenePatch {
  sceneId: string
  fromVersion: number
  toVersion: number
  ops: PatchOp[]
  actor: 'user' | 'agent' | 'system'
  turnId?: string
  proposalId?: string
}
```

### 5.5 持久化表

| 表 | 关键列 | 说明 |
|---|---|---|
| `scenes` | id, name, current_version, doc_json, created_at, updated_at | 当前快照与指针 |
| `scene_versions` | scene_id, version, doc_json, patch_json, inverse_json, actor, turn_id, created_at | append-only；保留最近 100 版 |
| `turns` | id, scene_id, status, user_message, error, usage_json, created_at, ended_at | turn 状态与用量 |
| `turn_events` | turn_id, seq, type, payload_json, created_at | 事件回放；每 turn 最多 500 条 |
| `proposals` | id, scene_id, turn_id, base_version, depends_on_json, ops_json, status, created_at, resolved_at | pending 提案 |
| `eval_runs` | id, started_at, git_sha, model, report_json | 评测报告 |

SQLite 使用 WAL、外键、事务；**SQLAlchemy 2.0 async（aiosqlite）+ Alembic** 管理模型与迁移；启动时执行 `schemaVersion` 链迁移，迁移测试必须覆盖旧版本场景。为保证 asyncio 锁与 SQLite 单写入正确，服务端固定 **`uvicorn --workers 1`**；需要多 worker 时必须改为 DB 租约/外部锁（不在本期）。服务重启时把所有 `running/queued/waiting_user` 的 turn 标记为 `failed`，其 pending proposal 标记为 `expired`。

### 5.6 跨语言契约与几何一致性

后端改为 Python 后，前后端不再共享同一个 zod 包。契约仍保持"一份来源"：

```
Pydantic v2（apps/server/app/contracts/）
  → FastAPI 自动生成 OpenAPI 3.1（REST）
  → scripts/export_contracts.py 导出 JSON Schema（WS 消息、LLM tools）
  → packages/contracts/generated/（openapi.json、ws.schema.json、tools.schema.json）
  → 前端生成：orval（REST TS 类型 + zod client）、json-schema-to-typescript（WS/tools）
```

规则：

1. **Pydantic 是唯一来源**。禁止在前端手写 REST/WS 接口；前端 `apps/web/src/contracts/` 只放生成物与薄封装。
2. **生成物入库并在 CI 校验**。`scripts/export_contracts.py` 与 `pnpm contracts:gen` 生成到 `packages/contracts/generated/`；CI 执行 `pnpm contracts:check`，生成物有 diff 即失败。
3. **共享常量用 JSON**。`packages/contracts/rules.json` 保存空间阈值、枚举、默认值；Python 读取并做 Pydantic 校验，TS 直接 import，避免两套常量漂移。
4. **WS 消息用 Pydantic 判别联合**。`WsEnvelope`、turn/state/host 事件均为带 `type` 判别字段的模型；导出 JSON Schema 供前端生成类型。OpenAPI 不覆盖 WebSocket，因此单独导出。
5. **几何一致性**：服务端 Python/Shapely 为权威；客户端只做拖拽预检（OBB SAT + 房间边界，不做门/过道/业务规则）。`packages/contracts/geometry-fixtures.json` 保存同一组多边形/OBB/期望结果，Python pytest 与前端 Vitest 双 runner 跑同一组向量；预检与服务端冲突时以服务端为准并回滚。
6. **LLM tools 的 schema 也从 Pydantic 生成**（`model_json_schema()`），注册到 OpenAI tools；禁止手写第二份 JSON Schema。

---

## 6. Agent 设计

### 6.1 工具清单

**M3 交付（7 个）：**

| 工具 | 类型 | 输入要点 | 主要 refusal 码 |
|---|---|---|---|
| `query_scene` | 读 | `roomId?`、`itemIds?`、`includePending?`（默认 true） | `not_found` `unknown_room` |
| `search_catalog` | 读 | 关键词/类别/尺寸范围 | `unknown_kind` |
| `add_item` | 写→proposal | `kindId`、`variantId?`、`roomId`、`relation`、`facing?`、`clearance?` | `no_space` `blocks_door` `blocks_window` `collision` `out_of_room` `item_too_large` `walkway_too_narrow` |
| `move_item` | 写→proposal | `itemId` + 新 `relation`，或 `offset`（相对当前位置的米制平移 `{ dx, dz }`） | `not_found` `collision` `blocks_door` `walkway_too_narrow` |
| `remove_item` | 写→proposal | `itemId` | `not_found` |
| `update_item` | 写→proposal | `itemId`、`variantId?`、`materialOverrides?` | `variant_missing` |
| `ask_user` | 特殊 | `question`、`options[]`、`allowMultiple?` | —（循环挂起） |

**M4 交付（2 个）：**

| 工具 | 类型 | 输入/说明 | 主要 refusal 码 |
|---|---|---|---|
| `auto_layout` | 写→proposal | 房间 + 家具清单 + 约束 → 完整提案（亮点①本体） | `no_space` `unsupported_room_shape` `solver_timeout` `item_too_large` |
| `verify_layout` | 读 | 跑合规检查，返回结构化问题清单（供 VLM 闭环与自纠错） | `unknown_room` |

可选（P2）：`batch`（多操作打包一次确认）、`capture_view`（宿主路由截图）。

**契约实现**：每个工具在 `apps/server/app/agent/tools/contracts.py` 定义一个 Pydantic 输入模型（`extra='forbid'`），`ToolRegistry` 用 `model_json_schema()` 生成 OpenAI tools；同一模型用于 L0 校验。导出前做 provider 兼容处理（展开 `$defs`、移除不支持的 keyword），并用快照测试钉住 schema。禁止手写第二份 JSON Schema。

**`relation` 形状（Agent 不输出裸坐标）：**

```ts
type Relation =
  | { type: 'against_wall'; wallId?: string; wallLabel?: string }
  | { type: 'near_window'; openingId?: string }
  | { type: 'near_item'; itemId: string; distance?: number }
  | { type: 'corner' }
  | { type: 'center' }
```

- 多候选时确定性选择：离房间质心最近优先，其次 `edgeIndex` 升序。
- `offset` 只允许在无 `relation` 时使用，表示相对当前位置的米制平移 `{ dx, dz }`；服务端仍会重新校验净空与碰撞。
- `near_item` 可以引用 pending overlay 中的 item；`query_scene` 默认返回 pending 项并标 `pending: true`。
- `facing` 指家具正面朝向；`toward_*` 在求解时转换为朝向目标的方向。
- 设计原则：语义化工具优先（不提供改任意 JSON 的裸工具）；写工具全走 proposal；读工具不门控；每个写工具配稳定 refusal 码。

### 6.2 Agent 循环

```
用户消息 → POST /api/scenes/:id/turns → TurnManager 建 turn
  每轮迭代：
    1. Guardrails：abort 检查 / token 预算（默认 120k）/ 迭代上限（40）/
       墙钟上限（120s）/ 上下文压缩（70% 触发，保留最近 6 条 + 固定事实）
    2. ContextBuilder：system prompt = 角色规则 + 工具守则 + 场景快照
       （rooms/北向/wallLabels/开口/家具/ pending 项）+ 目录摘要
    3. LLM 流式调用（OpenAI 兼容；GLM/Qwen/DeepSeek 可配；
       `openai` Python SDK（AsyncOpenAI）；超时 60s，断流重试 1 次，
       流停滞 30s 判超时）
    4. 增量解析：文本 → turn 事件流；tool_calls 跨 chunk 攒齐；
       按 tool_call_id 去重，重试不重复执行
    5. 分发执行：
       读工具 → pending overlay 查询
       写工具 → 五层校验 → proposal → overlay 更新 → WS 推 ghost
       ask_user → turn 置 waiting_user 挂起（10 分钟超时）
    6. tool result（含 refusal 详情）回灌 → 下一轮
  退出：纯文本收尾（收尾检查不过可注入提醒续跑）| 熔断 | 预算耗尽 | 用户取消
```

- 每场景同时只允许 1 个 `running` turn；新 turn 进入队列（最多 1 个），否则 `turn_in_progress`。`TurnManager` 用 `asyncio.create_task` 启动 turn，每场景一个 `asyncio.Lock`；取消用 `task.cancel()`。
- `turn.cancel`：中止 LLM 流、停止后续工具、把该 turn 未确认 proposal 置 `expired`，场景不受影响。
- 上下文预算：单条 tool result ≤4k tokens，超出截断并附摘要；目录摘要每类 top 20。token 计数优先用 provider `usage`；缺失时按字符估算（中文约 /3、英文约 /4），仅用于预算。
- Provider 适配：`LLMAdapter` 吸收 tool_calls 流式格式、`parallel_tool_calls`、JSON mode、usage 字段差异；每个 provider 有录制回放用例。
- 记录 `usage { iterations, tokensIn, tokensOut, refusals, wallClockMs }`，供评测与成本统计。

### 6.3 工具验证：五层金字塔

| 层 | 检查 | 位置 | 失败示例 |
|---|---|---|---|
| L0 语法 | Pydantic 模型（`model_validate`） | ToolRegistry | `invalid_input` |
| L1 语义 | 引用存在性（item/kind/room/opening） | Operation | `not_found` `unknown_kind` `variant_missing` `unknown_room` |
| L2 空间 | 放置求解 + OBB 碰撞、门净空、过道净宽、房间边界、不压窗、开口合法性 | SceneService | `collision` `blocks_door` `blocks_window` `walkway_too_narrow` `out_of_room` `no_space` |
| L3 业务 | 设计常识规则 | Operation | `unrealistic`（附 `ruleId` 与建议） |
| L4 人工 | ghost 确认/拒绝 | 宿主回调 | — |

**空间常量（集中在 `packages/contracts/rules.json`，Python/TS 共用；可配置）：**

| 常量 | 默认值 | 用途 |
|---|---|---|
| `WALL_HEIGHT` | 2.8 m | 墙高 |
| `WALL_THICKNESS` | 0.12 m | 墙厚 |
| `GRID_SNAP` | 0.10 m | 手动吸附/求解网格 |
| `MANUAL_ROTATION_STEP` | 15° | 手动旋转步进 |
| `SOLVER_ROTATIONS` | 0/90/180/270° | 求解器相对墙法线的允许角度 |
| `DOOR_CLEAR_DEPTH` | 0.60 m | 门扇开启侧净空深度 |
| `DOOR_APPROACH_WIDTH` | 0.90 m | 门两侧通行宽度 |
| `PASSAGE_PRIMARY` | 0.80 m | 主通道最小净宽 |
| `PASSAGE_SECONDARY` | 0.60 m | 次通道/家具间最小净宽 |
| `WINDOW_CLEAR_DEPTH` | 0.05 m | 窗洞室内侧禁放深度 |
| `WALL_FLUSH_GAP` | 0.02 m | "靠墙"允许的最大间隙 |
| `ROOM_INSET` | `wallThickness/2 + 0.02` | 家具可用区相对中心线的内缩 |
| `MIN_ROOM_AREA` | 1.0 ㎡ | 最小房间面积 |
| `AUTO_LAYOUT_MAX_ITEMS` | 20 | 单次求解最大件数 |
| `SOLVER_TIME_BUDGET` | 5000 ms | 求解墙钟上限 |
| `SOLVER_MAX_EVALS` | 50000 | 候选评估次数上限 |
| `SOLVER_BEAM_WIDTH` | 8 | beam search 宽度 |

**Refusal 规范：** `{ code, message, suggest, details? }`，`code` 全局唯一可统计；`suggest` 必须给出可行替代。写工具一次返回**所有**已发现错误（最多 5 条），便于模型自纠。

**Refusal / 错误码注册表（v1.1 冻结）：**

| 类别 | 层 | code | 触发 | suggest 示例 |
|---|---|---|---|---|
| 输入 | L0 | `invalid_input` | schema 不匹配 | 按工具 schema 修正字段/单位 |
| 语义 | L1 | `not_found` | item/opening id 不存在 | 先 `query_scene` 获取最新 id |
| 语义 | L1 | `unknown_kind` | 目录无此 kindId | 用 `search_catalog` 按关键词检索 |
| 语义 | L1 | `variant_missing` | variant 不存在 | 使用 `defaultVariantId` 或检索同 kind |
| 语义 | L1 | `unknown_room` | roomId 不存在 | 先 `query_scene` 获取房间 id |
| 空间 | L2 | `invalid_polygon` | 自交/点数不足/面积过小 | 修正顶点或重新绘制 |
| 空间 | L2 | `room_too_small` | 面积 <1㎡ | 扩大房间 |
| 空间 | L2 | `unsupported_room_shape` | `auto_layout` 遇凹形/非正交 | 手动布置 + `verify_layout`，或拆分房间 |
| 空间 | L2 | `out_of_room` | OBB 超出可用区 | 移向房间中心/换小 variant |
| 空间 | L2 | `collision` | 与现有家具 OBB 重叠 | 沿墙滑动/换位置 |
| 空间 | L2 | `no_space` | 求解器无可行解 | 减少必选件/换小 variant/换墙 |
| 空间 | L2 | `blocks_door` | 进入门净空 | 距门 ≥0.6m，或换到门侧墙 |
| 空间 | L2 | `blocks_window` | 遮挡窗洞体积 | 低于窗台或移开 |
| 空间 | L2 | `walkway_too_narrow` | 过道 <0.6m | 拉开两件家具/换布局 |
| 空间 | L2 | `item_too_large` | 单品超过可用墙段/房间 | 换小 variant/移除其他件 |
| 空间 | L2 | `opening_overlap` | 门窗重叠 | 调整 offset/width |
| 空间 | L2 | `opening_off_wall` | 开口不在墙段上/超出墙段 | 重新放置开口 |
| 空间 | L2 | `solver_timeout` | 求解超时 | 减少件数/放宽约束 |
| 业务 | L3 | `unrealistic` | 违反设计常识（`details.ruleId`） | 按 ruleId 给具体替代 |
| 协议 | — | `proposal_expired` | baseVersion 或依赖不满足 | 重新生成提案 |
| 协议 | — | `version_conflict` | 提交版本落后 | 拉取最新快照后重试 |
| 协议 | — | `turn_in_progress` | 已有 running turn | 等待或取消当前 turn |
| 协议 | — | `turn_cancelled` | 用户取消 | 重新发起 |
| 协议 | — | `budget_exceeded` | token/迭代/墙钟超限 | 缩小任务范围 |
| 系统 | — | `llm_error` | 模型超时/断流/返回非法 | 重试一次；仍失败提示检查配置 |
| 系统 | — | `tool_timeout` | 工具执行超时 | 缩小请求范围 |
| 系统 | — | `capture_unavailable` | 无浏览器/截图失败 | 跳过 VLM，仅做几何校验 |
| 系统 | — | `vlm_invalid_json` | VLM 输出无法解析（重试后） | 记录并跳过，不阻塞用户 |
| 系统 | — | `import_invalid` | 导入文档 schema/迁移失败 | 返回字段级错误 |

L3 规则（P0 至少 5 条，`ruleId` 稳定）：

| ruleId | 规则 | 处理 |
|---|---|---|
| `bed_not_against_wall` | 床头应靠墙（床尾朝向不受影响） | warning；`auto_layout` 作为软约束 |
| `wardrobe_no_clearance` | 衣柜正面净空 ≥0.6m | warning |
| `nightstand_too_far` | 床头柜距床侧 >0.6m | warning |
| `sofa_no_focus` | 沙发未朝向电视墙/茶几 | warning |
| `coffee_table_gap` | 茶几与沙发间距不在 0.3–0.6m | warning |

L3 默认返回 warning 并允许落位；当 `strict=true` 时升级为 refusal `unrealistic`。产品默认对 Agent 写操作使用 `strict=true`，对手动编辑使用 warning（避免打断用户）。

### 6.4 运行时保险

1. **熔断**：连续 3 轮迭代中所有写工具调用均失败 → 注入 system 指令，强制模型停止重试并向用户解释；turn 以 `failed` 结束并保留已产生的 pending proposals（供用户拒绝）。
2. **预算**：token 总量 + 迭代数 + 墙钟三重上限，超限优雅退出（`budget_exceeded`）。
3. **过期作废**：任何外部提交（手动编辑、undo、另一 turn 的确认）都会使所有 pending proposal 过期（`scene_changed`）；另有 15 分钟 TTL。confirm 时再次校验 `baseVersion` 与依赖状态。
4. **幂等**：confirm 按 `proposalId + status` 幂等；重复点击返回 `alreadyConfirmed`；turn 重试按 `tool_call_id` 去重。
5. **重启恢复**：服务启动时把未完成的 turn 标 `failed`，其 proposal 标 `expired`；客户端重连后收到快照与 turn 状态。
6. **取消**：`turn.cancel` 立即中止流与后续工具；已确认的 proposal 不回滚（用户可 undo）。
7. **并发与事件循环**：每场景一个 `asyncio.Lock`；CPU 密集的 `auto_layout` 通过 `ProcessPoolExecutor` 执行（`asyncio.to_thread` 作兜底），避免阻塞 WS 心跳；几何校验可放线程池。服务端固定 `uvicorn --workers 1`。

### 6.5 求解器规格（`auto_layout`）

**实现**：Python 3.12 + Shapely 2.x + NumPy；OBB 用仿射后的 Shapely Polygon（或自研 SAT），beam search 自研；求解在进程池执行，避免阻塞事件循环。

**输入：**

```ts
interface AutoLayoutInput {
  roomId: string
  items: Array<{
    kindId: string
    variantId?: string
    required?: boolean              // 默认 true
    constraint?: ItemConstraint
  }>
  options?: {
    allowPartial?: boolean          // 默认 false
    seed?: number                   // 仅用于平局决胜，不引入随机
    timeBudgetMs?: number           // 默认 5000
    replaceExisting?: boolean       // 默认 false：已有家具作为障碍物保留
  }
}
```

**输出：**

```ts
interface AutoLayoutResult {
  placements: Array<{
    itemId: string
    kindId: string
    variantId: string
    transform: Item['transform']
    constraint?: ItemConstraint
  }>
  unplaced: Array<{ kindId: string; reason: string; suggest: string }>
  removedItemIds?: string[]         // replaceExisting=true 时被移除的 item
  stats: {
    candidates: number
    backtracks: number
    elapsedMs: number
    score: number
  }
}
```

**`verify_layout` 输出：**

```ts
interface VerifyLayoutResult {
  ok: boolean
  issues: Array<{
    code: string
    severity: 'error' | 'warning' | 'info'
    message: string
    itemIds?: string[]
    openingIds?: string[]
    ruleId?: string
  }>
}
```

**算法（P0）：**

1. **预处理**：归一化 polygon；计算内缩可用区（`ROOM_INSET`）；解析 north/facing；把 constraint 转成硬/软约束。
2. **排序**：`required desc → anchorPriority desc → 占地面积 desc → kindId` 升序（固定顺序保证确定性）。
3. **候选位姿**：
   - 靠墙：沿每条墙以 0.1m 步长滑动；旋转取 `allowedRotations` 中与墙法线对齐的 90° 倍数；位置 = 墙内表面 + 深度/2 + 背侧净空。
   - 角落：4 个角 × 允许旋转。
   - 近家具：围绕锚定家具 footprint 以 0.1m 步长生成环形偏移，距离参考目录 `groupWith`。
   - 自由/居中：0.5m 粗网格 + 0.1m 局部细化。
4. **硬约束**：完全在可用区内、OBB 不重叠、门净空、窗洞体积、过道净宽、不压窗、必选件可放置。
5. **搜索**：beam search（宽度 8）+ 有限回溯；状态评分见下；候选评估上限 50k，墙钟上限 5s；超时取当前最优。
6. **软约束评分**（权重可配）：
   - 靠墙间隙：每米 −10（沙发/床/衣柜应贴墙）；
   - 朝向误差：每偏离 90° −20；
   - 成组距离超出推荐区间：−15；
   - 门净空余量 <0.7m：−5；
   - 过道 <0.6m：−20；
   - 窗前遮挡：−10。
7. **收尾**：取最高分完整解；无完整解且 `allowPartial` → 返回最优部分解 + `unplaced`；否则 `no_space` + suggest。
8. **确定性**：不使用随机数；所有集合按稳定键排序；平局按 `(score, itemId 序列, 坐标取整)` 决胜；同输入同输出，结果写入 `stats` 供评测断言。

**边界与失败：**

- 凸多边形：P0 支持。
- 正交 L 型：P1 矩形分解后分别求解，交界处留主过道；未实现前返回 `unsupported_room_shape`。
- 任意凹形：`unsupported_room_shape`，提示改用手动布置 + `verify_layout`。
- 已有家具：默认作为障碍物保留；`replaceExisting=true` 时在同一个 proposal 中包含 remove + add，原子确认（不拆成两个 proposal）。
- 单品放不下：`item_too_large` + suggest（换小 variant / 移除其他件 / 换墙）。
- 全部放不下：`no_space` + 具体建议，不做"硬塞"。
- 求解结果不得直接落库；必须走 proposal → confirm。

### 6.6 VLM 评审闭环（M4）

**流程：**

```
verify_layout 几何检查通过
  → 服务端下发 capture.request（正交俯视、隐藏天花板、ID 标注、固定相机）
  → 前端渲染并回传 PNG（≤1MB，长边 ≤1536）+ legend
  → VLM 按结构化清单评审（挡门/过道/动线/成组/压窗）
  → 服务端校验 JSON、ID 存在性、几何复核
  → issue 映射为约束 → 局部 solve → 新 proposal（ghost）
  → verify_layout 复检；最多 2 轮
```

**输入：**

- 截图：正交俯视，家具标 `A1/A2…`，门窗标 `D1/W1…`，附 legend。
- legend：

```json
{
  "items": [{ "label": "A1", "itemId": "it_...", "kindId": "sofa" }],
  "openings": [{ "label": "D1", "openingId": "op_..." }]
}
```

**输出 schema：**

```json
{
  "verdict": "pass",
  "issues": [
    {
      "type": "blocks_door",
      "severity": "high",
      "itemIds": ["A1"],
      "openingIds": ["D1"],
      "evidence": "A1 overlaps the swing area of D1",
      "suggest": "move A1 at least 0.6 m away from D1"
    }
  ]
}
```

- `type` 枚举：`blocks_door | walkway_narrow | group_misaligned | circulation_blocked | window_blocked`。
- `severity`：`high | medium | low`。
- 服务端只使用 `type + itemIds + openingIds + severity`；`evidence/suggest` 进日志与用户提示。
- **VLM 只做校验不做坐标**；任何坐标字段一律忽略。

**处理规则：**

1. JSON 解析失败重试 1 次（带错误信息要求只输出 JSON）；仍失败 → `vlm_invalid_json`，不阻塞用户。
2. ID 存在性校验；未知 ID 丢弃并记 `vlm_unknown_id`。
3. 几何复核：对每条 issue 用本地规则重算；未复现 → `vlm_issue_unconfirmed`（计为误报）。`circulation_blocked` 用主路径连通性检查（门 → 主要活动区/窗的栅格寻路），而不是仅两两净宽。
4. 映射为约束（见下表），只对受影响 item + 同组 item 局部重排；其余家具固定。
5. 重排后 `verify_layout`；最多 2 轮；仍失败 → 把问题清单交用户，不再自动重试。
6. 每次 VLM 调用超时 30s；每 turn 最多 2 次；成本与耗时记入 `usage`。

| issue type | 映射约束 | 局部重排范围 |
|---|---|---|
| `blocks_door` | 门净空 0.6m × 门宽，双向 | 该 item + 同组 |
| `walkway_narrow` | 两 item 间净宽 ≥0.6m（主通道 0.8m） | 两 item + 同组 |
| `group_misaligned` | 成组间距回到目录推荐区间 | 组内全部 |
| `circulation_blocked` | 房间主路径连通性约束 | 路径上 item |
| `window_blocked` | 窗洞体积前 0.05m 禁放 | 该 item |

**Headless 策略：**

- 实现：`openai` Python SDK 调 VLM；`Pillow` 校验/缩放截图；截图由 Node Playwright 或前端上传。
- CI：VLM 用录制桩（截图哈希 → 固定 JSON），保证确定性；只测"解析 → ID 校验 → 几何复核 → 映射 → 重排 → 复检"链路。
- Nightly/手动：真实 VLM，统计 JSON 可解析率、误报率、重排成功率。
- 截图来源：Playwright 驱动前端渲染；服务端无浏览器时返回 `capture_unavailable`，VLM 环节跳过并明确记录。
- G3 用例：fixture 预置一件挡住门的家具（坐标固定），VLM 桩返回对应 issue，断言重排后 `verify_layout` 为 0 且该家具移出净空区。

### 6.7 同步协议与状态机

**PatchOp** 见 §5.4；所有变更以语义 op 表达，服务端校验后生成 `ScenePatch`。

**Proposal 状态机：**

```
pending ──confirm──> confirmed
   │                    │
   ├──reject──> rejected│
   ├──ttl─────> expired │
   └──external commit──> expired
```

规则：

- `pending` 的 op 只存在于 overlay，不进 `scene_versions`。
- 同一 turn 内后序 proposal 的 `dependsOn` 包含前序 proposal；确认必须按依赖顺序。
- 新 proposal 不依赖其他 turn 的 pending proposal；若它引用/修改其他 turn pending 的 item，服务端拒绝为 `proposal_expired`（先确认或拒绝前一轮）。
- confirm 必须满足：依赖全部 confirmed；当前版本 == `baseVersion + 已确认依赖数`；未过 TTL。
- 任一外部 commit / undo / 另一 turn 的确认 → 其余 pending 全部 `expired`（`scene_changed`）。
- 重复 confirm 返回 `{ ok: true, alreadyConfirmed: true }`，不重复写。
- reject 后依赖项 `expired`，并在 turn 消息里给出原因。
- TTL 15 分钟；服务重启时 pending 全部过期。

**WS 信封：**

```ts
interface WsEnvelope<T = unknown> {
  v: 1
  ch: 'turn' | 'state' | 'host'
  type: string
  seq: number                 // 服务端每通道递增
  ts: number
  turnId?: string
  requestId?: string          // 客户端发起的请求
  payload: T
}
```

**turn 事件**：`turn.started`、`assistant.delta`、`assistant.message`、`tool.started`、`tool.args`、`tool.result`、`proposal.created`、`turn.waiting_user`、`turn.done`、`turn.failed`、`turn.cancelled`。

**state 事件**：`scene.snapshot`、`scene.patch`、`proposal.updated`。

**host 事件（客户端 → 服务端）**：`turn.subscribe`、`turn.cancel`、`proposal.confirm`、`proposal.reject`、`ask_user.answer`、`capture.result`、`commit`、`undo`、`redo`、`resume`。turn 创建走 REST；`turn.subscribe` 用于重连或第二标签订阅已存在的 turn。

**重连**：客户端发 `{ type: 'resume', lastSeqs: { turn, state } }`；服务端能补则补（每 turn 缓冲 500 条），否则 `scene.snapshot + turn.status`；客户端清空本地 Zundo 后重建。心跳 15s；断线指数退避重连。

**实现**：FastAPI WebSocket + Pydantic 判别联合校验每个入站/出站消息；OpenAPI 不覆盖 WebSocket，因此 `scripts/export_contracts.py` 单独导出 `ws.schema.json`；REST/WS 错误统一由 FastAPI exception handler 输出 `{ error: { code, message, suggest?, details? } }`。

**REST 端点（最小集）：**

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | `{ ok, version, model }` |
| GET | `/api/scenes` | 列表（id/name/version/updatedAt） |
| POST | `/api/scenes` | 创建（可带 fixtureId） |
| GET | `/api/scenes/:id` | 当前快照 |
| POST | `/api/scenes/:id/commit` | `{ baseVersion, ops, clientMutationId }` |
| POST | `/api/scenes/:id/undo` | `{ expectedVersion }` |
| POST | `/api/scenes/:id/redo` | `{ expectedVersion }` |
| GET | `/api/scenes/:id/export` | 方案 JSON |
| POST | `/api/scenes/import` | 导入（重映射 ID） |
| GET | `/api/catalog` | 目录 |
| POST | `/api/catalog/reload` | 热加载 |
| GET | `/api/scenes/:id/turns` | 历史消息 |
| POST | `/api/scenes/:id/turns` | 创建 turn，返回 turnId |
| POST | `/api/turns/:id/cancel` | 取消 |
| WS | `/ws?sceneId=` | 三通道复用 |

错误统一：`{ error: { code, message, suggest?, details? } }`，HTTP 4xx/5xx 与 refusal 码对齐。

**版本语义：**

- 每个 commit 产生 `version + 1`；undo 也是新版本，不回退版本号。
- `scene.patch` 带 `fromVersion/toVersion`；服务端拒绝 `baseVersion` 不匹配的提交（`version_conflict`）。
- 客户端手动编辑：拖动中只改本地；落位时提交，服务端跑 L2/L3 后返回 patch，客户端以服务端 patch 为准修正。
- Zundo 记录已提交 commit 的快照用于即时 UI；undo 调服务端，成功后应用 inverse patch，并抑制重复记录。

### 6.8 评测协议

**用例 schema：**

```json
{
  "id": "G1-01",
  "tags": ["golden", "agent", "proposal"],
  "scene": "fixtures/living-room.json",
  "user": "在客厅放一个双人沙发，靠电视墙，再配个茶几",
  "llm": {
    "mode": "scripted",
    "script": "fixtures/llm/G1-01.jsonl"
  },
  "expect": {
    "proposals": { "created": 2, "confirmed": 2 },
    "scene": {
      "items": [
        { "kindId": "sofa", "count": 1 },
        { "kindId": "coffee_table", "count": 1 }
      ]
    },
    "refusals": [],
    "maxTurns": 1,
    "maxLatencyMs": 15000,
    "assert": ["noCollision", "insideRoom", "doorClear"]
  }
}
```

**三个 runner（同一张用例表）：**

| runner | 启动方式 | 覆盖层 | CI |
|---|---|---|---|
| `operation` | pytest 直接调 Python Operation | 几何/校验/求解纯函数 | 每次 PR |
| `api-ws` | pytest + FastAPI TestClient/httpx + 脚本化 LLM | TurnManager/Registry/SceneService/WS | 每次 PR |
| `ui` | Playwright（Node）+ 前端 production build | 渲染、ghost、确认卡片、拖拽 | nightly / 手动 |

**假 LLM 模式与实现：**

- `scripted`：按 turn 回放 JSONL（assistant delta / tool_calls / finish），断言模型看到的上下文。
- `recorded`：真实模型响应录制后回放，用于提示词/工具描述回归。
- `live`：nightly 跑真模型，temperature 0（支持则设 seed），每例 3 次取通过率。
- `scripted`/`recorded` 由 `apps/server/app/testing/fake_llm.py` 实现为 OpenAI 兼容假服务（`/v1/chat/completions` SSE）；`api-ws` runner 通过 `LLM_BASE_URL` 指向它，走真实 `openai` SDK 路径。

**指标：**

- 用例通过率（脚本化 / 真模型分开统计）。
- 金线成功率、平均 turn 轮次、p50/p95 首响与总时长。
- refusal 码分布、refusal 后自纠成功率（同 turn 内下一次写操作成功）。
- solver：成功率、p95 耗时、平均回溯次数。
- VLM：JSON 可解析率、几何复核通过率（误报率）、重排成功率。
- 报告落 `eval/reports/YYYY-MM-DD.json`，README 数字只引用报告。

**用例分布（≥30）：**

| 类别 | 数量 |
|---|---|
| 工具契约/校验（每个 refusal 码至少 1 例） | 10 |
| proposal/确认/拒绝/过期/undo | 6 |
| 求解器（正常/边界/无解/部分/L 型/共墙） | 6 |
| VLM（通过/单问题/多问题+误报） | 3 |
| 多轮与断线（ask_user/取消/重连） | 3 |
| 导入导出/手绘 | 2 |

**CI 分层：**

- PR：`server`（Ruff + mypy + pytest unit/api-ws）+ `web`（typecheck + Biome + Vitest）+ `contracts`（生成物 diff），不调用真实模型。
- Nightly：`ui`（Playwright）+ live LLM + real VLM（secrets 仅在主仓 nightly 可用），产出报告。
- 报告记录 git sha、模型名、基准机、构建模式、Python/Node 版本。

---

## 7. 功能需求清单

| ID | 功能 | 优先级 | 验收要点 |
|---|---|---|---|
| FR-01 | 内置户型加载（3–5 个典型户型 JSON） | P0 | 首页可选户型，秒级加载渲染；JSON 含 rooms/walls/openings/items；schema 校验失败有结构化错误 |
| FR-02 | 房间手绘（多边形墙中心线 + 门窗开口放置） | P0 | 画出闭合房间，吸附 0.1m 网格；简单多边形校验；顶点可编辑；开口可放置在墙段上；非法 polygon 返回 `invalid_polygon` |
| FR-03 | 三维渲染（墙/地板/门窗/家具，2 光源 + 阴影） | P0 | 典型两居 ≤200 实例 60fps（p95）；缺失 GLB 用线框盒兜底；不做天花板 |
| FR-04 | 俯视布局模式（正交相机） | P0 | Tab 切换；墙在俯视下剖切/隐藏到 0.15m，避免遮挡；拖拽体验与 3D 一致 |
| FR-05 | 家具目录面板（分类 + 搜索 + 热加载） | P0 | 目录 JSON 驱动；30–60 件；分类/关键词搜索；`POST /api/catalog/reload` 热加载；缩略图 |
| FR-06 | 手动编辑（拖放/旋转 15° 步进/吸附/删除） | P0 | 撞碰撞体时红色警示并拒绝落位；落位提交服务端复核；失败回滚并 toast refusal；一次拖拽 = 一次 undo |
| FR-07 | 撤销重做（服务端版本历史 + Zundo 镜像） | P0 | AI 确认写入与手动编辑同一 undo 栈；undo 产生新版本；保留最近 100 版；刷新后从服务端重建 |
| FR-08 | 详情卡（点选家具显示属性 + AI 提问入口） | P0 | 显示 kind/variant/尺寸/位置/朝向/约束；点选信息反哺对话上下文（PointContext 简化版） |
| FR-09 | 对话面板（流式输出 + 工具过程可视化 + proposal 卡片） | P0 | 工具调用逐条可见；卡片带确认/拒绝按钮；支持取消 turn；断线后恢复 |
| FR-10 | Agent 写操作闭环（M3 7 工具 + ghost + confirm） | P0 | 金线 G1 全流程通过；pending overlay 可被后续工具引用；确认按依赖顺序 |
| FR-11 | 五层校验 + refusal 体系 | P0 | 注册表内全部 refusal 码有用例覆盖；写工具一次返回所有错误（≤5 条）；suggest 非空 |
| FR-12 | 会话持久化（turn 历史 + 场景版本） | P1 | 刷新后恢复场景、对话与 pending proposals；服务重启后未完成 turn 标 failed |
| FR-13 | 断线重连（seq 补发 + 快照兜底） | P1 | 重连后 2s 内恢复一致；能补发则补发，不足则 snapshot + turn.status；客户端 Zundo 重建 |
| FR-14 | 布局求解器 `auto_layout` | P0 | 金线 G2 通过；求解 ≤5s（p95）；确定性；凸多边形 P0；L 型 P1；凹形拒绝 |
| FR-15 | VLM 评审闭环 | P0 | 金线 G3 通过；JSON 可解析率 100%（含 1 次重试）；几何复核过滤误报；最多 2 轮 |
| FR-16 | before/after 截图（首轮写操作前 + 收尾后） | P1 | 自动附在 turn 消息上；截图失败返回 null 不阻塞；存 `data/screenshots/`，7 天清理，单张 ≤1MB |
| FR-17 | 评测集 + headless runner + CI | P0 | ≥30 例；三 runner 同用例表；PR 跑 pytest unit/api-ws，nightly 跑 ui/live；报告落 `eval/reports/` |
| FR-18 | 成果导出（场景 PNG + 方案 JSON） | P1 | 导出含 `schemaVersion`；导入默认新建场景并重映射 ID；导入后场景可还原；非法文档返回 `import_invalid` |
| FR-19 | 户型图照片 VLM 解析 | P2 | 拍照 → 房间多边形草案 → 手动修正；限制 5MB/JPEG/PNG；解析失败可重试 |
| FR-20 | 第一人称漫游 | P2 | WASD 移动，碰撞约束；不复用编辑交互 |
| FR-21 | 极简 MCP server（5 工具共用契约） | P2 | Python MCP server 与 chat 共用 Pydantic 契约；工具名/描述/schema 一致 |
| FR-22 | 日照环境层 | P2 | 锚定 north + 经纬度，阴影随时间轴变化 |
| FR-23 | Turn 生命周期控制 | P0 | 每场景 1 running + 队列 1；取消立即生效；waiting_user 10 分钟超时；重启恢复 |
| FR-24 | 协议一致性与契约测试 | P0 | WS 信封/seq/重连有契约测试；PatchOp/WS 消息由 Pydantic 生成 TS 类型；生成物 CI 校验无 drift；REST 与 refusal 码对齐 |

---

## 8. 非功能需求

### 8.1 性能

| 维度 | 要求 | 测量方法 |
|---|---|---|
| 渲染 | 典型两居 ≤200 实例，60fps（frame time p95 ≤16.7ms）；draw calls <200；三角面 <500k | production build；1920×1080；Chrome stable；采样 10s |
| proposal 生成 | 服务端操作 + 求解 ≤2s（p95，不含 LLM） | 服务端计时 |
| `auto_layout` | ≤5s（p95）；≤20 件；≤50k 候选评估 | 服务端计时 + `stats` |
| turn 首响 | 首个 token ≤1.5s（p95，不含 provider 故障） | 事件流时间戳 |
| 重连 | ≤2s 恢复一致 | Playwright/ws 测试 |
| 资产 | 单品 ≤1MB/15k tris；首屏 ≤20MB | 构建期检查 |

**基准机**：Apple M1 Pro 16GB 或同级 x86（Ryzen 5 5600 + 16GB + GTX 1660 Super）；Windows 11/macOS；Chrome 最新稳定版；服务端 Python 3.12 + `uvicorn --workers 1`；前端 production build。报告必须记录实际机器与构建模式。

### 8.2 可靠性

- LLM 超时 60s、断流重试 1 次、流停滞 30s 判超时；失败返回 `llm_error`。
- WS 心跳 15s，断线指数退避重连；每 turn 缓冲 500 条事件。
- turn 崩溃不损坏场景（SQLite 事务）；重启后未完成 turn 标 `failed`，pending proposal 过期。
- `asyncio` turn task 的异常必须被捕获并落库；CPU 密集的求解在进程池执行，不阻塞 WS 心跳与其他请求。
- 版本历史保留 100 版；undo/redo 以服务端为准。
- confirm 幂等；commit 带 `clientMutationId`，重复提交返回同一结果。

### 8.3 安全

- LLM/VLM key 仅存服务端环境变量；客户端不得出现 `VITE_*` 密钥；提供 `.env.example`。
- 默认绑定 `127.0.0.1`；`HOST=0.0.0.0` 需显式开启并在 README 标注风险。
- WS 校验 Origin 白名单；HTTP 请求体上限 2MB；图片上传 5MB。
- turn 创建限流 10/min/IP；单场景并发 turn 由 TurnManager 限制。
- 资产路径白名单（`assets/` 下），拒绝绝对路径、`..`、任意 URL；GLB 解析失败安全兜底。
- 场景名、用户文本、家具名均视为不可信数据；工具参数一律 Pydantic 校验（`extra='forbid'`）；不执行模型输出的 shell/URL。
- 图片上传用 `Pillow` 校验真实格式/尺寸并重编码，不信任扩展名；`python-multipart` 限制字段与大小。
- CORS/WS Origin 白名单；限流用 `slowapi`（单 worker 内存桶）或等价中间件。
- 场景 JSON 与截图会发送给第三方 LLM/VLM provider，README 明确披露；支持关闭 VLM。
- 本机自部署无账号鉴权，文档标注仅限本地/受信网络。

### 8.4 可移植

- Docker Compose 一键起（web + server + SQLite volume）；健康检查 `/api/health`。
- Python 3.12 + `uv`（后端）；Node 24 + pnpm 11（前端）；`uvicorn --workers 1`；Windows/macOS/Linux 开发环境均可跑。
- 根目录 `package.json` scripts 统一 `pnpm dev / pnpm test / pnpm contracts`（`concurrently` 起前后端；Windows 无需 make）。
- 环境变量：`LLM_BASE_URL/LLM_API_KEY/LLM_MODEL`、`VLM_BASE_URL/VLM_API_KEY/VLM_MODEL`、`HOST/PORT/DATA_DIR`。

### 8.5 可维护

- 契约/校验器/求解器/几何单测覆盖；评测集进 CI。
- Python：Ruff lint/format + mypy strict（Pydantic 插件）+ pytest；前端：TypeScript strict + Biome + Vitest。
- 生成契约（OpenAPI/JSON Schema → TS）必须无 drift 才能合并。
- ADR.md 记录 D1–D16 与借鉴范围；`CONVENTIONS.md` 记录轴向/单位/角度/ID/命名约定。
- 关键模块（geometry、rules、solver、protocol）不得依赖 React/three 渲染层；Python 几何/求解/校验不得 import FastAPI 或 ORM。
- 数据库迁移与场景 schema 迁移必须有测试。

### 8.6 兼容

Chrome/Edge 最新两个大版本；WebGL2（不要求 WebGPU）；Firefox/Safari 不作为验收目标。

---

## 9. 金线场景（验收剧本）

| ID | 剧本 | 通过标准 |
|---|---|---|
| G1 | "在客厅放一个双人沙发，靠电视墙，再配个茶几" | 目录检索 → 沙发 proposal P1（ghost）→ 茶几 proposal P2（`near_item` 引用 P1，`dependsOn=[P1]`）→ 逐个确认：P1 确认后版本 +1，P2 依赖检查通过并落库 → 两件都可 undo；全程无 refusal 失败 |
| G2 | "帮我把卧室布置好：床、衣柜、床头柜，床要朝南不挡门" | `auto_layout` 一次给出整室提案（床 + 衣柜 + 至少 1 个床头柜）；床尾朝南（与 `north` 点积验证）；`verify_layout` 零问题；求解 ≤5s；确认后 60fps |
| G3 | 对 G2 结果说"检查一下这个布局有没有问题" | fixture 预置一处几何规则未覆盖、但视觉上阻断门到窗主路径的摆件；`verify_layout` 通过 → 带 ID 标注的俯视图 → VLM 返回 `circulation_blocked` → 服务端主路径连通性复核 → 局部重排 → `verify_layout` + 主路径检查通过；最多 2 轮 |
| G4（加分） | 上传一张户型图照片 | VLM 解析出房间多边形草案 → 用户修正 → 进入布置流程 |

**量化目标（上线后以评测集实测替换）**：

- 评测集 ≥30 例；脚本化 runner 通过率 100%。
- 金线 G1–G3 真模型成功率 ≥90%（每例 3 次取通过率，temperature 0）。
- 平均 turn 轮次 ≤6；refusal 后自纠成功率 ≥70%。
- VLM JSON 可解析率 100%（含 1 次重试）；误报率（几何复核未复现）≤20%。
- `auto_layout` p95 ≤5s；渲染 60fps p95。
- 全部数字来自 `eval/reports/`，报告记录 git sha、模型、基准机、构建模式。

---

## 10. 里程碑

| 阶段 | 内容 | 周期 | 验收 |
|---|---|---|---|
| M0 | 脚手架：pnpm workspace（web/contracts/e2e）+ uv Python 项目（server）+ 契约生成流水线 + CI + Docker Compose + ADR + CONVENTIONS | 0.5 周 | `pnpm dev` 起 web+server；`/api/health` 通过；Ruff/mypy/pytest 绿；`pnpm typecheck` 绿；契约生成物无 drift；Docker 一键起；ADR-0001..0004（D1/D6/D9/D15） |
| M1 | 内核 + 渲染：schema、SceneService（Python/Pydantic，confirmed + overlay）、版本/patch、undo、R3F 渲染、俯视、几何单测、**协议冻结** | 2 周 | FR-01/03/04/07/24；手写 JSON 渲染 + 撤销；WS 协议文档与契约测试；几何单测（point-in-polygon/OBB/墙偏移/净空） |
| M2 | 编辑交互：目录、拖放/吸附、详情卡、手绘房间、开口、服务端复核、评测骨架 | 2 周 | FR-02/05/06/08；纯手动布置完一个客厅；拖拽拒绝可回滚；求解器 spike（2 天） |
| M3 | Agent 层：TurnManager（asyncio）/Orchestrator/Registry + 7 工具 + 五层校验 + proposal 链 + ghost/confirm + WS 三通道 + 取消/重连 | 2.5 周 | FR-09/10/11/12/13/23；金线 G1；全部 refusal 码有用例；重连 ≤2s |
| M4 | 求解器 + VLM + 评测：`auto_layout`（Python/Shapely）、`verify_layout`、VLM 闭环、before/after 截图、评测集 CI | 2.5 周 | FR-14/15/16/17；金线 G2/G3；≥30 例；产出量化数字 |
| M5 | 加餐（择做）：成果导出、第一人称、MCP、户型图 VLM 解析、日照 | 1–2 周 | FR-18/19/20/21/22 |

**排期风险控制：**

- M1 末冻结 WS/PatchOp/proposal 状态机，M3 不再改协议。
- M2 末做 2 天求解器 spike，验证凸多边形 + 门净空 + 成组约束；不通过则 M4 降级为"半自动布局"。
- 评测骨架在 M2 末落地，M3 起每个 PR 跑 api-ws 用例。
- M3 前 3 天先做"读工具 + add_item + confirm"垂直切片，再铺其余 6 个工具。
- 若时间不足，优先砍 FR-16 before/after 与 FR-12 会话历史，保 G1–G3 与评测。

---

## 11. 资产与外部依赖

### 11.1 技术栈（M0 锁定版本）

| 层 | 选择 |
|---|---|
| 运行时 | Python 3.12 + `uv`（后端）；Node 24 LTS + pnpm 11（前端） |
| 后端 | FastAPI + Uvicorn（`--workers 1`）+ Pydantic v2 + pydantic-settings |
| 前端 | React 19 + Vite 7 + R3F 9 + drei + three（WebGL2） |
| 状态 | Zustand 5 + Zundo 2 |
| 契约 | Pydantic v2 → OpenAPI 3.1 / JSON Schema → `orval`（REST TS + zod client）+ `json-schema-to-typescript`（WS/tools） |
| 几何 | Shapely 2.x + NumPy（服务端权威）；客户端轻量 OBB SAT 预检 |
| LLM/VLM | `openai` Python SDK（OpenAI 兼容）；`Pillow` 处理图片 |
| 数据库 | SQLite + SQLAlchemy 2.0 async + aiosqlite + Alembic |
| 测试 | pytest + pytest-asyncio + httpx + pytest-cov；Playwright（Node） |
| 质量 | Ruff + mypy strict（Pydantic 插件）；Biome 2 + `tsc --noEmit` + Vitest（前端） |
| 日志/限流 | structlog（JSON 日志）+ slowapi（单 worker 内存限流） |
| CI | GitHub Actions：server / web / contracts / e2e 四个 job |

### 11.2 仓库结构

```
apps/
  web/          React 19 + Vite + R3F 客户端
  server/       Python 3.12 + FastAPI
    pyproject.toml / uv.lock
    app/
      api/        REST + WebSocket 路由
      agent/      TurnManager / Orchestrator / ToolRegistry
      contracts/  Pydantic 模型（唯一契约来源）
      scene/      SceneService / 校验 / 版本历史
      geometry/   Shapely 几何
      solver/     auto_layout（beam search）
      vlm/        标注截图评审
      db/         SQLAlchemy 模型 / Alembic
      testing/    fake LLM / 录制回放
    tests/        pytest（operation / api-ws / eval）
packages/
  contracts/    OpenAPI/JSON Schema 生成物、TS 类型、rules.json、geometry-fixtures.json
e2e/            Playwright（Node）
assets/
  models/       GLB
  thumbnails/   缩略图
fixtures/       内置户型、评测 fixture、录制 LLM 响应
docs/
  adr/          ADR 记录（D1–D16）
  CONVENTIONS.md
eval/reports/   评测报告
scripts/
  export_contracts.py
package.json    根 scripts：dev / test / contracts（concurrently 起前后端）
```

### 11.3 外部依赖与许可

| 项 | 选择 | 许可/说明 |
|---|---|---|
| 家具模型主线 | Kenney Furniture Kit、Quaternius | CC0 |
| 模型点缀 | Poly Haven（少量） | CC0 |
| 模型补充 | Sketchfab 下载 glTF | CC-BY，保留署名清单 |
| LLM | OpenAI 兼容接口（GLM / Qwen / DeepSeek 可配） | 服务端持有 key |
| VLM | GLM-4V / Qwen-VL（可配） | 服务端持有 key；截图外发需披露 |
| Python 依赖 | FastAPI / SQLAlchemy / Shapely / NumPy / Pillow 等 | MIT/BSD/Apache；记录在 `docs/DEPENDENCIES.md` |
| 参考（不抄码） | aedifex `wiki/architecture/*`、Holodeck/SceneCraft 论文思想 | 架构文档阅读记录进 ADR |

---

## 12. 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| LLM 空间认知弱、输出不稳定 | 工具调用失败率高 | 约束式输出 + 五层校验 + refusal 自纠 + 熔断；评测集持续监控 |
| VLM 幻觉/定位漂移 | 评审误报 | VLM 只回 ID + issue type；服务端几何复核；最多 2 轮；误报率统计 |
| WS 状态同步复杂度 | 开发周期拉长 | 协议 M1 冻结；seq 补发 + 快照兜底；外部提交统一过期 pending |
| 共墙/开口重锚定 | 门窗丢失或穿墙 | `edgeIndex` + 重锚定规则；`opening_reanchored/opening_removed` 警告；用例覆盖 |
| Proposal 链过期 | 多 proposal 确认失败 | overlay + dependsOn；外部提交即过期；G1 用例钉死 |
| 求解器边界情况（L 型/异形） | 卡壳 | 凸多边形 P0；正交 L 型 P1；任意凹形拒绝并降级手动 |
| 资产风格不统一 | 演示观感差 | 主线锁 Kenney/Quaternius 同风格低模；资产预算 |
| 范围蔓延 | 做不完 | 严格按 §2.2 砍；M5 前不加新域 |
| 排期乐观 | M3/M4 延期 | 垂直切片、协议冻结、求解器 spike、评测前移、P1 可裁 |
| 真模型评测成本 | CI 慢/费用高 | PR 只跑脚本化；live 每周/手动；报告分开统计 |
| 单用户假设 | 多标签页冲突 | 明确第二标签只读或 version_conflict；不做实时协同 |
| 截图/VLM 依赖浏览器 | headless CI 不可用 | 录制 VLM 桩；无浏览器返回 `capture_unavailable`；Playwright 兜底 |
| 跨语言契约漂移 | 前后端字段/枚举不一致 | Pydantic 单一来源 + 自动生成 + CI `contracts:check` 失败即阻断 |
| Python 事件循环阻塞 | WS 心跳/其他请求卡顿 | 求解放进程池；几何校验放线程池；`uvicorn --workers 1`；接口压测 |
| SQLite 单写入/单 worker | 并发写入冲突或锁等待 | WAL + `busy_timeout` + 单 worker；写路径短事务；多 worker 需 DB 租约（不在本期） |
| Python/TS 几何双实现漂移 | 客户端预检与服务端判定不一致 | 共享 `geometry-fixtures.json` 双 runner；服务端权威；冲突回滚 |
| Shapely/NumPy 依赖 | 平台 wheel/版本兼容 | 锁定版本；CI 在 Windows/macOS/Linux 跑安装与单测；Docker 固定基础镜像 |

---

## 13. 合规与声明

- 本项目为独立自研实现；aedifex 仅作架构思想参考，不复制其源码；在 README 与简历中如实表述"参照开源项目架构思想自研"。
- 本项目采用 MIT 许可证；所有第三方资产保留 license 与署名清单（`assets/CREDITS.md`）。
- 简历中的全部量化指标必须来自本仓库评测集实测输出（`eval/reports/`），禁止估写。
- LLM/VLM key 只在服务端；README 披露场景 JSON 与截图会发送给第三方模型 provider，并说明如何关闭 VLM。
- ADR 记录阅读了哪些参考文档、借鉴了哪些思想、哪些是独立实现。

---

## 附录 A：v1.1 / v1.2 修订对照

| 原问题 | v1.0 位置 | 修正位置 |
|---|---|---|
| 工具数量 8/9/7 不一致 | §2.1/§6.1/FR-10 | §2.1、§6.1、FR-10（统一 9 = 7+2） |
| 墙/开口模型缺失 | §5 | §5.1、§5.2、D7 |
| 门窗字段不足 | §5 | §5.1 `Opening` |
| proposal 版本冲突 / pending 引用 | §5、§6.4 | §6.7、D8、§5.4 |
| undo 归属不清 | FR-07 | D9、§6.7、FR-07 |
| 无指北 / facing 歧义 | §5、G2 | §4.3、§5.1、D10、G2 |
| 求解器规格不足 | §6.1、FR-14 | §6.5、D13 |
| VLM 不可验证 | §6.5 | §6.6、D14 |
| 协议未定义 | §4.1 | §6.7、D11、§5.4 |
| 评测三 runner 未定义 | FR-17 | §6.8 |
| 空间阈值缺失 | §2.1/§6.3 | §6.3 常量表 |
| 性能缺基准 | §8 | §8.1 |
| 安全缺细则 | §8 | §8.3 |
| 排期乐观 | §10 | §10 风险控制 |
| 后端 Node/TS/Fastify/zod | §4.1/§11 | Python 3.12 + FastAPI + Pydantic（D15、§11） |
| 前后端契约共享方式 | §3/D6 | Pydantic → OpenAPI/JSON Schema → TS（D6、§5.6） |
| 跨语言几何重复实现 | §5/FR-06 | 客户端预检 + Python 权威 + 共享 fixture（D16、§5.6） |
| Python 并发/评测/部署 | §6/§8 | asyncio + 进程池 + pytest runner + 单 worker（§6.4/§6.8/§8） |