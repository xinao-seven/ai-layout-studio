import type {
  CommitRequest,
  Item,
  Opening,
  Room,
  SceneDocument,
} from '../contracts/generated/client/rest'

export type PatchOp = CommitRequest['ops'][number]

type JsonObject = Record<string, unknown>

function isObject(value: unknown): value is JsonObject {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function deepMerge(base: JsonObject, updates: JsonObject): JsonObject {
  const merged: JsonObject = { ...base }
  for (const [key, value] of Object.entries(updates)) {
    const existing = merged[key]
    merged[key] = isObject(existing) && isObject(value) ? deepMerge(existing, value) : value
  }
  return merged
}

function applyItemUpdate(item: Item, after: JsonObject): Item {
  return deepMerge(item as unknown as JsonObject, after) as unknown as Item
}

function applyRoomUpdate(room: Room, after: JsonObject): Room {
  return deepMerge(room as unknown as JsonObject, after) as unknown as Room
}

function applyOpeningUpdate(opening: Opening, after: JsonObject): Opening {
  return deepMerge(opening as unknown as JsonObject, after) as unknown as Opening
}

/**
 * Lightweight local patch application for the Zustand/Zundo mirror.
 *
 * The server remains authoritative: the browser applies the same semantic ops
 * optimistically, then adopts the server snapshot whenever versions disagree.
 */
export function applyPatchToDocument(document: SceneDocument, ops: PatchOp[]): SceneDocument {
  let next = structuredClone(document)
  for (const op of ops) {
    switch (op.op) {
      case 'item.add':
        next.items = [...next.items, op.item]
        break
      case 'item.remove':
        next.items = next.items.filter((item) => item.id !== op.itemId)
        break
      case 'item.update': {
        if (!op.after) throw new Error(`item.update ${op.itemId} has no after`)
        next.items = next.items.map((item) =>
          item.id === op.itemId ? applyItemUpdate(item, op.after as JsonObject) : item,
        )
        break
      }
      case 'room.add':
        next.rooms = [...next.rooms, op.room]
        break
      case 'room.remove':
        next.rooms = next.rooms.filter((room) => room.id !== op.roomId)
        break
      case 'room.update': {
        if (!op.after) throw new Error(`room.update ${op.roomId} has no after`)
        next.rooms = next.rooms.map((room) =>
          room.id === op.roomId ? applyRoomUpdate(room, op.after as JsonObject) : room,
        )
        break
      }
      case 'opening.add':
        next.openings = [...next.openings, op.opening]
        break
      case 'opening.remove':
        next.openings = next.openings.filter((opening) => opening.id !== op.openingId)
        break
      case 'opening.update': {
        if (!op.after) {
          throw new Error(`opening.update ${op.openingId} has no after`)
        }
        next.openings = next.openings.map((opening) =>
          opening.id === op.openingId
            ? applyOpeningUpdate(opening, op.after as JsonObject)
            : opening,
        )
        break
      }
      case 'scene.update': {
        if (!op.after) throw new Error('scene.update has no after')
        // Merge into the accumulated `next` (not the original document) and
        // keep processing: a patch may mix scene.update with item/room ops.
        next = deepMerge(
          next as unknown as JsonObject,
          structuredClone(op.after) as JsonObject,
        ) as unknown as SceneDocument
        break
      }
    }
  }
  return next
}
