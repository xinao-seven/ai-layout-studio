# Protocol (M1 freeze)

Status: **frozen at M1 (2026-11-16); M2, M3 and M4 are additive (2026-11-16)**.
M3 implements the agent loop against this contract without redefining envelopes,
patch ops, versioning or resume. M2 adds `SceneWarning` to REST/ack payloads,
`POST /api/catalog/reload`, and static `/assets/*` delivery. M3 adds turn REST
endpoints, turn-channel events, `turn.subscribe`/`turn.cancel`/`ask_user.answer`
host messages and `proposal.updated`. M4 adds `auto_layout`/`verify_layout`,
`capture.request`/`capture.result` and an optional legend in the request.
Post-M4 adds the `add_room` write tool (the only tool accepting raw polygon
coordinates; the server grid-snaps, normalizes winding and validates before
proposing). M6 adds the `model_item` write tool, the runtime asset library and
the `catalog.updated` state event. No existing field changes meaning.

The wire contract is generated from Pydantic models
(`apps/server/app/contracts/`): see `packages/contracts/generated/ws.schema.json`
and `apps/web/src/contracts/generated/ws.d.ts`.

## Transport

- One WebSocket per open scene: `GET /ws?sceneId=<sceneId>`.
- JSON text frames only; a frame larger than 2 MB is rejected (close 1009).
- Three logical channels share the socket:
  - `state` — snapshots and patches (server → client)
  - `turn` — agent event stream (M3; envelope reserved now)
  - `host` — client → server callbacks and acknowledgements
- Server sequence numbers are **per scene, per channel**, monotonically
  increasing. `scene.snapshot` also consumes a state sequence.
- Every server frame is a `WsEnvelope`:

```json
{
  "v": 1,
  "ch": "state",
  "type": "scene.patch",
  "seq": 2,
  "ts": 1763000000000,
  "turnId": null,
  "requestId": null,
  "payload": { "patch": { "sceneId": "scene_...", "fromVersion": 1, "toVersion": 2 } }
}
```

## State events

| type | payload | notes |
|---|---|---|
| `scene.snapshot` | `{ "scene": SceneDocument }` | sent on connect and when replay history is unavailable |
| `scene.patch` | `{ "patch": ScenePatch }` | one version step; clients apply ops or refetch |
| `proposal.updated` | `{ "proposal": Proposal }` | reserved for M3 ghost rendering |
| `catalog.updated` | `{ "entry": CatalogEntry }` | M6; a generated asset joined the catalog |

A `ScenePatch` carries `fromVersion`, `toVersion`, semantic `ops`, `actor`,
optional `turnId`/`proposalId` and `clientMutationId`. Ops:

| op | fields | inverse |
|---|---|---|
| `item.add` | `item` | `item.remove` with `before` |
| `item.remove` | `itemId`, `before` | `item.add` |
| `item.update` | `itemId`, typed partial `before`/`after` fields (no `id`) | swap `before`/`after` |
| `room.add` | `room` | `room.remove` |
| `room.remove` | `roomId`, `before` | `room.add` |
| `room.update` | `roomId`, `before`/`after` | swap |
| `opening.add` | `opening` | `opening.remove` |
| `opening.remove` | `openingId`, `before` | `opening.add` |
| `opening.update` | `openingId`, `before`/`after` | swap |
| `scene.update` | typed partial `before`/`after` (`north`, `environment`) | swap |

Update payloads are typed partial models (`ItemUpdateFields` etc.) with
`extra="forbid"`; identity fields such as `id` are not mutable. The server
resolves client updates into full field snapshots (excluding identity) and
stores inverses in `scene_versions.inverse_json` (latest 100 versions retained).

## Host messages

Client frames use the discriminated `HostMessage` union (`type` discriminator),
`ch: "host"`, `seq: 0`, `ts` in Unix milliseconds. A `requestId` is echoed in
`host.ack`/`host.error`.

| type | payload | M1 behaviour |
|---|---|---|
| `commit` | `{ baseVersion, ops, clientMutationId? }` | validate, commit, broadcast patch, ack |
| `undo` | `{ expectedVersion }` | apply stored inverse as version + 1 |
| `redo` | `{ expectedVersion }` | re-apply the undone forward ops as version + 1 |
| `resume` | `{ lastSeqs: { state: number } }` | replay state patches after `lastSeqs.state`; snapshot on gap |
| `ping` | `{}` | replies `host.pong` with the client timestamp |
| `proposal.confirm` | `{ proposalId }` | confirm in dependency order (M3 uses it) |
| `proposal.reject` | `{ proposalId, reason? }` | reject and expire dependants |
| `turn.subscribe` / `turn.cancel` / `ask_user.answer` | `{ turnId, lastSeq? }` / `{ turnId }` / `{ turnId, answers }` | replay, cancel, resume waiting turn |
| `capture.result` | `{ turnId, pngBase64, legend }` | host reply to a `capture.request`; must echo `requestId` |

Ack/error frames: `host.ack` with `{ type, version, warnings? }`, `host.error`
with `{ error: { code, message, suggest?, details? } }`.

`warnings` is an optional array of `SceneWarning` (`{ code, message, details? }`).
M2 emits `opening_reanchored` / `opening_removed` when a `room.update` changes a
polygon and existing openings are re-anchored (see below).

## M4 capture and VLM loop

Server -> host:

| type | payload | behaviour |
|---|---|---|
| `capture.request` | `{ width, height, format: "png", maxBytes }` | sent on the host channel with a fresh `requestId` and the turn id |

The host renders an orthographic top view (no ceiling, walls cut low), labels
furniture `A1/A2…` and doors/windows `D1/W1…`, then replies with
`capture.result` echoing `requestId`. The server validates the PNG, resizes the
long edge to 1536 px, keeps it at or below 1 MB and stores it under
`data/screenshots/<sceneId>/<turnId>-<purpose>.png` (7-day retention). Review
captures are processed for the VLM but not persisted; before/after turn
captures are stored. No connected host or a timeout yields
`capture_unavailable`; the turn continues.

VLM output is restricted to `{ verdict, issues[] }` with
`type ∈ blocks_door | walkway_narrow | group_misaligned | circulation_blocked |
window_blocked` and label ids. Coordinates are ignored. The server maps labels,
drops unknown ids, rechecks each issue geometrically (circulation via grid
pathfinding) and locally re-solves confirmed issues into a normal ghost
proposal. JSON failures are retried once; after that the visual step is skipped
without blocking the user.

## M2 editing endpoints and validation

| endpoint | behaviour |
|---|---|
| `GET /api/catalog` | reads `fixtures/catalog.json` (30–60 entries) |
| `POST /api/catalog/reload` | reloads the JSON, validates every live scene against it, then refreshes all validators |
| `GET /assets/models/*.glb`, `GET /assets/thumbnails/*.png` | normalized CC0 assets served by the API server |
| `POST /api/scenes/{id}/items` | manual add with server-resolved variant + free position |
| `PATCH/DELETE /api/scenes/{id}/items/{itemId}` | manual move/rotate/delete |

`SceneResult` (all item/commit/undo/redo responses) is
`{ scene, patch, warnings? }`.

## Uniform item scale

`Transform.scale` (float, default 1.0) resizes an item uniformly around its
bottom-centre pivot. It is a normal transform field, so `item.update` with a
partial `{ transform: { scale } }` is enough; undo/redo, versioning and the
client patch merge need no special cases.

- **Server authority**: the effective footprint is
  `catalogVariant.sizeM × scale`. L2 collision, door/window clearance, walkway
  checks, `verify_layout`, the VLM re-check and the solver's existing-obstacle
  set all use that effective size, so a resized item collides at the size the
  user sees.
- **Bounds**: `ITEM_SCALE_MIN` / `ITEM_SCALE_MAX` in
  `packages/contracts/rules.json` (currently 0.4–2.0). Values outside the range
  are refused with `unrealistic` and `details.ruleId = "item_scale_bounds"`.
- **Ceiling**: a resized item whose effective height exceeds the room's
  `wallHeight` (or `WALL_HEIGHT`) is refused with
  `details.ruleId = "item_exceeds_ceiling"`. Legacy items at scale 1.0 are not
  re-litigated.
- **Client**: dragging the corner grip or using the sidebar stepper snaps to
  `ITEM_SCALE_STEP` and commits exactly one `item.update`, so one resize is one
  undo step. The client precheck mirrors the server rules for live feedback.

L2 placement review is **delta-based**: `item.add` and `item.update` check the
changed item's OBB corners against the room inset and its OBB against every
other item plus door clearance
(`max(door.width, DOOR_APPROACH_WIDTH) × 2 × DOOR_CLEAR_DEPTH`). Catalog
`rules.allowOverlap` items (rugs, table lamps, small plants) are exempt.
`opening.add` / `opening.update` and re-anchored doors check all existing
furniture. A stale `constraint.roomId` never exempts an item: room membership is
resolved from its position.
Legacy fixture furniture is not re-litigated on every edit; the client still
runs the same OBB/boundary/door precheck for live red feedback.

## Room polygon edits and opening re-anchoring

`room.update` accepts a mutable typed `after` payload. When `polygon` changes,
the server keeps each opening's world position, projects it onto the nearest new
edge, and emits:

- `opening.update` for openings whose edge/offset changed;
- `opening.remove` for openings farther than 0.5 m or that no longer fit;
- `SceneWarning` entries with `code: opening_reanchored` or `opening_removed`.

Opening constraints on removed openings are cleared in the same commit. A room
polygon edit also re-validates every existing item in the old/new footprint
against the new wall inset, so shrinking a wall cannot leave furniture crossing
it. Item position updates synchronise `constraint.roomId` from the containing
room. The patch is invertible and the client applies the extra ops normally.

## Versions and undo

- The confirmed document is the server's single source of truth.
- Every commit produces `version + 1`; undo is also a new version and never
  moves the version pointer backwards.
- `commit` rejects a stale `baseVersion` with `version_conflict` (409).
- `clientMutationId` is idempotent per scene runtime: repeating a commit
  returns the original patch without writing another version.
- A manual (`actor=user`) commit/undo/redo expires all pending proposals with
  `scene_changed`.
- The client mirrors Zustand/Zundo locally but treats server snapshots and
  patches as authoritative; when `fromVersion` does not match it refetches.

## Pending proposals and dependencies

- `Proposal.baseVersion` is the confirmed version when the turn started.
- `dependsOn` references earlier proposals that are still pending in the same
  turn; depending on an already-confirmed proposal is rejected. Confirmation
  must follow dependency order; duplicate confirmations return
  `alreadyConfirmed` semantics without writing a version.
- Pending proposals expire after a 15-minute TTL, on reject (with transitive
  dependants), when the expected version is no longer reachable, or on any
  external commit/undo (all pending).
- Confirm requires every dependency confirmed and
  `currentVersion == baseVersion + confirmedDependencyCount`.
- Overlay = confirmed document + pending proposals applied in topological order;
  reads and later writes validate against the overlay.
- Reject expires all transitive dependants; any external commit/undo expires
  all pending proposals.

## Resume

1. Client reconnects with exponential backoff (0.5 s → 5 s) and sends
   `resume` with the last state sequence it applied.
2. If the server's ring buffer (500 frames/scene) still contains the gap, it
   replays state frames with `seq > lastSeqs.state`.
3. Otherwise it sends a fresh `scene.snapshot`; a connection also receives a
   snapshot immediately on connect.
4. The client ignores state frames older than its current snapshot sequence, so
   replay and snapshot cannot regress state. The replay buffer is a redundancy
   path; the snapshot is authoritative on connect.

## Errors

REST and WebSocket errors share the envelope:

```json
{ "error": { "code": "version_conflict", "message": "...", "suggest": "..." } }
```

Codes are frozen in PRD §6.3. M1 emits `not_found`, `unknown_room`,
`unknown_kind`, `variant_missing`, `version_conflict`, `invalid_polygon`,
`room_too_small`, `out_of_room`, `opening_off_wall`, `opening_overlap`,
`invalid_input`, `proposal_expired`, `import_invalid` and `internal_error`.
M2 adds `collision`, `blocks_door` and `no_space` (manual add). Every refusal
carries a non-empty `suggest`.

## Single-worker invariant

State, sequence counters, undo/redo stacks and pending proposals live in one
process. The server refuses `WEB_CONCURRENCY`/`UVICORN_WORKERS` > 1; SQLite runs
with WAL, foreign keys and a 5 s busy timeout.

## M2 additions

- Catalog assets are normalized to metric, Y-up, bottom-center pivot and front
  `+Z`; `fixtures/catalog.json` `sizeM` is `[width, depth, height]` and is
  validated against the GLB AABB.
- Client catalog drag uses HTML5 drag/drop; the canvas raycasts the ground
  plane, snaps to 0.1 m and shows a green/red ghost before commit.
- One item drag/rotate/delete produces exactly one server version (one undo
  step); invalid drops never call the API.
- Freehand rooms are drawn on the floor plane with 0.1 m snapping, closed by
  clicking the first vertex, and stored as CCW wall-centerline polygons.
  Canvas vertex handles and the numeric vertex editor commit `room.update`;
  the server rejects self-intersections.
- Vertex dragging and room drawing snap to **every** vertex coordinate in the
  scene (any room), not just the immediate neighbours, within
  `ALIGN_THRESHOLD` = 0.25 m. That is how two rooms end up sharing an exact
  wall line instead of landing one grid cell apart; holding the free-drag
  modifier skips alignment and keeps the 0.1 m grid.
- Dragging a wall translates **both** of its vertices along the wall normal in
  one commit, so a rectangle stays rectangular instead of skewing. Edges in
  other rooms that are geometrically the same segment move with it, keeping a
  shared partition shared. The drag previews live, refuses self-intersection,
  sub-`MIN_ROOM_AREA` rooms and items left outside the new wall, and a plain
  click without movement still just selects the wall.
- `/assets/models/*` and `/assets/thumbnails/*` are proxied by Vite (dev) and
  nginx (Docker) to the API server. The Vite bundle itself lives in
  `/assets/index-*.js|css`, so the proxy is deliberately limited to those two
  catalog namespaces. Missing GLBs fall back to wireframe boxes in the editor.

## M3 additions

- **REST turns**: `GET/POST /api/scenes/{id}/turns`, `GET
  /api/scenes/{id}/turns/{turnId}`, `POST /api/turns/{id}/cancel`, and
  `GET /api/scenes/{id}/proposals?status=`. Turn creation returns a `Turn` with
  `queued` or `running`; only one turn runs and one queues per scene, further
  requests get `turn_in_progress` (409).
- **Turn events** (`ch: "turn"`, `seq` monotonic per scene/channel):
  `turn.started`, `assistant.delta`, `assistant.message`, `tool.started`,
  `tool.args`, `tool.result`, `proposal.created`, `turn.waiting_user`,
  `turn.done`, `turn.failed`, `turn.cancelled`, plus `turn.status` for
  reconnect/subscribe. Assistant text streams as deltas; tool calls are
  accumulated and de-duplicated by `toolCallId`.
- **Write tools** (`add_room`, `add_item`, `move_item`, `remove_item`,
  `update_item`, `auto_layout`)
  validate through L0–L3 and create a pending `Proposal`; the tool result
  returns `{ ok, proposalId, dependsOn, ops, preview, needsConfirmation }`.
  Read tools (`query_scene`, `search_catalog`) are not gated and include pending
  overlay items with `pending: true`. Refusals return
  `{ ok: false, refusals: Refusal[] }`, capped at five.
- **Proposal chain**: a new proposal depends on every proposal still pending in
  the **same turn**, so confirmation order follows creation/dependency order.
  References to items owned by another turn's pending proposal are refused
  (`proposal_expired`) because that turn's confirmation would expire the
  dependency. Each confirmation writes one version; duplicate confirmation
  returns `alreadyConfirmed: true` on `host.ack` without writing. Reject expires
  all transitive dependants; any external commit/undo, another turn's
  confirmation, TTL (15 min) or restart expires pending proposals. Expiry is
  broadcast as `proposal.updated` with the terminal status and reason.
- **Cancel/queue**: `turn.cancel` (WS) or `POST /api/turns/{id}/cancel` stops
  the model stream, expires that turn's pending proposals and leaves the scene
  untouched. Confirmed proposals are not rolled back; the user can undo. A
  scene runs one turn and queues at most one more (`turn_in_progress`).
- **Transcript persistence**: `turns.messages_json` is updated after each model
  iteration and on terminal states, so `turn.status` after reconnect includes
  the partial transcript; a fresh `turn.subscribe { lastSeq: 0 }` replays the
  whole buffered turn before the status.
- **Proposal reads**: `GET /api/scenes/{id}/proposals` first materialises
  TTL/stale expiry, persists `proposal_expired` and broadcasts
  `proposal.updated`; confirm/reject of an already-expired proposal returns
  `proposal_expired`.
- **waiting_user**: `ask_user` emits `turn.waiting_user` and suspends the turn;
  `ask_user.answer` resumes it. A 10-minute timeout fails the turn with
  `tool_timeout`.
- **Reconnect**: `resume.lastSeqs` may include `{ state, turn }`. State replay
  keeps the M1 snapshot fallback; turn replay sends buffered turn events after
  `lastSeqs.turn` **before** the live `turn.status`, so deltas/proposal events
  are not skipped. A `turn.status` payload also carries the current
  `waiting` question/options when the turn is `waiting_user`. Snapshots include
  `serverEpoch`; clients reset their turn cursor when it changes after a server
  restart. `turn.subscribe` uses the same replay-then-status order.
- **Manual edits**: REST item/commit/undo/redo mutations persist and broadcast
  expiry for all pending proposals, so live clients drop stale ghosts.
- **Restart**: unfinished turns are marked `failed` (including graceful
  shutdown) and pending proposals `expired`; clients recover from snapshot +
  turn status.
- **LLM adapter**: OpenAI-compatible streaming (`AsyncOpenAI`), 60 s timeout,
  30 s stall detection, one retry before any event is emitted, usage accounting.
  `ScriptedAdapter`/`app.testing.fake_llm` replay deterministic JSONL for
  tests; the fake service speaks real SSE so the OpenAI SDK path is exercised.

### M3 refusal codes

In addition to the M1/M2 codes, M3 emits `invalid_input` (L0/tool schema),
`turn_in_progress`, `turn_cancelled`, `budget_exceeded`, `llm_error`,
`tool_timeout`, `unrealistic` (L3, `details.ruleId`), `blocks_window`,
`walkway_too_narrow`, `item_too_large` and `scene_changed`. Every refusal
carries a non-empty `suggest`.

## M5 additions

- `POST /api/scenes/import_photo` parses a floor-plan photo (JPEG/PNG, ≤5MB)
  into an **unsaved** draft `SceneDocument` + `warnings[]` (FR-19). The VLM is
  prompted for meter-scaled CCW room polygons; the server snaps to the 0.1 m
  grid, drops rooms that would fail scene validation and reports them as
  warnings. The client previews the draft (editable names/types) and creates
  the scene through `POST /api/scenes/import`. Errors: `vlm_unavailable`,
  `photo_too_large`, `photo_invalid`, `photo_unsupported`, `parse_failed`.
- `scene.update` patch op mutates scene-level fields only (`north`,
  `environment.latitude/longitude`), with undo/redo inverses like every other
  op (FR-22). The client commits it through the normal commit endpoint.
- MCP server (`python -m app.mcp`, JSON-RPC 2.0 over stdio) exposes
  `query_scene`, `search_catalog`, `add_item`, `move_item`, `remove_item`
  (FR-21). `tools/list` schemas come from the same `ToolSpec` registry as the
  chat agent; `tools/call` arguments additionally accept a reserved `sceneId`
  routing key that is stripped before L0 validation. Write tools reuse the
  L1–L3 placement rules and commit **directly** (`actor=agent`) instead of
  creating a proposal — MCP has no confirmation UI; the payload carries
  `needsConfirmation: false` and `appliedDirectly: true`.

## M6 additions: parametric asset library

- **Write tool `model_item`**: generates custom furniture from a family spec
  (`bookshelf`, `wardrobe`, `tv_stand`, `desk_l`, `counter_run`, `scale`,
  `primitives`) and proposes placing it through the existing L0–L3 pipeline. The
  tool result adds an `asset` block (`kindId`, `name`, `family`, `sizeM`,
  `reusable`).
- **Composed primitives** (`family: "primitives"`): 1–24 axis-aligned boxes with
  `size` and centre `position` in item-local metres (`y = 0` is the floor). The
  spec must declare a display `name` and a `category`; at least one box must
  touch the floor and none may go below it. The server measures the overall
  footprint from the parts (never trusts a declared size), rejects compositions
  over 3.5 m on any axis or under 2 cm thick, and inherits the placement
  clearance of the declared category. This is the long-tail escape hatch for
  simple orthogonal objects (drying rack, shoe rack, plant stand) — collision
  still uses the solid AABB footprint, so gaps inside a composition do not let
  other items nest into it.
- **Asset storage**: generated GLBs are written under
  `<dataDir>/assets/{tmp,models}/{specHash}.glb` with 256×256 PNG thumbnails.
  `manifest.json` in that directory is the source of truth; there is no DB
  migration. The fixture catalog stays untouched (still 30–60 entries). The
  spec hash is 12 hex chars.
- **Serving**: only the `models`, `thumbnails` and `tmp` subdirectories are
  mounted (at `/assets/library/<subdir>`); `manifest.json` is not public. They
  are proxied by the same narrow namespaces as `/assets/models`. nginx must not
  proxy `/assets/` as a whole (it would shadow the Vite bundle).
- **Catalog merge**: `GET /api/catalog` returns fixtures plus library entries.
  Library entries carry `source: "library"` and kind ids `lib_<family>_<hash8>`;
  a built-in kind id always wins a collision.
- **`catalog.updated` ordering**: emitted on the state channel *before* the
  `proposal.created` that references the new kind, so a host processes the
  model URL before it has to render the ghost. The owning scene is always
  emitted (and buffered) even with no live connection; other connected scenes
  receive it too.
- **Promotion**: confirming a proposal that introduces a library asset moves
  its files from `tmp/` to `models/` and emits `catalog.updated` with the
  permanent URLs. Promotion happens **before** `scene.patch`/`proposal.updated`
  so hosts never render the item with a URL that has just moved; a proposal
  introducing several generated kinds emits one event per entry.
  Promoted assets are never garbage collected — a scene only stores `kindId`,
  so deleting one would break that scene on load. `tmp/` assets are reclaimed
  after `assetTmpTtlHours` (default 24 h) **unless** a persisted scene
  references their kind.
- **Determinism**: the spec hash is the asset identity (`kindId` suffix) and
  the cache key; the same spec always produces the same bytes, and a cache hit
  re-verifies the stored spec.
- **Rescaling scope**: `scale` only accepts sources whose category is
  `storage`, `table` or `bed`, and inherits the source's category, rules,
  clearance and rotation set.
- **Disabled servers**: `assetGenerationEnabled=false` makes `model_item`
  refuse with `invalid_input`; `search_catalog` accepts
  `source=any|builtin|library`.

## M7 additions

- **Capabilities**: `GET /api/agent/capabilities` returns
  `{ model, reasoning, reasoningStyle, thinkingLevels, defaultThinkingLevel,
  granular }`. `reasoning=false` (or a single `off` level) means the configured
  provider has no reasoning channel; `granular=false` means the endpoint only
  distinguishes on/off and low/medium/high are equivalent.
- **Thinking level**: `POST /api/scenes/{id}/turns` accepts an optional
  `thinkingLevel` (`off | low | medium | high`). It applies to that turn only;
  `off` asks for nothing extra and keeps the provider default. The server maps
  the level per provider family (`reasoning_effort`, `thinking.budget_tokens`,
  `thinking.type`, `enable_thinking`); `LLM_REASONING_STYLE` overrides
  auto-detection for custom gateways. A provider that rejects the parameter is
  retried once without it and the capability is remembered for the process.
- **Reasoning stream** (`ch: "turn"`): `assistant.reasoning_delta` carries
  `{ text }` increments and arrives before the answer text. The authoritative
  message of a model iteration is `assistant.message`, which now also carries
  `reasoning` and `finishReason`. `reasoning` is persisted on
  `TurnMessage.reasoning`, so reconnect/REST history can render the thinking
  block without replaying events.
- **Delta coalescing**: the server batches streamed text/reasoning into frames
  of at least 24 characters (or 50 ms), and always flushes before tool events
  and `assistant.message`. Clients treat `assistant.delta` /
  `assistant.reasoning_delta` as incremental and `assistant.message` as the
  final replacement.

## Known limitations

- Opening overlap validation groups by `(roomId, edgeIndex)`. Openings placed
  from both sides of a shared wall are rendered against the merged wall line by
  the client, but cross-room overlap validation is deferred; built-in fixtures
  place shared-wall doors on one side.
- Client precheck mirrors the L2 result for the dragged item: OBB SAT, the room
  inset, door clearance, window clearance and the walkway sliver rule. It is
  feedback only — the server re-runs the same checks on commit, and L3 business
  rules stay server-authoritative and apply to agent writes.
- Dragging snaps to neighbouring faces as well as the 0.1 m grid: inside
  `EDGE_SNAP_THRESHOLD` (0.25 m) an item's edge jumps flush to a wall-inset
  plane or another item's edge. This makes the "gap is either 0 or ≥ 0.6 m"
  walkway rule reachable by hand; snapping stays on the grid for non-orthogonal
  rotations.
- L3 rules cover the five P0 cases and are enforced strictly for agent writes;
  manual edits still only surface L2 refusals.
- Undo/redo stacks live in the single server process. Versions and inverses are
  persisted, but an app restart starts a fresh undo stack.
