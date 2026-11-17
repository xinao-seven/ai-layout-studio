import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api/client'
import type { CatalogEntry, SceneDocument } from '../contracts/generated/client/rest'
import { clampScale, useSceneStore } from './sceneStore'

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>()
  return { ...actual, commitScene: vi.fn() }
})

const commitScene = vi.mocked(api.commitScene)

function catalogEntry(): CatalogEntry {
  return {
    kindId: 'sofa',
    name: '沙发',
    category: 'sofa',
    tags: [],
    placement: 'floor',
    defaultVariantId: 'default',
    variants: [
      {
        id: 'default',
        // 2.0 x 0.9 footprint -> half-diagonal 1.0966 m
        sizeM: [2, 0.9, 0.85],
        modelPath: '/assets/models/sofa.glb',
        thumbnailPath: '/assets/thumbnails/sofa.png',
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

function sceneWithSofa(): SceneDocument {
  return {
    schemaVersion: 1,
    id: 'scene_scale',
    name: 'Scale',
    version: 1,
    units: 'metric',
    north: [0, -1],
    defaults: { wallHeight: 2.8, wallThickness: 0.12, wallMaterial: 'default' },
    rooms: [
      {
        id: 'room_1',
        name: 'Room',
        type: 'living',
        polygon: [
          [0, 0],
          [10, 0],
          [10, 10],
          [0, 10],
        ],
      },
    ],
    openings: [],
    items: [
      {
        id: 'it_sofa',
        kindId: 'sofa',
        variantId: 'default',
        transform: { position: [5, 0, 5], rotationY: 0 },
      },
    ],
    createdAt: '2026-01-01T00:00:00Z',
    updatedAt: '2026-01-01T00:00:00Z',
  }
}

function seed() {
  const scene = sceneWithSofa()
  useSceneStore.setState({
    scene,
    catalog: [catalogEntry()],
    activeSceneId: scene.id,
    scaleDrag: null,
    error: null,
  })
  return scene
}

beforeEach(() => {
  commitScene.mockReset()
  useSceneStore.getState().cancelScaleDrag()
})

describe('scale drag pipeline', () => {
  it('commits exactly one item.update carrying the snapped scale', async () => {
    const scene = seed()
    const sofa = scene.items[0]
    if (!sofa) throw new Error('fixture scene must contain the sofa')
    const nextVersion = scene.version + 1
    commitScene.mockImplementation(async () => ({
      scene: {
        ...scene,
        version: nextVersion,
        items: [{ ...sofa, transform: { ...sofa.transform, scale: 1.1 } }],
      },
      patch: { sceneId: scene.id, fromVersion: 1, toVersion: nextVersion, ops: [] },
    }))

    useSceneStore.getState().startScaleDrag('it_sofa')
    expect(useSceneStore.getState().scaleDrag?.itemId).toBe('it_sofa')

    // Distance 1.2 m / half-diagonal 1.0966 = 1.094 -> snapped to 1.1
    useSceneStore.getState().updateScaleDrag([6.2, 5])
    expect(useSceneStore.getState().scaleDrag?.scale).toBeCloseTo(1.1, 6)
    expect(useSceneStore.getState().scaleDrag?.valid).toBe(true)

    await useSceneStore.getState().endScaleDrag()

    expect(commitScene).toHaveBeenCalledTimes(1)
    const call = commitScene.mock.calls[0]
    if (!call) throw new Error('expected one commit')
    const [, request] = call
    expect(request.baseVersion).toBe(1)
    expect(request.ops).toEqual([
      { op: 'item.update', itemId: 'it_sofa', after: { transform: { scale: 1.1 } } },
    ])
    expect(useSceneStore.getState().scaleDrag).toBeNull()
    expect(useSceneStore.getState().scene?.version).toBe(nextVersion)
  })

  it('clamps a huge drag to the upper bound', async () => {
    seed()
    commitScene.mockImplementation(async () => {
      throw new Error('should not commit an out-of-range scale twice')
    })
    useSceneStore.getState().startScaleDrag('it_sofa')
    useSceneStore.getState().updateScaleDrag([50, 5])
    expect(useSceneStore.getState().scaleDrag?.scale).toBe(2)
    useSceneStore.getState().cancelScaleDrag()
    expect(useSceneStore.getState().scaleDrag).toBeNull()
  })

  it('does not commit when the drag ends on the same scale', async () => {
    seed()
    useSceneStore.getState().startScaleDrag('it_sofa')
    // Half-diagonal distance reproduces scale 1.0.
    useSceneStore.getState().updateScaleDrag([5 + 1.0966, 5])
    await useSceneStore.getState().endScaleDrag()
    expect(commitScene).not.toHaveBeenCalled()
  })

  it('refuses an invalid scale and reports the placement issue', async () => {
    seed()
    // A 2.0x item near the wall cannot grow without leaving the room inset.
    useSceneStore.setState((state) => ({
      scene: state.scene
        ? {
            ...state.scene,
            items: state.scene.items.map((item) => ({
              ...item,
              transform: { ...item.transform, position: [1.05, 0, 5] as [number, number, number] },
            })),
          }
        : state.scene,
    }))
    useSceneStore.getState().startScaleDrag('it_sofa')
    // Grow to 2.0x: a 4 m wide sofa centred at x=1.05 leaves the room.
    useSceneStore.getState().updateScaleDrag([3.25, 5])
    expect(useSceneStore.getState().scaleDrag?.scale).toBeCloseTo(2, 6)
    expect(useSceneStore.getState().scaleDrag?.valid).toBe(false)
    await useSceneStore.getState().endScaleDrag()
    expect(commitScene).not.toHaveBeenCalled()
    expect(useSceneStore.getState().error?.code).toBe('out_of_room')
  })
})

describe('scale clamping', () => {
  it('snaps to the shared step and stays inside the envelope', () => {
    expect(clampScale(1.03)).toBe(1.05)
    expect(clampScale(0.01)).toBe(0.4)
    expect(clampScale(99)).toBe(2)
  })
})
