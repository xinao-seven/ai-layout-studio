// biome-ignore-all lint/suspicious/noArrayIndexKey: wall edge index is the stable wall identity.
// biome-ignore-all lint/a11y/useSemanticElements: compact nested cards keep div/field semantics for the editor layout.
import rules from '@ai-layout/contracts/rules.json'
import {
  ArrowDown,
  ArrowLeft,
  ArrowRight,
  ArrowUp,
  Circle,
  DoorOpen,
  Loader2,
  Minus,
  Pencil,
  Plus,
  RefreshCw,
  RotateCcw,
  RotateCw,
  Search,
  Sparkles,
  Square,
  Trash2,
  X,
} from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import type {
  CatalogEntry,
  DoorSpec,
  Item,
  Opening,
  OpeningUpdateFields,
  SceneDocument,
} from '../../contracts/generated/client/rest'
import { cn } from '../../lib/cn'
import { setCatalogDragKind } from '../../scene/catalogDrag'
import { edgeInfo, MANUAL_ROTATION_RADIANS, polygonArea } from '../../scene/geometry'
import {
  CATEGORY_ACCENTS,
  CATEGORY_LABELS,
  ROOM_ACCENTS,
  ROOM_TYPE_LABELS,
} from '../../scene/labels'
import { describeConstraint } from '../../scene/pointContext'
import { clampScale, type SidebarTab, useSceneStore } from '../../store/sceneStore'
import { useUiStore } from '../../store/uiStore'
import { Badge } from '../ui/badge'
import { Button } from '../ui/button'
import { Separator } from '../ui/separator'

const TAB_META: Record<SidebarTab, { title: string; subtitle: string }> = {
  scene: { title: '场景概览', subtitle: '结构、面积与版本' },
  rooms: { title: '房间与墙', subtitle: '手绘、顶点与门窗开口' },
  items: { title: '家具目录', subtitle: '拖放到画布或点击添加' },
}

const CATEGORIES: Array<{ id: string; label: string }> = [
  { id: 'all', label: '全部' },
  { id: 'sofa', label: '沙发' },
  { id: 'bed', label: '床' },
  { id: 'table', label: '桌几' },
  { id: 'storage', label: '收纳' },
  { id: 'lighting', label: '灯具' },
  { id: 'decor', label: '软装' },
  { id: 'other', label: '其他' },
]

function Stat({
  label,
  value,
  suffix,
}: {
  label: string
  value: string | number
  suffix?: string
}) {
  return (
    <div className="rounded-xl border border-border/70 bg-background/40 px-3 py-2.5">
      <p className="text-[10px] text-muted-foreground">{label}</p>
      <p className="mt-1 font-medium text-sm tabular-nums">
        {value}
        {suffix ? (
          <span className="ml-0.5 font-normal text-[10px] text-muted-foreground">{suffix}</span>
        ) : null}
      </p>
    </div>
  )
}

function roomArea(room: SceneDocument['rooms'][number]): number {
  return polygonArea(room.polygon as [number, number][])
}

function ItemRow({
  item,
  scene,
  catalog,
  selected,
  onSelect,
}: {
  item: Item
  scene: SceneDocument
  catalog: CatalogEntry[]
  selected: boolean
  onSelect: () => void
}) {
  const entry = catalog.find((candidate) => candidate.kindId === item.kindId)
  const room = scene.rooms.find((candidate) => candidate.id === item.constraint?.roomId)
  return (
    <button
      className={cn(
        'flex w-full items-center gap-2.5 rounded-lg border px-2.5 py-2 text-left transition-colors',
        selected
          ? 'border-primary/30 bg-primary/10'
          : 'border-transparent hover:border-border hover:bg-accent/50',
      )}
      onClick={onSelect}
      type="button"
    >
      <span
        className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-white/10"
        style={{
          backgroundColor: `${CATEGORY_ACCENTS[entry?.category ?? 'other'] ?? CATEGORY_ACCENTS.other}22`,
        }}
      >
        <span
          className="h-2.5 w-2.5 rounded-[3px]"
          style={{
            backgroundColor: CATEGORY_ACCENTS[entry?.category ?? 'other'] ?? CATEGORY_ACCENTS.other,
          }}
        />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[13px]">{entry?.name ?? item.kindId}</span>
        <span className="block truncate text-[10px] text-muted-foreground">
          {room?.name ?? '未归属房间'} · {item.transform.position[0].toFixed(1)},{' '}
          {item.transform.position[2].toFixed(1)} m
        </span>
      </span>
    </button>
  )
}

function SceneTab({ scene }: { scene: SceneDocument }) {
  const scenes = useSceneStore((state) => state.scenes)
  const openScene = useSceneStore((state) => state.openScene)

  const area = useMemo(
    () => scene.rooms.reduce((total, room) => total + roomArea(room), 0),
    [scene.rooms],
  )

  return (
    <div className="space-y-4 p-3">
      <div className="grid grid-cols-2 gap-2">
        <Stat label="使用面积" suffix="㎡" value={area.toFixed(1)} />
        <Stat label="房间" value={scene.rooms.length} />
        <Stat label="家具" value={scene.items.length} />
        <Stat label="门窗" value={scene.openings.length} />
      </div>

      <div>
        <div className="mb-2 flex items-center justify-between px-1">
          <p className="font-medium text-[11px] text-muted-foreground tracking-wide">房间</p>
          <Badge tone="neutral">{scene.rooms.length}</Badge>
        </div>
        <div className="space-y-1">
          {scene.rooms.map((room) => (
            <div
              className="flex items-center gap-2.5 rounded-lg px-2.5 py-2 hover:bg-accent/40"
              key={room.id}
            >
              <span
                className="h-2 w-2 shrink-0 rounded-full"
                style={{ backgroundColor: ROOM_ACCENTS[room.type ?? 'other'] }}
              />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[13px]">{room.name}</span>
                <span className="text-[10px] text-muted-foreground">
                  {ROOM_TYPE_LABELS[room.type ?? 'other'] ?? room.type ?? '其他'}
                </span>
              </span>
              <span className="text-[10px] text-muted-foreground tabular-nums">
                {roomArea(room).toFixed(1)}㎡
              </span>
            </div>
          ))}
        </div>
      </div>

      <Separator />

      <div>
        <div className="mb-2 flex items-center justify-between px-1">
          <p className="font-medium text-[11px] text-muted-foreground tracking-wide">切换场景</p>
          <Badge tone="neutral">{scenes.length}</Badge>
        </div>
        <div className="space-y-1">
          {scenes.map((candidate) => (
            <button
              className={cn(
                'flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-[12px] transition-colors',
                candidate.id === scene.id
                  ? 'bg-primary/10 text-foreground'
                  : 'text-muted-foreground hover:bg-accent/50 hover:text-foreground',
              )}
              key={candidate.id}
              onClick={() => void openScene(candidate.id)}
              type="button"
            >
              <Circle className="h-1.5 w-1.5 fill-current" />
              <span className="min-w-0 flex-1 truncate">{candidate.name}</span>
              <span className="text-[10px] tabular-nums">v{candidate.version}</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}

function NumberField({
  label,
  value,
  step,
  min,
  onChange,
}: {
  label: string
  value: number
  step: number
  min?: number
  onChange: (value: number) => void
}) {
  return (
    <label className="flex items-center gap-1.5 text-[10px] text-muted-foreground">
      <span className="w-10 shrink-0">{label}</span>
      <input
        className="h-6 w-full rounded-md border border-border/70 bg-background/60 px-1.5 text-right text-[10px] text-foreground tabular-nums outline-none focus:border-primary/40"
        min={min}
        onChange={(event) => {
          const parsed = Number(event.target.value)
          if (Number.isFinite(parsed)) onChange(parsed)
        }}
        step={step}
        type="number"
        value={value}
      />
    </label>
  )
}

function OpeningRow({ opening }: { opening: Opening }) {
  const updateOpening = useSceneStore((state) => state.updateOpening)
  const deleteOpening = useSceneStore((state) => state.deleteOpening)
  const selectOpening = useSceneStore((state) => state.selectOpening)
  const selectedOpeningId = useSceneStore((state) => state.selectedOpeningId)
  const selected = selectedOpeningId === opening.id
  const [offset, setOffset] = useState(opening.offset)
  const [width, setWidth] = useState(opening.width)
  const [height, setHeight] = useState(opening.height)
  const [sill, setSill] = useState(opening.sillHeight ?? 0.9)
  const [swing, setSwing] = useState(opening.door?.swing ?? 'single')
  const [hinge, setHinge] = useState(opening.door?.hinge ?? 'start')
  const isDoor = opening.type === 'door'

  const apply = () => {
    const after: OpeningUpdateFields = {
      offset,
      width,
      height,
      sillHeight: isDoor ? null : sill,
    }
    if (isDoor) {
      const door: DoorSpec = {
        ...(opening.door ?? {}),
        swing: swing as DoorSpec['swing'],
        hinge: hinge as DoorSpec['hinge'],
      }
      after.door = door
    }
    void updateOpening(opening.id, after)
  }

  return (
    <div
      className={cn(
        'mt-2 rounded-lg border p-2',
        selected ? 'border-primary/40 bg-primary/10' : 'border-border/60 bg-background/40',
      )}
      onClick={(event) => {
        event.stopPropagation()
        selectOpening(opening.id)
      }}
      onKeyDown={() => undefined}
      role="group"
    >
      <div className="mb-1.5 flex items-center justify-between">
        <span className="flex items-center gap-1 text-[11px]">
          {isDoor ? <DoorOpen className="h-3 w-3" /> : <Square className="h-3 w-3" />}
          {isDoor ? '门' : '窗'} · {opening.width.toFixed(2)}×{opening.height.toFixed(2)}m
        </span>
        <button
          aria-label="删除开口"
          className="rounded p-1 text-muted-foreground hover:bg-white/10 hover:text-red-300"
          onClick={(event) => {
            event.stopPropagation()
            void deleteOpening(opening.id)
          }}
          type="button"
        >
          <Trash2 className="h-3 w-3" />
        </button>
      </div>
      <div className="grid grid-cols-2 gap-1.5">
        <NumberField label="偏移" min={0} onChange={setOffset} step={0.05} value={offset} />
        <NumberField label="宽度" min={0.2} onChange={setWidth} step={0.05} value={width} />
        <NumberField label="高度" min={0.2} onChange={setHeight} step={0.05} value={height} />
        {isDoor ? (
          <span />
        ) : (
          <NumberField label="窗台" min={0} onChange={setSill} step={0.05} value={sill} />
        )}
      </div>
      {isDoor ? (
        <div className="mt-1.5 grid grid-cols-2 gap-1.5 text-[10px]">
          <label className="flex items-center gap-1 text-muted-foreground">
            <span className="w-10 shrink-0">开向</span>
            <select
              className="h-6 w-full rounded-md border border-border/70 bg-background/60 px-1 text-[10px] text-foreground outline-none"
              onChange={(event) => setSwing(event.target.value as NonNullable<DoorSpec['swing']>)}
              value={swing}
            >
              <option value="single">单开</option>
              <option value="double">双开</option>
              <option value="sliding">推拉</option>
            </select>
          </label>
          <label className="flex items-center gap-1 text-muted-foreground">
            <span className="w-10 shrink-0">铰链</span>
            <select
              className="h-6 w-full rounded-md border border-border/70 bg-background/60 px-1 text-[10px] text-foreground outline-none"
              onChange={(event) => setHinge(event.target.value as NonNullable<DoorSpec['hinge']>)}
              value={hinge}
            >
              <option value="start">起点</option>
              <option value="end">终点</option>
            </select>
          </label>
        </div>
      ) : null}
      <Button className="mt-1.5 h-6 w-full text-[10px]" onClick={apply} size="sm" variant="outline">
        应用开口参数
      </Button>
    </div>
  )
}

function RoomVertexEditor({ room }: { room: SceneDocument['rooms'][number] }) {
  const updateRoomPolygon = useSceneStore((state) => state.updateRoomPolygon)
  const [points, setPoints] = useState<[number, number][]>(room.polygon as [number, number][])
  useEffect(() => {
    setPoints(room.polygon as [number, number][])
  }, [room.polygon])

  return (
    <div
      className="mt-2 rounded-lg border border-border/60 bg-background/40 p-2"
      onClick={(event) => event.stopPropagation()}
      onKeyDown={() => undefined}
      role="group"
    >
      <div className="mb-1.5 flex items-center justify-between">
        <p className="text-[10px] text-muted-foreground">顶点（画布手柄支持拖动，此处精确输入）</p>
        <Button
          className="h-6 px-1.5 text-[10px]"
          onClick={() => void updateRoomPolygon(room.id, points)}
          size="sm"
          variant="outline"
        >
          应用顶点
        </Button>
      </div>
      <div className="grid grid-cols-2 gap-1">
        {points.map((point, index) => (
          <div className="flex items-center gap-1" key={`${room.id}-vertex-input-${index}`}>
            <span className="w-4 text-[9px] text-muted-foreground">V{index}</span>
            <input
              aria-label={`顶点 ${index} X`}
              className="h-6 w-full rounded-md border border-border/70 bg-background/60 px-1 text-right text-[10px] tabular-nums outline-none focus:border-primary/40"
              onChange={(event) => {
                const parsed = Number(event.target.value)
                if (!Number.isFinite(parsed)) return
                setPoints((current) =>
                  current.map((candidate, candidateIndex) =>
                    candidateIndex === index ? [parsed, candidate[1]] : candidate,
                  ),
                )
              }}
              step={0.1}
              type="number"
              value={Math.round(point[0] * 100) / 100}
            />
            <input
              aria-label={`顶点 ${index} Z`}
              className="h-6 w-full rounded-md border border-border/70 bg-background/60 px-1 text-right text-[10px] tabular-nums outline-none focus:border-primary/40"
              onChange={(event) => {
                const parsed = Number(event.target.value)
                if (!Number.isFinite(parsed)) return
                setPoints((current) =>
                  current.map((candidate, candidateIndex) =>
                    candidateIndex === index ? [candidate[0], parsed] : candidate,
                  ),
                )
              }}
              step={0.1}
              type="number"
              value={Math.round(point[1] * 100) / 100}
            />
          </div>
        ))}
      </div>
    </div>
  )
}

function WallPanel({
  scene,
  roomId,
  edgeIndex,
}: {
  scene: SceneDocument
  roomId: string
  edgeIndex: number
}) {
  const addOpening = useSceneStore((state) => state.addOpening)
  const room = scene.rooms.find((candidate) => candidate.id === roomId)
  if (!room) return null
  const edge = edgeInfo(room.polygon as [number, number][], edgeIndex)
  const openings = scene.openings.filter(
    (opening) => opening.roomId === roomId && opening.edgeIndex === edgeIndex,
  )
  const label = room.wallLabels?.[String(edgeIndex)] ?? `墙 e${edgeIndex}`
  return (
    <div
      className="mt-2 rounded-lg border border-primary/30 bg-primary/5 p-2"
      onClick={(event) => event.stopPropagation()}
      onKeyDown={() => undefined}
      role="group"
    >
      <div className="flex items-center justify-between">
        <p className="font-medium text-[11px]">
          {label} · {edge ? `${edge.length.toFixed(2)}m` : '—'}
        </p>
        <div className="flex gap-1">
          <Button
            className="h-6 px-1.5 text-[10px]"
            onClick={() => void addOpening(roomId, edgeIndex, 'door')}
            size="sm"
            variant="outline"
          >
            <DoorOpen className="h-3 w-3" />门
          </Button>
          <Button
            className="h-6 px-1.5 text-[10px]"
            onClick={() => void addOpening(roomId, edgeIndex, 'window')}
            size="sm"
            variant="outline"
          >
            <Square className="h-3 w-3" />窗
          </Button>
        </div>
      </div>
      {openings.length === 0 ? (
        <p className="mt-1.5 text-[10px] text-muted-foreground">这段墙还没有门窗。</p>
      ) : (
        openings.map((opening) => (
          <OpeningRow
            key={`${opening.id}:${opening.offset}:${opening.width}:${opening.height}:${opening.sillHeight}:${opening.door?.swing}:${opening.door?.hinge}`}
            opening={opening}
          />
        ))
      )}
    </div>
  )
}

function RoomsTab({ scene }: { scene: SceneDocument }) {
  const selectedRoomId = useSceneStore((state) => state.selectedRoomId)
  const selectedWall = useSceneStore((state) => state.selectedWall)
  const selectRoom = useSceneStore((state) => state.selectRoom)
  const selectWall = useSceneStore((state) => state.selectWall)
  const deleteRoom = useSceneStore((state) => state.deleteRoom)
  const tool = useSceneStore((state) => state.tool)
  const setTool = useSceneStore((state) => state.setTool)

  return (
    <div className="space-y-2 p-3">
      <Button
        className="w-full justify-start"
        onClick={() => setTool(tool === 'draw-room' ? 'select' : 'draw-room')}
        size="sm"
        variant={tool === 'draw-room' ? 'default' : 'outline'}
      >
        <Pencil className="h-3.5 w-3.5" />
        {tool === 'draw-room'
          ? '正在绘制房间（点击加点 · 默认直角+网格吸附 · Shift 自由）'
          : '绘制房间'}
      </Button>

      {scene.rooms.length === 0 ? (
        <p className="px-1 py-4 text-center text-[11px] text-muted-foreground">
          还没有房间。用上方工具在地面上点击墙中心线：默认直角+0.1m 网格吸附，按住 Shift
          自由绘制，点击起点闭合。
        </p>
      ) : null}

      {scene.rooms.map((room) => {
        const selected = selectedRoomId === room.id
        const openings = scene.openings.filter((opening) => opening.roomId === room.id)
        return (
          <article
            className={cn(
              'rounded-xl border bg-background/35 p-3 transition-colors',
              selected ? 'border-primary/40 bg-primary/5' : 'border-border/70',
            )}
            key={room.id}
            onClick={(event) => {
              const target = event.target as HTMLElement
              if (target.closest('button,input,select,label')) return
              selectRoom(room.id)
            }}
            onKeyDown={() => undefined}
            role="group"
          >
            <div className="flex items-start justify-between gap-2">
              <div className="flex items-center gap-2">
                <span
                  className="h-2.5 w-2.5 rounded-full"
                  style={{ backgroundColor: ROOM_ACCENTS[room.type ?? 'other'] }}
                />
                <div>
                  <h3 className="font-medium text-[13px]">{room.name}</h3>
                  <p className="text-[10px] text-muted-foreground">
                    {ROOM_TYPE_LABELS[room.type ?? 'other'] ?? room.type ?? '其他'} ·{' '}
                    {roomArea(room).toFixed(1)}㎡
                  </p>
                </div>
              </div>
              <div className="flex items-center gap-1">
                <Badge tone="neutral">{room.polygon.length} 面墙</Badge>
                <button
                  aria-label="删除房间"
                  className="rounded p-1 text-muted-foreground hover:bg-white/10 hover:text-red-300"
                  onClick={(event) => {
                    event.stopPropagation()
                    void deleteRoom(room.id)
                  }}
                  type="button"
                >
                  <Trash2 className="h-3 w-3" />
                </button>
              </div>
            </div>
            <div className="mt-2 grid grid-cols-2 gap-1.5 text-[10px] text-muted-foreground">
              <span>墙高 {room.wallHeight ?? scene.defaults.wallHeight} m</span>
              <span>墙厚 {room.wallThickness ?? scene.defaults.wallThickness} m</span>
              <span>{openings.filter((o) => o.type === 'door').length} 门</span>
              <span>{openings.filter((o) => o.type === 'window').length} 窗</span>
            </div>
            <div className="mt-2 flex flex-wrap gap-1">
              {(room.polygon as [number, number][]).map((_, edgeIndex) => {
                const active =
                  selectedWall?.roomId === room.id && selectedWall.edgeIndex === edgeIndex
                return (
                  <button
                    className={cn(
                      'rounded-md border px-1.5 py-0.5 text-[9px] transition-colors',
                      active
                        ? 'border-primary/50 bg-primary/15 text-foreground'
                        : 'border-border/60 bg-background/50 text-muted-foreground hover:text-foreground',
                    )}
                    key={`${room.id}-e${edgeIndex}`}
                    onClick={(event) => {
                      event.stopPropagation()
                      selectWall({ roomId: room.id, edgeIndex })
                    }}
                    type="button"
                  >
                    {room.wallLabels?.[String(edgeIndex)] ?? `e${edgeIndex}`}
                  </button>
                )
              })}
            </div>
            {selected ? <RoomVertexEditor room={room} /> : null}
            {selected && selectedWall?.roomId === room.id ? (
              <WallPanel edgeIndex={selectedWall.edgeIndex} roomId={room.id} scene={scene} />
            ) : null}
          </article>
        )
      })}
    </div>
  )
}

function CatalogCard({
  entry,
  selected,
  onSelect,
}: {
  entry: CatalogEntry
  selected: boolean
  onSelect: () => void
}) {
  const addItem = useSceneStore((state) => state.addItemFromCatalog)
  const clearCatalogGhost = useSceneStore((state) => state.clearCatalogGhost)
  const [imageFailed, setImageFailed] = useState(false)
  const variant = entry.variants[0]
  const size = variant?.sizeM ?? [1, 1, 1]
  const accent = CATEGORY_ACCENTS[entry.category ?? 'other'] ?? CATEGORY_ACCENTS.other

  return (
    <button
      className={cn(
        'group relative flex cursor-grab flex-col items-start gap-1.5 overflow-hidden rounded-xl border bg-background/35 p-2 text-left transition-all active:cursor-grabbing',
        selected
          ? 'border-primary/45 bg-primary/10'
          : 'border-border/70 hover:border-white/20 hover:bg-accent/50',
      )}
      draggable
      onClick={onSelect}
      onDoubleClick={() => void addItem(entry.kindId)}
      onDragEnd={() => {
        setCatalogDragKind(null)
        clearCatalogGhost()
      }}
      onDragStart={(event) => {
        setCatalogDragKind(entry.kindId)
        event.dataTransfer.effectAllowed = 'copy'
        event.dataTransfer.setData('text/plain', entry.kindId)
      }}
      type="button"
    >
      <span
        className="relative flex h-16 w-full items-center justify-center overflow-hidden rounded-lg border border-white/5"
        style={{ backgroundColor: `${accent}12` }}
      >
        {variant && !imageFailed ? (
          <img
            alt={entry.name}
            className="h-full w-full object-contain p-1"
            draggable={false}
            onError={() => setImageFailed(true)}
            src={variant.thumbnailPath}
          />
        ) : (
          <span
            className="h-7 w-10 rounded-[4px] border border-white/20"
            style={{ backgroundColor: accent }}
          />
        )}
        <span className="absolute top-1 right-1 flex h-5 w-5 items-center justify-center rounded-md border border-border/70 bg-background/70 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100">
          <Plus className="h-3 w-3" />
        </span>
      </span>
      <span className="min-w-0 w-full">
        <span className="block truncate text-[12px]">{entry.name}</span>
        <span className="block text-[9px] text-muted-foreground">
          {size[0].toFixed(2)}×{size[1].toFixed(2)}×{size[2].toFixed(2)} m · 双击添加
        </span>
      </span>
    </button>
  )
}

function ItemsTab({ scene, catalog }: { scene: SceneDocument; catalog: CatalogEntry[] }) {
  const [query, setQuery] = useState('')
  const [category, setCategory] = useState('all')
  const [selectedKindId, setSelectedKindId] = useState<string | null>(null)
  const selectItem = useSceneStore((state) => state.selectItem)
  const selectedItemId = useSceneStore((state) => state.selectedItemId)
  const reloadCatalog = useSceneStore((state) => state.reloadCatalog)
  const addItem = useSceneStore((state) => state.addItemFromCatalog)

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase()
    return catalog.filter((entry) => {
      if (category !== 'all' && entry.category !== category) return false
      if (!needle) return true
      return (
        entry.name.toLowerCase().includes(needle) ||
        entry.kindId.includes(needle) ||
        (entry.tags ?? []).some((tag) => tag.toLowerCase().includes(needle)) ||
        (CATEGORY_LABELS[entry.category ?? 'other'] ?? '').includes(needle)
      )
    })
  }, [catalog, category, query])

  const selectedEntry = catalog.find((entry) => entry.kindId === selectedKindId)

  return (
    <div className="space-y-3 p-3">
      <div className="flex items-center justify-between px-1">
        <p className="font-medium text-[11px] text-muted-foreground tracking-wide">目录</p>
        <div className="flex items-center gap-1">
          <Badge tone="neutral">{catalog.length}</Badge>
          <button
            aria-label="热加载目录"
            className="rounded p-1 text-muted-foreground hover:bg-white/10 hover:text-foreground"
            onClick={() => void reloadCatalog()}
            title="POST /api/catalog/reload"
            type="button"
          >
            <RefreshCw className="h-3 w-3" />
          </button>
        </div>
      </div>

      <label className="relative block">
        <Search className="absolute top-1/2 left-2.5 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
        <input
          className="h-8 w-full rounded-lg border border-border/70 bg-background/50 pr-2 pl-8 text-[12px] outline-none placeholder:text-muted-foreground/70 focus:border-primary/40"
          onChange={(event) => setQuery(event.target.value)}
          placeholder="搜索家具 / 关键词"
          value={query}
        />
      </label>

      <div className="flex flex-wrap gap-1">
        {CATEGORIES.map((item) => (
          <button
            className={cn(
              'rounded-full border px-2 py-0.5 text-[10px] transition-colors',
              category === item.id
                ? 'border-primary/40 bg-primary/15 text-foreground'
                : 'border-border/60 bg-background/40 text-muted-foreground hover:text-foreground',
            )}
            key={item.id}
            onClick={() => setCategory(item.id)}
            type="button"
          >
            {item.label}
          </button>
        ))}
      </div>

      {selectedEntry ? (
        <div className="rounded-xl border border-border/70 bg-background/40 p-2.5">
          <div className="flex items-start justify-between">
            <div>
              <p className="font-medium text-[12px]">{selectedEntry.name}</p>
              <p className="text-[9px] text-muted-foreground">
                {CATEGORY_LABELS[selectedEntry.category ?? 'other']} · {selectedEntry.kindId}
              </p>
            </div>
            <Button
              className="h-6 px-2 text-[10px]"
              onClick={() => void addItem(selectedEntry.kindId)}
              size="sm"
              variant="outline"
            >
              <Plus className="h-3 w-3" />
              添加
            </Button>
          </div>
          <div className="mt-1.5 grid grid-cols-3 gap-1 text-[9px] text-muted-foreground">
            <span>宽 {selectedEntry.variants[0]?.sizeM[0].toFixed(2)}m</span>
            <span>深 {selectedEntry.variants[0]?.sizeM[1].toFixed(2)}m</span>
            <span>高 {selectedEntry.variants[0]?.sizeM[2].toFixed(2)}m</span>
          </div>
          <p className="mt-1.5 text-[9px] text-muted-foreground">
            净空 前{selectedEntry.clearance?.front ?? 0}m · 侧{selectedEntry.clearance?.sides ?? 0}m
            · 后{selectedEntry.clearance?.back ?? 0}m
            {selectedEntry.rules?.mustAgainstWall ? ' · 必须靠墙' : ''}
            {selectedEntry.rules?.allowOverlap ? ' · 可叠放' : ''}
          </p>
        </div>
      ) : null}

      <div className="grid grid-cols-2 gap-2">
        {filtered.map((entry) => (
          <CatalogCard
            entry={entry}
            key={entry.kindId}
            onSelect={() => setSelectedKindId(entry.kindId)}
            selected={selectedKindId === entry.kindId}
          />
        ))}
      </div>
      {filtered.length === 0 ? (
        <p className="px-1 py-4 text-center text-[11px] text-muted-foreground">没有匹配的家具。</p>
      ) : null}

      <Separator />

      <div>
        <div className="mb-2 flex items-center justify-between">
          <p className="font-medium text-[11px] text-muted-foreground tracking-wide">已放置</p>
          <Badge tone="neutral">{scene.items.length}</Badge>
        </div>
        <div className="space-y-1">
          {scene.items.map((item) => (
            <ItemRow
              catalog={catalog}
              item={item}
              key={item.id}
              onSelect={() => selectItem(item.id)}
              scene={scene}
              selected={item.id === selectedItemId}
            />
          ))}
        </div>
      </div>
    </div>
  )
}

function ItemDetails() {
  const scene = useSceneStore((state) => state.scene)
  const catalog = useSceneStore((state) => state.catalog)
  const selectedItemId = useSceneStore((state) => state.selectedItemId)
  const pointContext = useSceneStore((state) => state.pointContext)
  const selectItem = useSceneStore((state) => state.selectItem)
  const setDockOpen = useUiStore((state) => state.setDockOpen)
  const moveSelectedItem = useSceneStore((state) => state.moveSelectedItem)
  const rotateSelectedItem = useSceneStore((state) => state.rotateSelectedItem)
  const deleteSelectedItem = useSceneStore((state) => state.deleteSelectedItem)
  const setItemScale = useSceneStore((state) => state.setItemScale)

  const item = scene?.items.find((candidate) => candidate.id === selectedItemId)
  if (!item) return null
  const entry = catalog.find((candidate) => candidate.kindId === item.kindId)
  const variant =
    entry?.variants.find((candidate) => candidate.id === item.variantId) ?? entry?.variants[0]
  const size = variant?.sizeM ?? [1, 1, 1]
  const [x, y, z] = item.transform.position
  const room = scene?.rooms.find((candidate) => candidate.id === item.constraint?.roomId)
  const constraints = describeConstraint(item.constraint ?? null)

  const nudge = (dx: number, dz: number) => {
    void moveSelectedItem([x + dx, y, z + dz], item.transform.rotationY)
  }

  const currentScale = item.transform.scale ?? 1
  const stepScale = (delta: number) => {
    const next = clampScale(currentScale + delta)
    if (Math.abs(next - currentScale) <= 1e-9) return
    void setItemScale(item.id, next)
  }

  return (
    <div className="subtle-scrollbar max-h-[46%] shrink-0 overflow-y-auto border-border/50 border-t bg-sidebar p-3">
      <div className="mb-2 flex items-start justify-between">
        <div className="min-w-0">
          <p className="truncate font-medium text-[13px]">{entry?.name ?? item.kindId}</p>
          <p className="mt-0.5 truncate font-mono text-[9px] text-muted-foreground">
            {item.id} · v{item.variantId}
          </p>
        </div>
        <Button
          aria-label="关闭家具详情"
          onClick={() => selectItem(null)}
          size="icon"
          variant="ghost"
        >
          <X className="h-3.5 w-3.5" />
        </Button>
      </div>

      <div className="mb-2 grid grid-cols-3 gap-1 text-[10px] text-muted-foreground">
        <span className="rounded-md bg-background/40 px-1.5 py-1">
          宽 {(size[0] * currentScale).toFixed(2)}m
        </span>
        <span className="rounded-md bg-background/40 px-1.5 py-1">
          深 {(size[1] * currentScale).toFixed(2)}m
        </span>
        <span className="rounded-md bg-background/40 px-1.5 py-1">
          高 {(size[2] * currentScale).toFixed(2)}m
        </span>
      </div>

      <div className="mb-2 flex items-center gap-1 text-[10px] text-muted-foreground">
        <span className="shrink-0">缩放</span>
        <Button
          aria-label="缩小 5%"
          disabled={currentScale <= rules.ITEM_SCALE_MIN + 1e-9}
          onClick={() => stepScale(-rules.ITEM_SCALE_STEP)}
          size="icon"
          variant="outline"
        >
          <Minus className="h-3.5 w-3.5" />
        </Button>
        <button
          className="min-w-14 rounded-md bg-background/40 px-1.5 py-1 font-mono tabular-nums hover:bg-background/60"
          onClick={() => stepScale(1 - currentScale)}
          title="点击恢复原始尺寸"
          type="button"
        >
          {Math.round(currentScale * 100)}%
        </button>
        <Button
          aria-label="放大 5%"
          disabled={currentScale >= rules.ITEM_SCALE_MAX - 1e-9}
          onClick={() => stepScale(rules.ITEM_SCALE_STEP)}
          size="icon"
          variant="outline"
        >
          <Plus className="h-3.5 w-3.5" />
        </Button>
        <span className="ml-auto truncate text-[9px]">拖拽右下角手柄也可缩放</span>
      </div>

      <div className="mb-2 grid grid-cols-2 gap-1 text-[10px] text-muted-foreground">
        <span className="rounded-md bg-background/40 px-1.5 py-1">
          位置 {x.toFixed(2)}, {z.toFixed(2)}
        </span>
        <span className="rounded-md bg-background/40 px-1.5 py-1">
          朝向 {((item.transform.rotationY * 180) / Math.PI).toFixed(0)}°
        </span>
        <span className="col-span-2 truncate rounded-md bg-background/40 px-1.5 py-1">
          房间 {room?.name ?? '未归属'}
        </span>
      </div>

      {constraints.length > 0 || pointContext ? (
        <div className="mb-2 flex flex-wrap gap-1">
          {constraints.map((line) => (
            <span
              className="rounded-md border border-border/60 bg-background/50 px-1.5 py-0.5 text-[9px] text-muted-foreground"
              key={line}
            >
              {line}
            </span>
          ))}
          {pointContext ? (
            <span className="rounded-md border border-primary/30 bg-primary/10 px-1.5 py-0.5 text-[9px] text-foreground/80">
              PointContext 可用
            </span>
          ) : null}
        </div>
      ) : null}

      <div className="flex items-center justify-between gap-2">
        <div className="grid grid-cols-3 gap-0.5">
          <span />
          <Button
            aria-label="向北移动"
            onClick={() => nudge(0, -0.1)}
            size="icon"
            variant="outline"
          >
            <ArrowUp className="h-3.5 w-3.5" />
          </Button>
          <span />
          <Button
            aria-label="向西移动"
            onClick={() => nudge(-0.1, 0)}
            size="icon"
            variant="outline"
          >
            <ArrowLeft className="h-3.5 w-3.5" />
          </Button>
          <span className="flex items-center justify-center text-[9px] text-muted-foreground">
            {x.toFixed(1)},
            <br />
            {z.toFixed(1)}
          </span>
          <Button aria-label="向东移动" onClick={() => nudge(0.1, 0)} size="icon" variant="outline">
            <ArrowRight className="h-3.5 w-3.5" />
          </Button>
          <span />
          <Button aria-label="向南移动" onClick={() => nudge(0, 0.1)} size="icon" variant="outline">
            <ArrowDown className="h-3.5 w-3.5" />
          </Button>
          <span />
        </div>

        <div className="flex flex-col gap-1">
          <div className="flex gap-1">
            <Button
              aria-label="逆时针旋转 15 度"
              onClick={() => void rotateSelectedItem(-MANUAL_ROTATION_RADIANS)}
              size="icon"
              variant="outline"
            >
              <RotateCcw className="h-3.5 w-3.5" />
            </Button>
            <Button
              aria-label="顺时针旋转 15 度"
              onClick={() => void rotateSelectedItem(MANUAL_ROTATION_RADIANS)}
              size="icon"
              variant="outline"
            >
              <RotateCw className="h-3.5 w-3.5" />
            </Button>
          </div>
          <Button onClick={() => void deleteSelectedItem()} size="sm" variant="destructive">
            <Trash2 className="h-3.5 w-3.5" />
            删除
          </Button>
        </div>
      </div>

      <Button className="mt-2 w-full" onClick={() => setDockOpen(true)} size="sm" variant="outline">
        <Sparkles className="h-3.5 w-3.5" />用 PointContext 询问 AI
      </Button>
    </div>
  )
}

export function Sidebar() {
  const sidebarTab = useSceneStore((state) => state.sidebarTab)
  const scene = useSceneStore((state) => state.scene)
  const catalog = useSceneStore((state) => state.catalog)
  const loading = useSceneStore((state) => state.loading)
  const meta = TAB_META[sidebarTab] ?? TAB_META.scene

  return (
    <aside className="flex h-full w-80 shrink-0 flex-col border-border/50 border-r bg-sidebar">
      <div className="flex h-10 shrink-0 items-center justify-between border-border/50 border-b px-3">
        <div className="min-w-0">
          <p className="truncate font-medium text-[13px]">{meta.title}</p>
          <p className="truncate text-[9px] text-muted-foreground">{meta.subtitle}</p>
        </div>
        {loading ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />
        ) : (
          <Badge tone="neutral">M3</Badge>
        )}
      </div>

      <div className="subtle-scrollbar min-h-0 flex-1 overflow-y-auto">
        {!scene ? (
          <div className="flex h-full items-center justify-center p-6 text-center text-[11px] text-muted-foreground">
            正在载入场景…
          </div>
        ) : sidebarTab === 'scene' ? (
          <SceneTab scene={scene} />
        ) : sidebarTab === 'rooms' ? (
          <RoomsTab scene={scene} />
        ) : (
          <ItemsTab catalog={catalog} scene={scene} />
        )}
      </div>

      <ItemDetails />
    </aside>
  )
}
