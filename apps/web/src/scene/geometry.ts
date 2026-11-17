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

/** Axis-aligned bounding box as [minX, minZ, maxX, maxZ]. */
export function polygonBounds(points: Vec2[]): [number, number, number, number] {
  let minX = Number.POSITIVE_INFINITY
  let minZ = Number.POSITIVE_INFINITY
  let maxX = Number.NEGATIVE_INFINITY
  let maxZ = Number.NEGATIVE_INFINITY
  for (const [x, z] of points) {
    minX = Math.min(minX, x)
    minZ = Math.min(minZ, z)
    maxX = Math.max(maxX, x)
    maxZ = Math.max(maxZ, z)
  }
  return [minX, minZ, maxX, maxZ]
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

function pointSegmentDistance(point: Vec2, a: Vec2, b: Vec2): number {
  const dx = b[0] - a[0]
  const dz = b[1] - a[1]
  const lengthSq = dx * dx + dz * dz
  if (lengthSq <= EPSILON * EPSILON) return distance(point, a)
  const t = Math.max(0, Math.min(1, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dz) / lengthSq))
  return Math.hypot(point[0] - (a[0] + dx * t), point[1] - (a[1] + dz * t))
}

/**
 * Shortest distance between two OBB footprints on the floor plane.
 *
 * Returns 0 whenever the footprints intersect *or touch*. The walkway rule
 * refuses any strictly positive gap below the passage width, so flush must read
 * as zero — and a tiny interpenetration (which the collision test tolerates)
 * must read as zero too, matching the server's polygon distance.
 */
export function obbDistance(a: Obb, b: Obb): number {
  // tolerance 0: touching edges already count as intersecting.
  if (obbOverlap(a, b, 0)) return 0
  const cornersA = obbCorners(a)
  const cornersB = obbCorners(b)
  let best = Number.POSITIVE_INFINITY
  for (let i = 0; i < cornersA.length; i += 1) {
    const a0 = cornersA[i] as Vec2
    const a1 = cornersA[(i + 1) % cornersA.length] as Vec2
    for (let j = 0; j < cornersB.length; j += 1) {
      const b0 = cornersB[j] as Vec2
      const b1 = cornersB[(j + 1) % cornersB.length] as Vec2
      best = Math.min(best, pointSegmentDistance(a0, b0, b1), pointSegmentDistance(b0, a0, a1))
    }
  }
  return best
}

export const GRID_STEP = rules.GRID_SNAP
export const MANUAL_ROTATION_RADIANS = (rules.MANUAL_ROTATION_STEP * Math.PI) / 180

export function snapValue(value: number, step = GRID_STEP): number {
  // 消除 Math.round(v/step)*step 的浮点尘埃(如 1.2000000000000002),保证吸附后坐标可精确比较。
  return Number((Math.round(value / step) * step).toFixed(4))
}

export function snapPoint(point: Vec2, step = GRID_STEP): Vec2 {
  return [snapValue(point[0], step), snapValue(point[1], step)]
}

/**
 * Alignment snap radius in metres. Deliberately larger than the 0.1 m grid step
 * so that dragging a vertex near another room's wall bites reliably instead of
 * landing one grid cell off.
 */
export const ALIGN_THRESHOLD = 0.25

export interface AlignPlanes {
  xs: number[]
  zs: number[]
}

export interface VertexRef {
  roomId: string
  vertexIndex: number
}

/**
 * Every vertex coordinate in the scene, split into x/z planes.
 *
 * Walls line up when their centerline vertices share a coordinate, so a vertex
 * dragged near any existing corner snaps onto that exact value. The vertex being
 * dragged is excluded so it cannot snap onto itself.
 */
export function collectAlignPlanes(
  rooms: ReadonlyArray<{ id: string; polygon: Vec2[] }>,
  exclude?: VertexRef | VertexRef[],
): AlignPlanes {
  const excluded = new Set(
    (Array.isArray(exclude) ? exclude : exclude ? [exclude] : []).map(
      (ref) => `${ref.roomId}:${ref.vertexIndex}`,
    ),
  )
  const xs: number[] = []
  const zs: number[] = []
  for (const room of rooms) {
    room.polygon.forEach((vertex, index) => {
      if (excluded.has(`${room.id}:${index}`)) return
      xs.push(vertex[0])
      zs.push(vertex[1])
    })
  }
  return { xs, zs }
}

/** Snap to the nearest candidate plane, falling back to the grid. */
export function alignValue(
  value: number,
  candidates: number[],
  step = GRID_STEP,
  threshold = ALIGN_THRESHOLD,
): number {
  let best: number | null = null
  let bestDelta = threshold
  for (const candidate of candidates) {
    const delta = Math.abs(value - candidate)
    if (delta <= bestDelta) {
      best = candidate
      bestDelta = delta
    }
  }
  return best ?? snapValue(value, step)
}

export function snapToPlanes(
  point: Vec2,
  planes: AlignPlanes,
  step = GRID_STEP,
  threshold = ALIGN_THRESHOLD,
): Vec2 {
  return [
    alignValue(point[0], planes.xs, step, threshold),
    alignValue(point[1], planes.zs, step, threshold),
  ]
}

/** 直角吸附:把 from→point 的线段锁定到最近的水平/垂直方向,移动轴坐标做网格吸附。 */
export function orthoSnap(from: Vec2, point: Vec2, step = GRID_STEP): Vec2 {
  if (Math.abs(point[0] - from[0]) >= Math.abs(point[1] - from[1])) {
    return [snapValue(point[0], step), from[1]]
  }
  return [from[0], snapValue(point[1], step)]
}

/**
 * 顶点拖拽吸附:光标靠近任意“共横/共纵”的既有顶点时吸附过去,否则网格吸附。
 *
 * Candidates come from this polygon's other vertices plus any ``extra`` planes
 * (normally the rest of the scene), which is what lets two rooms share a wall
 * exactly instead of landing 5 cm apart on different grid cells.
 */
export function vertexAlignSnap(
  polygon: Vec2[],
  index: number,
  point: Vec2,
  step = GRID_STEP,
  threshold = ALIGN_THRESHOLD,
  extra?: AlignPlanes,
): Vec2 {
  const xs: number[] = extra ? [...extra.xs] : []
  const zs: number[] = extra ? [...extra.zs] : []
  polygon.forEach((vertex, vertexIndex) => {
    if (vertexIndex === index) return
    xs.push(vertex[0])
    zs.push(vertex[1])
  })
  return [alignValue(point[0], xs, step, threshold), alignValue(point[1], zs, step, threshold)]
}

/**
 * 画房间时的落点吸附:与上一点保持直角,非锁定轴优先吸附到既有墙线,再退回网格。
 */
export function snapDraftPoint(
  anchor: Vec2 | undefined,
  point: Vec2,
  planes: AlignPlanes,
  step = GRID_STEP,
  threshold = ALIGN_THRESHOLD,
): Vec2 {
  if (!anchor) return snapToPlanes(point, planes, step, threshold)
  const horizontal = Math.abs(point[0] - anchor[0]) >= Math.abs(point[1] - anchor[1])
  return horizontal
    ? [alignValue(point[0], planes.xs, step, threshold), anchor[1]]
    : [anchor[0], alignValue(point[1], planes.zs, step, threshold)]
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
