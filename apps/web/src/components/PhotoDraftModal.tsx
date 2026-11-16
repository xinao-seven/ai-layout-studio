import { AlertTriangle, ArrowRight, Loader2 } from 'lucide-react'
import { useState } from 'react'
import type { PhotoImportResult } from '../api/client'
import * as api from '../api/client'
import type { RoomType } from '../contracts/generated/client/rest'
import { useSceneStore } from '../store/sceneStore'
import { Button } from './ui/button'

const ROOM_TYPES: { value: RoomType; label: string }[] = [
  { value: 'living', label: '客厅' },
  { value: 'bedroom', label: '卧室' },
  { value: 'kitchen', label: '厨房' },
  { value: 'bathroom', label: '卫生间' },
  { value: 'study', label: '书房' },
  { value: 'balcony', label: '阳台' },
  { value: 'other', label: '其他' },
]

interface DraftRoom {
  id: string
  name: string
  type: RoomType
  areaM2: number
}

function polygonAreaM2(polygon: [number, number][]): number {
  let total = 0
  for (let index = 0; index < polygon.length; index += 1) {
    const [x1, z1] = polygon[index] as [number, number]
    const [x2, z2] = polygon[(index + 1) % polygon.length] as [number, number]
    total += x1 * z2 - x2 * z1
  }
  return Math.abs(total / 2)
}

export function PhotoDraftModal({
  draft,
  onClose,
}: {
  draft: PhotoImportResult
  onClose: () => void
}) {
  const openScene = useSceneStore((state) => state.openScene)
  const refreshScenes = useSceneStore((state) => state.refreshScenes)
  const setError = useSceneStore((state) => state.setError)
  const [rooms, setRooms] = useState<DraftRoom[]>(() =>
    draft.document.rooms.map((room) => ({
      id: room.id,
      name: room.name,
      type: room.type ?? 'other',
      areaM2: polygonAreaM2(room.polygon as [number, number][]),
    })),
  )
  const [creating, setCreating] = useState(false)

  const updateRoom = (roomId: string, patch: Partial<DraftRoom>) => {
    setRooms((current) =>
      current.map((room) => (room.id === roomId ? { ...room, ...patch } : room)),
    )
  }

  const createScene = async () => {
    setCreating(true)
    try {
      const document = {
        ...draft.document,
        rooms: draft.document.rooms.map((room) => {
          const edited = rooms.find((candidate) => candidate.id === room.id)
          return edited ? { ...room, name: edited.name || room.name, type: edited.type } : room
        }),
      }
      const imported = await api.importScene(document)
      await refreshScenes()
      onClose()
      await openScene(imported.id)
    } catch (error) {
      setError({
        code: error instanceof api.ApiError ? error.code : 'import_invalid',
        message: error instanceof api.ApiError ? error.message : '场景创建失败。',
        suggest: error instanceof api.ApiError ? (error.suggest ?? null) : null,
      })
      setCreating(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-6 backdrop-blur-sm">
      <div className="w-full max-w-md rounded-2xl border border-border bg-card p-5 shadow-2xl">
        <h2 className="font-medium text-[15px]">户型图解析草案</h2>
        <p className="mt-1 text-[11px] leading-5 text-muted-foreground">
          确认/修正房间名称与类型后创建场景；多边形的顶点可进入编辑器后拖拽微调。
        </p>

        {draft.warnings.length > 0 ? (
          <div className="mt-3 rounded-lg border border-amber-400/30 bg-amber-400/10 px-3 py-2">
            <p className="flex items-center gap-1.5 text-[11px] text-amber-200">
              <AlertTriangle className="h-3 w-3" />
              解析提示
            </p>
            <ul className="mt-1 list-disc pl-5 text-[11px] leading-5 text-amber-200/85">
              {draft.warnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          </div>
        ) : null}

        <div className="mt-3 max-h-64 space-y-2 overflow-y-auto subtle-scrollbar">
          {rooms.map((room) => (
            <div className="flex items-center gap-2" key={room.id}>
              <input
                className="h-8 min-w-0 flex-1 rounded-md border border-border bg-background/80 px-2 text-xs outline-none focus:border-white/30"
                onChange={(event) => updateRoom(room.id, { name: event.target.value })}
                value={room.name}
              />
              <select
                className="h-8 rounded-md border border-border bg-background/80 px-1.5 text-xs outline-none"
                onChange={(event) => updateRoom(room.id, { type: event.target.value as RoomType })}
                value={room.type}
              >
                {ROOM_TYPES.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
              <span className="w-16 text-right text-[11px] text-muted-foreground tabular-nums">
                {room.areaM2.toFixed(1)}㎡
              </span>
            </div>
          ))}
        </div>

        <div className="mt-4 flex justify-end gap-2">
          <Button disabled={creating} onClick={onClose} size="sm" variant="outline">
            取消
          </Button>
          <Button
            disabled={creating || rooms.length === 0}
            onClick={() => void createScene()}
            size="sm"
          >
            {creating ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <ArrowRight className="h-3.5 w-3.5" />
            )}
            创建场景
          </Button>
        </div>
      </div>
    </div>
  )
}
