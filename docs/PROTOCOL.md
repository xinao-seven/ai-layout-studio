# Protocol (M1 freeze)

Status: **frozen at M1 (2026-11-16)**. M3 implements the agent loop against this
contract without redefining envelopes, patch ops, versioning or resume.

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

A `ScenePatch` carries `fromVersion`, `toVersion`, semantic `ops`, `actor`,
optional `turnId`/`proposalId` and `clientMutationId`. Ops:

| op | fields | inverse |
|---|---|---|
| `item.add` | `item` | `item.remove` with `before` |
| `item.remove` | `itemId`, `before` | `item.add` |
| `item.update` | `itemId`, full `before`/`after` snapshots | swap `before`/`after` |
| `room.add` | `room` | `room.remove` |
| `room.remove` | `roomId`, `before` | `room.add` |
| `room.update` | `roomId`, `before`/`after` | swap |
| `opening.add` | `opening` | `opening.remove` |
| `opening.remove` | `openingId`, `before` | `opening.add` |
| `opening.update` | `openingId`, `before`/`after` | swap |

The server resolves partial update payloads into full `before`/`after`
snapshots; inverses are stored in `scene_versions.inverse_json` (latest 100
versions retained).

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
| `turn.subscribe` / `turn.cancel` / `ask_user.answer` / `capture.result` | reserved | M3 |

Ack/error frames: `host.ack` with `{ type, version }`, `host.error` with
`{ error: { code, message, suggest?, details? } }`.

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
- `dependsOn` references earlier proposals in the same turn; confirmation must
  follow dependency order.
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
3. Otherwise it sends a fresh `scene.snapshot`.
4. The client ignores state frames older than its current snapshot sequence, so
   replay and snapshot cannot regress state.

## Errors

REST and WebSocket errors share the envelope:

```json
{ "error": { "code": "version_conflict", "message": "...", "suggest": "..." } }
```

Codes are frozen in PRD §6.3. M1 emits `not_found`, `unknown_room`,
`unknown_kind`, `variant_missing`, `version_conflict`, `invalid_polygon`,
`room_too_small`, `out_of_room`, `opening_off_wall`, `opening_overlap`,
`invalid_input`, `proposal_expired`, `import_invalid` and `internal_error`.

## Single-worker invariant

State, sequence counters, undo/redo stacks and pending proposals live in one
process. The server refuses `WEB_CONCURRENCY`/`UVICORN_WORKERS` > 1; SQLite runs
with WAL, foreign keys and a 5 s busy timeout.