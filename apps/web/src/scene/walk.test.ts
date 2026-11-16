import { describe, expect, it } from 'vitest'
import type { CatalogEntry, SceneDocument } from '../contracts/generated/client/rest'
import { obbFromBox } from './geometry'
import {
  buildWalkColliders,
  circleHitsObb,
  resolveWalkMove,
  WALKER_RADIUS,
  walkPositionBlocked,
  walkSpawn,
} from './walk'

function catalogEntry(kindId: string, width: number, depth: number): CatalogEntry {
  return {
    kindId,
    name: kindId,
    category: 'other',
    defaultVariantId: 'default',
    variants: [
      {
        id: 'default',
        name: kindId,
        sizeM: [width, depth, 0.8],
        modelPath: null,
        thumbnailPath: null,
      },
    ],
    rules: { allowOverlap: false },
  } as unknown as CatalogEntry
}

function sceneFixture(): SceneDocument {
  return {
    schemaVersion: 1,
    id: 'scene_test',
    name: 'Test',
    version: 1,
    units: 'metric',
    north: [0, -1],
    environment: null,
    defaults: { wallHeight: 2.8, wallThickness: 0.12, wallMaterial: 'default' },
    rooms: [
      {
        id: 'room_a',
        name: 'A',
        type: 'living',
        polygon: [
          [0, 0],
          [4, 0],
          [4, 4],
          [0, 4],
        ],
        floorMaterial: 'default',
      },
      {
        id: 'room_b',
        name: 'B',
        type: 'bedroom',
        polygon: [
          [4, 0],
          [8, 0],
          [8, 4],
          [4, 4],
        ],
        floorMaterial: 'default',
      },
    ],
    openings: [
      {
        id: 'op_door',
        type: 'door',
        roomId: 'room_a',
        edgeIndex: 1,
        offsetFrom: 'start',
        offset: 2,
        width: 0.9,
        height: 2.1,
        sillHeight: null,
        door: { swing: 'single', hinge: 'start', opensTo: 'in' },
        style: null,
      },
    ],
    items: [],
    createdAt: '2026-11-16T00:00:00Z',
    updatedAt: '2026-11-16T00:00:00Z',
  } as unknown as SceneDocument
}

describe('buildWalkColliders', () => {
  it('punches a door gap into the shared wall but keeps window walls solid', () => {
    const scene = sceneFixture()
    const colliders = buildWalkColliders(scene, [])
    // room_a edge x=4 and room_b edge x=4 share the wall; only one door gap each.
    const gapCenter = 2
    const insideGap = [4, gapCenter] as [number, number]
    const besideGap = [4, 0.5] as [number, number]
    expect(walkPositionBlocked(colliders, insideGap)).toBe(false)
    expect(walkPositionBlocked(colliders, besideGap)).toBe(true)
  })

  it('blocks around furniture but skips allow-overlap items', () => {
    const scene = sceneFixture()
    scene.items = [
      {
        id: 'it_sofa',
        kindId: 'sofa',
        variantId: 'default',
        transform: { position: [2, 0, 2], rotationY: 0, scale: 1 },
        source: 'manual',
      },
      {
        id: 'it_rug',
        kindId: 'rug',
        variantId: 'default',
        transform: { position: [1, 0, 1], rotationY: 0, scale: 1 },
        source: 'manual',
      },
    ] as SceneDocument['items']
    const catalog = [
      catalogEntry('sofa', 2, 1),
      { ...catalogEntry('rug', 3, 2), rules: { allowOverlap: true } },
    ]
    const colliders = buildWalkColliders(scene, catalog)
    expect(colliders.items).toHaveLength(1)
    expect(walkPositionBlocked(colliders, [2, 2])).toBe(true)
    expect(walkPositionBlocked(colliders, [1, 1])).toBe(false)
  })
})

describe('resolveWalkMove', () => {
  const colliders = buildWalkColliders(sceneFixture(), [])

  it('moves freely inside the room', () => {
    const next = resolveWalkMove(colliders, [1, 1], [0.5, 0.5])
    expect(next).toEqual([1.5, 1.5])
  })

  it('stops at the wall instead of crossing it', () => {
    // Edge x=4 is solid except the door gap at z≈2. Deltas stay below the
    // wall thickness so point sampling cannot tunnel through (the rig substeps).
    expect(walkPositionBlocked(colliders, [3.9, 0.5])).toBe(true)
    const blocked = resolveWalkMove(colliders, [3.6, 0.5], [0.15, 0])
    expect(blocked[0]).toBeLessThan(4 - WALKER_RADIUS)
    const throughDoor = resolveWalkMove(colliders, [3.8, 2], [0.4, 0])
    expect(throughDoor[0]).toBeGreaterThan(4)
    expect(walkPositionBlocked(colliders, [4.1, 2])).toBe(false)
  })

  it('slides along the wall when moving diagonally into it', () => {
    const next = resolveWalkMove(colliders, [3.6, 0.5], [0.15, 0.5])
    expect(next[0]).toBeLessThan(4)
    expect(next[1]).toBeCloseTo(1, 5)
  })

  it('returns the origin position when fully enclosed', () => {
    const zero = resolveWalkMove(colliders, [1, 1], [0, 0])
    expect(zero).toEqual([1, 1])
  })
})

describe('circleHitsObb / walkSpawn', () => {
  it('inflates the OBB by the walker radius', () => {
    const obb = obbFromBox([0, 0], [1, 1], 0)
    expect(circleHitsObb([0.7, 0], obb, WALKER_RADIUS)).toBe(true)
    expect(circleHitsObb([0.76, 0], obb, WALKER_RADIUS)).toBe(false)
  })

  it('spawns on a free spot in the largest room looking along its first edge', () => {
    const colliders = buildWalkColliders(sceneFixture(), [])
    const spawn = walkSpawn(sceneFixture(), colliders)
    expect(spawn.position[0]).toBeGreaterThan(0)
    expect(spawn.position[0]).toBeLessThan(4)
    expect(walkPositionBlocked(colliders, spawn.position)).toBe(false)
    expect(Number.isFinite(spawn.yaw)).toBe(true)
  })

  it('avoids a spawn blocked by furniture (e.g. auto_layout bed in the middle)', () => {
    const scene = sceneFixture()
    // Drop a big item right on the largest room's centre.
    scene.items = [
      {
        id: 'it_bed',
        kindId: 'bed',
        variantId: 'default',
        transform: { position: [2, 0, 2], rotationY: 0, scale: 1 },
        source: 'agent',
      },
    ] as SceneDocument['items']
    const catalog = [catalogEntry('bed', 2.2, 2.2)]
    const colliders = buildWalkColliders(scene, catalog)
    const spawn = walkSpawn(scene, colliders)
    expect(walkPositionBlocked(colliders, spawn.position)).toBe(false)
    expect(resolveWalkMove(colliders, spawn.position, [0.2, 0.2])).not.toEqual(spawn.position)
  })
})
