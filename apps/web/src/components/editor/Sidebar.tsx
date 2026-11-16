import {
  ArrowDown,
  ArrowLeft,
  ArrowRight,
  ArrowUp,
  Circle,
  Loader2,
  Plus,
  RotateCcw,
  RotateCw,
  Search,
  Sparkles,
  Trash2,
  X,
} from 'lucide-react'
import { useMemo, useState } from 'react'
import type { CatalogEntry, Item, SceneDocument } from '../../contracts/generated/client/rest'
import { cn } from '../../lib/cn'
import { polygonArea } from '../../scene/geometry'
import {
  CATEGORY_ACCENTS,
  CATEGORY_LABELS,
  ROOM_ACCENTS,
  ROOM_TYPE_LABELS,
} from '../../scene/labels'
import { type SidebarTab, useSceneStore } from '../../store/sceneStore'
import { Badge } from '../ui/badge'
import { Button } from '../ui/button'
import { Separator } from '../ui/separator'

const TAB_META: Record<SidebarTab, { title: string; subtitle: string }> = {
  scene: { title: '场景概览', subtitle: '结构、面积与版本' },
  rooms: { title: '房间与墙', subtitle: '多边形、墙段与开口' },
  items: { title: '家具目录', subtitle: '从目录添加到当前场景' },
  ai: { title: 'AI 助手', subtitle: 'Agent 循环在 M3 接入' },
}

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

function RoomsTab({ scene }: { scene: SceneDocument }) {
  return (
    <div className="space-y-2 p-3">
      {scene.rooms.map((room) => {
        const openings = scene.openings.filter((opening) => opening.roomId === room.id)
        return (
          <article
            className="rounded-xl border border-border/70 bg-background/35 p-3"
            key={room.id}
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
              <Badge tone="neutral">{room.polygon.length} 面墙</Badge>
            </div>
            <div className="mt-2 grid grid-cols-2 gap-1.5 text-[10px] text-muted-foreground">
              <span>墙高 {room.wallHeight ?? scene.defaults.wallHeight} m</span>
              <span>墙厚 {room.wallThickness ?? scene.defaults.wallThickness} m</span>
              <span>{openings.filter((o) => o.type === 'door').length} 门</span>
              <span>{openings.filter((o) => o.type === 'window').length} 窗</span>
            </div>
            {room.wallLabels && Object.keys(room.wallLabels).length > 0 ? (
              <div className="mt-2 flex flex-wrap gap-1">
                {Object.entries(room.wallLabels).map(([edge, label]) => (
                  <span
                    className="rounded-md border border-border/60 bg-background/50 px-1.5 py-0.5 text-[9px] text-muted-foreground"
                    key={edge}
                  >
                    {label} · e{edge}
                  </span>
                ))}
              </div>
            ) : null}
          </article>
        )
      })}
    </div>
  )
}

function CatalogCard({ entry }: { entry: CatalogEntry }) {
  const addItem = useSceneStore((state) => state.addItemFromCatalog)
  const variant = entry.variants[0]
  const size = variant?.sizeM ?? [1, 1, 1]
  const accent = CATEGORY_ACCENTS[entry.category ?? 'other'] ?? CATEGORY_ACCENTS.other
  return (
    <button
      className="group relative flex flex-col items-start gap-2 overflow-hidden rounded-xl border border-border/70 bg-background/35 p-2.5 text-left transition-all hover:border-white/20 hover:bg-accent/50"
      onClick={() => void addItem(entry.kindId)}
      type="button"
    >
      <span className="flex w-full items-center justify-between">
        <span
          className="flex h-9 w-9 items-center justify-center rounded-lg border border-white/10"
          style={{ backgroundColor: `${accent}22` }}
        >
          <span
            className="h-3.5 w-3.5 rounded-[4px] border border-white/20"
            style={{ backgroundColor: accent }}
          />
        </span>
        <span className="flex h-6 w-6 items-center justify-center rounded-md border border-border/70 bg-background/60 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100">
          <Plus className="h-3.5 w-3.5" />
        </span>
      </span>
      <span className="min-w-0">
        <span className="block truncate text-[12px]">{entry.name}</span>
        <span className="block text-[9px] text-muted-foreground">
          {size[0].toFixed(1)}×{size[1].toFixed(1)}×{size[2].toFixed(1)} m
        </span>
      </span>
    </button>
  )
}

function ItemsTab({ scene, catalog }: { scene: SceneDocument; catalog: CatalogEntry[] }) {
  const [query, setQuery] = useState('')
  const selectItem = useSceneStore((state) => state.selectItem)
  const selectedItemId = useSceneStore((state) => state.selectedItemId)

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase()
    if (!needle) return catalog
    return catalog.filter(
      (entry) =>
        entry.name.toLowerCase().includes(needle) ||
        entry.kindId.includes(needle) ||
        (entry.tags ?? []).some((tag) => tag.toLowerCase().includes(needle)) ||
        (CATEGORY_LABELS[entry.category ?? 'other'] ?? '').includes(needle),
    )
  }, [catalog, query])

  return (
    <div className="space-y-4 p-3">
      <div>
        <div className="mb-2 flex items-center justify-between">
          <p className="font-medium text-[11px] text-muted-foreground tracking-wide">目录</p>
          <Badge tone="neutral">{filtered.length}</Badge>
        </div>
        <label className="relative mb-2 block">
          <Search className="absolute top-1/2 left-2.5 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
          <input
            className="h-8 w-full rounded-lg border border-border/70 bg-background/50 pr-2 pl-8 text-[12px] outline-none placeholder:text-muted-foreground/70 focus:border-primary/40"
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索家具 / 关键词"
            value={query}
          />
        </label>
        <div className="grid grid-cols-2 gap-2">
          {filtered.map((entry) => (
            <CatalogCard entry={entry} key={entry.kindId} />
          ))}
        </div>
      </div>

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

function AiTab() {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 p-6 text-center">
      <span className="flex h-12 w-12 items-center justify-center rounded-2xl border border-primary/20 bg-primary/10">
        <Sparkles className="h-5 w-5 text-foreground" />
      </span>
      <div>
        <p className="font-medium text-sm">AI 布局助手</p>
        <p className="mt-1.5 text-[11px] text-muted-foreground leading-5">
          Agent 循环、9 个工具与 ghost 提案在 M3 接入。当前 M1 已具备服务端场景、
          版本历史、WebSocket 同步与手动家具编辑。
        </p>
      </div>
      <div className="mt-2 w-full rounded-xl border border-border/60 bg-background/40 p-3 text-left">
        <p className="text-[10px] text-muted-foreground">规划中的能力</p>
        <ul className="mt-2 space-y-1.5 text-[11px] text-muted-foreground">
          <li>· “在客厅放一个双人沙发，靠电视墙”</li>
          <li>· auto_layout 整室求解与 verify_layout</li>
          <li>· VLM 俯视评审 + 局部重排</li>
        </ul>
      </div>
    </div>
  )
}

function ItemDetails() {
  const scene = useSceneStore((state) => state.scene)
  const catalog = useSceneStore((state) => state.catalog)
  const selectedItemId = useSceneStore((state) => state.selectedItemId)
  const selectItem = useSceneStore((state) => state.selectItem)
  const moveSelectedItem = useSceneStore((state) => state.moveSelectedItem)
  const rotateSelectedItem = useSceneStore((state) => state.rotateSelectedItem)
  const deleteSelectedItem = useSceneStore((state) => state.deleteSelectedItem)

  const item = scene?.items.find((candidate) => candidate.id === selectedItemId)
  if (!item) return null
  const entry = catalog.find((candidate) => candidate.kindId === item.kindId)
  const variant =
    entry?.variants.find((candidate) => candidate.id === item.variantId) ?? entry?.variants[0]
  const size = variant?.sizeM ?? [1, 1, 1]
  const [x, y, z] = item.transform.position

  const nudge = (dx: number, dz: number) => {
    void moveSelectedItem([x + dx, y, z + dz], item.transform.rotationY)
  }

  return (
    <div className="shrink-0 border-border/50 border-t bg-sidebar p-3">
      <div className="mb-2 flex items-start justify-between">
        <div>
          <p className="font-medium text-[13px]">{entry?.name ?? item.kindId}</p>
          <p className="mt-0.5 text-[10px] text-muted-foreground">
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
        <span className="rounded-md bg-background/40 px-1.5 py-1">宽 {size[0].toFixed(2)}m</span>
        <span className="rounded-md bg-background/40 px-1.5 py-1">深 {size[1].toFixed(2)}m</span>
        <span className="rounded-md bg-background/40 px-1.5 py-1">高 {size[2].toFixed(2)}m</span>
      </div>

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
              onClick={() => void rotateSelectedItem(-Math.PI / 12)}
              size="icon"
              variant="outline"
            >
              <RotateCcw className="h-3.5 w-3.5" />
            </Button>
            <Button
              aria-label="顺时针旋转 15 度"
              onClick={() => void rotateSelectedItem(Math.PI / 12)}
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
    </div>
  )
}

export function Sidebar() {
  const sidebarTab = useSceneStore((state) => state.sidebarTab)
  const scene = useSceneStore((state) => state.scene)
  const catalog = useSceneStore((state) => state.catalog)
  const loading = useSceneStore((state) => state.loading)
  const meta = TAB_META[sidebarTab]

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
          <Badge tone="neutral">M1</Badge>
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
        ) : sidebarTab === 'items' ? (
          <ItemsTab catalog={catalog} scene={scene} />
        ) : (
          <AiTab />
        )}
      </div>

      <ItemDetails />
    </aside>
  )
}
