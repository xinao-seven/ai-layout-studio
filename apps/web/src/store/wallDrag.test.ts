import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api/client'
import type { CatalogEntry, Room, SceneDocument } from '../contracts/generated/client/rest'
import { useSceneStore } from './sceneStore'

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>()
  return { ...actual, commitScene: vi.fn() }
})

const commitScene = vi.mocked(api.commitScene)

function room(id: string, polygon: [number, number][]): Room {
  return { id, name: id, type: 'other', polygon } as Room
}

function sceneWith(rooms: Room[], items: SceneDocument['items'] = []): SceneDocument {
  return {
    schemaVersion: 1,
    id: 'scene_wall',
    name: 'Wall',
    version: 1,
    units: 'metric',
    north: [0, -1],
    defaults: { wallHeight: 2.8, wallThickness: 0.12, wallMaterial: 'default' },
    rooms,
    openings: [],
    items,
    createdAt: '2026-01-01T00:00:00Z',
    updatedAt: '2026-01-01T00:00:00Z',
  }
}

function catalogEntry(kindId: string, width: number, depth: number): CatalogEntry {
  return {
    kindId,
    name: kindId,
    category: 'storage',
    tags: [],
    placement: 'floor',
    defaultVariantId: 'default',
    variants: [
      {
        id: 'default',
        sizeM: [width, depth, 0.8],
        modelPath: `/assets/models/${kindId}.glb`,
        thumbnailPath: `/assets/thumbnails/${kindId}.png`,
      },
    ],
    frontAxis: '+Z',
    pivot: 'bottom-center',
    collision: { type: 'obb' },
    clearance: {},
    allowedRotations: [0, 90, 180, 270],
    rules: null,
  }
}

beforeEach(() => {
  commitScene.mockReset()
  useSceneStore.setState({
    scene: null,
    catalog: [],
    wallDrag: null,
    vertexDrag: null,
    error: null,
  })
  useSceneStore.getState().cancelWallDrag()
})

describe('wall drag', () => {
  it('moves both endpoints of the dragged wall and leaves the rest alone', () => {
    const scene = sceneWith([
      room('room_a', [
        [0, 0],
        [4, 0],
        [4, 3],
        [0, 3],
      ]),
    ])
    useSceneStore.setState({ scene })
    // Edge 1 is the x = 4 wall; its normal points -x (into the room).
    useSceneStore.getState().startWallDrag('room_a', 1, [4, 1.5])
    useSceneStore.getState().updateWallDrag([4.1, 1.5])
    const drag = useSceneStore.getState().wallDrag
    expect(drag?.moved).toBe(true)
    expect(drag?.valid).toBe(true)
    expect(drag?.polygons.room_a).toEqual([
      [0, 0],
      [4.1, 0],
      [4.1, 3],
      [0, 3],
    ])
    useSceneStore.getState().cancelWallDrag()
  })

  it('moves a shared wall in both rooms', () => {
    const scene = sceneWith([
      room('room_a', [
        [0, 0],
        [4, 0],
        [4, 3],
        [0, 3],
      ]),
      room('room_b', [
        [4, 0],
        [8, 0],
        [8, 3],
        [4, 3],
      ]),
    ])
    useSceneStore.setState({ scene })
    useSceneStore.getState().startWallDrag('room_a', 1, [4, 1.5])
    expect(useSceneStore.getState().wallDrag?.edges).toHaveLength(2)
    useSceneStore.getState().updateWallDrag([4.1, 1.5])
    const drag = useSceneStore.getState().wallDrag
    expect(drag?.polygons.room_a?.[1]).toEqual([4.1, 0])
    expect(drag?.polygons.room_b?.[0]).toEqual([4.1, 0])
    expect(drag?.polygons.room_b?.[3]).toEqual([4.1, 3])
    useSceneStore.getState().cancelWallDrag()
  })

  it('snaps the moved wall onto another room wall', () => {
    const scene = sceneWith([
      room('room_a', [
        [0, 0],
        [4, 0],
        [4, 3],
        [0, 3],
      ]),
      room('room_c', [
        [4.13, 0],
        [6, 0],
        [6, 2],
        [4.13, 2],
      ]),
    ])
    useSceneStore.setState({ scene })
    useSceneStore.getState().startWallDrag('room_a', 1, [4, 1.5])
    useSceneStore.getState().updateWallDrag([4.1, 1.5])
    // 4.10 is within the 0.25 m radius of 4.13, so it locks exactly.
    expect(useSceneStore.getState().wallDrag?.polygons.room_a?.[1]?.[0]).toBeCloseTo(4.13, 9)
    useSceneStore.getState().cancelWallDrag()
  })

  it('flags a move that would shrink the room below the minimum area', () => {
    const scene = sceneWith([
      room('room_a', [
        [0, 0],
        [4, 0],
        [4, 3],
        [0, 3],
      ]),
    ])
    useSceneStore.setState({ scene })
    useSceneStore.getState().startWallDrag('room_a', 1, [4, 1.5])
    // Pushing the wall to x = -0.3 leaves a 0.3 x 3 m strip (0.9 m² < 1 m²).
    useSceneStore.getState().updateWallDrag([-0.3, 1.5])
    const drag = useSceneStore.getState().wallDrag
    expect(drag?.valid).toBe(false)
    expect(drag?.issue?.code).toBe('room_too_small')
    useSceneStore.getState().cancelWallDrag()
  })

  it('flags a move that would leave furniture outside the wall', () => {
    const item = {
      id: 'it_1',
      kindId: 'wardrobe',
      variantId: 'default',
      transform: { position: [3.4, 0, 1.5] as [number, number, number], rotationY: 0 },
    }
    const scene = sceneWith(
      [
        room('room_a', [
          [0, 0],
          [4, 0],
          [4, 3],
          [0, 3],
        ]),
      ],
      [item],
    )
    useSceneStore.setState({ scene, catalog: [catalogEntry('wardrobe', 1, 0.6)] })
    useSceneStore.getState().startWallDrag('room_a', 1, [4, 1.5])
    useSceneStore.getState().updateWallDrag([3.2, 1.5])
    const drag = useSceneStore.getState().wallDrag
    expect(drag?.valid).toBe(false)
    expect(drag?.issue?.code).toBe('out_of_room')
    useSceneStore.getState().cancelWallDrag()
  })

  it('commits one room.update per affected room on release', async () => {
    const scene = sceneWith([
      room('room_a', [
        [0, 0],
        [4, 0],
        [4, 3],
        [0, 3],
      ]),
      room('room_b', [
        [4, 0],
        [8, 0],
        [8, 3],
        [4, 3],
      ]),
    ])
    useSceneStore.setState({ scene })
    commitScene.mockImplementation(async () => ({
      scene: { ...scene, version: 2 },
      patch: { sceneId: scene.id, fromVersion: 1, toVersion: 2, ops: [] },
    }))

    useSceneStore.getState().startWallDrag('room_a', 1, [4, 1.5])
    useSceneStore.getState().updateWallDrag([4.1, 1.5])
    await useSceneStore.getState().endWallDrag()

    expect(commitScene).toHaveBeenCalledTimes(1)
    const call = commitScene.mock.calls[0]
    if (!call) throw new Error('expected a commit')
    const [, request] = call
    expect(request.baseVersion).toBe(1)
    expect(request.ops).toHaveLength(2)
    const updated = request.ops
      .filter((op) => op.op === 'room.update')
      .map((op) => op.roomId)
      .sort()
    expect(updated).toEqual(['room_a', 'room_b'])
    expect(useSceneStore.getState().wallDrag).toBeNull()
  })

  it('treats a click without movement as a selection, not a commit', async () => {
    const scene = sceneWith([
      room('room_a', [
        [0, 0],
        [4, 0],
        [4, 3],
        [0, 3],
      ]),
    ])
    useSceneStore.setState({ scene })
    useSceneStore.getState().startWallDrag('room_a', 1, [4, 1.5])
    useSceneStore.getState().updateWallDrag([4.005, 1.5])
    await useSceneStore.getState().endWallDrag()
    expect(commitScene).not.toHaveBeenCalled()
    expect(useSceneStore.getState().wallDrag).toBeNull()
  })
})
