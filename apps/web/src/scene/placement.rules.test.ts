import rules from '@ai-layout/contracts/rules.json'
import { describe, expect, it } from 'vitest'
import type { CatalogEntry, Item, Opening, SceneDocument } from '../contracts/generated/client/rest'
import { obbDistance, obbFromBox } from './geometry'
import { checkPlacement } from './placement'
import { resolveSnap } from './snapping'

interface EntryOptions {
  width?: number
  depth?: number
  height?: number
  category?: string
  groupWith?: string[]
  allowOverlap?: boolean
}

function entry(kindId: string, options: EntryOptions = {}): CatalogEntry {
  const { width = 1, depth = 1, height = 0.8 } = options
  return {
    kindId,
    name: kindId,
    category: options.category ?? 'other',
    tags: [],
    placement: 'floor',
    defaultVariantId: 'default',
    variants: [
      {
        id: 'default',
        sizeM: [width, depth, height],
        modelPath: `/assets/models/${kindId}.glb`,
        thumbnailPath: `/assets/thumbnails/${kindId}.png`,
      },
    ],
    frontAxis: '+Z',
    pivot: 'bottom-center',
    collision: { type: 'obb' },
    clearance: {},
    allowedRotations: [0, 90, 180, 270],
    rules: {
      allowOverlap: options.allowOverlap ?? false,
      groupWith: options.groupWith ?? [],
    },
  } as CatalogEntry
}

function item(id: string, kindId: string, x: number, z: number, yaw = 0): Item {
  return {
    id,
    kindId,
    variantId: 'default',
    transform: { position: [x, 0, z], rotationY: yaw },
  }
}

function sceneWith(items: Item[], openings: Opening[] = []): SceneDocument {
  return {
    schemaVersion: 1,
    id: 'scene_rules',
    name: 'Rules',
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
    openings,
    items,
    createdAt: '2026-01-01T00:00:00Z',
    updatedAt: '2026-01-01T00:00:00Z',
  }
}

const wardrobe = entry('wardrobe', { width: 1, depth: 0.6, category: 'storage' })
const nightstand = entry('nightstand', { width: 0.8, depth: 0.4, category: 'storage' })
const bedDouble = entry('bed_double', {
  width: 1.7,
  depth: 2,
  category: 'bed',
  groupWith: ['nightstand'],
})
const sofa = entry('sofa', { width: 2, depth: 0.9, category: 'sofa', groupWith: ['coffee_table'] })
const chair = entry('chair', { width: 0.4, depth: 0.4, category: 'other' })

describe('walkway rule parity with the server', () => {
  it('accepts a flush placement', () => {
    // wardrobe occupies z 4.7..5.3; nightstand z 5.3..5.7 -> gap 0
    const scene = sceneWith([item('it_wardrobe', 'wardrobe', 5, 5)])
    const result = checkPlacement(scene, [wardrobe, nightstand], {
      kindId: 'nightstand',
      position: [5, 5.5],
      rotationY: 0,
    })
    expect(result.valid).toBe(true)
  })

  it('refuses a sliver gap between two large pieces', () => {
    // nightstand z 5.5..5.9 -> gap 0.2
    const scene = sceneWith([item('it_wardrobe', 'wardrobe', 5, 5)])
    const result = checkPlacement(scene, [wardrobe, nightstand], {
      kindId: 'nightstand',
      position: [5, 5.7],
      rotationY: 0,
    })
    expect(result.valid).toBe(false)
    expect(result.issue?.code).toBe('walkway_too_narrow')
    expect(result.issue?.message).toContain('0.20 m')
  })

  it('accepts a secondary passage width', () => {
    const scene = sceneWith([item('it_wardrobe', 'wardrobe', 5, 5)])
    const result = checkPlacement(scene, [wardrobe, nightstand], {
      kindId: 'nightstand',
      position: [5, 6.2], // z 6.0..6.4 -> gap 0.7
      rotationY: 0,
    })
    expect(result.valid).toBe(true)
  })

  it('exempts catalog-declared groups', () => {
    // bed occupies z 4..6; nightstand z 6.2..6.6 -> gap 0.2 but grouped.
    const scene = sceneWith([item('it_bed', 'bed_double', 5, 5)])
    const result = checkPlacement(scene, [bedDouble, nightstand], {
      kindId: 'nightstand',
      position: [5, 6.4],
      rotationY: 0,
    })
    expect(result.valid).toBe(true)
  })

  it('ignores pairs where one side is not a circulation blocker', () => {
    // sofa occupies z 4.55..5.45; chair z 5.65..6.05 -> gap 0.2
    const scene = sceneWith([item('it_sofa', 'sofa', 5, 5)])
    const result = checkPlacement(scene, [sofa, chair], {
      kindId: 'chair',
      position: [5, 5.85],
      rotationY: 0,
    })
    expect(result.valid).toBe(true)
  })
})

describe('window clearance parity', () => {
  const window: Opening = {
    id: 'op_window',
    type: 'window',
    roomId: 'room_1',
    edgeIndex: 0,
    offsetFrom: 'start',
    offset: 5,
    width: 1.2,
    height: 1.5,
    sillHeight: 0.9,
  }
  const tallCabinet = entry('tall_cabinet', {
    width: 0.8,
    depth: 0.4,
    height: 1.6,
    category: 'other',
  })
  const lowCabinet = entry('low_cabinet', {
    width: 0.8,
    depth: 0.4,
    height: 0.4,
    category: 'other',
  })

  it('refuses a tall item inside the window strip', () => {
    const scene = sceneWith([], [window])
    const result = checkPlacement(scene, [tallCabinet], {
      kindId: 'tall_cabinet',
      position: [5, 0.29], // footprint z 0.09..0.49 overlaps the 0.06..0.11 strip
      rotationY: 0,
    })
    expect(result.valid).toBe(false)
    expect(result.issue?.code).toBe('blocks_window')
  })

  it('allows an item lower than the sill', () => {
    const scene = sceneWith([], [window])
    const result = checkPlacement(scene, [lowCabinet], {
      kindId: 'low_cabinet',
      position: [5, 0.29],
      rotationY: 0,
    })
    expect(result.valid).toBe(true)
  })
})

describe('obbDistance', () => {
  it('is zero for touching and overlapping footprints', () => {
    const a = obbFromBox([0, 0], [2, 2])
    const b = obbFromBox([2, 0], [2, 2])
    const c = obbFromBox([1, 0], [2, 2])
    expect(obbDistance(a, b)).toBeCloseTo(0, 9)
    expect(obbDistance(a, c)).toBe(0)
  })

  it('measures a real gap', () => {
    const a = obbFromBox([0, 0], [2, 2])
    const b = obbFromBox([2.3, 0], [2, 2])
    expect(obbDistance(a, b)).toBeCloseTo(0.3, 9)
  })
})

describe('edge snapping', () => {
  const box = entry('box', { width: 1, depth: 0.5, category: 'other' })

  it('snaps flush to the wall inset instead of the grid', () => {
    const scene = sceneWith([])
    // Half width 0.5; raw centre 0.6 puts the left edge at 0.10, 2 cm off the
    // ROOM_INSET plane at 0.08.
    const snapped = resolveSnap({ scene, catalog: [box], kindId: 'box', rotationY: 0 }, [0.6, 5])
    expect(snapped[0]).toBeCloseTo(rules.ROOM_INSET + 0.5, 9)
    expect(snapped[1]).toBeCloseTo(5, 9)
  })

  it('snaps flush to a neighbouring item', () => {
    const scene = sceneWith([item('it_other', 'box', 5.5, 5)])
    const snapped = resolveSnap({ scene, catalog: [box], kindId: 'box', rotationY: 0 }, [6.6, 5])
    // Neighbour spans x 5..6; the moving box lands with its left edge at 6.
    expect(snapped[0]).toBeCloseTo(6.5, 9)
  })

  it('falls back to the grid when no edge is within reach', () => {
    const scene = sceneWith([item('it_other', 'box', 5.5, 5)])
    const snapped = resolveSnap({ scene, catalog: [box], kindId: 'box', rotationY: 0 }, [7.03, 5])
    expect(snapped[0]).toBeCloseTo(7, 9)
  })

  it('keeps the grid for non-orthogonal rotations', () => {
    const scene = sceneWith([])
    const snapped = resolveSnap(
      { scene, catalog: [box], kindId: 'box', rotationY: Math.PI / 4 },
      [0.6, 5],
    )
    expect(snapped[0]).toBeCloseTo(0.6, 9)
  })

  it('produces a position the placement check accepts', () => {
    const scene = sceneWith([item('it_other', 'box', 5.5, 5)])
    const snapped = resolveSnap({ scene, catalog: [box], kindId: 'box', rotationY: 0 }, [6.6, 5])
    const check = checkPlacement(scene, [box], {
      kindId: 'box',
      position: snapped,
      rotationY: 0,
      ignoreItemId: 'it_other',
    })
    expect(check.valid).toBe(true)
  })

  it('does not snap a flush position onto the wrong side of a plane', () => {
    const scene = sceneWith([item('it_other', 'box', 5.5, 5)])
    // Approaching from the left: right edge 4.9 is 0.1 short of the plane at 5.
    const snapped = resolveSnap({ scene, catalog: [box], kindId: 'box', rotationY: 0 }, [4.4, 5])
    expect(snapped[0]).toBeCloseTo(4.5, 9)
  })
})
