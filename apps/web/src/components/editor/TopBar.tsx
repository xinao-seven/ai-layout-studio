import {
  ArrowLeft,
  Circle,
  Download,
  ImageDown,
  Loader2,
  Redo2,
  Sparkles,
  Undo2,
  Wifi,
  WifiOff,
} from 'lucide-react'
import { useSceneStore } from '../../store/sceneStore'
import { Badge } from '../ui/badge'
import { Button } from '../ui/button'
import { Tooltip, TooltipContent, TooltipTrigger } from '../ui/tooltip'

function ConnectionIndicator() {
  const connection = useSceneStore((state) => state.connection)
  const labels = {
    idle: '未连接',
    connecting: '连接中',
    open: '已同步',
    reconnecting: '重连中',
    closed: '已断开',
  } as const
  const tone =
    connection === 'open'
      ? 'text-emerald-400'
      : connection === 'reconnecting' || connection === 'connecting'
        ? 'text-amber-400'
        : 'text-muted-foreground'
  const Icon =
    connection === 'connecting' || connection === 'reconnecting'
      ? Loader2
      : connection === 'open'
        ? Wifi
        : WifiOff
  return (
    <span className={`flex items-center gap-1.5 text-[11px] ${tone}`}>
      <Icon
        className={`h-3 w-3 ${connection === 'reconnecting' || connection === 'connecting' ? 'animate-spin' : ''}`}
      />
      {labels[connection]}
    </span>
  )
}

export function TopBar() {
  const scene = useSceneStore((state) => state.scene)
  const scenes = useSceneStore((state) => state.scenes)
  const undoDepth = useSceneStore((state) => state.undoDepth)
  const redoDepth = useSceneStore((state) => state.redoDepth)
  const closeScene = useSceneStore((state) => state.closeScene)
  const undo = useSceneStore((state) => state.undo)
  const redo = useSceneStore((state) => state.redo)
  const exportSceneJson = useSceneStore((state) => state.exportSceneJson)
  const exportScenePng = useSceneStore((state) => state.exportScenePng)

  const summary = scenes.find((candidate) => candidate.id === scene?.id)
  const area = summary?.areaM2 ?? 0

  return (
    <header className="z-20 flex h-12 shrink-0 items-center gap-2 border-border/50 border-b bg-sidebar px-2 lg:px-3">
      <Button onClick={closeScene} size="icon" title="返回项目" variant="ghost">
        <ArrowLeft className="h-4 w-4" />
      </Button>

      <div className="h-5 w-px bg-border/60" />

      <div className="flex min-w-0 items-center gap-2">
        <span className="relative flex h-7 w-7 shrink-0 items-center justify-center overflow-hidden rounded-lg bg-gradient-to-br from-sky-300 via-indigo-400 to-emerald-300">
          <span className="h-2.5 w-2.5 rounded-[2px] border border-black/40 bg-white/80" />
        </span>
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h1 className="truncate font-medium text-sm">{scene?.name ?? '加载中…'}</h1>
            <Badge tone="accent">M5</Badge>
          </div>
          <p className="flex items-center gap-2 text-[10px] text-muted-foreground">
            <span>v{scene?.version ?? 1}</span>
            <Circle className="h-1 w-1 fill-current" />
            <span>{area.toFixed(1)}㎡</span>
            <Circle className="h-1 w-1 fill-current" />
            <span>{scene?.rooms.length ?? 0} 个房间</span>
          </p>
        </div>
      </div>

      <div className="flex-1" />

      <ConnectionIndicator />
      <div className="h-5 w-px bg-border/60" />

      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            aria-label="撤销"
            disabled={undoDepth <= 0}
            onClick={() => void undo()}
            size="icon"
            variant="ghost"
          >
            <Undo2 className="h-4 w-4" />
          </Button>
        </TooltipTrigger>
        <TooltipContent side="bottom">撤销（服务端版本）</TooltipContent>
      </Tooltip>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            aria-label="重做"
            disabled={redoDepth <= 0}
            onClick={() => void redo()}
            size="icon"
            variant="ghost"
          >
            <Redo2 className="h-4 w-4" />
          </Button>
        </TooltipTrigger>
        <TooltipContent side="bottom">重做</TooltipContent>
      </Tooltip>

      <div className="h-5 w-px bg-border/60" />

      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            aria-label="导出方案 JSON"
            disabled={!scene}
            onClick={() => void exportSceneJson()}
            size="icon"
            variant="ghost"
          >
            <Download className="h-4 w-4" />
          </Button>
        </TooltipTrigger>
        <TooltipContent side="bottom">导出方案 JSON（含 schemaVersion，可再导入）</TooltipContent>
      </Tooltip>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            aria-label="导出场景 PNG"
            disabled={!scene}
            onClick={exportScenePng}
            size="icon"
            variant="ghost"
          >
            <ImageDown className="h-4 w-4" />
          </Button>
        </TooltipTrigger>
        <TooltipContent side="bottom">导出当前视图 PNG</TooltipContent>
      </Tooltip>

      <div className="hidden items-center gap-1.5 rounded-full border border-border/70 bg-background/50 px-2.5 py-1 text-[10px] text-muted-foreground sm:flex">
        <Sparkles className="h-3 w-3" />
        Agent 已就绪
      </div>
    </header>
  )
}
