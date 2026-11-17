import { describe, expect, it } from 'vitest'
import type { CatalogEntry, SceneDocument } from '../contracts/generated/client/rest'
import { insetPolygon, isSimplePolygon, snapPoint } from './geometry'
import { checkPlacement } from './placement'

function entry(kindId: string, width: number, depth: number, allowOverlap = false): CatalogEntry {
  return {
    kindId,
    name: kindId,
    category: 'other',
    tags: [],
    placement: 'floor',
    defaultVariantId: 'default',
    variants: [
      {
        id: 'default',
        sizeM: [width, depth, 0.6],
        modelPath: `/assets/models/${kindId}.glb`,
        thumbnailPath: `/assets/thumbnails/${kindId}.png`,
      },
    ],
    frontAxis: '+Z',
    pivot: 'bottom-center',
    collision: { type: 'obb' },
    clearance: {},
    allowedRotations: [0, 90, 180, 270],
    rules: allowOverlap ? { allowOverlap: true } : null,
    license: { name: 'CC0-1.0', url: 'https://creativecommons.org/publicdomain/zero/1.0/' },
  }
}

function scene(): SceneDocument {
  return {
    schemaVersion: 1,
    id: 'scene_test',
    name: 'Test',
    version: 1,
    units: 'metric',
    north: [0, -1],
    defaults: { wallHeight: 2.8, wallThickness: 0.12, wallMaterial: 'default' },
    rooms: [
      {
        id: 'room_living',
        name: 'Living',
        type: 'living',
        polygon: [
          [0, 0],
          [6, 0],
          [6, 5],
          [0, 5],
        ],
      },
    ],
    openings: [
      {
        id: 'op_door',
        type: 'door',
        roomId: 'room_living',
        edgeIndex: 0,
        offsetFrom: 'start',
        offset: 3,
        width: 0.9,
        height: 2.1,
      },
    ],
    items: [
      {
        id: 'it_sofa',
        kindId: 'sofa',
        variantId: 'default',
        transform: { position: [2, 0, 2], rotationY: 0 },
      },
    ],
    createdAt: '2026-01-01T00:00:00Z',
    updatedAt: '2026-01-01T00:00:00Z',
  }
}

const catalog = [entry('sofa', 2.0, 0.9), entry('plant', 0.4, 0.4), entry('rug', 2.4, 1.4, true)]

describe('geometry helpers', () => {
  it('snaps to the 0.1 m grid', () => {
    const [x, z] = snapPoint([1.24, -0.06])
    expect(x).toBeCloseTo(1.2, 6)
    expect(z).toBeCloseTo(-0.1, 6)
  })

  it('insets a rectangle inward', () => {
    const inset = insetPolygon(
      [
        [0, 0],
        [4, 0],
        [4, 3],
        [0, 3],
      ],
      0.2,
    )
    expect(inset).not.toBeNull()
    expect(inset?.[0]?.[0]).toBeCloseTo(0.2)
    expect(inset?.[2]?.[0]).toBeCloseTo(3.8)
  })

  it('rejects self-intersecting polygons', () => {
    expect(
      isSimplePolygon([
        [0, 0],
        [2, 2],
        [2, 0],
        [0, 2],
      ]),
    ).toBe(false)
    expect(
      isSimplePolygon([
        [0, 0],
        [2, 0],
        [2, 2],
        [0, 2],
      ]),
    ).toBe(true)
  })
})

describe('client placement precheck', () => {
  it('accepts a free floor position', () => {
    const result = checkPlacement(scene(), catalog, {
      kindId: 'plant',
      position: [4.5, 3.5],
      rotationY: 0,
    })
    expect(result.valid).toBe(true)
    expect(result.roomId).toBe('room_living')
  })

  it('rejects positions outside rooms', () => {
    const result = checkPlacement(scene(), catalog, {
      kindId: 'plant',
      position: [9, 9],
      rotationY: 0,
    })
    expect(result.valid).toBe(false)
    expect(result.issue?.code).toBe('out_of_room')
  })

  it('rejects OBB collisions', () => {
    const result = checkPlacement(scene(), catalog, {
      kindId: 'plant',
      position: [2, 2],
      rotationY: 0,
    })
    expect(result.valid).toBe(false)
    expect(result.issue?.code).toBe('collision')
  })

  it('allows rugs to overlap furniture', () => {
    const result = checkPlacement(scene(), catalog, {
      kindId: 'rug',
      position: [2, 2],
      rotationY: 0,
    })
    expect(result.valid).toBe(true)
  })

  it('rejects door clearance violations', () => {
    const result = checkPlacement(scene(), catalog, {
      kindId: 'plant',
      position: [3, 0.5],
      rotationY: 0,
    })
    expect(result.valid).toBe(false)
    expect(result.issue?.code).toBe('blocks_door')
  })

  it('rejects wall inset violations', () => {
    const result = checkPlacement(scene(), catalog, {
      kindId: 'plant',
      position: [0.05, 3],
      rotationY: 0,
    })
    expect(result.valid).toBe(false)
    expect(result.issue?.code).toBe('out_of_room')
  })

  it('scales the footprint of the item being placed', () => {
    const grown = checkPlacement(scene(), catalog, {
      kindId: 'plant',
      position: [4, 3],
      rotationY: 0,
      scale: 2,
    })
    expect(grown.valid).toBe(true)

    const nearWall = checkPlacement(scene(), catalog, {
      kindId: 'plant',
      position: [0.35, 3],
      rotationY: 0,
      scale: 2,
    })
    expect(nearWall.valid).toBe(false)
    expect(nearWall.issue?.code).toBe('out_of_room')
  })

  it('rejects a scaled item that would exceed the ceiling', () => {
    const result = checkPlacement(scene(), catalog, {
      kindId: 'sofa',
      position: [4, 3],
      rotationY: 0,
      scale: 6,
    })
    expect(result.valid).toBe(false)
    expect(result.issue?.code).toBe('unrealistic')
  })

  it('measures existing items at their own scale when checking collisions', () => {
    const document = scene()
    const outside = checkPlacement(document, catalog, {
      kindId: 'plant',
      position: [3.2, 2],
      rotationY: 0,
    })
    expect(outside.valid).toBe(true)

    const sofa = document.items[0]
    if (!sofa) throw new Error('fixture scene must contain the sofa')
    document.items[0] = {
      ...sofa,
      transform: { ...sofa.transform, scale: 1.5 },
    }
    const overlap = checkPlacement(document, catalog, {
      kindId: 'plant',
      position: [3.2, 2],
      rotationY: 0,
    })
    expect(overlap.valid).toBe(false)
    expect(overlap.issue?.code).toBe('collision')
  })
})
