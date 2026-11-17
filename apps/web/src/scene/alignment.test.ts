import { beforeEach, describe, expect, it } from 'vitest'
import type { Room, SceneDocument } from '../contracts/generated/client/rest'
import { useSceneStore } from '../store/sceneStore'
import {
  ALIGN_THRESHOLD,
  alignValue,
  collectAlignPlanes,
  snapDraftPoint,
  type Vec2,
  vertexAlignSnap,
} from './geometry'

interface RoomSpec {
  id: string
  polygon: Vec2[]
}

function room(spec: RoomSpec): Room {
  return { id: spec.id, name: spec.id, type: 'other', polygon: spec.polygon } as Room
}

function sceneWithRooms(specs: RoomSpec[]): SceneDocument {
  return {
    schemaVersion: 1,
    id: 'scene_align',
    name: 'Align',
    version: 1,
    units: 'metric',
    north: [0, -1],
    defaults: { wallHeight: 2.8, wallThickness: 0.12, wallMaterial: 'default' },
    rooms: specs.map(room),
    openings: [],
    items: [],
    createdAt: '2026-01-01T00:00:00Z',
    updatedAt: '2026-01-01T00:00:00Z',
  }
}

beforeEach(() => {
  useSceneStore.setState({ scene: null, draftRoom: [], draftCursor: null, vertexDrag: null })
})

describe('alignment planes', () => {
  it('collects every vertex coordinate from every room', () => {
    const planes = collectAlignPlanes([
      room({
        id: 'a',
        polygon: [
          [0, 0],
          [3, 0],
          [3, 4],
          [0, 4],
        ],
      }),
      room({
        id: 'b',
        polygon: [
          [3.05, 0],
          [6, 0],
          [6, 4],
          [3.05, 4],
        ],
      }),
    ])
    expect([...new Set(planes.xs)].sort((l, r) => l - r)).toEqual([0, 3, 3.05, 6])
    expect([...new Set(planes.zs)].sort((l, r) => l - r)).toEqual([0, 4])
  })

  it('skips the vertex being dragged', () => {
    const planes = collectAlignPlanes(
      [
        room({
          id: 'a',
          polygon: [
            [0, 0],
            [2.13, 0],
            [4, 0],
          ],
        }),
      ],
      { roomId: 'a', vertexIndex: 1 },
    )
    expect(planes.xs).not.toContain(2.13)
  })

  it('snaps to the nearest plane within the radius and grids otherwise', () => {
    expect(alignValue(3.13, [3.05, 6])).toBeCloseTo(3.05, 9)
    expect(alignValue(3.13, [0, 6])).toBeCloseTo(3.1, 9)
    // 0.35 m away is outside the 0.25 m radius -> plain grid snap.
    expect(alignValue(3.4, [3.05], 0.1, ALIGN_THRESHOLD)).toBeCloseTo(3.4, 9)
  })
})

describe('vertex alignment', () => {
  it('aligns to a non-adjacent vertex of the same room', () => {
    // L-shaped hexagon: vertex 1 (2.13, 0) is far from vertex 4's x (2.13 is
    // the only place that x appears, so this checks the exclusion of self).
    const polygon: Vec2[] = [
      [0, 0],
      [2.13, 0],
      [4, 0],
      [4, 2],
      [0, 2],
    ]
    // Dragging vertex 1 to x=2.2 must not snap back to its own old x.
    expect(vertexAlignSnap(polygon, 1, [2.2, 0.5])).toEqual([2.2, 0.5])
  })

  it('aligns to another room wall through extra planes', () => {
    const polygon: Vec2[] = [
      [3.2, 0],
      [6, 0],
      [6, 4],
      [3.2, 4],
    ]
    const extra = collectAlignPlanes([
      room({
        id: 'other',
        polygon: [
          [0, 0],
          [3.05, 0],
          [3.05, 4],
          [0, 4],
        ],
      }),
    ])
    const snapped = vertexAlignSnap(polygon, 0, [3.08, 0.5], 0.1, ALIGN_THRESHOLD, extra)
    expect(snapped[0]).toBeCloseTo(3.05, 9)
    expect(snapped[1]).toBeCloseTo(0.5, 9)
  })

  it('still grid-snaps an axis with no plane nearby', () => {
    const polygon: Vec2[] = [
      [0, 0],
      [4, 0],
      [4, 3],
      [0, 3],
    ]
    expect(vertexAlignSnap(polygon, 0, [2, 1.44])).toEqual([2, 1.4])
  })
})

describe('draft drawing alignment', () => {
  const planes = collectAlignPlanes([
    room({
      id: 'existing',
      polygon: [
        [0, 0],
        [3.05, 0],
        [3.05, 4],
        [0, 4],
      ],
    }),
  ])

  it('snaps the first point onto an existing wall line', () => {
    expect(snapDraftPoint(undefined, [3.13, 2.0], planes)).toEqual([3.05, 2])
  })

  it('keeps the right angle and snaps the free axis', () => {
    // Horizontal movement from the anchor: x snaps to 3.05, z stays on the anchor.
    expect(snapDraftPoint([0, 0], [3.13, 0.06], planes)).toEqual([3.05, 0])
    // Vertical movement: z snaps to 4 (existing wall), x stays on the anchor.
    expect(snapDraftPoint([5, 4.12], [5.05, 3.95], planes)).toEqual([5, 4])
  })
})

describe('store wiring', () => {
  it('aligns a dragged vertex to a neighbouring room wall', () => {
    const scene = sceneWithRooms([
      {
        id: 'room_a',
        polygon: [
          [0, 0],
          [3.05, 0],
          [3.05, 4],
          [0, 4],
        ],
      },
      {
        id: 'room_b',
        polygon: [
          [3.2, 0],
          [6, 0],
          [6, 4],
          [3.2, 4],
        ],
      },
    ])
    useSceneStore.setState({ scene })
    useSceneStore.getState().startVertexDrag('room_b', 0, [3.2, 0])
    useSceneStore.getState().updateVertexDrag([3.08, 0.5])
    const position = useSceneStore.getState().vertexDrag?.position
    expect(position?.[0]).toBeCloseTo(3.05, 9)
    expect(position?.[1]).toBeCloseTo(0.5, 9)
    useSceneStore.getState().cancelVertexDrag()
  })

  it('snaps a new room corner onto an existing wall', () => {
    useSceneStore.setState({
      scene: sceneWithRooms([
        {
          id: 'room_a',
          polygon: [
            [0, 0],
            [3.05, 0],
            [3.05, 4],
            [0, 4],
          ],
        },
      ]),
    })
    useSceneStore.getState().setDraftCursor([3.13, 2.0])
    expect(useSceneStore.getState().draftCursor?.[0]).toBeCloseTo(3.05, 9)
  })

  it('leaves a free (shift) drag on the grid', () => {
    const scene = sceneWithRooms([
      {
        id: 'room_a',
        polygon: [
          [0, 0],
          [3.05, 0],
          [3.05, 4],
          [0, 4],
        ],
      },
      {
        id: 'room_b',
        polygon: [
          [3.2, 0],
          [6, 0],
          [6, 4],
          [3.2, 4],
        ],
      },
    ])
    useSceneStore.setState({ scene })
    useSceneStore.getState().startVertexDrag('room_b', 0, [3.2, 0])
    useSceneStore.getState().updateVertexDrag([3.08, 0.5], { free: true })
    expect(useSceneStore.getState().vertexDrag?.position).toEqual([3.1, 0.5])
    useSceneStore.getState().cancelVertexDrag()
  })
})
