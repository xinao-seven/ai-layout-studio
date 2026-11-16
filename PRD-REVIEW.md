# PRD 评审与补漏建议（v1.0 → v1.1）

| 项 | 内容 |
|---|---|
| 评审对象 | [PRD.md](D:/code/three/PRD.md) v1.0（2026-11-16） |
| 参考 | aedifex `DECISIONS.md`、`wiki/architecture/*`（只读，不抄码） |
| 结论 | **方向与架构成立，可以立项；但有 7 个阻塞级缺口，建议补齐后再进 M0。** |

---

## 0. 总体评价

**做得好的地方**

- 一句话定位清楚：AI 出约束、求解器出坐标、人拍板，不是"让 LLM 直接生成 3D"。
- D1–D6 六个架构决策都有理由，且互相自洽；服务端持真相源使 headless 评测成立。
- 三层工具范式、五层校验、proposal/confirm、评测闭环，都是可演示、可量化的亮点。
- 范围外清单明确，风险表诚实，合规声明到位。

**主要问题**

- 数据模型停在概念级：墙、开口、变换、约束、目录规则都缺关键字段和约定。
- 状态机只画了主干：多 proposal 的版本链、undo 归属、断线重放没有定义。
- 求解器与 VLM 是两大亮点，但输入/输出规格不足以直接实现和验收。
- 量化目标缺少测量协议：怎么跑、跑几次、什么机器、什么构建。
- 排期偏乐观：M3/M4 各 2.5 周，对单人全栈偏紧。

---

## 1. 阻塞级问题（P0，开发前必须定）

### B1 墙体与开口模型缺失

**问题**

1. `Room` 只有 `polygon`，没有墙厚、墙高、polygon 是墙中心线还是内表面。
2. `Opening.wallSegmentRef` 指向什么没有定义；polygon 顶点增删后引用会失效。
3. 开口字段不足：窗缺 `sillHeight`，门缺 `swing/hinge`，`offset` 从哪一端量没写。
4. 两个房间共墙时，门/窗归属哪一侧、渲染几次、净空算几侧，均未定义。
5. `Room.floorHeight` 命名有歧义：是地面标高还是墙高/层高？

**影响**：渲染、碰撞、门净空、窗下家具、手绘编辑、内置户型全部依赖它；不定就会在 M2 返工。

**建议决议**

- 明确约定：`polygon` 为**墙中心线**，单位米，点序 CCW，坐标 `[x, z]`；墙厚/墙高为场景默认值（0.12m / 2.8m），可按房间覆盖。
- 派生 `WallSegment`，ID 稳定为 `${roomId}:e${edgeIndex}`；开口引用 `{ roomId, edgeIndex, offsetFrom: 'start' }`。
- polygon 编辑时按"最近边 + 最近点"重锚定开口；距离超过 0.5m 则删除并在提交结果里返回 `opening_removed` 警告（不能静默丢失）。
- 共墙检测：两条共线且重叠的边视为同一面墙，渲染去重；开口切两侧；校验两侧净空。若 M2 实现成本过高，MVP 可先限制"内置户型共墙处不放门"，但必须写进限制说明。
- 开口补齐：`width/height/sillHeight`（窗，默认 0.9m）、`door.swing: single|double|sliding`、`door.hinge: start|end`、`door.opensTo: in|out`。
- `floorHeight` 重命名为 `wallHeight`；楼层标高不在本期模型内。

### B2 Proposal 的版本链与"待确认家具"引用

**问题**

1. G1 中"先放沙发，再放茶几（near 沙发）"：第二个 `add_item` 需要引用第一个**尚未确认**的 item，但当前模型里它还不存在。
2. 每个写工具各产生一个 proposal，且都以 turn 开始时的 `baseVersion` 为基准；确认第一个后版本 +1，第二个 confirm 时 `baseVersion` 不一致，按 §6.4 会被作废。
3. 多个 pending proposal 的覆盖关系、确认顺序、拒绝后依赖项怎么办，均未定义。
4. proposal 过期只写了状态，没有触发条件。

**建议决议（推荐方案：待确认覆盖层 + 依赖链）**

- `SceneService` 维护两层：`confirmedScene` 与 `pendingOverlay = confirmedScene + 所有 pending proposals（按创建顺序）`。
- 读工具（`query_scene/search_catalog` 的场景查询）读 overlay；pending item 带 `pending: true` 与 proposalId。
- item ID 在 proposal 创建时就由服务端铸好，确认后直接落库，不需要临时 ID 映射。
- 每个 proposal 记录：`baseVersion`、`dependsOn: proposalId[]`（同 turn 内前序写操作 + 之前仍 pending 的 proposal）。
- 确认规则：所有依赖已 confirmed；当前版本 == baseVersion + 已确认依赖数；否则返回 `proposal_expired`（附当前版本与重新生成建议）。
- 拒绝规则：该 proposal 置 `rejected`；依赖它的 proposal 置 `expired`。
- 外部提交（手动编辑、undo、另一 turn 的确认）一律使所有 pending proposal 过期，理由 `scene_changed`；这是最省心且可解释的规则。
- TTL：15 分钟未操作自动 `expired`。
- 确认幂等：按 proposalId + 状态判断，重复点击返回同一结果，不重复落库。
- 这样 G1 的"逐个确认"成立，且第二个 proposal 的坐标在创建时已把第一个 ghost 算进去。

### B3 Undo 的归属：客户端 Zundo vs 服务端真相源

**问题**：PRD 说 Zundo 管撤销重做（含 AI 确认写入），但场景真相源在服务端。若 undo 只回滚浏览器状态，服务端版本与场景会漂移；若只回滚服务端，客户端 Zundo 栈会错位。

**建议决议**

- **服务端持有版本历史**：`scene_versions(scene_id, version, doc_json, patch_json, inverse_json, actor, turn_id)`，append-only；undo 是"应用 inverse patch 产生一个新版本"，版本号单调递增，不回退。
- **客户端 Zundo 只做两层**：① 拖拽过程中的本地草稿/手势合并；② 已提交 commit 的镜像栈，用于即时 UI 反馈。
- 手动编辑：拖动中只改本地；落位时发 `commit { baseVersion, ops }`，服务端跑 L2/L3 后返回 `{ version, patch, inverse }`，客户端以服务端 patch 为准修正本地。
- undo/redo：客户端发 `POST /scenes/:id/undo`（带 `expectedVersion`），服务端返回 inverse patch；客户端应用后把结果写入 Zundo，但抑制重复记录。
- 断线重连：清空客户端 Zundo，按服务端快照重建；历史只保留最近 100 个版本。
- 这样 FR-07"AI 写入与手动编辑同一 undo 栈"在服务端成立，客户端只是视图。

### B4 缺少指北/朝向，"床要朝南"无法实现

**问题**：`SceneDocument` 没有 north/orientation；G2 的"床要朝南"、未来日照层都无法落地。且 `facing` 的语义（家具正面朝哪、床的正面是床尾还是床头）未定义。

**建议决议**

- `SceneDocument.north: Vec2`，单位向量，默认 `[0, -1]`（世界 -Z 为北）；世界约定 `+X 东、-Z 北、Y 上`。
- 工具 `facing` 枚举：`north|east|south|west|toward_door|toward_window|toward_item`；`toward_*` 需给 id。
- 定义"正面"：目录资产约定 `frontAxis: '+Z'`；床的正面是**床尾方向**。工具描述里写明"facing 指家具正面朝向"，避免"床头朝南/床尾朝南"歧义；必要时 LLM 应 `ask_user`。
- 所有朝向在存储时落为 `rotationY`（弧度） + 保留原 constraint，便于解释和重排。

### B5 求解器规格不足，无法直接实现与验收

**问题**：`auto_layout` 只写了"房间 + 家具清单 + 约束 → 完整提案"和"按区域关系退避搜索"。缺：

- 家具清单由谁给、必选/可选怎么表达；
- 约束语言与优先级；
- 候选位姿如何生成、评分函数、搜索与回溯上限；
- 失败时是部分布局还是 refusal；
- 确定性、时间预算、最大件数；
- 已有家具是保留、替换还是合并；
- L 型/凹形房间到底支持到什么程度。

**建议决议**（详见 §6 的规格草案）

- 输入显式包含 `items[]`（`kindId/variantId/required/constraint`）与 `options { allowPartial, seed, timeBudgetMs }`。
- 算法：约束图 → 按"必选、锚定优先级、占地面积"排序 → 逐件生成候选位姿（沿墙 0.1m 滑动 × 90° 旋转；近家具环形偏移）→ beam search（宽度 8）+ 有限回溯 → 硬约束剪枝 + 软约束评分 → 取最优完整解。
- 确定性：固定遍历顺序，平局按 `(score, itemId 序列, 坐标取整)` 决胜，不用随机数；同输入同输出。
- 预算：≤5s、≤20 件、≤50k 次候选评估；超时返回已找到的最优部分解并标 `solver_timeout`。
- 失败：`required` 件无解 → `no_space` + suggest（减少件数/换小 variant/换墙）；有可选件时允许部分布局并逐件说明 `unplaced`。
- 形状：P0 只支持凸多边形；正交 L 型可用矩形分解作为 P1 加分；任意凹形 `auto_layout` 明确拒绝 `unsupported_room_shape`，降级为手动 + 校验（与 PRD 风险表一致，但要写成 refusal 而不是"静默降级"）。
- 已有家具：默认 `preserve`（作为障碍物）；`replace` 需显式参数，并先生成 before/after 提案。

### B6 VLM 闭环缺少"可验证"的中间契约

**问题**

1. "VLM 只做校验不定位"与"意见转化为约束回流触发局部重排"矛盾：不定位就无法指出是哪件家具、哪条过道。
2. 评审输出只有四类问题的自然语言描述，没有 JSON schema、severity、证据字段、ID 引用。
3. 截图由浏览器渲染，headless CI 里没有浏览器时 VLM 闭环怎么跑没定义。
4. G3 要求"构造一处过道侵占"——评测里如何确定性构造、如何断言复检通过，未定义。
5. VLM 幻觉/误报如何拦截、最多重排几轮、成本与超时，均未定义。

**建议决议**（详见 §7）

- 渲染**带 ID 标注的俯视图**：家具标 `A1/A2…`，门窗标 `D1/W1…`，附 legend；VLM 只引用这些 ID，不输出坐标。
- 输出固定 JSON：`{ verdict, issues: [{ type, severity, itemIds, openingIds, evidence, suggest }] }`；服务端只采信 `type + ids + severity`，`evidence/suggest` 仅供人和日志。
- 服务端校验：所有 ID 必须存在于当前场景；每条 issue 先经几何复核，未复现的丢弃并记 `vlm_issue_unconfirmed`（计入误报率）。
- 映射表：`blocks_door → 门净空约束`、`walkway_narrow → 最小过道约束`、`group_misaligned → 成组间距约束`、`circulation_blocked → 动线约束`；映射后只对受影响 item + 同组成员做局部 solve，其他家具固定。
- 重排上限 2 轮；每轮后跑 `verify_layout` 几何复检；仍失败则把问题清单交给用户，不再自动重试。
- Headless：CI 用**录制的 VLM 响应桩**（同一截图 → 同一 JSON）；真实 VLM 调用只在 nightly/手动跑，报告分开统计。截图由 Playwright 驱动前端产生，或由前端在 turn 中上传。
- G3 用例：fixture 里预先塞一件挡住门的家具（坐标固定），VLM 桩返回对应 issue，断言重排后 `verify_layout` 为 0 且该家具移出净空区。

### B7 同步协议未定义到可实现

**问题**：PRD 说 WS 三通道复用一条连接，但没有消息信封、seq、ack、断线重放、快照/补丁切换规则；REST 只有一句"场景 CRUD / 目录 / 历史会话"；`PatchOp` 没有定义。

**建议决议**（详见 §5）

- 统一信封：`{ v, ch: 'turn'|'state'|'host', type, seq, ts, turnId?, payload }`；服务端每通道单调 `seq`。
- 断线重连：客户端带 `lastSeqs` 重连；服务端优先补发缓冲区事件（每 turn 500 条），不足则发 `scene.snapshot + turn.status`，客户端整屏替换。
- `PatchOp` 用**语义 op**（`item.add/remove/update`、`room.*`、`opening.*`），不用 RFC6902；每个 op 带 `before` 以便生成 inverse。
- REST 明确到端点（§5 给出表）；turn 创建走 REST，事件走 WS，确认/拒绝走 WS host 通道（也可 REST 双保险）。
- 版本语义：每个 commit 产生 `version+1`；`scene.patch` 带 `fromVersion/toVersion`；服务端拒绝 `baseVersion` 不匹配的提交（`version_conflict`）。

---

## 2. 重要缺口（P1，进 M3 前补齐）

| # | 缺口 | 位置 | 建议 |
|---|---|---|---|
| I1 | 工具数量自相矛盾：§2.1 写"8 个"，§6 是 7+2=9，FR-10 又写"7 工具" | §2.1/§6.1/§7 | 统一为"M3 7 个 + M4 2 个 = 9 个；MVP 验收按 7 个" |
| I2 | refusal 码没有注册表，无法做到"全部码有用例" | §6.3/FR-11 | 建一张 code 表：code/layer/message 模板/suggest 模板/触发条件/用例 ID；先冻结 20 个码 |
| I3 | 空间阈值全是形容词："门净空""过道净宽""不压窗" | §2.1/§6.3 | 常量集中到 `shared/rules.ts`：门净空深 0.6m、门两侧通道 0.9m、主过道 0.8m/次 0.6m、窗洞体积（sill~sill+h）前 0.05m 禁放 |
| I4 | 目录 schema 缺关键字段：正面轴、放置类型、缩略图、搜索标签、默认 variant、clearance 规则 | §5/FR-05 | 见 §4 的 CatalogEntry v1.1；资产约定：GLB 米制、Y-up、底部中心、front=+Z、AABB→OBB、缩略图 256px |
| I5 | 手动编辑只写了"客户端红警拒绝"，没有服务端复核 | FR-06 | 客户端校验只做 UX；落位必须提交服务端跑同一套 L2/L3；失败回滚到上次服务端状态并 toast refusal |
| I6 | turn 并发/取消/超时/服务重启未定义 | §6.2 | 每场景同时 1 个 running turn，队列最多 1；`turn.cancel` 中止流并过期未确认 proposal；`waiting_user` 10 分钟超时；重启把 running 标 failed |
| I7 | 上下文预算与工具结果截断未定义 | §6.2 | 默认 prompt 32k tokens；单条 tool result ≤4k tokens；目录摘要每类 top 20；70% 触发压缩，保留最近 6 条 + 固定事实；记录 usage |
| I8 | 评测协议缺失：三 runner 是哪三个、假 LLM 怎么做、指标怎么算 | FR-17/§9 | 见 §8；三 runner = operation / api-ws / Playwright UI；CI 用脚本化 LLM，nightly 跑真模型 |
| I9 | 性能目标没有基准机器、构建模式、测量方法 | §8 | 写明基准硬件、production build、p50/p95、采样 10s；资产预算：单品 ≤1MB/15k tris，首屏 ≤20MB |
| I10 | 持久化只有"SQLite（场景/会话/评测结果）" | §4.1 | 给出 6 张表与迁移策略；`scene_versions` 存全量快照；WAL；保留最近 100 版 |
| I11 | 安全只写了 key 不落前端 | §8 | 默认绑 127.0.0.1、WS Origin 校验、turn 限流 10/min、请求体 2MB、资产路径白名单、VLM 图片外发声明、`.env` 不提交 |
| I12 | 导出/导入没有 ID 与版本语义 | FR-18 | 导出含 `schemaVersion`；导入默认新建场景并重映射 ID，保留文档内引用；版本从 0 开始；非法文档结构化拒绝 |
| I13 | 截图（before/after、VLM）缺兜底与存储 | FR-16/§6.5 | `preserveDrawingBuffer` 或手动 `gl.render` 后 `toDataURL`；失败返回 null 不阻塞 turn；存 `data/screenshots/`，7 天清理，单张 ≤1MB |

---

## 3. 次要问题与编辑性修订

| # | 位置 | 问题 | 建议 |
|---|---|---|---|
| M1 | §5 | `SceneDocument` 缺 `id/name/schemaVersion/north/createdAt/updatedAt` | 补全（见 §4） |
| M2 | §5 | `Item.transform` 未定义 | `{ position: [x,y,z], rotationY: number, scale?: number }` |
| M3 | §5 | `CatalogEntry.rules` 未定义 | 明确字段：`mustAgainstWall/groupWith/preferredWall/anchorPriority/roomTypes/clearance` |
| M4 | §5 | `Proposal.ops` 未定义 | 用 §5 的语义 PatchOp |
| M5 | §5 | 没有墙/顶面材质、没有房间类型 | 加 `room.type`、`scene.defaults.wallMaterial`；不做天花板或俯视自动隐藏 |
| M6 | §7 FR-04 | "2D 俯视 + 正交相机"是 3D 正交还是 2D canvas 不明确 | 推荐 R3F 正交俯视，墙在俯视下按 0.15m 高度剖切/隐藏，避免遮挡 |
| M7 | §8 兼容 | 未说明 WebGL2 vs WebGPU | 明确 WebGL2（R3F 默认），不要求 WebGPU |
| M8 | §10 M1 | "净空推导（简化 reconcile）"含义不清 | 改成"房间/开口派生 + 基础几何校验" |
| M9 | §9 | "三 runner"未定义 | 在 §8 或 §9 写清三个 runner 与 CI 分层 |
| M10 | §2.2/§4 | 未说明多标签页/多客户端 | 明确单用户单主标签；第二标签只读或 version_conflict，不做实时协同 |
| M11 | §13 | 项目自身许可证未定 | 建议 MIT；`LICENSE` + `assets/CREDITS.md` |
| M12 | §11 | 参考项目是 MIT，但本项目"不抄码"需可核查 | 在 ADR 记录"读了哪些文档、借鉴了哪些思想、没有复制源码" |
| M13 | 全文 | 术语"区域（region）"未定义 | 明确 region = roomId；`near_window` 多窗时按"离 region 锚点最近"确定性选择，或允许显式 openingId |
| M14 | 全文 | `near{itemId}` 在 pending 场景下的语义 | 由 B2 的 overlay 解决；工具描述里写明可引用 pending item |
| M15 | §6.1 | `move_item` 的"偏移"没有定义 | 明确是相对当前位置的米制偏移，还是相对锚定物的约束；建议只用 relation/锚定，不暴露裸坐标 |

---

## 4. 建议的数据模型 v1.1

```ts
type Vec2 = [number, number]        // [x, z]，米
type Vec3 = [number, number, number]

interface SceneDocument {
  schemaVersion: 1
  id: string
  name: string
  version: number                   // 服务端单调递增
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
  wallHeight?: number
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
    id?: string
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
    modelPath: string
    thumbnailPath: string
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

**派生墙段**（不落库，读时生成；开口引用 edgeIndex）：

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

**关键约定**

- 世界：`+X 东、-Z 北、Y 上`；`rotationY` 以 +Y 为轴、从 +Z 转向 +X 为正（写进 `CONVENTIONS.md` 并用单测钉住）。
- polygon 必须简单、无自交、面积 ≥1㎡；服务端归一化并返回 `invalid_polygon/room_too_small`。
- 墙段共线重叠视为共墙，渲染去重；开口切两侧。
- 所有长度单位为米，所有角度存储为弧度，工具输入输出可用度（zod 转换）。

---

## 5. 建议的协议

### 5.1 PatchOp（语义 op，服务端与客户端共用）

```ts
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

### 5.2 Proposal 状态机

```
pending ──confirm──> confirmed
   │                    │
   ├──reject──> rejected│
   ├──ttl─────> expired │
   └──external commit──> expired
```

规则：

- `pending` 的 op 只存在于 overlay，不进 `scene_versions`。
- confirm 必须满足：依赖全部 confirmed、当前版本 == `baseVersion + 已确认依赖数`、未过 TTL。
- 任一外部 commit / undo / 新 turn 的确认 → 其余 pending 全部 `expired`（`scene_changed`）。
- 重复 confirm 返回 `{ ok: true, alreadyConfirmed: true }`，不重复写。
- reject 后依赖项 `expired`，并在 turn 消息里给出原因。

### 5.3 WS 信封

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

**host 事件**（客户端 → 服务端）：`turn.start`（或走 REST）、`turn.cancel`、`proposal.confirm`、`proposal.reject`、`ask_user.answer`、`capture.result`、`commit`、`undo`、`redo`、`resume`。

**重连**：客户端发 `{ type: 'resume', lastSeqs: { turn, state } }`；服务端能补则补，否则 `scene.snapshot + turn.status`；客户端清空本地 Zundo 后重建。

### 5.4 REST 端点（最小集）

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
| GET | `/api/scenes/:id/turns` | 历史消息 |
| POST | `/api/scenes/:id/turns` | 创建 turn，返回 turnId |
| POST | `/api/turns/:id/cancel` | 取消 |
| WS | `/ws?sceneId=` | 三通道复用 |

错误统一：`{ error: { code, message, suggest?, details? } }`，HTTP 4xx/5xx 与 refusal 码对齐。

---

## 6. 求解器规格草案（`auto_layout`）

### 6.1 输入

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
  }
}
```

### 6.2 输出

```ts
interface AutoLayoutResult {
  proposal: {
    placements: Array<{
      itemId: string
      kindId: string
      variantId: string
      transform: Item['transform']
      constraint?: ItemConstraint
    }>
  }
  unplaced: Array<{ kindId: string; reason: RefusalCode; suggest: string }>
  stats: { candidates: number; backtracks: number; elapsedMs: number; score: number }
}
```

### 6.3 算法（P0）

1. **预处理**：归一化 polygon；计算内缩可用区（墙厚/2 + 0.02）；解析 north/facing；把 constraint 转成硬/软约束。
2. **排序**：`required desc → anchorPriority desc → 占地面积 desc → kindId`（固定顺序保证确定性）。
3. **候选位姿**：
   - 靠墙：沿每条墙以 0.1m 步长滑动，旋转为墙法线方向 ±90°/180°（按 `allowedRotations` 过滤），位置 = 墙内表面 + 深度/2 + 背侧净空；
   - 角落：4 个角 × 允许旋转；
   - 近家具：围绕锚定家具 footprint 以 0.1m 步长生成环形偏移，距离用目录 `groupWith` 推荐值；
   - 自由/居中：0.5m 粗网格 + 0.1m 局部细化。
4. **硬约束**：完全在可用区内、OBB 不重叠、门净空、窗洞体积、过道净宽、不压窗、必选件可放置。
5. **搜索**：beam search（宽度 8）+ 有限回溯；状态评分见 6.4；扩展次数上限 50k；超时取当前最优。
6. **软约束评分**（权重可配，建议值）：
   - 靠墙间隙：每米 -10（沙发/床/衣柜应贴墙）；
   - 朝向误差：每偏离 90° -20；
   - 成组距离超出推荐区间：-15；
   - 门净空余量 <0.7m：-5；
   - 过道 <0.6m：-20；
   - 窗前遮挡：-10。
7. **收尾**：取最高分完整解；无完整解且 `allowPartial` → 返回最优部分解 + `unplaced`；否则 `no_space` + suggest。
8. **确定性**：不使用随机数；所有集合按稳定键排序；同输入同输出；结果写入 `stats` 供评测断言。

### 6.4 失败与边界

- 凸多边形：P0 支持。
- 正交 L 型：P1 矩形分解后分别求解，交界处留过道；未实现前返回 `unsupported_room_shape`。
- 任意凹形：`unsupported_room_shape`，提示改用手动布置 + `verify_layout`。
- 已有家具：默认作为障碍物保留；`replace: true` 时先生成移除提案，再生成新布局提案（两个 proposal，链式确认）。
- 单品放不下：`item_too_large` + suggest（换小 variant / 移除其他件 / 换墙）。
- 全部放不下：`no_space` + 具体建议，不做"硬塞"。

---

## 7. VLM 评审规格草案

### 7.1 输入

- 服务端下发 `capture.request`（含相机位姿、正交俯视、隐藏天花板、标注开关）。
- 前端渲染后返回 PNG（≤1MB，长边 ≤1536）+ `legend`：
  ```json
  { "items": [{ "label": "A1", "itemId": "it_...", "kindId": "sofa" }],
    "openings": [{ "label": "D1", "openingId": "op_..." }] }
  ```
- VLM prompt 包含：任务说明、评审清单（挡门/过道/动线/成组/压窗）、legend、输出 JSON schema、禁止输出坐标。

### 7.2 输出 schema

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

### 7.3 处理流程

1. 校验 JSON；失败重试 1 次（带错误信息要求只输出 JSON）；仍失败 → `vlm_invalid_json`，不阻塞用户。
2. ID 存在性校验；未知 ID 丢弃并记 `vlm_unknown_id`。
3. 几何复核：对每条 issue 用本地规则重算；未复现 → `vlm_issue_unconfirmed`（误报）。
4. 映射为约束（见下表），只对受影响 item + 同组 item 局部重排；其余固定。
5. 重排后 `verify_layout`；最多 2 轮；仍失败 → 把问题清单交用户。

| issue type | 映射约束 | 局部重排范围 |
|---|---|---|
| `blocks_door` | 门净空 0.6m × 门宽，双向 | 该 item + 同组 |
| `walkway_narrow` | 两 item 间净宽 ≥0.6m（主通道 0.8m） | 两 item + 同组 |
| `group_misaligned` | 成组间距回到目录推荐区间 | 组内全部 |
| `circulation_blocked` | 房间主路径连通性约束 | 路径上 item |
| `window_blocked` | 窗洞体积前 0.05m 禁放 | 该 item |

### 7.4 Headless 策略

- CI：VLM 用录制桩（截图哈希 → 固定 JSON），保证确定性；只测"解析→映射→重排→复检"链路。
- Nightly/手动：真实 VLM，统计 JSON 可解析率、误报率、重排成功率。
- 截图来源：Playwright 驱动前端渲染；服务端无浏览器时返回 `capture_unavailable`，VLM 环节跳过并明确记录。

---

## 8. 评测协议草案

### 8.1 用例 schema

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

### 8.2 三个 runner（同一张用例表）

| runner | 启动方式 | 覆盖层 | CI |
|---|---|---|---|
| `operation` | Vitest 直接调 Operation | 几何/校验/求解纯函数 | 每次 PR |
| `api-ws` | 起 Fastify 测试实例 + 脚本化 LLM + ws client | TurnManager/Registry/SceneService/WS | 每次 PR |
| `ui` | Playwright + 前端 production build | 渲染、ghost、确认卡片、拖拽 | nightly / 手动 |

### 8.3 假 LLM

- `scripted`：按 turn 回放 JSONL（assistant delta / tool_calls / finish）；支持断言"模型看到了什么"。
- `recorded`：真实模型响应录制后回放，用于回归提示词/工具描述改动。
- `live`：nightly 跑真模型，temperature 0（支持则设 seed），每例 3 次取通过率。

### 8.4 指标

- 用例通过率（脚本化 / 真模型分开统计）。
- 金线成功率、平均 turn 轮次、p50/p95 首响与总时长。
- refusal 码分布、refusal 后自纠成功率（同 turn 内下一次写操作成功）。
- solver：成功率、p95 耗时、平均回溯次数。
- VLM：JSON 可解析率、几何复核通过率（误报率）、重排成功率。
- 报告落 `eval/reports/YYYY-MM-DD.json`，README 数字只引用报告。

### 8.5 用例分布（≥30）

- 工具契约/校验：10（每个 refusal 码至少 1 例）。
- proposal/确认/拒绝/过期/undo：6。
- 求解器：6（正常/边界/无解/部分/L 型/共墙）。
- VLM：3（通过/单问题/多问题+误报）。
- 多轮与断线：3（ask_user/取消/重连）。
- 导入导出/手绘：2。

---

## 9. 排期与范围风险

| 风险 | 说明 | 建议 |
|---|---|---|
| M3 过重 | WS 三通道 + Agent 循环 + 7 工具 + 五层校验 + ghost/confirm + 重连，2.5 周对单人偏紧 | 协议在 M1 末冻结；M3 先做"读工具 + 2 个写工具 + confirm"垂直切片，再补工具 |
| M4 过重 | 求解器 + VLM + 评测 + CI 同时做 | 评测骨架提前到 M2；求解器在 M2 末做 2 天 spike；VLM 若延期降为 P1 |
| FR-13 重连 | 完整补发 + 快照切换复杂 | M3 只做 `seq` 补发 + 快照兜底；多标签页协同明确不做 |
| FR-16 截图 | before/after 与 VLM 截图需求重叠 | 先做 VLM 截图（M4 必需），before/after 展示放 M5 |
| 真模型评测成本 | 30 例 × 3 次 nightly 费用与时长 | CI 默认脚本化；真模型每周 1 次或手动触发 |
| 性能声明 | 60fps 依赖资产与机器 | 先定资产预算与基准机器，再写指标；报告附环境 |
| 手绘 + 共墙 | 编辑器与数据模型容易互相拖累 | M2 先支持单房间；多房间与共墙放 M2 末/M3 初 |

---

## 10. 需要拍板的决策（附推荐）

| # | 决策 | 推荐 | 理由 |
|---|---|---|---|
| D-a | 墙体模型：polygon 派生 vs 显式 Wall 实体 | **polygon 派生 + 稳定 edgeIndex**（M2 若共墙痛苦再升级） | 保留手绘体验，改动最小 |
| D-b | Proposal 粒度：每工具一个 vs 每 turn 一个 | **每工具一个 + 依赖链 + overlay** | 满足 G1"逐个确认"，语义清晰 |
| D-c | Undo 权威：客户端 vs 服务端 | **服务端版本历史，Zundo 做镜像** | 真相源一致，断线可恢复 |
| D-d | 朝向：compass vs 向量 | **`north: Vec2` + facing 枚举** | 可测试、可扩展日照 |
| D-e | 求解器形状支持 | **P0 凸多边形；正交 L 型 P1；任意凹形拒绝** | 与风险表一致，避免无底洞 |
| D-f | VLM 证据：自然语言 vs ID 标注 | **ID 标注 + 几何复核** | 可验证、可映射、可统计误报 |
| D-g | 评测 runner | **operation / api-ws / ui 三个** | 与三层工具范式对齐 |
| D-h | 服务端框架 | **Fastify 5 + ws** | schema 校验、TS 友好、WS 可控 |
| D-i | zod 版本 | **zod 4**（用内置 `toJSONSchema`） | 一份 schema 前后端 + LLM tools |
| D-j | 项目许可证 | **MIT** | 与参考项目一致，简历项目友好 |
| D-k | 并发 turn | **每场景 1 个 running + 队列 1** | 成本与状态机可控 |
| D-l | 多客户端 | **不做实时协同；第二标签 version_conflict/只读** | 范围外，避免 CRDT 复杂度 |

---

## 11. 可直接合入 PRD 的修订清单

1. §2.1 "8 个工具" → "9 个（M3 7 + M4 2）；MVP 验收按 7 个"。
2. §5 `SceneDocument` 增补 `schemaVersion/id/name/north/defaults/createdAt/updatedAt`；`Room.floorHeight` → `wallHeight`；`Room` 增 `type`。
3. §5 `Opening` 增 `offsetFrom/sillHeight/door{swing,hinge,opensTo}`；明确 `edgeIndex` 语义与重锚定规则。
4. §5 `Item` 增 `source`；明确 `transform` 结构；`constraint` 增 facing/anchor/near/clearance/group 字段。
5. §5 `CatalogEntry` 增 `tags/defaultVariantId/placement/frontAxis/clearance/allowedRotations/license`；`rules` 展开。
6. §5 `Proposal` 增 `dependsOn/baseVersion` 语义、overlay 规则、TTL 与过期触发条件。
7. §6.1 补 `auto_layout/verify_layout` 的输入输出；`move_item` 明确 relation/偏移语义。
8. §6.2 补 turn 并发、取消、waiting_user 超时、服务重启处理、上下文预算与 tool result 截断。
9. §6.3 增 refusal 码注册表（至少 20 个）与"所有错误一起返回"的策略。
10. §6.4 过期规则改为"外部提交即过期 + 15 分钟 TTL"。
11. §6.5 补 VLM 输入（带 ID 的俯视截图 + legend）、输出 JSON schema、几何复核、映射表、2 轮上限、headless 策略。
12. §7 增 FR：协议冻结（M1 末）、turn 取消/重连、服务端校验手动编辑、版本历史/undo、目录热加载、导入导出 ID 重映射。
13. §8 增性能基准（机器/构建/采样）、资产预算、安全细则（绑定地址/Origin/限流/路径白名单/图片外发声明）。
14. §9 量化目标补测量协议：脚本化 vs 真模型、每例次数、报告路径、环境记录。
15. §10 M1 改"净空推导"为"房间/开口派生 + 基础几何校验"；M3/M4 增加垂直切片与 spike。
16. §11 增 `assets/CREDITS.md`、`LICENSE`、`CONVENTIONS.md`（轴向/单位/角度/ID 约定）。
17. §12 风险表增"共墙/开口重锚定""proposal 链过期""VLM 误报"三行。
18. §13 增"本项目 MIT；参考项目仅架构思想，ADR 记录阅读与借鉴范围"。

---

## 附录：与参考项目的对齐检查

| aedifex 模式 | 本项目对应 | 差异 |
|---|---|---|
| Contract / Operation / Executor 三层 | §6 三层工具范式 | 我们的 Executor 在服务端，参考项目分 MCP/chat |
| Detached scene + patch + dependency check | proposal 的 baseVersion + overlay | 我们不做完整 dependency 追踪，用版本号 + 过期规则 |
| Refusal as answer | refusal `{code,message,suggest}` | 我们要求 suggest 必填，并建注册表 |
| PointContext | 简化版点选反哺 | 我们只做点选 + 房间/家具上下文，不做截图气泡 |
| Capture runtime（host-routed） | VLM 截图任务下发 | 我们限定俯视标注图，不做自由相机 |
| Agent surfaces parity | chat 与 MCP 共用契约（P2） | 我们 P2 才做 MCP，先保证契约纯净 |
| ADR/DECISIONS 纪律 | ADR.md | 建议 M0 就写 ADR-0001..0006 |

> 注意：aedifex 基于 Pascal Editor（MIT），本项目只读其架构文档；实现时不得复制源码。所有借鉴点在 ADR 中如实记录。