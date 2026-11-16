# AI 室内布局工作台 · 需求文档（PRD）

| 项 | 内容 |
|---|---|
| 版本 | v1.0（2026-11-16） |
| 状态 | 评审中 → 待开发 |
| 项目性质 | 个人简历亮点项目（面向通用前端/全栈岗位） |
| 参照声明 | 架构思想参照开源项目 aedifex（MIT）的公开架构文档，**仅参考设计思想，不复制代码** |

---

## 1. 项目概述

### 1.1 一句话定位

一个对话式 AI 室内布局工作台：用户上传/绘制户型，用自然语言和直接操作（拖拽、点选）完成家具布置；AI Agent 以"输出约束、确定性求解器产出坐标"的方式生成布局提案，经用户确认后落库。

### 1.2 项目目标

1. **产品目标**：从"输入户型"到"导出布置成果"的完整闭环可用
2. **技术目标**：完整自研实现一套 Agent 驱动的三维编辑器，包含：客户端 3D 编辑器、服务端 Agent 循环、三层工具范式、五层空间校验、布局约束求解器、VLM 视觉评审闭环、评测回归体系
3. **简历目标**：支撑四条亮点——Agent 架构 / VLM 闭环 / 人机协同交互 / 质量工程，所有量化指标来自自建评测集实测

### 1.3 目标用户

- 有布置需求的普通用户（租房/新房/改造），不想学专业软件
- （隐性读者）面试官：通过演示与代码理解架构能力

---

## 2. 范围界定

### 2.1 范围内（In Scope）

| 域 | 内容 |
|---|---|
| 户型输入 | 手绘房间（多边形墙线）、门窗开口放置、内置 3–5 个典型户型 JSON；（加分项）户型图照片 → VLM 解析多边形 |
| 三维场景 | R3F 渲染房间/门窗/家具，俯视布局模式，第一人称漫游（P1） |
| 手动编辑 | 家具目录、拖放/旋转/吸附、选择与详情卡、撤销重做 |
| AI 布局 | 服务端 Agent 循环 + 8 个工具（详见 §6），ghost 预览 + 确认制写入 |
| 空间校验 | OBB 碰撞、门净空、过道净宽、房间边界、不压窗、业务常识规则 |
| 布局求解器 | 整室多家具约束求解（朝向/成组/净空/动线），P0 按区域关系退避搜索 |
| VLM 评审 | 布局结果俯视渲染 → 视觉模型结构化评审 → 意见回流触发局部重排 |
| 人机协同 | 点选家具反哺对话（简化版 PointContext）、before/after 截图、多轮编辑式指令 |
| 成果输出 | 场景快照导出 PNG、布置方案 JSON 导入导出 |
| 质量工程 | 评测集（≥30 例）、断言最终场景状态、三 runner 同用例表、headless CI |

### 2.2 范围外（Out of Scope，明确不做）

多楼层/楼梯/屋顶/CSG 布尔运算、IFC 导入、插件体系、多语言 i18n、2D CAD 图纸、实时协同多人编辑、日照/窗外视野环境层（列为 M5 可选）、text-to-3D 生成家具（仅作彩蛋演示）、移动端适配、用户账号体系（单用户自部署）。

---

## 3. 术语定义

| 术语 | 定义 |
|---|---|
| Scene / 场景文档 | 服务端持有的唯一真相源：房间、开口、家具实例、版本号的集合 |
| Proposal / 提案 | AI 写操作产生的待确认变更包（patch），确认前不进入场景历史 |
| Ghost / 鬼影 | Proposal 在客户端的半透明预览渲染 |
| 契约（Contract） | 工具的名称、描述、zod 输入 schema 的唯一定义 |
| Operation | 契约对应的纯函数：校验、求解、产出变更与 refusal |
| Refusal | 结构化拒绝：稳定错误码 + 人话原因 + 可行建议 |
| Turn | 一次用户消息触发的完整 Agent 循环（异步任务） |
| 评测集 | 断言最终场景状态的自动化任务用例集合 |

---

## 4. 系统架构

### 4.1 总体形态

```
浏览器（渲染端 + 交互采集）
  React 19 + Vite + R3F + Zustand + Zundo
        ▲ │ REST：场景 CRUD / 目录 / 历史会话
        │ ▼ WebSocket（三通道复用一条连接）
        │   ① turn 事件流：token 增量 / tool_call 事件 / 进度 / 完成
        │   ② 状态同步：版本号 + patch（proposal 与 commit）
        │   ③ 宿主回调：confirm / reject / ask_user 回答 /（可选）截图
┌───────┴─────────────────────────────────┐
│ 服务端（Node + TS）                      │
│  SyncGateway  WS 三通道                  │
│  TurnManager  异步任务状态机              │
│  Orchestrator Agent 循环 + Guardrails    │
│  ToolRegistry 契约 → LLM tools           │
│  SceneService 场景真相源 + 校验器 + proposal │
│  CatalogService 家具目录与资产下发         │
│  VLMService    评审服务（截图走宿主路由）   │
│  存储：SQLite（场景/会话/评测结果）        │
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
| D6 | 契约层前后端同构（shared package） | 前端表单校验与后端执行用同一份 schema |

---

## 5. 数据模型（概要）

```
SceneDocument {
  version: number                 // 乐观锁 + 同步基准
  units: 'metric'
  rooms: Room[]                   // { id, name, polygon: Vec2[], floorHeight, floorMaterial }
  openings: Opening[]             // { id, type: door|window, wallSegmentRef, offset, width, height, style }
  items: Item[]                   // { id, kindId, variantId, transform, constraint?, materialOverrides? }
}

Item.transform  由放置求解器产出（存储最终坐标与朝向）
Item.constraint 保留 LLM 输出的关系约束（可解释、可重排）

CatalogEntry {
  kindId, name, category,          // sofa / bed / table ...
  variants: [{ id, sizeM: [w,d,h], modelPath }],
  pivot: 'bottom-center',
  collision: 'obb-from-aabb',
  rules?: { facing?, mustAgainstWall?, groupWith? }
}

Proposal {
  id, baseVersion, ops: PatchOp[],
  status: 'pending' | 'confirmed' | 'rejected' | 'expired',
  createdAt, createdBy: 'agent' | 'user-turn'
}

Turn {
  id, sceneId, status: queued|running|waiting_user|done|failed|cancelled,
  messages: [{ role: user|assistant|tool, content, toolCalls?, toolResult? }],
  usage: { iterations, tokensIn, tokensOut, refusals: RefusalCode[] }
}
```

资产规范：GLB、米制、Y-up、底部中心锚点、AABB 自动生成 OBB 碰撞体；目录由 JSON 元数据驱动。

---

## 6. Agent 设计

### 6.1 工具清单

**M3 交付（7 个）：**

| 工具 | 类型 | 输入要点 | 主要 refusal 码 |
|---|---|---|---|
| `query_scene` | 读 | 关注区域/类别过滤 | — |
| `search_catalog` | 读 | 关键词/类别/尺寸范围 | `unknown_kind` |
| `add_item` | 写→proposal | kindId、region、relation（near_window/against_wall/near{itemId}）、facing、clearance | `no_space` `blocks_door` `collision` `out_of_room` |
| `move_item` | 写→proposal | itemId + 新 relation/偏移 | `not_found` `collision` |
| `remove_item` | 写→proposal | itemId | `not_found` |
| `update_item` | 写→proposal | itemId、变体/材质 | `variant_missing` |
| `ask_user` | 特殊 | 问题 + 选项 | —（循环挂起） |

**M4 交付（2 个）：**

| 工具 | 类型 | 说明 |
|---|---|---|
| `auto_layout` | 写→proposal | 整室布局求解：房间 + 家具清单 + 约束 → 完整提案（亮点①本体） |
| `verify_layout` | 读 | 跑合规检查，返回结构化问题清单（供 VLM 闭环与自纠错） |

可选（P2）：`batch`（多操作打包一次确认）、`capture_view`（宿主路由截图）。

设计原则：语义化工具优先（不提供改任意 JSON 的裸工具）；写工具全走 proposal；读工具不门控；每个写工具配稳定 refusal 码。

### 6.2 Agent 循环

```
用户消息 → TurnManager 建 turn
  每轮迭代：
    1. Guardrails：abort 检查 / token 预算 / 迭代上限（默认 40）/ 上下文压缩（阈值可配，保留最近 6 条）
    2. ContextBuilder：system prompt = 角色规则 + 工具守则 + 场景快照（每轮重取）+ 目录摘要
    3. LLM 流式调用（OpenAI 兼容；GLM/Qwen/DeepSeek 可配）
    4. 增量解析：文本 → 事件流通道；tool_calls 跨 chunk 攒齐
    5. 分发执行：读工具直查；写工具穿五层校验 → proposal → WS 推 ghost；
       ask_user → turn 置 waiting_user 挂起
    6. tool result（含 refusal 详情）回灌 → 下一轮
  退出：纯文本收尾（收尾检查不过可注入提醒续跑）| 熔断 | 预算耗尽 | 用户取消
```

### 6.3 工具验证：五层金字塔

| 层 | 检查 | 位置 | 失败示例 |
|---|---|---|---|
| L0 语法 | zod schema | ToolRegistry | `invalid_input` |
| L1 语义 | 引用存在性（item/kind/region） | Operation | `not_found` `unknown_kind` |
| L2 空间 | 放置求解 + OBB 碰撞、门净空、过道净宽、房间边界、不压窗 | SceneService | `collision` `blocks_door` `no_space` |
| L3 业务 | 设计常识规则 | Operation | `unrealistic`（附建议） |
| L4 人工 | ghost 确认/拒绝 | 宿主回调 | — |

Refusal 规范：`{ code, message, suggest }`，code 全局唯一可统计；suggest 必须给出可行替代（如"墙净宽 0.8m，建议换 0.7m 推拉门或改放对面墙"）。

### 6.4 运行时保险

1. **熔断**：连续 3 轮所有校验失败 → 注入 system 指令，强制模型停止重试并向用户解释
2. **预算**：token 总量 + 迭代数双上限，超限优雅退出
3. **过期作废**：confirm 时校验 proposal 的 baseVersion 与当前版本一致，不一致则作废并提示重新生成

### 6.5 VLM 评审闭环（M4）

流程：`verify_layout` 几何检查通过后 → 服务端向浏览器下发截图任务（host-routed）→ 俯视渲染图 → VLM 按结构化清单评审（挡门/过道侵占/动线遮挡/成组错位）→ 输出 JSON 意见 → 意见转化为约束回流 → 触发局部重排（solve + 新 proposal）。约束：**VLM 只做校验不做坐标**；评审结论必须映射到字段化 JSON，不采纳自由文本。

---

## 7. 功能需求清单

| ID | 功能 | 优先级 | 验收要点 |
|---|---|---|---|
| FR-01 | 内置户型加载（3–5 个典型户型 JSON） | P0 | 首页可选户型，秒级加载渲染 |
| FR-02 | 房间手绘（多边形墙线 + 门窗开口放置） | P0 | 画出闭合房间，吸附 0.1m 网格 |
| FR-03 | 三维渲染（墙/地板/门窗/家具，2 光源 + 阴影） | P0 | 典型两居 ≤200 实例 60fps |
| FR-04 | 俯视布局模式（2D 俯视 + 正交相机） | P0 | Tab 切换，拖拽体验一致 |
| FR-05 | 家具目录面板（分类 + 搜索） | P0 | 目录 JSON 驱动，热插拔 |
| FR-06 | 手动编辑（拖放/旋转 15° 步进/吸附/删除） | P0 | 撞碰撞体时红色警示并拒绝落位 |
| FR-07 | 撤销重做（Zundo，含 AI 确认写入） | P0 | AI 写入与手动编辑同一 undo 栈 |
| FR-08 | 详情卡（点选家具显示属性 + AI 提问入口） | P0 | 点选信息反哺对话上下文 |
| FR-09 | 对话面板（流式输出 + 工具过程可视化 + proposal 卡片） | P0 | 工具调用逐条可见；卡片带确认/拒绝按钮 |
| FR-10 | Agent 写操作闭环（7 工具 + ghost + confirm） | P0 | 金线 G1 全流程通过 |
| FR-11 | 五层校验 + refusal 体系 | P0 | 全部 refusal 码有用例覆盖 |
| FR-12 | 会话持久化（turn 历史 + 场景版本） | P1 | 刷新后恢复场景与对话 |
| FR-13 | 断线重连（版本比对 + 快照/patch 补发） | P1 | 重连后 2s 内恢复一致 |
| FR-14 | 布局求解器 `auto_layout` | P0 | 金线 G2 通过；求解 ≤5s |
| FR-15 | VLM 评审闭环 | P0 | 金线 G3 通过；评审 JSON 可解析率 100% |
| FR-16 | before/after 截图（首轮写操作前 + 收尾后） | P1 | 自动附在 turn 消息上 |
| FR-17 | 评测集 + headless runner + CI | P0 | ≥30 例，PR 自动回归 |
| FR-18 | 成果导出（场景 PNG + 方案 JSON） | P1 | 导出文件可再导入还原 |
| FR-19 | 户型图照片 VLM 解析 | P2 | 拍照 → 房间多边形草案 → 手动修正 |
| FR-20 | 第一人称漫游 | P2 | WASD 移动，碰撞约束 |
| FR-21 | 极简 MCP server（5 工具共用契约） | P2 | 同一契约两 surface 对等测试 |
| FR-22 | 日照环境层（P2，可视作 M5 加餐） | P2 | 锚定经纬度朝向，阴影随时间轴变化 |

---

## 8. 非功能需求

| 维度 | 要求 |
|---|---|
| 性能 | 渲染 60fps（典型两居 ≤200 实例）；写工具 proposal 生成 ≤2s；`auto_layout` ≤5s；turn 首响 ≤1.5s |
| 可靠性 | LLM 超时/断流重试 1 次；WS 断线自动重连；turn 崩溃不损坏场景（SQLite 事务） |
| 安全 | LLM/VLM key 仅存服务端环境变量；WS 消息 zod 校验；场景接口本机自部署无鉴权（文档标注） |
| 可移植 | Docker Compose 一键起（web + server）；Windows/macOS/Linux 开发环境均可跑 |
| 可维护 | 契约/校验器/求解器单测覆盖；评测集进 CI；架构决策记录（ADR.md，参照 aedifex DECISIONS 思想自写） |
| 兼容 | Chrome/Edge 最新两个大版本 |

---

## 9. 金线场景（验收剧本）

| ID | 剧本 | 通过标准 |
|---|---|---|
| G1 | "在客厅放一个双人沙发，靠电视墙，再配个茶几" | 目录检索 → 两件 proposal（ghost）→ 逐个确认落库 → 可撤销；全程无 refusal 失败 |
| G2 | "帮我把卧室布置好：床、衣柜、床头柜，床要朝南不挡门" | `auto_layout` 一次给出整室提案，`verify_layout` 零问题，确认后 60fps |
| G3 | 对 G2 结果说"检查一下这个布局有没有问题" | VLM 评审返回 JSON 意见（构造一处过道侵占）→ 自动局部重排 → 复检通过 |
| G4（加分） | 上传一张户型图照片 | VLM 解析出房间多边形草案 → 用户修正 → 进入布置流程 |

**量化目标**（上线后以评测集实测替换）：评测集 ≥30 例；金线场景成功率 ≥90%；平均 turn 轮次 ≤6；refusal 后自纠成功率 ≥70%。

---

## 10. 里程碑

| 阶段 | 内容 | 周期 | 验收 |
|---|---|---|---|
| M0 | 脚手架：pnpm workspace（web/server/shared）+ CI 骨架 | 0.5 周 | 空应用部署跑通 |
| M1 | 内核 + 渲染：schema、SceneService、undo、R3F 渲染、净空推导（简化 reconcile） | 2 周 | FR-01/03；手写 JSON 渲染 + 撤销 |
| M2 | 编辑交互：目录、拖放/吸附、详情卡、俯视模式、手绘房间 | 2 周 | FR-02/04/05/06/07；纯手动布置完一个客厅 |
| M3 | Agent 层：TurnManager/Orchestrator/Registry + 7 工具 + 五层校验 + ghost/confirm + WS 三通道 | 2.5 周 | FR-09/10/11/13；金线 G1 |
| M4 | 求解器 + VLM + 评测：`auto_layout`、`verify_layout`、VLM 闭环、评测集 CI | 2.5 周 | FR-14/15/17；金线 G2/G3，产出量化数字 |
| M5 | 加餐（择做）：成果导出、第一人称、MCP、户型图 VLM 解析、日照 | 1–2 周 | 对应 FR |

---

## 11. 资产与外部依赖

| 项 | 选择 | 许可/说明 |
|---|---|---|
| 家具模型主线 | Kenney Furniture Kit、Quaternius | CC0 |
| 模型点缀 | Poly Haven（少量） | CC0 |
| 模型补充 | Sketchfab 下载 glTF | CC-BY，保留署名清单 |
| LLM | OpenAI 兼容接口（GLM / Qwen / DeepSeek 可配） | 服务端持有 key |
| VLM | GLM-4V / Qwen-VL（可配） | 服务端持有 key |
| 参考（不抄码） | aedifex `wiki/architecture/*`、Holodeck/SceneCraft 论文思想 | 架构文档阅读记录进 ADR |

---

## 12. 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| LLM 空间认知弱、输出不稳定 | 工具调用失败率高 | 约束式输出 + 五层校验 + refusal 自纠 + 熔断；评测集持续监控 |
| VLM 幻觉/定位漂移 | 评审误报 | VLM 只校验不定位；结论必须字段化；复检闭环 |
| WS 状态同步复杂度 | 开发周期拉长 | 版本号 + patch 从简起步；先快照后增量；M1 就定协议 |
| 资产风格不统一 | 演示观感差 | 主线锁 Kenney/Quaternius 同风格低模 |
| 范围蔓延 | 做不完 | 严格按 §2.2 砍；M5 前不加新域 |
| 求解器边界情况（L 型房/异形） | 卡壳 | P0 支持凸多边形 + 退避搜索；凹形降级为手动 + 校验 |

---

## 13. 合规与声明

- 本项目为独立自研实现；aedifex 仅作架构思想参考，不复制其源码；在 README 与简历中如实表述"参照开源项目架构思想自研"。
- 所有第三方资产保留 license 与署名清单（`assets/CREDITS.md`）。
- 简历中的全部量化指标必须来自本仓库评测集实测输出（`eval/reports/`），禁止估写。
