import { describe, expect, it } from 'vitest'
import type { SceneDocument } from '../contracts/generated/client/rest'
import { applyPatchToDocument, type PatchOp } from './patch'

function document(): SceneDocument {
  return {
    schemaVersion: 1,
    id: 'scene_test',
    name: 'Test',
    version: 1,
    units: 'metric',
    north: [0, -1],
    defaults: {
      wallHeight: 2.8,
      wallThickness: 0.12,
      wallMaterial: 'default',
    },
    rooms: [
      {
        id: 'room_living',
        name: 'Living',
        type: 'living',
        polygon: [
          [0, 0],
          [4, 0],
          [4, 4],
          [0, 4],
        ],
        floorMaterial: 'default',
      },
    ],
    openings: [],
    items: [],
    createdAt: '2026-11-16T00:00:00Z',
    updatedAt: '2026-11-16T00:00:00Z',
  }
}

function item() {
  return {
    id: 'it_sofa',
    kindId: 'sofa',
    variantId: 'default',
    transform: {
      position: [2, 0, 2] as [number, number, number],
      rotationY: 0,
      scale: 1,
    },
    source: 'manual' as const,
  }
}

describe('applyPatchToDocument', () => {
  it('adds and removes items without mutating the source', () => {
    const source = document()
    const addOp: PatchOp = { op: 'item.add', item: item() }
    const added = applyPatchToDocument(source, [addOp])
    expect(added.items).toHaveLength(1)
    expect(source.items).toHaveLength(0)

    const removed = applyPatchToDocument(added, [{ op: 'item.remove', itemId: 'it_sofa' }])
    expect(removed.items).toHaveLength(0)
  })

  it('deep-merges partial item updates', () => {
    const withItem = applyPatchToDocument(document(), [{ op: 'item.add', item: item() }])
    const moved = applyPatchToDocument(withItem, [
      {
        op: 'item.update',
        itemId: 'it_sofa',
        after: { transform: { rotationY: 1.5 } },
      },
    ])
    expect(moved.items[0]?.transform.position).toEqual([2, 0, 2])
    expect(moved.items[0]?.transform.rotationY).toBe(1.5)
    expect(withItem.items[0]?.transform.rotationY).toBe(0)
  })

  it('updates rooms and openings', () => {
    const updated = applyPatchToDocument(document(), [
      { op: 'room.update', roomId: 'room_living', after: { name: '客厅' } },
      {
        op: 'opening.add',
        opening: {
          id: 'op_door',
          type: 'door',
          roomId: 'room_living',
          edgeIndex: 0,
          offsetFrom: 'start',
          offset: 1,
          width: 0.9,
          height: 2.1,
        },
      },
    ])
    expect(updated.rooms[0]?.name).toBe('客厅')
    expect(updated.openings).toHaveLength(1)
  })
})

describe('scene.update', () => {
  it('merges environment settings without touching children', () => {
    const source = document()
    const updated = applyPatchToDocument(source, [
      {
        op: 'scene.update',
        after: { environment: { latitude: 31.23, longitude: 121.47 } },
      } as PatchOp,
    ])
    expect(updated.environment).toEqual({ latitude: 31.23, longitude: 121.47 })
    expect(updated.rooms).toEqual(source.rooms)
    expect(source.environment).toBeUndefined()
  })

  it('rotates north', () => {
    const updated = applyPatchToDocument(document(), [
      { op: 'scene.update', after: { north: [1, 0] } } as PatchOp,
    ])
    expect(updated.north).toEqual([1, 0])
  })

  it('throws when scene.update has no after', () => {
    expect(() =>
      applyPatchToDocument(document(), [{ op: 'scene.update' } as unknown as PatchOp]),
    ).toThrow()
  })
})
