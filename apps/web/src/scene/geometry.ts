import rules from '@ai-layout/contracts/rules.json'

export type Vec2 = [number, number]

export interface Obb {
  center: Vec2
  size: Vec2
  yaw: number
}

const EPSILON = 1e-9

export function signedArea(points: Vec2[]): number {
  let total = 0
  for (let index = 0; index < points.length; index += 1) {
    const [x1, z1] = points[index] as Vec2
    const [x2, z2] = points[(index + 1) % points.length] as Vec2
    total += x1 * z2 - x2 * z1
  }
  return total / 2
}

export function polygonArea(points: Vec2[]): number {
  return Math.abs(signedArea(points))
}

export function isCcw(points: Vec2[]): boolean {
  return signedArea(points) > 0
}

export function polygonCentroid(points: Vec2[]): Vec2 {
  if (points.length < 3) return [0, 0]
  let doubleArea = 0
  let x = 0
  let z = 0
  for (let index = 0; index < points.length; index += 1) {
    const [x1, z1] = points[index] as Vec2
    const [x2, z2] = points[(index + 1) % points.length] as Vec2
    const cross = x1 * z2 - x2 * z1
    doubleArea += cross
    x += (x1 + x2) * cross
    z += (z1 + z2) * cross
  }
  if (Math.abs(doubleArea) <= EPSILON) return points[0] as Vec2
  return [x / (3 * doubleArea), z / (3 * doubleArea)]
}

function pointOnSegment(point: Vec2, a: Vec2, b: Vec2): boolean {
  const cross = (b[0] - a[0]) * (point[1] - a[1]) - (b[1] - a[1]) * (point[0] - a[0])
  if (Math.abs(cross) > 1e-9) return false
  const dot = (point[0] - a[0]) * (point[0] - b[0]) + (point[1] - a[1]) * (point[1] - b[1])
  return dot <= 1e-9
}

export function pointInPolygon(point: Vec2, polygon: Vec2[]): boolean {
  if (polygon.length < 3) return false
  for (let index = 0; index < polygon.length; index += 1) {
    const a = polygon[index] as Vec2
    const b = polygon[(index + 1) % polygon.length] as Vec2
    if (pointOnSegment(point, a, b)) return true
  }
  let inside = false
  for (let index = 0, previous = polygon.length - 1; index < polygon.length; previous = index++) {
    const [xi, zi] = polygon[index] as Vec2
    const [xj, zj] = polygon[previous] as Vec2
    const intersects = zi > point[1] !== zj > point[1]
    if (intersects && point[0] < ((xj - xi) * (point[1] - zi)) / (zj - zi) + xi) {
      inside = !inside
    }
  }
  return inside
}

export function wallInnerNormal(a: Vec2, b: Vec2): Vec2 {
  const dx = b[0] - a[0]
  const dz = b[1] - a[1]
  const length = Math.hypot(dx, dz)
  if (length <= EPSILON) return [0, 0]
  return [-dz / length, dx / length]
}

export function obbAxes(obb: Obb): [Vec2, Vec2] {
  const cos = Math.cos(obb.yaw)
  const sin = Math.sin(obb.yaw)
  return [
    [cos, -sin],
    [sin, cos],
  ]
}

export function obbCorners(obb: Obb): Vec2[] {
  const [axisX, axisZ] = obbAxes(obb)
  const halfW = obb.size[0] / 2
  const halfD = obb.size[1] / 2
  const [cx, cz] = obb.center
  const signs: Vec2[] = [
    [-1, -1],
    [1, -1],
    [1, 1],
    [-1, 1],
  ]
  return signs.map(([sx, sz]) => [
    cx + axisX[0] * halfW * sx + axisZ[0] * halfD * sz,
    cz + axisX[1] * halfW * sx + axisZ[1] * halfD * sz,
  ])
}

function project(points: Vec2[], axis: Vec2): [number, number] {
  let min = Number.POSITIVE_INFINITY
  let max = Number.NEGATIVE_INFINITY
  for (const point of points) {
    const value = point[0] * axis[0] + point[1] * axis[1]
    min = Math.min(min, value)
    max = Math.max(max, value)
  }
  return [min, max]
}

export function obbOverlap(a: Obb, b: Obb, tolerance = EPSILON): boolean {
  const cornersA = obbCorners(a)
  const cornersB = obbCorners(b)
  for (const axis of [...obbAxes(a), ...obbAxes(b)]) {
    const [minA, maxA] = project(cornersA, axis)
    const [minB, maxB] = project(cornersB, axis)
    if (maxA < minB - tolerance || maxB < minA - tolerance) return false
  }
  return true
}

export function obbCornersInsidePolygon(obb: Obb, polygon: Vec2[]): boolean {
  return obbCorners(obb).every((corner) => pointInPolygon(corner, polygon))
}

export const GRID_STEP = rules.GRID_SNAP
export const MANUAL_ROTATION_RADIANS = (rules.MANUAL_ROTATION_STEP * Math.PI) / 180

export function snapValue(value: number, step = GRID_STEP): number {
  return Math.round(value / step) * step
}

export function snapPoint(point: Vec2, step = GRID_STEP): Vec2 {
  return [snapValue(point[0], step), snapValue(point[1], step)]
}

export function distance(a: Vec2, b: Vec2): number {
  return Math.hypot(a[0] - b[0], a[1] - b[1])
}

function cross(a: Vec2, b: Vec2): number {
  return a[0] * b[1] - a[1] * b[0]
}

function subtract(a: Vec2, b: Vec2): Vec2 {
  return [a[0] - b[0], a[1] - b[1]]
}

function normalize(vector: Vec2): Vec2 {
  const length = Math.hypot(vector[0], vector[1])
  if (length <= EPSILON) return [0, 0]
  return [vector[0] / length, vector[1] / length]
}

function lineIntersection(
  pointA: Vec2,
  directionA: Vec2,
  pointB: Vec2,
  directionB: Vec2,
): Vec2 | null {
  const denominator = cross(directionA, directionB)
  if (Math.abs(denominator) <= 1e-9) return null
  const offset = subtract(pointB, pointA)
  const t = cross(offset, directionB) / denominator
  return [pointA[0] + directionA[0] * t, pointA[1] + directionA[1] * t]
}

/**
 * Inward offset of a CCW polygon (miter joins).
 *
 * Client-side room-bound precheck only; the server uses Shapely for the
 * authoritative inset. Returns null when the offset collapses.
 */
export function insetPolygon(points: Vec2[], distanceMeters: number): Vec2[] | null {
  if (points.length < 3) return null
  const ccw = isCcw(points) ? points : [...points].reverse()
  const lines = ccw.map((a, index) => {
    const b = ccw[(index + 1) % ccw.length] as Vec2
    const tangent = normalize(subtract(b, a))
    const inward: Vec2 = [-tangent[1], tangent[0]]
    return {
      point: [a[0] + inward[0] * distanceMeters, a[1] + inward[1] * distanceMeters] as Vec2,
      direction: tangent,
    }
  })
  const result: Vec2[] = []
  for (let index = 0; index < lines.length; index += 1) {
    const previous = lines[(index - 1 + lines.length) % lines.length]
    const current = lines[index]
    if (!previous || !current) return null
    const vertex = lineIntersection(
      previous.point,
      previous.direction,
      current.point,
      current.direction,
    )
    if (vertex === null) return null
    result.push(vertex)
  }
  return polygonArea(result) > EPSILON ? result : null
}

function segmentsIntersect(a: Vec2, b: Vec2, c: Vec2, d: Vec2): boolean {
  const ab = subtract(b, a)
  const cd = subtract(d, c)
  const denominator = cross(ab, cd)
  if (Math.abs(denominator) <= 1e-9) return false
  const t = cross(subtract(c, a), cd) / denominator
  const u = cross(subtract(c, a), ab) / denominator
  return t > 1e-9 && t < 1 - 1e-9 && u > 1e-9 && u < 1 - 1e-9
}

/** True when the polygon has at least three distinct vertices and no crossing. */
export function isSimplePolygon(points: Vec2[]): boolean {
  if (points.length < 3) return false
  const unique = new Set(points.map(([x, z]) => `${x.toFixed(6)}:${z.toFixed(6)}`))
  if (unique.size !== points.length) return false
  const count = points.length
  for (let first = 0; first < count; first += 1) {
    const firstNext = (first + 1) % count
    for (let second = first + 1; second < count; second += 1) {
      const secondNext = (second + 1) % count
      if (
        first === second ||
        firstNext === second ||
        secondNext === first ||
        (first === 0 && secondNext === 0)
      ) {
        continue
      }
      const a = points[first] as Vec2
      const b = points[firstNext] as Vec2
      const c = points[second] as Vec2
      const d = points[secondNext] as Vec2
      if (segmentsIntersect(a, b, c, d)) return false
    }
  }
  return true
}

export interface EdgeInfo {
  a: Vec2
  b: Vec2
  tangent: Vec2
  normal: Vec2
  length: number
}

export function edgeInfo(points: Vec2[], edgeIndex: number): EdgeInfo | null {
  if (points.length < 3 || edgeIndex < 0 || edgeIndex >= points.length) return null
  const a = points[edgeIndex] as Vec2
  const b = points[(edgeIndex + 1) % points.length] as Vec2
  const dx = b[0] - a[0]
  const dz = b[1] - a[1]
  const length = Math.hypot(dx, dz)
  if (length <= EPSILON) return null
  const tangent: Vec2 = [dx / length, dz / length]
  return { a, b, tangent, normal: [-tangent[1], tangent[0]], length }
}

export function pointAlongEdge(edge: EdgeInfo, offset: number): Vec2 {
  return [edge.a[0] + edge.tangent[0] * offset, edge.a[1] + edge.tangent[1] * offset]
}

/** Rotation that aligns an OBB's local +X axis with the edge tangent. */
export function yawAlongEdge(edge: EdgeInfo): number {
  return Math.atan2(-edge.tangent[1], edge.tangent[0])
}

export function obbFromBox(center: Vec2, size: Vec2, yaw = 0): Obb {
  return { center, size, yaw }
}
