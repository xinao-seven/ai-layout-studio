import { Loader2 } from 'lucide-react'
import { memo } from 'react'
import { prettyPayload, toolHint } from '../../agent/toolHint'
import { cn } from '../../lib/cn'
import type { ChatEntry } from '../../store/sceneStore'

const STATUS_LABEL = {
  running: '运行中',
  done: '完成',
  failed: '失败',
} as const

export const ToolCallCard = memo(function ToolCallCard({ entry }: { entry: ChatEntry }) {
  const status = entry.status ?? 'running'
  const hint = toolHint(entry.name ?? '', entry.arguments)
  const args = prettyPayload(entry.arguments)
  const result = prettyPayload(entry.result ?? entry.content)

  return (
    <details
      className={cn(
        'tool-call',
        status === 'failed' && 'tool-call--error',
        status === 'done' && 'tool-call--success',
      )}
      open={status === 'failed'}
    >
      <summary>
        <span className="tool-glyph">›_</span>
        <span className="tool-name">{entry.name ?? 'tool'}</span>
        {hint ? (
          <span className="tool-hint" title={hint}>
            {hint}
          </span>
        ) : null}
        <span className={cn('tool-status', status === 'running' && 'tool-status--running')}>
          {status === 'running' ? <Loader2 className="h-2.5 w-2.5 animate-spin" /> : null}
          {STATUS_LABEL[status]}
        </span>
      </summary>
      {args ? (
        <div className="tool-section">
          <div className="tool-label">参数</div>
          <pre>{args}</pre>
        </div>
      ) : null}
      {result ? (
        <div className="tool-section">
          <div className="tool-label">结果</div>
          <pre>{result}</pre>
        </div>
      ) : null}
    </details>
  )
})
