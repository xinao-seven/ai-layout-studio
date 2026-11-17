import { PanelRightClose, Sparkles } from 'lucide-react'
import { cn } from '../../lib/cn'
import { useSceneStore } from '../../store/sceneStore'
import {
  clampDockWidth,
  DOCK_DEFAULT_WIDTH,
  DOCK_MAX_WIDTH,
  DOCK_MIN_WIDTH,
  useUiStore,
} from '../../store/uiStore'
import { ChatPanel } from '../agent/ChatPanel'
import { Badge } from '../ui/badge'
import { Button } from '../ui/button'
import { ResizeHandle } from './ResizeHandle'

function statusLabel(status: string | null): string {
  if (status === 'running') return '运行中'
  if (status === 'queued') return '排队中'
  if (status === 'waiting_user') return '等待回答'
  if (status === 'done') return '已完成'
  if (status === 'failed') return '失败'
  if (status === 'cancelled') return '已取消'
  return '空闲'
}

function connectionLabel(connection: string): string {
  if (connection === 'open') return '已同步'
  if (connection === 'connecting') return '连接中'
  if (connection === 'reconnecting') return '重连中'
  return '未连接'
}

export function AgentDock({ overlay }: { overlay: boolean }) {
  const dockWidth = useUiStore((state) => state.dockWidth)
  const setDockWidth = useUiStore((state) => state.setDockWidth)
  const setDockOpen = useUiStore((state) => state.setDockOpen)
  const capabilities = useUiStore((state) => state.capabilities)
  const turnStatus = useSceneStore((state) => state.turnStatus)
  const connection = useSceneStore((state) => state.connection)

  return (
    <aside
      className={cn('agent-dock', overlay && 'agent-dock--overlay')}
      style={{ width: dockWidth }}
    >
      <ResizeHandle
        computeWidth={(clientX) =>
          clampDockWidth(window.innerWidth - clientX, overlay ? undefined : window.innerWidth)
        }
        label="调整 AI 面板宽度"
        max={DOCK_MAX_WIDTH}
        min={DOCK_MIN_WIDTH}
        onReset={() => setDockWidth(DOCK_DEFAULT_WIDTH)}
        onResize={setDockWidth}
        width={dockWidth}
      />
      <header className="agent-dock-head">
        <span className="agent-dock-mark">
          <Sparkles className="h-3.5 w-3.5" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <p className="truncate font-medium text-[12px] leading-4">AI 布局助手</p>
            {capabilities?.model ? <Badge tone="neutral">{capabilities.model}</Badge> : null}
          </div>
          <p className="truncate text-[9px] text-muted-foreground">
            {statusLabel(turnStatus)} · {connectionLabel(connection)}
          </p>
        </div>
        <Button
          aria-label="收起 AI 面板"
          onClick={() => setDockOpen(false)}
          size="icon"
          title="收起（双击分隔条可复位宽度）"
          variant="ghost"
        >
          <PanelRightClose className="h-3.5 w-3.5" />
        </Button>
      </header>
      <div className="min-h-0 flex-1">
        <ChatPanel />
      </div>
    </aside>
  )
}
