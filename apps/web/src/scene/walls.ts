import type { Opening, SceneDocument } from '../contracts/generated/client/rest'
import { type Vec2, wallInnerNormal } from './geometry'

const EPS = 1e-6
const TOP_VIEW_WALL_HEIGHT = 0.15

export interface WallEdgeRef {
  roomId: string
  edgeIndex: number
}

export interface WallBox {
  key: string
  center: Vec2
  length: number
  thickness: number
  height: number
  y: number
  rotationY: number
  part: 'wall' | 'door-lintel' | 'window-sill' | 'window-lintel'
  edges: WallEdgeRef[]
}

export interface DoorPanel {
  key: string
  center: Vec2
  width: number
  height: number
  rotationY: number
  thickness: number
}

export interface WindowPanel {
  key: string
  center: Vec2
  width: number
  height: number
  sill: number
  rotationY: number
  thickness: number
}

export interface WallBuildResult {
  walls: WallBox[]
  doors: DoorPanel[]
  windows: WindowPanel[]
}

interface EdgeInfo {
  lineKey: string
  normal: Vec2
  tangent: Vec2
  offset: number
  length: number
  thickness: number
  height: number
  span: [number, number]
}

interface OpeningSpan {
  opening: Opening
  start: number
  end: number
  sill: number
  height: number
}

interface LineGroup {
  key: string
  normal: Vec2
  tangent: Vec2
  offset: number
  thickness: number
  height: number
  spans: [number, number][]
  openings: OpeningSpan[]
  edges: WallEdgeRef[]
}

function canonicalLine(normal: Vec2, a: Vec2): { normal: Vec2; offset: number } {
  let [nx, nz] = normal
  let offset = nx * a[0] + nz * a[1]
  if (nx < -EPS || (Math.abs(nx) <= EPS && nz < -EPS)) {
    nx = -nx
    nz = -nz
    offset = -offset
  }
  return { normal: [nx, nz], offset }
}

function lineKey(normal: Vec2, offset: number): string {
  return `${normal[0].toFixed(3)}:${normal[1].toFixed(3)}:${offset.toFixed(3)}`
}

function dot(point: Vec2, axis: Vec2): number {
  return point[0] * axis[0] + point[1] * axis[1]
}

function pointOnLine(normal: Vec2, tangent: Vec2, offset: number, along: number): Vec2 {
  return [normal[0] * offset + tangent[0] * along, normal[1] * offset + tangent[1] * along]
}

function mergeSpans(spans: [number, number][]): [number, number][] {
  if (spans.length === 0) return []
  const sorted = [...spans].sort((a, b) => a[0] - b[0])
  const merged: [number, number][] = []
  for (const span of sorted) {
    const last = merged[merged.length - 1]
    if (!last || span[0] > last[1] + EPS) {
      merged.push([span[0], span[1]])
    } else {
      last[1] = Math.max(last[1], span[1])
    }
  }
  return merged
}

function subtractSpan(spans: [number, number][], cut: [number, number]): [number, number][] {
  const result: [number, number][] = []
  for (const [start, end] of spans) {
    if (cut[1] <= start + EPS || cut[0] >= end - EPS) {
      result.push([start, end])
      continue
    }
    if (cut[0] > start + EPS) result.push([start, Math.max(start, cut[0])])
    if (cut[1] < end - EPS) result.push([Math.min(end, cut[1]), end])
  }
  return result
}

function edgeInfo(
  document: SceneDocument,
  roomIndex: number,
  edgeIndex: number,
  heightOverride: number | null,
): EdgeInfo | null {
  const room = document.rooms[roomIndex]
  if (!room) return null
  const points = room.polygon as Vec2[]
  const a = points[edgeIndex]
  const b = points[(edgeIndex + 1) % points.length]
  if (!a || !b) return null
  const length = Math.hypot(b[0] - a[0], b[1] - a[1])
  if (length <= EPS) return null
  const inner = wallInnerNormal(a, b)
  const { normal, offset } = canonicalLine(inner, a)
  const tangent: Vec2 = [-normal[1], normal[0]]
  const thickness = room.wallThickness ?? document.defaults.wallThickness ?? 0.12
  const height = heightOverride ?? room.wallHeight ?? document.defaults.wallHeight ?? 2.8
  const start = dot(a, tangent)
  const end = dot(b, tangent)
  return {
    lineKey: lineKey(normal, offset),
    normal,
    tangent,
    offset,
    length,
    thickness,
    height,
    span: [Math.min(start, end), Math.max(start, end)],
  }
}

function windowSill(opening: Opening): number {
  if (opening.type !== 'window') return 0
  return opening.sillHeight ?? 0.9
}

/**
 * Build render-ready wall and opening boxes.
 *
 * Wall edges from adjacent rooms are grouped by their infinite line and their
 * covered intervals are unioned, so a shared wall renders once. Openings from
 * either side cut the union; lintels/sills fill the vertical remainder.
 */
export function buildWalls(
  document: SceneDocument,
  options: { topView?: boolean } = {},
): WallBuildResult {
  const topView = options.topView === true
  const heightOverride = topView ? TOP_VIEW_WALL_HEIGHT : null
  const groups = new Map<string, LineGroup>()
  const edgeKeys = new Map<string, string>()

  document.rooms.forEach((room, roomIndex) => {
    const points = room.polygon as Vec2[]
    points.forEach((_, edgeIndex) => {
      const info = edgeInfo(document, roomIndex, edgeIndex, heightOverride)
      if (!info) return
      const key = info.lineKey
      edgeKeys.set(`${room.id}:e${edgeIndex}`, key)
      const group = groups.get(key) ?? {
        key,
        normal: info.normal,
        tangent: info.tangent,
        offset: info.offset,
        thickness: info.thickness,
        height: info.height,
        spans: [],
        openings: [],
        edges: [],
      }
      group.thickness = Math.max(group.thickness, info.thickness)
      group.height = Math.max(group.height, info.height)
      group.spans.push(info.span)
      if (!group.edges.some((entry) => entry.roomId === room.id && entry.edgeIndex === edgeIndex)) {
        group.edges.push({ roomId: room.id, edgeIndex })
      }
      groups.set(key, group)
    })
  })

  for (const opening of document.openings) {
    const key = edgeKeys.get(`${opening.roomId}:e${opening.edgeIndex}`)
    const group = key ? groups.get(key) : undefined
    if (!group) continue
    const centerAlong = dot(
      (() => {
        const room = document.rooms.find((candidate) => candidate.id === opening.roomId)
        if (!room) return [0, 0] as Vec2
        const points = room.polygon as Vec2[]
        const a = points[opening.edgeIndex] as Vec2
        const b = points[(opening.edgeIndex + 1) % points.length] as Vec2
        const length = Math.hypot(b[0] - a[0], b[1] - a[1])
        const ratio = length <= EPS ? 0 : opening.offset / length
        return [a[0] + (b[0] - a[0]) * ratio, a[1] + (b[1] - a[1]) * ratio] as Vec2
      })(),
      group.tangent,
    )
    group.openings.push({
      opening,
      start: centerAlong - opening.width / 2,
      end: centerAlong + opening.width / 2,
      sill: windowSill(opening),
      height: opening.height,
    })
  }

  const walls: WallBox[] = []
  const doors: DoorPanel[] = []
  const windows: WindowPanel[] = []
  const seenOpenings = new Set<string>()

  for (const group of groups.values()) {
    let solidSpans = mergeSpans(group.spans)
    const activeOpenings = topView
      ? group.openings.filter((opening) => opening.opening.type === 'door')
      : group.openings
    for (const opening of activeOpenings) {
      solidSpans = subtractSpan(solidSpans, [opening.start, opening.end])
    }

    solidSpans.forEach(([start, end], index) => {
      const length = end - start
      if (length <= EPS) return
      walls.push({
        key: `${group.key}:wall:${index}`,
        center: pointOnLine(group.normal, group.tangent, group.offset, (start + end) / 2),
        length,
        thickness: group.thickness,
        height: group.height,
        y: group.height / 2,
        rotationY: Math.atan2(-group.tangent[1], group.tangent[0]),
        part: 'wall',
        edges: group.edges,
      })
    })

    if (topView) continue

    for (const opening of group.openings) {
      const openingKey = `${opening.opening.id}:${group.key}`
      if (seenOpenings.has(openingKey)) continue
      seenOpenings.add(openingKey)
      const width = opening.end - opening.start
      if (width <= EPS) continue
      const center = pointOnLine(
        group.normal,
        group.tangent,
        group.offset,
        (opening.start + opening.end) / 2,
      )
      const rotationY = Math.atan2(-group.tangent[1], group.tangent[0])
      const wallHeight = group.height

      if (opening.opening.type === 'door') {
        doors.push({
          key: `${openingKey}:door`,
          center,
          width,
          height: opening.height,
          rotationY,
          thickness: group.thickness,
        })
        if (opening.height < wallHeight - EPS) {
          walls.push({
            key: `${openingKey}:lintel`,
            center,
            length: width,
            thickness: group.thickness,
            height: wallHeight - opening.height,
            y: (wallHeight + opening.height) / 2,
            rotationY,
            part: 'door-lintel',
            edges: group.edges,
          })
        }
      } else {
        windows.push({
          key: `${openingKey}:window`,
          center,
          width,
          height: opening.height,
          sill: opening.sill,
          rotationY,
          thickness: group.thickness,
        })
        if (opening.sill > EPS) {
          walls.push({
            key: `${openingKey}:sill`,
            center,
            length: width,
            thickness: group.thickness,
            height: opening.sill,
            y: opening.sill / 2,
            rotationY,
            part: 'window-sill',
            edges: group.edges,
          })
        }
        const top = opening.sill + opening.height
        if (top < wallHeight - EPS) {
          walls.push({
            key: `${openingKey}:lintel`,
            center,
            length: width,
            thickness: group.thickness,
            height: wallHeight - top,
            y: (wallHeight + top) / 2,
            rotationY,
            part: 'window-lintel',
            edges: group.edges,
          })
        }
      }
    }
  }

  return { walls, doors, windows }
}
