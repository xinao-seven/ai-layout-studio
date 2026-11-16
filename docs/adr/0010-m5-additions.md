# ADR-0010: M5 加餐的范围与折衷

日期：2026-11-16 · 状态：已接受 · 关联：PRD FR-18/19/20/21/22

## 背景

M0–M4 交付了核心闭环（G1–G3、30 例评测）。M5 是 PRD 的可选加餐：成果导出、
第一人称漫游、极简 MCP server、户型图照片解析、日照环境层。单人实现需要在
一天内完成全部五项，因此每项都选择"最小可用、契约一致、不破坏既有架构"的
实现路径。

## 决策

### 1. 导出/导入复用 M1 已有实现（FR-18）

`GET /scenes/{id}/export` 与 `POST /scenes/import`（ID 重映射、schemaVersion、
`import_invalid` envelope）在 M1 已按契约建成并有测试；M5 只补前端入口
（TopBar 导出 JSON/PNG、首页导入）与"导出 → 导入 → 再导出"往返测试。
不新增第二个导出格式。

### 2. scene.update 只开放场景级渲染语义字段（FR-22）

日照需要持久化经纬度。新增第 10 个 PatchOp `scene.update`，可变字段仅
`north` 与 `environment(latitude/longitude)`，走与其他 op 完全相同的
resolve/inverse/undo 链路。不做 `name`/`defaults` 修改——它们没有产品诉求，
且扩大协议面。时间轴（0–24h）是纯前端状态，不入库。

### 3. 日照时间轴使用"太阳时"（FR-22）

无时区配置时，滑块值按**当地平均太阳时**解释（UTC 时刻 = 滑块 − 经度/15），
滑块 12:00 恒为太阳正午；日期取当天真实日期以保留季节性赤纬。太美方差
（时区/夏令时/政策）全部绕开，符合"锚定 north + 经纬度"的验收语义。
太阳方位以场景 `north` 向量为基准旋转，罗盘/求解器"床尾朝南"与阴影同源。

### 4. 第一人称与编辑互斥（FR-20）

漫游是独立 viewMode（`3d | top | walk`），进入后禁用 OrbitControls、拖拽、
点选与手绘；碰撞是**客户端预检**：墙段（门洞打洞，含共享墙共线段）+ 家具
OBB 膨胀圆，轴向分离滑动，子步长 0.2 m 防隧穿。服务端不参与漫游——漫游不
改变场景，与"服务端真相源"无冲突。

### 5. MCP 写工具直接落库，不走 proposal（FR-21）

`tools/list` 的名称/描述/参数 schema 与 chat 完全同源（`ToolSpec` 注册表），
对等性由测试锁定。写工具（add/move/remove_item）复用 `agent/placement` 的
L1–L3 规则，但**直接 commit**（`actor=agent`）而不是创建 proposal——MCP 客户端
没有确认 UI，proposal 会永久挂起。payload 用 `needsConfirmation: false` +
`appliedDirectly: true` 显式标记这一差异。协议只实现 MCP stdio 的最小子集
（initialize / ping / tools/list / tools/call），不引入 mcp SDK 依赖；
`tools/call` 参数额外接受保留键 `sceneId` 做路由（L0 校验前剥离）。

已知语义差异：写工具的参考系是 overlay（含 chat 未确认提案的幽灵家具），
commit 落在 confirmed 文档上并在 commit 侧重新全量校验——若 MCP 的放置与
随后确认的 chat 提案冲突，确认会以校验失败拒绝并保持 pending 至 TTL 过期，
不会产生脏数据。

MCP 客户端（如 Claude Desktop）示例配置：

```json
{
  "mcpServers": {
    "ai-layout": {
      "command": "uv",
      "args": ["run", "--directory", "<仓库路径>/apps/server", "python", "-m", "app.mcp"]
    }
  }
}
```

### 6. 户型图照片只产出"未保存草案"（FR-19）

VLM 返回米制多边形；服务端 0.1 m 吸附、绕一圈归一、丢弃过小/自相交房间并
转成 warnings，返回**未入库**的 draft 文档。用户在弹窗里改名称/类型后经
`/scenes/import` 创建（二次校验 + ID 重映射），多边形在编辑器里手动修正。
这样 VLM 的任何幻觉都不会直接污染真相源。

## 后果

- 协议新增一个 op 与一个 REST 端点；契约已重新生成。
- MCP 与 chat 的工具"契约一致"由测试保证，"执行语义"（proposal vs 直写）
  有意不同并已在 README/PROTOCOL 标注。
- 漫游碰撞是客户端近似（圆 vs OBB 膨胀），与求解器/服务端 OBB SAT 的
  权威性无关。
- 户型图解析质量依赖 VLM；未配置 VLM 时端点返回 `vlm_unavailable`，
  其余功能不受影响。
