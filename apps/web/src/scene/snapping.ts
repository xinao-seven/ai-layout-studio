import rules from '@ai-layout/contracts/rules.json'
import type { CatalogEntry, SceneDocument } from '../contracts/generated/client/rest'
import {
  GRID_STEP,
  insetPolygon,
  obbCorners,
  obbCornersInsidePolygon,
  obbFromBox,
  pointInPolygon,
  snapPoint,
  snapValue,
  type Vec2,
} from './geometry'
import { itemFootprint, variantFor } from './placement'

/**
 * Edge snapping distance. Inside this range the item jumps to the neighbouring
 * face (wall inset or furniture edge) instead of the 0.1 m grid, so tight
 * layouts can actually reach a flush position.
 */
export const EDGE_SNAP_THRESHOLD = 0.25

const ALIGN_EPS = 1e-6

export interface SnapInput {
  scene: SceneDocument
  catalog: CatalogEntry[]
  kindId: string
  variantId?: string | null
  scale?: number
  rotationY: number
  ignoreItemId?: string | null
}

interface AxisOption {
  value: number
  delta: number
}

function isAxisAligned(yaw: number): boolean {
  const cos = Math.abs(Math.cos(yaw))
  const sin = Math.abs(Math.sin(yaw))
  return cos < ALIGN_EPS || sin < ALIGN_EPS
}

function roomFor(scene: SceneDocument, point: Vec2) {
  return scene.rooms.find((room) => pointInPolygon(point, room.polygon as Vec2[]))
}

function pushAxisPlanes(polygon: Vec2[], xs: number[], zs: number[]): void {
  for (let index = 0; index < polygon.length; index += 1) {
    const a = polygon[index] as Vec2
    const b = polygon[(index + 1) % polygon.length] as Vec2
    if (Math.abs(a[0] - b[0]) < ALIGN_EPS) xs.push(a[0])
    if (Math.abs(a[1] - b[1]) < ALIGN_EPS) zs.push(a[1])
  }
}

function axisOptions(raw: number, half: number, planes: number[]): AxisOption[] {
  const options: AxisOption[] = []
  const lowEdge = raw - half
  const highEdge = raw + half
  for (const plane of planes) {
    // Sit on the +, then the - side of the plane; rank by how close the facing
    // edge already is, not by how far the centre would jump.
    const above = Math.abs(lowEdge - plane)
    if (above <= EDGE_SNAP_THRESHOLD) options.push({ value: plane + half, delta: above })
    const below = Math.abs(highEdge - plane)
    if (below <= EDGE_SNAP_THRESHOLD) options.push({ value: plane - half, delta: below })
  }
  options.sort((first, second) => first.delta - second.delta)
  // Grid snap is the fallback, ranked after every in-range edge candidate so a
  // mixed axis (one snapped, one free) still prefers the edge.
  options.push({ value: snapValue(raw, GRID_STEP), delta: EDGE_SNAP_THRESHOLD + 1 })
  return options
}

/**
 * Resolve a dragged item position to the nearest legal edge plane or grid point.
 *
 * Rotation is snapped only for axis-aligned orientations (0/90/180/270), which
 * is where flush placement matters; other angles fall back to the grid so the
 * result never lands in a strange half-overlapping pose.
 */
export function resolveSnap(input: SnapInput, raw: Vec2): Vec2 {
  const variant = variantFor(input.catalog, input.kindId, input.variantId)
  if (!variant || !isAxisAligned(input.rotationY)) return snapPoint(raw)

  const scale = input.scale ?? 1
  const size: Vec2 = [variant.sizeM[0] * scale, variant.sizeM[1] * scale]
  const room = roomFor(input.scene, raw)
  const inset = room ? insetPolygon(room.polygon as Vec2[], rules.ROOM_INSET) : null

  const xs: number[] = []
  const zs: number[] = []
  if (inset) pushAxisPlanes(inset, xs, zs)

  for (const item of input.scene.items) {
    if (item.id === input.ignoreItemId) continue
    if (!isAxisAligned(item.transform.rotationY)) continue
    const center: Vec2 = [item.transform.position[0], item.transform.position[2]]
    const itemRoom = roomFor(input.scene, center)
    if (room && itemRoom && itemRoom.id !== room.id) continue
    const corners = obbCorners(
      obbFromBox(center, itemFootprint(item, input.catalog), item.transform.rotationY),
    )
    const itemXs = corners.map((corner) => corner[0])
    const itemZs = corners.map((corner) => corner[1])
    xs.push(Math.min(...itemXs), Math.max(...itemXs))
    zs.push(Math.min(...itemZs), Math.max(...itemZs))
  }

  // A 90/270 degree item has its catalog width along world Z.
  const swapped = Math.abs(Math.cos(input.rotationY)) < ALIGN_EPS
  const halfX = (swapped ? size[1] : size[0]) / 2
  const halfZ = (swapped ? size[0] : size[1]) / 2

  const xOptions = axisOptions(raw[0], halfX, xs)
  const zOptions = axisOptions(raw[1], halfZ, zs)

  let best: Vec2 | null = null
  let bestDelta = Number.POSITIVE_INFINITY
  for (const x of xOptions) {
    for (const z of zOptions) {
      const total = x.delta + z.delta
      if (total >= bestDelta) continue
      const candidate: Vec2 = [x.value, z.value]
      if (inset) {
        const box = obbFromBox(candidate, size, input.rotationY)
        if (!obbCornersInsidePolygon(box, inset)) continue
      }
      best = candidate
      bestDelta = total
    }
  }
  return best ?? snapPoint(raw)
}
