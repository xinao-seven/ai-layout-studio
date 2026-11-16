import type {
  CatalogEntry,
  Item,
  ItemConstraint,
  SceneDocument,
} from '../contracts/generated/client/rest'

export interface PointContext {
  itemId: string
  kindId: string
  variantId: string
  roomId: string | null
  roomName: string | null
  position: [number, number, number]
  rotationY: number
  rotationDeg: number
  size: [number, number, number] | null
  constraint: ItemConstraint | null
  selectedAt: string
}

export function buildPointContext(
  scene: SceneDocument,
  catalog: CatalogEntry[],
  item: Item,
): PointContext {
  const entry = catalog.find((candidate) => candidate.kindId === item.kindId)
  const variant =
    entry?.variants.find((candidate) => candidate.id === item.variantId) ?? entry?.variants[0]
  const roomId = item.constraint?.roomId ?? null
  const room = scene.rooms.find((candidate) => candidate.id === roomId)
  return {
    itemId: item.id,
    kindId: item.kindId,
    variantId: item.variantId,
    roomId,
    roomName: room?.name ?? null,
    position: item.transform.position,
    rotationY: item.transform.rotationY,
    rotationDeg: (item.transform.rotationY * 180) / Math.PI,
    size: variant?.sizeM ?? null,
    constraint: item.constraint ?? null,
    selectedAt: new Date().toISOString(),
  }
}

export function describeConstraint(constraint: ItemConstraint | null): string[] {
  if (!constraint) return []
  const lines: string[] = []
  if (constraint.anchor) {
    const anchor = constraint.anchor
    if (anchor.wallId) lines.push(`靠墙 ${anchor.wallId}`)
    if (anchor.wallLabel) lines.push(`墙标签 ${anchor.wallLabel}`)
    if (anchor.openingId) lines.push(`锚定开口 ${anchor.openingId}`)
  }
  if (constraint.near) {
    if (constraint.near.itemId) lines.push(`靠近家具 ${constraint.near.itemId}`)
    if (constraint.near.openingId) lines.push(`靠近开口 ${constraint.near.openingId}`)
    if (constraint.near.distance) lines.push(`距离 ${constraint.near.distance.toFixed(2)}m`)
  }
  if (constraint.facing) {
    if (typeof constraint.facing === 'string') {
      lines.push(`朝向 ${constraint.facing}`)
    } else {
      lines.push(
        `朝向 ${constraint.facing.toward}${constraint.facing.id ? ` ${constraint.facing.id}` : ''}`,
      )
    }
  }
  if (constraint.clearance) {
    const { front, sides, back } = constraint.clearance
    if (front) lines.push(`前方净空 ${front.toFixed(2)}m`)
    if (sides) lines.push(`两侧净空 ${sides.toFixed(2)}m`)
    if (back) lines.push(`背面净空 ${back.toFixed(2)}m`)
  }
  if (constraint.group) lines.push(`分组 ${constraint.group}`)
  return lines
}
