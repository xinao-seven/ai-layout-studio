import { describe, expect, it } from 'vitest'
import type { Proposal, SceneDocument } from '../contracts/generated/client/rest'
import { pendingOverlay } from './proposals'

const scene = {
  rooms: [],
  items: [
    {
      id: 'it_keep',
      kindId: 'plant',
      variantId: 'default',
      transform: { position: [1, 0, 1], rotationY: 0 },
    },
    {
      id: 'it_remove',
      kindId: 'chair',
      variantId: 'default',
      transform: { position: [2, 0, 2], rotationY: 0 },
    },
  ],
} as unknown as SceneDocument

function proposal(id: string, ops: Proposal['ops']): Proposal {
  return {
    id,
    sceneId: 'scene_test',
    turnId: 'turn_test',
    baseVersion: 1,
    dependsOn: [],
    ops,
    status: 'pending',
    createdAt: `2026-01-01T00:00:0${id.length}Z`,
  } as Proposal
}

describe('pendingOverlay', () => {
  it('merges pending adds, removes and updates for ghost display', () => {
    const overlay = pendingOverlay(scene, {
      prop_one: proposal('prop_one', [
        {
          op: 'item.add',
          item: {
            id: 'it_new',
            kindId: 'sofa',
            variantId: 'default',
            transform: { position: [3, 0, 1], rotationY: 0 },
          },
        },
        { op: 'item.remove', itemId: 'it_remove' },
        {
          op: 'item.update',
          itemId: 'it_keep',
          after: { transform: { position: [4, 0, 4], rotationY: 0 } },
        },
      ] as Proposal['ops']),
    })
    expect(overlay.addedItems.map((item) => item.id)).toEqual(['it_new'])
    expect([...overlay.removedItemIds]).toEqual(['it_remove'])
    expect([...overlay.updatedItemIds]).toEqual(['it_keep'])
    expect(overlay.updatedItems[0]?.transform.position).toEqual([4, 0, 4])
  })

  it('ignores non-pending proposals', () => {
    const confirmed = proposal('prop_done', [
      {
        op: 'item.add',
        item: {
          id: 'it_confirmed',
          kindId: 'sofa',
          variantId: 'default',
          transform: { position: [3, 0, 1], rotationY: 0 },
        },
      },
    ] as Proposal['ops'])
    confirmed.status = 'confirmed'
    const overlay = pendingOverlay(scene, { [confirmed.id]: confirmed })
    expect(overlay.addedItems).toEqual([])
  })
})

describe('pendingOverlay rooms', () => {
  it('surfaces agent-proposed rooms as addedRooms', () => {
    const roomScene = {
      rooms: [
        {
          id: 'room_keep',
          name: '客厅',
          type: 'living',
          polygon: [
            [0, 0],
            [4, 0],
            [4, 3],
            [0, 3],
          ],
        },
      ],
      items: [],
    } as unknown as SceneDocument
    const overlay = pendingOverlay(roomScene, {
      prop_room: proposal('prop_room', [
        {
          op: 'room.add',
          room: {
            id: 'room_new',
            name: '卧室',
            type: 'bedroom',
            polygon: [
              [5, 0],
              [9, 0],
              [9, 4],
              [5, 4],
            ],
          },
        },
      ]),
    })
    expect(overlay.addedRooms.map((room) => room.id)).toEqual(['room_new'])
    expect(overlay.addedItems).toEqual([])
  })

  it('keeps confirmed rooms out of addedRooms', () => {
    const roomScene = {
      rooms: [
        {
          id: 'room_keep',
          name: '客厅',
          type: 'living',
          polygon: [
            [0, 0],
            [4, 0],
            [4, 3],
            [0, 3],
          ],
        },
      ],
      items: [],
    } as unknown as SceneDocument
    const overlay = pendingOverlay(roomScene, {})
    expect(overlay.addedRooms).toEqual([])
  })
})
