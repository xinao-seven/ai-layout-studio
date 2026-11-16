import { Box, MousePointer2, Pencil, RotateCcw, Square } from 'lucide-react'
import { useSceneStore } from '../../store/sceneStore'
import { Button } from '../ui/button'
import { Tooltip, TooltipContent, TooltipTrigger } from '../ui/tooltip'

export function FloatingToolbar({ perfMode }: { perfMode: boolean }) {
  const viewMode = useSceneStore((state) => state.viewMode)
  const setViewMode = useSceneStore((state) => state.setViewMode)
  const resetCamera = useSceneStore((state) => state.resetCamera)
  const tool = useSceneStore((state) => state.tool)
  const setTool = useSceneStore((state) => state.setTool)

  return (
    <div className="pointer-events-none absolute top-3 right-3 z-20 flex items-center gap-2">
      <div className="pointer-events-auto inline-flex h-8 items-stretch overflow-hidden rounded-xl border border-border bg-background/90 shadow-2xl backdrop-blur-md">
        <button
          className={`flex items-center gap-1.5 px-2.5 text-[11px] transition-colors ${
            tool === 'select'
              ? 'bg-white/10 text-foreground'
              : 'text-muted-foreground hover:bg-white/[0.06] hover:text-foreground'
          }`}
          onClick={() => setTool('select')}
          type="button"
        >
          <MousePointer2 className="h-3.5 w-3.5" />
          选择
        </button>
        <span className="my-1.5 w-px bg-border/70" />
        <button
          className={`flex items-center gap-1.5 px-2.5 text-[11px] transition-colors ${
            tool === 'draw-room'
              ? 'bg-primary/20 text-foreground'
              : 'text-muted-foreground hover:bg-white/[0.06] hover:text-foreground'
          }`}
          onClick={() => setTool(tool === 'draw-room' ? 'select' : 'draw-room')}
          type="button"
        >
          <Pencil className="h-3.5 w-3.5" />
          画房间
        </button>
      </div>

      <div className="pointer-events-auto inline-flex h-8 items-stretch overflow-hidden rounded-xl border border-border bg-background/90 shadow-2xl backdrop-blur-md">
        <button
          className={`flex items-center gap-1.5 px-2.5 text-[11px] transition-colors ${
            viewMode === '3d'
              ? 'bg-white/10 text-foreground'
              : 'text-muted-foreground hover:bg-white/[0.06] hover:text-foreground'
          }`}
          onClick={() => setViewMode('3d')}
          type="button"
        >
          <Box className="h-3.5 w-3.5" />
          3D
        </button>
        <span className="my-1.5 w-px bg-border/70" />
        <button
          className={`flex items-center gap-1.5 px-2.5 text-[11px] transition-colors ${
            viewMode === 'top'
              ? 'bg-white/10 text-foreground'
              : 'text-muted-foreground hover:bg-white/[0.06] hover:text-foreground'
          }`}
          onClick={() => setViewMode('top')}
          type="button"
        >
          <Square className="h-3.5 w-3.5" />
          俯视
        </button>
      </div>

      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            aria-label="重置相机"
            className="pointer-events-auto h-8 w-8 rounded-xl border border-border bg-background/90 shadow-2xl backdrop-blur-md"
            onClick={resetCamera}
            size="icon"
            variant="ghost"
          >
            <RotateCcw className="h-3.5 w-3.5" />
          </Button>
        </TooltipTrigger>
        <TooltipContent side="bottom">重置相机</TooltipContent>
      </Tooltip>

      {perfMode ? (
        <span className="pointer-events-auto rounded-full border border-sky-400/30 bg-sky-400/10 px-2 py-1 text-[10px] text-sky-300">
          200 proxies
        </span>
      ) : null}
    </div>
  )
}
