import fixtures from '@ai-layout/contracts/geometry-fixtures.json'
import { describe, expect, it } from 'vitest'
import {
  type Obb,
  obbOverlap,
  orthoSnap,
  pointInPolygon,
  signedArea,
  type Vec2,
  vertexAlignSnap,
  wallInnerNormal,
} from './geometry'

const polygons = fixtures.polygons as unknown as Record<string, Vec2[]>

function polygon(id: string): Vec2[] {
  return polygons[id] as Vec2[]
}

describe('shared geometry fixtures (client runner)', () => {
  it('computes the signed area and winding', () => {
    for (const testCase of fixtures.signedAreaCases) {
      const points = polygon(testCase.polygonId)
      expect(signedArea(points)).toBeCloseTo(testCase.expected, 9)
      expect(signedArea(points) > 0).toBe(testCase.ccw)
    }
  })

  it('matches point-in-polygon vectors', () => {
    for (const testCase of fixtures.pointCases) {
      expect(pointInPolygon(testCase.point as Vec2, polygon(testCase.polygonId))).toBe(
        testCase.inside,
      )
    }
  })

  it('derives inward wall normals', () => {
    for (const testCase of fixtures.wallCases) {
      const points = polygon(testCase.polygonId)
      const a = points[testCase.edgeIndex] as Vec2
      const b = points[(testCase.edgeIndex + 1) % points.length] as Vec2
      const normal = wallInnerNormal(a, b)
      const expected = testCase.normal as [number, number]
      expect(normal[0]).toBeCloseTo(expected[0], 9)
      expect(normal[1]).toBeCloseTo(expected[1], 9)
    }
  })

  it('matches OBB SAT overlap vectors', () => {
    for (const testCase of fixtures.obbCases) {
      const a: Obb = {
        center: testCase.a.center as Vec2,
        size: testCase.a.size as Vec2,
        yaw: testCase.a.yaw,
      }
      const b: Obb = {
        center: testCase.b.center as Vec2,
        size: testCase.b.size as Vec2,
        yaw: testCase.b.yaw,
      }
      expect(obbOverlap(a, b)).toBe(testCase.overlap)
      expect(obbOverlap(b, a)).toBe(testCase.overlap)
    }
  })
})

describe('room snap helpers', () => {
  it('ortho-snaps to the dominant axis and keeps the other coordinate exact', () => {
    expect(orthoSnap([0, 0], [2.34, 0.42])).toEqual([2.3, 0])
    expect(orthoSnap([0, 0], [0.42, 2.34])).toEqual([0, 2.3])
    expect(orthoSnap([1.23, 2.47], [1.31, 2.52])).toEqual([1.3, 2.47])
    expect(orthoSnap([0, 0], [3, 0.09])).toEqual([3, 0])
    expect(orthoSnap([0, 0], [0.09, 3])).toEqual([0, 3])
  })

  it('snaps a draw point to the grid when no axis wins', () => {
    expect(orthoSnap([0, 0], [0, 0])).toEqual([0, 0])
  })

  it('vertexAlignSnap restores right angles near neighbors', () => {
    const square: Vec2[] = [
      [0, 0],
      [4, 0],
      [4, 3],
      [0, 3],
    ]
    // vertex 0: prev = [0, 3], next = [4, 0] → cursor near the corner realigns both axes
    expect(vertexAlignSnap(square, 0, [0.12, 0.06])).toEqual([0, 0])
    expect(vertexAlignSnap(square, 0, [0.12, 1])).toEqual([0, 1])
    expect(vertexAlignSnap(square, 0, [2, 0.08])).toEqual([2, 0])
    // nothing within threshold → plain grid snap
    expect(vertexAlignSnap(square, 0, [2, 1.44])).toEqual([2, 1.4])
  })

  it('vertexAlignSnap keeps aligned coordinates exact even off-grid', () => {
    const polygon: Vec2[] = [
      [1.05, 0],
      [4, 0],
      [4, 3],
    ]
    // x would grid-snap to 1.0/1.1, but alignment with prev.x wins and stays exact
    expect(vertexAlignSnap(polygon, 1, [1.12, 0.07])).toEqual([1.05, 0])
  })
})
