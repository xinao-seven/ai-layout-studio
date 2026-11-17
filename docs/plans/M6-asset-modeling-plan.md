# M6 变更计划：Agent 参数化建模与运行时资产库

- Status: Draft
- Date: 2026-11-17
- 范围: 后端生成器 + 资产库 + agent 工具 + 前端资产推送 + 部署配置
- 不做: T3（外部 text-to-3D / image-to-3D）

---

## 0. 决策锁定

| # | 决策 | 理由 |
|---|---|---|
| D1 | 生成资产写入 `data/assets/`，**不写** `fixtures/catalog.json` | catalog.json 有 60 条硬上限（`assets.py:15`）、assets-index 需构建期生成、热重载会抹掉活场景引用 |
| D2 | 目录为持久化真相，**不建 DB 表** | manifest.json 足够；无 alembic 迁移；场景侧本来就只存 kindId |
| D3 | `CatalogService` 双源合并（builtin + library） | 上层（校验/搜索/求解/AgentLoop）零改动即可看见新资产 |
| D4 | kindId 命名空间 `lib_<family>_<hash8>` | 与内置 60 条永不撞名；可直接用于"单独列出" |
| D5 | T1 缩放实现为退化生成器 `scale` family，而非 variant 加 `scale` 字段 | 保持唯一不变量「GLB AABB == sizeM」；校验器、渲染器、前端零改动 |
| D6 | 新增 state 通道事件 `catalog.updated` | 前端 catalog 只在 bootstrap 拉一次，新资产必须主动推送，否则 ghost 渲染不出来 |
| D7 | 工具通过 feature flag 开关，默认开启、生产可关 | 生成器是新面，出问题可一键降级 |
| D8 | `revise_asset` 需要 `replace_pending_ops`，作为独立阶段 | 是"spec 即状态"能力的前置；可延后 |

---

## 1. 目标与非目标

### 目标
1. Agent 能按参数生成家具（定制尺寸的书柜/衣柜/L 型书桌/橱柜/电视柜）。
2. Agent 能对现有资产做合法缩放（T1）。
3. 生成物**走现有 proposal/ghost/确认流**，不改 agent loop、不改确认协议。
4. 资产沉淀为可复用库，`search_catalog` 可检索，跨回合可用。
5. 全链路可确定性测试（同 spec → 同 hash → 同字节）。

### 非目标
- 外部 AI 3D 生成（T3）。
- 异步 job / 长任务工具（T3 才需要）。
- 资产版本管理、编辑历史（只做"新建 + 修订 pending proposal"）。
- 用户上传自有模型（后续可复用同一资产库路径，但不在本期）。
- 材质/贴图生成（只用几套内置纯色/木纹材质预设）。

---

## 2. 数据流

```
用户: "加一个 2.4 米宽的通顶书柜"
  │
  ▼
AgentLoop ── tool: model_item{spec} ──► ToolExecutor
                                          │
                                          ▼
                                     ModelGenService
                                     spec → specHash
                                       ├─ 命中缓存 → 直接返回 entry
                                       └─ 未命中 → 网格发射 → write_glb
                                            → glb_stats 自检(AABB==spec, tri≤15k)
                                            → PIL 缩略图 256×256
                                            → 写 data/assets/tmp/<hash>.glb
                                          │
                                          ▼
                                     CatalogService.register(entry)
                                     → registry.update_catalog / manager.update_catalog
                                          │
                                          ▼
                              gateway.emit_catalog_updated(entry)   [state 通道, 先发]
                                          │
                                          ▼
                              place_add(kindId, relation, ...)      [现有 L1/L2/L3]
                                          │
                                          ▼
                              registry.propose(...) → Proposal
                                          │
                                          ▼
                              emit proposal.created                 [state 通道, 后发]
                                          │
                    ┌─────────────────────┴─────────────────────┐
                    ▼                                           ▼
      前端 catalog 合并新 entry                      前端 pendingOverlay
      （拿到 modelPath/sizeM）                       （按 ops 画 ghost）
                    └─────────────────────┬─────────────────────┘
                                          ▼
                              用户确认 → 现有 confirm_proposal
                                          → scene.patch → 应用 op
                                          → promote 资产到 models/
```

**顺序不变量**：`catalog.updated` 必须在引用它的 `proposal.created` 之前发出。两者同在 state 通道、seq 单调，前端按 seq 顺序处理即可保证。

---

## 3. 阶段拆解

### P0 — 基础设施（1.5–2 人日）

只搭骨架，不生成任何资产。完成后：库目录为空时系统行为与今天完全一致。

| 文件 | 改动 |
|---|---|
| `app/core/config.py` | 新增 `asset_library_dir: Path \| None = None`（默认 `data_dir/"assets"`）、`asset_generation_enabled: bool = True`、`asset_tmp_ttl_hours: int = 24` |
| `app/catalog/library.py`（新） | 资产库读写：`load_manifest()` / `save_manifest()` / `promote()` / `gc_tmp()` / `path_for(hash)` |
| `app/catalog/service.py` | `_load_builtin()` + `_load_library()`；`_entries` 改为合并结果；`preview()` 也合并两源；合并后做一次重复 kindId 检查 |
| `app/catalog/assets.py` | 60 条上下限**只校验 builtin**；新增 `validate_library_entries(entries, library_root)` 复用现有 GLB 检查但放开 license 要求 |
| `app/main.py:156` | 在 `/assets` 挂载后新增 `app.mount("/assets/library", StaticFiles(directory=library_models_parent))` |
| `apps/web/vite.config.ts:13` | 代理白名单加 `/assets/library` |
| `apps/web/nginx.conf` | 新增 `location /assets/library/` 窄代理（**不能**写 `location /assets/`） |
| `scripts/check_web_assets_proxy.mjs` | 要求列表加 `/assets/library/`，并保持 `/assets/` 宽代理的禁止断言 |

**验收**：`npm run check:assets` 通过；空库启动后 `GET /api/catalog` 仍返回 60 条；`POST /api/catalog/reload` 跑通。

**测试**：`tests/test_catalog_library.py` — 空 manifest / 有 manifest / manifest 与文件不一致 / 两源 kindId 冲突。

---

### P1 — 生成器内核 + T1 缩放（2–3 人日）

纯函数层，不接 agent。

| 文件 | 内容 |
|---|---|
| `app/modelgen/mesh.py`（新） | `Mesh` 数据结构（positions/normals/indices）；`box()`、`box_with_inset()`；合并多个 box 为一个 mesh |
| `app/modelgen/glb_builder.py`（新） | Mesh → glTF JSON + bin buffer → 复用 `catalog/glb.py:write_glb`。纯 stdlib |
| `app/modelgen/families/__init__.py`（新） | 族注册表：`family → (params_model, build(params) -> Mesh, front_axis_z_ok)` |
| `app/modelgen/families/scale.py`（新） | 读源 GLB（`read_glb`）→ 对 POSITION 应用缩放 → 重算 accessor min/max → 写回。约 60 行 |
| `app/modelgen/thumbnails.py`（新） | Pillow 正交投影线框 → 256×256 PNG（满足 `THUMBNAIL_SIZE` 校验） |
| `app/modelgen/compile.py`（新） | `spec → specHash → temp GLB → glb_stats 自检 → thumbnail`；自检失败抛 `SceneError("internal_error")` |
| `app/modelgen/spec.py`（新） | `SpecBase` + `spec_hash()`（规范化 JSON 的 sha256，取前 8 位）；kindId 生成 `lib_{family}_{hash8}` |

**自检规则（硬性）**：
- `stats.size` 与 spec 声明尺寸误差 ≤ 3mm（`SIZE_TOLERANCE`）
- `abs(stats.minimum[1]) <= 3mm`（bottom-center）
- 三角形数 ≤ 15000、字节 ≤ 1MB（对齐 `assets.py` 常量）
- x/z 包围盒中心 ≈ 0

**验收**：同一 spec 两次编译字节完全一致；`glb_stats` 能读回；缩略图 256×256。

**测试**：`tests/test_modelgen.py` — 确定性、AABB 匹配、超限拒绝、scale family 对每个内置资产的 AABB 线性缩放。

---

### P2 — 参数化族（3–4 人日）

按族增量交付，每族独立可测。建议顺序：

| 族 | 参数 | 复杂度 |
|---|---|---|
| `bookshelf` | width/depth/height, shelves, glass_doors, door_count, material | 低（正交箱体 + 层板） |
| `wardrobe` | width/depth/height, door_count, door_type(sliding/hinged), material | 低 |
| `desk_l` | width_a/width_b/depth/height, keyboard_tray, drawer_count | 中（L 形两箱拼接） |
| `counter_run` | length/depth/height, segments[], sinks, cooktop_slots, material | 中（沿墙分段） |
| `tv_stand` | width/depth/height, drawers, open_shelf | 低 |

每族产出一个 `CatalogEntry`：
```json
{
  "kindId": "lib_bookshelf_a1b2c3d4",
  "name": "通顶书柜 2.4×0.35×2.6",
  "category": "storage",
  "tags": ["bookshelf", "书柜", "custom", "通顶"],
  "defaultVariantId": "default",
  "variants": [{"id":"default","sizeM":[2.4,0.35,2.6],
                "modelPath":"/assets/library/models/a1b2c3d4.glb",
                "thumbnailPath":"/assets/library/thumbnails/a1b2c3d4.png"}],
  "frontAxis": "+Z", "pivot": "bottom-center",
  "clearance": {...}, "allowedRotations": [0,90,180,270],
  "rules": {"mustAgainstWall": true, "roomTypes": ["study","bedroom"]},
  "license": {"name":"generated:parametric","url":"internal://modelgen/bookshelf"},
  "source": "library"
}
```

**注意 `category`**：`CatalogCategory` 是枚举（`contracts/enums.py`），需要确认现有枚举里是否有 storage/cabinet 类目；没有则选最接近的（多数是 `other` 或 `storage`），或新增枚举值（这会触发契约再生成，需同步前端）。

**测试**：每族一组参数 → 尺寸断言 + 三角形上限；荒谬参数（width 5m、height 0.2m、shelves 0）必须被 Pydantic 参数范围拒掉。

---

### P3 — 工具接入（2–3 人日）

这是 agent 第一次接触到生成器。

#### 3.1 工具契约（`app/agent/contracts.py`）

```python
class ModelItemInput(CamelModel):
    spec: FamilySpec          # 判别联合，discriminator="family"
    name: str | None = None   # 不给则按 spec 自动命名
    room_id: str = Field(min_length=1)
    relation: Relation
    facing: FacingInput | None = None
    clearance: Clearance | None = None

FamilySpec = Annotated[
    BookshelfSpec | WardrobeSpec | DeskLSpec | CounterRunSpec | TvStandSpec | ScaleSpec,
    Field(discriminator="family"),
]
```

用判别联合而非 `params: dict`：LLM 能从 JSON Schema 看到每族的合法参数与数值范围，L0 校验天然生效，无需二次校验。

#### 3.2 注册（`app/agent/registry.py:36`）

```python
ToolSpec(
    name="model_item",
    description=(
        "Generate a custom parametric furniture item in a fixed family "
        "(bookshelf/wardrobe/desk_l/counter_run/tv_stand) with exact dimensions, "
        "then propose placing it. This is the only way to get sizes outside the "
        "built-in catalog. The proposal is shown as a ghost until confirmed. "
        "Generated assets are saved to the scene library and reusable later."
    ),
    input_model=ModelItemInput,
    kind="write",
)
```
并新增 `scale_existing`（或用 `ScaleSpec.family="scale"` 走同一工具，倾向后者：一个工具语义更简单）。相应地 `scale` 需要 `source_kind_id` 字段。

#### 3.3 执行（`app/agent/tools.py`）

```python
async def _model_item(self, req: ModelItemInput) -> ToolExecution:
    if not self._settings.asset_generation_enabled:
        return _refused("model_item", make_refusal(
            "internal_error", "Asset generation is disabled on this server.",
            suggest="Use a catalog item instead."))
    spec = req.spec
    try:
        outcome = await self._modelgen.ensure(spec)   # 编译/缓存 + 缩略图 + 写 tmp
    except SceneError as e:
        return _refused("model_item", make_refusal(e.code, e.message, suggest=e.suggest))

    # 1) 先注册 + 先推送 catalog
    self._catalog.register_runtime(outcome.entry)
    await self._on_catalog_updated(outcome.entry)     # 新增回调，注入 gateway
    self._registry.update_catalog(self._catalog.by_kind)
    self._manager_update_catalog(self._catalog.by_kind)   # 见下

    # 2) 再走现有语义摆放 + proposal
    return await self._write(
        "model_item", req,
        lambda document, _pending: _from_placement(
            place_add(document, _as_add_item(req, outcome.entry), 
                      catalog=self._catalog.by_kind, rules=self._rules, strict=True)))
```

**为什么必须在 propose 之前注册**：`place_add` 会 `_catalog_entry(catalog, kind_id)` 查尺寸/clearance/rule，未注册直接 `unknown_kind`。

**`manager_update_catalog` 的处理**：`TurnExecutor` 目前没有 manager 引用。两个选法：
- (a) 给 `ToolExecutor` 加 `on_catalog_updated: Callable[[CatalogEntry], Awaitable[None]]`，由 `TurnManager._executor` 注入一个同时做「gateway 推送 + `self.update_catalog`」的闭包。**推荐**，改动最小且不需要新耦合。
- (b) 让 ToolExecutor 直接持有 manager，会形成循环依赖。

选 (a)。同时 `ToolExecutor` 构造需要新增 `modelgen: ModelGenService`。

#### 3.4 协议新增 `catalog.updated`

`app/contracts/ws.py` 增加 payload 模型；`app/sync/gateway.py` 增加 `emit_catalog(entry)`，走 state 通道、`buffer=True`（这样断线重连 replay 也能拿到）。

前端 `apps/web/src/store/sceneStore.ts` 的 `handleWsEnvelope` 增加分支：
```ts
if (envelope.type === 'catalog.updated') {
  const entry = (envelope.payload as {entry?: CatalogEntry})?.entry
  if (!entry) return
  set((s) => ({ catalog: [...s.catalog.filter(e => e.kindId !== entry.kindId), entry] }))
  return
}
```

**验收**：模型请求「2.4m 通顶书柜」→ 前端 catalog 出现新条目 → ghost 用新 GLB 渲染 → 确认后场景里是自定义尺寸。

**测试**：`tests/test_api_agent_model_item.py` — ScriptedAdapter 脚本：`model_item` → `proposal.created` → 确认；断言 catalog.updated 在 proposal.created 之前；断言 kindId 在 `/api/catalog` 可见。

---

### P4 — `revise_asset` 与 pending ops 替换（2–3 人日，可延后）

**前置**：`SceneRegistry.replace_pending_ops(scene_id, proposal_id, builder)`。

守卫（缺一不可）：
- proposal 状态必须 `pending`
- proposal 的 `turn_id` 必须等于调用方 turn
- 新 ops 重新过 propose 阶段那套检查（重复 id、外部 turn 引用）
- 保持 `id` / `base_version` / `depends_on` 不变（否则依赖链断裂）
- 成功后 `emit proposal.updated`，前端 ghost 立即变化

`ReviseAssetInput`：
```python
class ReviseAssetInput(CamelModel):
    proposal_id: str
    spec: FamilySpec        # 完整新 spec，不是 patch
```
选完整替换而非 patch：LLM 重发整个对象比构造合法 patch 更可靠，且校验路径唯一。

流程：`proposal.ops[0].item.kind_id` → manifest 查出原 spec → 用新 spec 重编译 → 新 kindId → `replace_pending_ops`（`item.add` 的 kind+transform）+ `catalog.updated` + 旧 tmp 资产标记可回收。

**注意**：新 kindId 意味着尺寸可能变，原 transform 仍有效但可能需要重新过 L2；保守做法是把新 ops 重新交给 `place_add` 生成，而不是原地替换 kind。

---

### P5 — T0 组合预设（1–2 人日，与 P4 正交）

不涉及资产生成，只涉及"一次 proposal 多个 item.add"。

- `fixtures/group-presets.json`：`{id, name, category, members: [{kindId, relation, facing, clearance}]}`
- agent 工具 `place_group`：解析 preset → 逐成员 `place_add` → 合并成一个 ops 列表 → 一个 proposal
- 已有 `auto_layout` 就是这种形态，可复用 `_write` 的多 op 路径

**测试**：L 型沙发预设 → 一个 proposal、N 个 item.add、确认后一次成版。

---

### P6 — 前端资产库（1–2 人日，可延后）

- 目录面板加"自定义资产"分组（`entry.source === 'library'` 时打角标）
- `search_catalog` 返回 `source` 字段，前端可过滤
- 资产管理：删除未引用资产（`DELETE /api/library/assets/{kindId}`，服务端先扫描活场景引用）

---

### P7 — 收尾（1–2 人日）

| 事项 | 内容 |
|---|---|
| eval | 新增 5–8 个 case：合法生成、荒谬参数拒绝、尺寸超限拒绝、scale 越界拒绝、同 spec 幂等、确认后重载场景 |
| 系统提示词 | `context.py:SYSTEM_PROMPT` 加一条规则：内置 catalog 满足时优先用内置，只有尺寸/形态无法满足时才 `model_item` |
| `_catalog_summary` | `context.py:102` 改为内置 / library 两组；library 只给"最近 10 个 + 总数"，其余靠 `search_catalog` 按需查（防止提示词被撑爆） |
| `search_catalog` | 增加 `source: Literal["any","builtin","library"]` 过滤；返回 `source` |
| PROTOCOL.md | 新增 M6 章节：`catalog.updated`、`/assets/library`、资产库语义 |
| ADR | `docs/adr/0011-runtime-asset-library.md`：记录 D1–D5 的取舍 |
| `docs/DEPENDENCIES.md` | 确认无新运行时依赖（Pillow 已在） |

---

## 4. 资产库布局

```
data/assets/
  manifest.json                  # {schemaVersion, assets: [CatalogEntry & {generator, createdAt, promoted}]}
  models/<hash8>.glb             # 已提升，永久保留
  thumbnails/<hash8>.png         # 256×256
  tmp/<hash8>.glb                # proposal 期，TTL 回收
  tmp/<hash8>.png
```

manifest 条目追加字段（不污染 `CatalogEntry` 契约本体，作为 wrapper）：
```json
{
  "entry": { ...CatalogEntry... },
  "generator": {"family": "bookshelf", "params": {...}, "specHash": "a1b2c3d4"},
  "createdAt": "2026-11-17T10:00:00Z",
  "promoted": false
}
```

`source` 字段加在 `CatalogEntry` 上（可选，默认 `"builtin"`），因为前端和 `search_catalog` 都要用 → **需要跑 `scripts/export_contracts.py` 并提交生成的 TS**。

---

## 5. 生命周期

| 阶段 | 触发 | 动作 |
|---|---|---|
| 编译 | `model_item` | 写 `tmp/`，entry 进内存 catalog，`catalog.updated` 推送 |
| 确认 | `proposal.confirm` | 把 `tmp/` 文件移到 `models/`+`thumbnails/`，manifest 标 `promoted: true` |
| 拒绝 / 过期 / 回合取消 | 现有过期链路 | 资产留在 `tmp/`，由 GC 回收 |
| GC | 启动 + 每小时 | 删 `tmp/` 中超过 `asset_tmp_ttl_hours` 且 manifest 未 promoted 的文件 |
| 永久保留 | — | promoted 资产**不自动删除**；删除必须走显式接管 + 场景引用扫描 |

**关键约束**：promoted 资产一旦被场景引用就不能删——`validate_document(document, catalog=...)` 会在加载场景时失败。因为场景只存 `kindId`，删资产 = 删场景。所以：
- GC 只碰 `tmp/`
- 显式删除接口必须先扫描 `repository.list_scenes()` 的 kindId 引用

---

## 6. 风险与回滚

| 风险 | 影响 | 缓解 |
|---|---|---|
| 资产库条目损坏 → 场景加载失败 | 高 | 启动时校验库；损坏条目降级为不可用 + 告警日志，不阻塞启动；GC 只碰 tmp |
| 系统提示词被 library 资产撑爆 | 中 | `_catalog_summary` 分组限量（P7） |
| `catalog.update` 推送顺序错误 → ghost 渲染不出 | 中 | 严格「先 catalog 后 proposal」；测试覆盖 |
| nginx 宽代理误配 → 生产白屏 | 高 | `check_web_assets_proxy.mjs` 守卫已有先例，本期扩展它 |
| 生成器 bug 产出非法 GLB | 中 | `glb_stats` 自检 + 本地 `write_glb` 往返测试；tri/bytes 上限 |
| 每族参数范围定得太窄，模型反复失败 | 中 | 参数范围写进 JSON Schema description；refusal 带 `suggest` 给具体可用区间 |
| 缩放 family 让家具比例失真 | 低 | `ScaleSpec` 限制各轴 0.5–1.5，且只作用于衣柜/书柜/桌类（白名单） |

**回滚**：`asset_generation_enabled=false` 后工具返回 refusal，agent 退回内置 catalog；已 promoted 的资产继续可用（只是不能再生成新的）。数据层面无迁移，回滚零成本。

---

## 7. 工作量

| 阶段 | 人日 | 可并行 |
|---|---|---|
| P0 基础设施 | 1.5–2 | — |
| P1 生成器内核 | 2–3 | 与 P0 可部分并行 |
| P2 参数化族（5 族） | 3–4 | 每族独立 |
| P3 工具接入 + 协议事件 | 2–3 | 依赖 P0–P2 |
| P4 revise + ops 替换 | 2–3 | 依赖 P3 |
| P5 T0 组合 | 1–2 | 独立 |
| P6 前端库 UI | 1–2 | 依赖 P3 |
| P7 收尾（eval/文档/ADR） | 1–2 | 依赖全部 |
| **合计** | **14–21** | |

最小可用切片（先跑通一个族）≈ **7–9 人日**：P0 + P1 + bookshelf + P3。

---

## 8. 建议的第一刀

先做 **P0 + P1 + `bookshelf` 单族 + P3**，端到端跑通"2.4m 通顶书柜"这一条链路。理由：

1. 验证四条最不确定的假设：双源 catalog 不影响现有 60 条校验、nginx 白名单能扩、`catalog.updated` 顺序正确、生成 GLB 能被前端正常渲染。
2. 这一刀交付了完整的新能力面，后面加族是纯复制（每族一个 params model + 一个 mesh 函数）。
3. 若 `catalog.updated` 这条推送链走不通，需要改协议——越早发现越好。

---

## 9. 实施状态（2026-11-17）

### 已完成

| 阶段 | 状态 | 落地内容 |
|---|---|---|
| P0 基础设施 | ✅ | `Settings.library_dir` / `asset_generation_enabled` / `asset_tmp_ttl_hours`；`app/catalog/library.py`（manifest、promote、gc_tmp）；`CatalogService` 双源合并 + `preview_merged`；`/assets/library` 挂载（在 `/assets` **之前**注册，否则被遮蔽）；Vite/nginx 窄代理 + 守卫脚本 |
| P1 生成器内核 | ✅ | `modelgen/{mesh,glb_builder,specs,families,scaling,thumbnails,compile,service}.py`，纯 stdlib 网格发射 + `write_glb` 复用；Pillow 正交投影缩略图；spec hash 内容寻址 |
| P2 参数化族 | ✅ | bookshelf / wardrobe / tv_stand / desk_l / counter_run 五族 + scale 退化族 |
| P3 工具接入 | ✅ | `model_item` 工具（第 11 个）、`ModelItemInput` 判别联合契约、catalog 注册 → 发布 → 摆放顺序、`catalog.updated` 协议事件、确认后晋升（`gateway.set_confirm_hook`） |
| P3 前端 | ✅ | store 处理 `catalog.updated`；`refreshTurnState` 顺带重拉 catalog 补齐断线期间漏掉的条目 |
| P7 收尾（部分） | ✅ | 提示词规则 12–13、`_catalog_summary` 分组限量、`search_catalog.source` 过滤、`PROTOCOL.md` M6 章节、`ADR-0011` |

### 关键实现决策（相对计划的变化）

1. **`revise_asset` 延后（P4 未做）**：需要 `SceneRegistry.replace_pending_ops`，会改动 proposal 不可变性假设，单独一阶段评估更稳。
2. **`catalog.updated` 改为按场景显式发送**：计划里只发给「已连接场景」，实现时发现无连接时事件不入 ring buffer，导致断线重连也无法重放；改为「归属场景始终发送并缓冲 + 其他在线场景也发」。
3. **把手/水槽几何内缩 `HANDLE_DEPTH`**：初版把手突出使 AABB 超出声明深度 2 cm，触发自检失败；改为柜门内缩、把手与声明前平面齐平。
4. **`scale` 族只缩放场景根节点**：GLB 普遍带 `__catalog_normalize` 父节点，逐节点缩放会按层级重复应用（实测 x 因子被平方）。

### 未做

| 项 | 原因 |
|---|---|
| P4 `revise_asset`（spec 迭代） | 需先设计 pending proposal 的 ops 替换语义 |
| P5 T0 组合预设 | 与资产生成正交，可独立排期 |
| P6 前端资产库 UI（分组/角标/删除） | 后端已具备 `source` 字段与删除所需的引用检查基础，UI 可延后 |
| T3 AI 生成 | 明确不做 |

---

## 10. 子 agent 审核后的修复（2026-11-17）

审核结论为「需要返工」。已修复项：

| 级别 | 问题 | 修复 |
|---|---|---|
| **阻塞** | 手动 `POST /items` 或 WS `commit` 可引用未晋升的 `lib_*` 资产；TTL GC 之后删文件+删记录，场景永久 `unknown_kind` | `gc_tmp(protected=...)` 接收「被任何持久化场景引用的 kind 集合」，启动与每小时 GC 都从 `repository.referenced_kind_ids()` 取值；被引用的临时资产永不回收 |
| 高 | 一个 proposal 含多个生成资产时只推送第一个 `catalog.updated` | `on_proposal_confirmed` 返回 `list[CatalogEntry]`，逐个发布 |
| 高 | LLM schema 未把 `family` 标为必填，模型按 schema 省略时运行时被拒 | 各 spec 的 `family` 去掉默认值，成为必填；导出 schema 六个分支均 `required: [family]` |
| 中 | 确认时 `scene.patch` 先于晋升广播，前端短暂用失效的 tmp URL | 晋升（含 `catalog.updated`）提前到 `broadcast_patch` 之前 |
| 中 | 晋升非崩溃原子：移动成功但 manifest 未写会丢记录或留下 tmp URL | `promote()` 在同一次 `_save_locked(strict=True)` 内完成移动+URL 改写；`_load` 依据磁盘实际状态修复 promoted/tmp 不一致 |
| 中 | 无周期 GC；被拒绝的 `model_item` 永久留在 catalog 与提示词里 | 新增每小时 GC 任务；`_model_item` 在摆放失败且无 pending proposal 引用时 `release()` 该条目 |
| 中 | 生成器自检失败被报成用户参数错误 | `CompileError` → `internal_error`，未知源 → `unknown_kind`，不可行参数 → `invalid_input` |
| 中 | 窄宽度 + 4 扇玻璃门会产出反向盒子 | 玻璃门框宽度按门宽/高度收敛（`min(0.05, w/4, h/4)`） |
| 中 | `scale` 无白名单且丢失源资产的类别/规则/净空 | 限定 `storage/table/bed`，并继承源 entry 的 category、rules、clearance、rotations |
| 中 | 8 位 hash + 仅按 hash 命名文件存在碰撞风险 | hash 扩到 12 位；缓存命中时比对 canonical spec，不一致按碰撞报错 |
| 中 | `gc_tmp` 在锁外读取 `_records` | 移入锁内 |
| 低 | `manifest.json` 被静态目录公开 | 只挂载 `models/thumbnails/tmp` 三个子目录，manifest 返回 404 |
| 低 | `sourceKindId` 无长度上限 | `max_length=128` |
| 低 | 前端 `refreshTurnState` 用 REST 结果覆盖 `catalog.updated` | 改为按 kindId 合并 |
| 低 | 橱柜踢脚线朝前凸出 | 改为向前内缩 |
| 低 | 资产库摘要按 hash 排序 | 改为最新优先 |
| 低 | 配置可把库指到仓库 `assets/` | 启动时断言库目录不在 `find_assets_root()` 内；`asset_tmp_ttl_hours` 限制 `ge=1` |

新增回归测试 9 项（GC 保护、多条目晋升、family 必填、跨库字节一致、崩溃修复两条、缩放白名单与元数据继承、manifest 不可访问）。

**最终状态**：后端 214 项测试通过、ruff/mypy 干净；前端 typecheck 通过、66 项测试通过；契约已重新生成。