import fixtures from '@ai-layout/contracts/geometry-fixtures.json'
import { describe, expect, it } from 'vitest'
import {
  type Obb,
  obbOverlap,
  pointInPolygon,
  signedArea,
  type Vec2,
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
