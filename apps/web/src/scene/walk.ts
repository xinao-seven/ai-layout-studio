import type { CatalogEntry, SceneDocument } from '../contracts/generated/client/rest'
import { edgeInfo, type Obb, obbAxes, obbFromBox, pointAlongEdge, type Vec2 } from './geometry'
import { allowsOverlap, itemFootprint } from './placement'

/** First-person walker constants (PRD FR-20). */
export const WALKER_RADIUS = 0.24
export const WALKER_HEIGHT = 1.6
export const WALK_SPEED = 2.2
export const WALK_RUN_SPEED = 3.6
/** Extra gap around a door span so the walker fits through the doorway. */
const DOOR_GAP_MARGIN = 0.08
const EPSILON = 1e-9

export interface WalkWallCollider {
  a: Vec2
  b: Vec2
  halfThickness: number
}

export interface WalkItemCollider {
  center: Vec2
  size: Vec2
  yaw: number
}

export interface WalkColliders {
  walls: WalkWallCollider[]
  items: WalkItemCollider[]
}

function allowsOverlapKind(catalog: CatalogEntry[], kindId: string): boolean {
  return allowsOverlap(catalog.find((candidate) => candidate.kindId === kindId))
}

interface WallSpan {
  a: Vec2
  b: Vec2
  halfThickness: number
}

function subtractGap(span: WallSpan, gapA: Vec2, gapB: Vec2): WallSpan[] {
  const dx = span.b[0] - span.a[0]
  const dz = span.b[1] - span.a[1]
  const lengthSq = dx * dx + dz * dz
  if (lengthSq <= EPSILON) return [span]
  const length = Math.sqrt(lengthSq)
  const dir: Vec2 = [dx / length, dz / length]
  const normal: Vec2 = [-dir[1], dir[0]]
  const project = (point: Vec2): { t: number; perp: number } => {
    const ox = point[0] - span.a[0]
    const oz = point[1] - span.a[1]
    return { t: ox * dir[0] + oz * dir[1], perp: Math.abs(ox * normal[0] + oz * normal[1]) }
  }
  const start = project(gapA)
  const end = project(gapB)
  // The gap only punches walls lying on the same (collinear) line: the shared
  // wall appears once per adjacent room, and both copies must open.
  const LINE_TOLERANCE = 0.08
  if (start.perp > LINE_TOLERANCE || end.perp > LINE_TOLERANCE) return [span]
  const from = Math.max(0, Math.min(start.t, end.t))
  const to = Math.min(length, Math.max(start.t, end.t))
  if (to - from <= EPSILON) return [span]
  const parts: WallSpan[] = []
  if (from > EPSILON) {
    parts.push({
      a: span.a,
      b: [span.a[0] + dir[0] * from, span.a[1] + dir[1] * from],
      halfThickness: span.halfThickness,
    })
  }
  if (length - to > EPSILON) {
    parts.push({
      a: [span.a[0] + dir[0] * to, span.a[1] + dir[1] * to],
      b: span.b,
      halfThickness: span.halfThickness,
    })
  }
  return parts
}

/**
 * Wall segments (with door gaps) plus furniture footprints as walk blockers.
 * Windows keep their wall: glass blocks the body even though it has no sill gap.
 */
export function buildWalkColliders(scene: SceneDocument, catalog: CatalogEntry[]): WalkColliders {
  const spans: WallSpan[] = []
  for (const room of scene.rooms) {
    const polygon = room.polygon as Vec2[]
    const halfThickness = (room.wallThickness ?? scene.defaults.wallThickness ?? 0.12) / 2
    for (let edgeIndex = 0; edgeIndex < polygon.length; edgeIndex += 1) {
      const edge = edgeInfo(polygon, edgeIndex)
      if (!edge) continue
      spans.push({ a: edge.a, b: edge.b, halfThickness })
    }
  }

  const doorGaps: [Vec2, Vec2][] = []
  for (const opening of scene.openings) {
    if (opening.type !== 'door') continue
    const room = scene.rooms.find((candidate) => candidate.id === opening.roomId)
    if (!room) continue
    const edge = edgeInfo(room.polygon as Vec2[], opening.edgeIndex)
    if (!edge) continue
    const start = Math.max(0, opening.offset - opening.width / 2 - DOOR_GAP_MARGIN)
    const end = Math.min(edge.length, opening.offset + opening.width / 2 + DOOR_GAP_MARGIN)
    if (end - start <= 2 * WALKER_RADIUS) continue
    doorGaps.push([pointAlongEdge(edge, start), pointAlongEdge(edge, end)])
  }

  const walls: WalkWallCollider[] = []
  for (const span of spans) {
    let parts: WallSpan[] = [span]
    for (const [gapA, gapB] of doorGaps) {
      parts = parts.flatMap((part) => subtractGap(part, gapA, gapB))
    }
    for (const part of parts) {
      walls.push({ a: part.a, b: part.b, halfThickness: part.halfThickness })
    }
  }

  const items: WalkItemCollider[] = []
  for (const item of scene.items) {
    if (allowsOverlapKind(catalog, item.kindId)) continue
    const [width, depth] = itemFootprint(item, catalog)
    items.push({
      center: [item.transform.position[0], item.transform.position[2]],
      size: [width, depth],
      yaw: item.transform.rotationY,
    })
  }
  return { walls, items }
}

function distancePointSegment(point: Vec2, a: Vec2, b: Vec2): number {
  const dx = b[0] - a[0]
  const dz = b[1] - a[1]
  const lengthSq = dx * dx + dz * dz
  if (lengthSq <= EPSILON) return Math.hypot(point[0] - a[0], point[1] - a[1])
  const t = Math.max(0, Math.min(1, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dz) / lengthSq))
  return Math.hypot(point[0] - (a[0] + dx * t), point[1] - (a[1] + dz * t))
}

/** Circle-vs-OBB as an inflated OBB point test (good enough for sliding). */
export function circleHitsObb(point: Vec2, obb: Obb, radius: number): boolean {
  const [axisX, axisZ] = obbAxes(obb)
  const dx = point[0] - obb.center[0]
  const dz = point[1] - obb.center[1]
  const localX = dx * axisX[0] + dz * axisX[1]
  const localZ = dx * axisZ[0] + dz * axisZ[1]
  return (
    Math.abs(localX) <= obb.size[0] / 2 + radius && Math.abs(localZ) <= obb.size[1] / 2 + radius
  )
}

export function walkPositionBlocked(colliders: WalkColliders, point: Vec2): boolean {
  for (const wall of colliders.walls) {
    if (distancePointSegment(point, wall.a, wall.b) < WALKER_RADIUS + wall.halfThickness) {
      return true
    }
  }
  for (const item of colliders.items) {
    if (circleHitsObb(point, obbFromBox(item.center, item.size, item.yaw), WALKER_RADIUS)) {
      return true
    }
  }
  return false
}

/**
 * Axis-separated slide: full move first, then x-only, then z-only.
 * Returns the farthest non-blocking position among the three attempts.
 */
export function resolveWalkMove(colliders: WalkColliders, from: Vec2, delta: Vec2): Vec2 {
  if (delta[0] === 0 && delta[1] === 0) return from
  const full: Vec2 = [from[0] + delta[0], from[1] + delta[1]]
  if (!walkPositionBlocked(colliders, full)) return full
  const alongX: Vec2 = [from[0] + delta[0], from[1]]
  if (delta[0] !== 0 && !walkPositionBlocked(colliders, alongX)) return alongX
  const alongZ: Vec2 = [from[0], from[1] + delta[1]]
  if (delta[1] !== 0 && !walkPositionBlocked(colliders, alongZ)) return alongZ
  return from
}

/** Spawn at the centroid of the largest room; falls back to the origin. */
export function walkSpawn(scene: SceneDocument): { position: Vec2; yaw: number } {
  let best: { center: Vec2; area: number } | null = null
  for (const room of scene.rooms) {
    const polygon = room.polygon as Vec2[]
    const area = polygon.reduce((total, point, index) => {
      const next = polygon[(index + 1) % polygon.length] as Vec2
      return total + (point[0] * next[1] - next[0] * point[1])
    }, 0)
    const centroid: Vec2 = [
      polygon.reduce((total, point) => total + point[0], 0) / polygon.length,
      polygon.reduce((total, point) => total + point[1], 0) / polygon.length,
    ]
    const absArea = Math.abs(area / 2)
    if (!best || absArea > best.area) best = { center: centroid, area: absArea }
  }
  const position = best?.center ?? [0, 0]
  // Face the room's first edge so the view starts looking "into" the space.
  const firstRoom = scene.rooms[0]
  const edge = firstRoom ? edgeInfo(firstRoom.polygon as Vec2[], 0) : null
  const target = edge
    ? pointAlongEdge(edge, edge.length / 2)
    : ([position[0], position[1] - 1] as Vec2)
  const yaw = Math.atan2(-(target[0] - position[0]), -(target[1] - position[1]))
  return { position, yaw }
}
