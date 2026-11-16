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
