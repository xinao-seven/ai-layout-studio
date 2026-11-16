import rules from '@ai-layout/contracts/rules.json'
import type { CatalogEntry, Item, SceneDocument } from '../contracts/generated/client/rest'
import {
  edgeInfo,
  insetPolygon,
  type Obb,
  obbCorners,
  obbFromBox,
  obbOverlap,
  pointAlongEdge,
  pointInPolygon,
  type Vec2,
  yawAlongEdge,
} from './geometry'

export interface PlacementIssue {
  code: string
  message: string
  suggest?: string
}

export interface PlacementCheck {
  valid: boolean
  roomId: string | null
  issue?: PlacementIssue
}

export interface PlacementRequest {
  kindId: string
  variantId?: string | null
  position: Vec2
  rotationY: number
  ignoreItemId?: string | null
}

const COLLISION_TOLERANCE = -0.005

export function catalogEntry(catalog: CatalogEntry[], kindId: string): CatalogEntry | undefined {
  return catalog.find((candidate) => candidate.kindId === kindId)
}

export function variantFor(
  catalog: CatalogEntry[],
  kindId: string,
  variantId?: string | null,
): CatalogEntry['variants'][number] | undefined {
  const entry = catalogEntry(catalog, kindId)
  return entry?.variants.find((candidate) => candidate.id === variantId) ?? entry?.variants[0]
}

export function itemFootprint(item: Item, catalog: CatalogEntry[]): [number, number] {
  const variant = variantFor(catalog, item.kindId, item.variantId)
  const size = variant?.sizeM ?? [0.8, 0.8, 0.8]
  return [size[0], size[1]]
}

export function allowsOverlap(entry: CatalogEntry | undefined): boolean {
  return entry?.rules?.allowOverlap === true
}

function obbFor(position: Vec2, size: [number, number], rotationY: number): Obb {
  return obbFromBox(position, size, rotationY)
}

export function checkPlacement(
  scene: SceneDocument,
  catalog: CatalogEntry[],
  request: PlacementRequest,
): PlacementCheck {
  const entry = catalogEntry(catalog, request.kindId)
  if (!entry) {
    return {
      valid: false,
      roomId: null,
      issue: {
        code: 'unknown_kind',
        message: `目录中没有 ${request.kindId}`,
        suggest: '刷新目录后重试。',
      },
    }
  }
  const variant = variantFor(catalog, request.kindId, request.variantId)
  if (!variant) {
    return {
      valid: false,
      roomId: null,
      issue: {
        code: 'variant_missing',
        message: `该家具缺少变体 ${request.variantId ?? ''}`,
        suggest: '选择默认变体。',
      },
    }
  }

  const room = scene.rooms.find((candidate) =>
    pointInPolygon(request.position, candidate.polygon as Vec2[]),
  )
  if (!room) {
    return {
      valid: false,
      roomId: null,
      issue: {
        code: 'out_of_room',
        message: '家具位置不在任何房间内。',
        suggest: '把家具拖到房间地板上。',
      },
    }
  }

  const footprint: [number, number] = [variant.sizeM[0], variant.sizeM[1]]
  const box = obbFor(request.position, footprint, request.rotationY)
  const inset = insetPolygon(room.polygon as Vec2[], rules.ROOM_INSET)
  if (!inset) {
    return {
      valid: false,
      roomId: room.id,
      issue: {
        code: 'room_too_small',
        message: '这个房间太小，放不下该家具。',
        suggest: '扩大房间或换一件更小的家具。',
      },
    }
  }
  if (!obbCorners(box).every((corner) => pointInPolygon(corner, inset))) {
    return {
      valid: false,
      roomId: room.id,
      issue: {
        code: 'out_of_room',
        message: '家具超出房间边界或贴墙太紧。',
        suggest: '向房间内部移动一点。',
      },
    }
  }

  for (const item of scene.items) {
    if (item.id === request.ignoreItemId) continue
    const otherEntry = catalogEntry(catalog, item.kindId)
    if (allowsOverlap(entry) || allowsOverlap(otherEntry)) continue
    const otherRoomId =
      scene.rooms.find((candidate) =>
        pointInPolygon(
          [item.transform.position[0], item.transform.position[2]],
          candidate.polygon as Vec2[],
        ),
      )?.id ??
      item.constraint?.roomId ??
      null
    if (otherRoomId !== null && otherRoomId !== room.id) continue
    const otherBox = obbFor(
      [item.transform.position[0], item.transform.position[2]],
      itemFootprint(item, catalog),
      item.transform.rotationY,
    )
    if (obbOverlap(box, otherBox, COLLISION_TOLERANCE)) {
      return {
        valid: false,
        roomId: room.id,
        issue: {
          code: 'collision',
          message: `与「${otherEntry?.name ?? item.kindId}」发生碰撞。`,
          suggest: '换一个位置或旋转家具。',
        },
      }
    }
  }

  if (!allowsOverlap(entry)) {
    for (const opening of scene.openings) {
      if (opening.type !== 'door') continue
      const openingRoom = scene.rooms.find((candidate) => candidate.id === opening.roomId)
      if (!openingRoom) continue
      const edge = edgeInfo(openingRoom.polygon as Vec2[], opening.edgeIndex)
      if (!edge) continue
      const zone = obbFromBox(
        pointAlongEdge(edge, opening.offset),
        [Math.max(opening.width, rules.DOOR_APPROACH_WIDTH), rules.DOOR_CLEAR_DEPTH * 2],
        yawAlongEdge(edge),
      )
      if (obbOverlap(box, zone, COLLISION_TOLERANCE)) {
        return {
          valid: false,
          roomId: room.id,
          issue: {
            code: 'blocks_door',
            message: '家具挡住了门前的通行区域。',
            suggest: '为门留出至少 0.6 m 的净空。',
          },
        }
      }
    }
  }

  return { valid: true, roomId: room.id }
}
