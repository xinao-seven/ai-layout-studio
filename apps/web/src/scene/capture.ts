import type { SceneDocument } from '../contracts/generated/client/rest'

export interface CaptureLegendEntry {
  label: string
  itemId?: string
  kindId?: string
  openingId?: string
  type?: 'door' | 'window'
}

export interface CaptureLegend {
  items: CaptureLegendEntry[]
  openings: CaptureLegendEntry[]
}

export interface CaptureLabels {
  legend: CaptureLegend
  itemLabels: Array<{ itemId: string; label: string }>
  openingLabels: Array<{ openingId: string; label: string; x: number; z: number }>
}

/**
 * Build the same label ordering as the server (`app.vlm.service.build_legend`):
 * items and openings sorted by id, A1/D1/W1 per scene.
 */
export function buildCaptureLabels(scene: SceneDocument): CaptureLabels {
  const items = [...scene.items].sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0))
  const itemLabels = items.map((item, index) => ({
    itemId: item.id,
    label: `A${index + 1}`,
  }))
  const openings = [...scene.openings].sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0))
  const counters: Record<'door' | 'window', number> = { door: 0, window: 0 }
  const openingLabels = openings.map((opening) => {
    const type: 'door' | 'window' = opening.type === 'door' ? 'door' : 'window'
    counters[type] += 1
    const label = `${type === 'door' ? 'D' : 'W'}${counters[type]}`
    const room = scene.rooms.find((candidate) => candidate.id === opening.roomId)
    const point = openingPoint(room?.polygon as [number, number][] | undefined, opening)
    return { openingId: opening.id, label, x: point[0], z: point[1], type }
  })
  return {
    legend: {
      items: itemLabels.map((entry, index) => ({
        ...entry,
        kindId: items[index]?.kindId,
      })),
      openings: openingLabels.map((entry) => ({
        label: entry.label,
        openingId: entry.openingId,
        type: entry.type,
      })),
    },
    itemLabels,
    openingLabels,
  }
}

export function openingPoint(
  polygon: [number, number][] | undefined,
  opening: { edgeIndex: number; offset: number },
): [number, number] {
  if (!polygon || polygon.length === 0) return [0, 0]
  const index = opening.edgeIndex % polygon.length
  const a = polygon[index] ?? [0, 0]
  const b = polygon[(index + 1) % polygon.length] ?? a
  const dx = b[0] - a[0]
  const dz = b[1] - a[1]
  const length = Math.hypot(dx, dz)
  if (length <= 1e-9) return a
  const ratio = opening.offset / length
  return [a[0] + dx * ratio, a[1] + dz * ratio]
}
