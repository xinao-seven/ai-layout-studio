import { Send, Square } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Button } from '../ui/button'
import { ThinkingLevelControl } from './ThinkingLevelControl'

export function Composer({
  busy,
  contextLabel,
  onCancel,
  onSend,
}: {
  busy: boolean
  contextLabel: string | null
  onCancel: () => void
  onSend: (text: string) => void
}) {
  const [draft, setDraft] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)

  useEffect(() => {
    const textarea = textareaRef.current
    if (!textarea) return
    textarea.style.height = 'auto'
    textarea.style.height = `${Math.min(textarea.scrollHeight, 180)}px`
  }, [])

  const resize = () => {
    const textarea = textareaRef.current
    if (!textarea) return
    textarea.style.height = 'auto'
    textarea.style.height = `${Math.min(textarea.scrollHeight, 180)}px`
  }

  const submit = () => {
    const text = draft.trim()
    if (!text) return
    setDraft('')
    if (textareaRef.current) textareaRef.current.style.height = 'auto'
    onSend(text)
  }

  return (
    <div className="composer">
      <div className="composer-controls">
        <ThinkingLevelControl />
        <span className="composer-context">{contextLabel ?? '写操作需确认后落库'}</span>
      </div>
      <div className="composer-box">
        <textarea
          onChange={(event) => {
            setDraft(event.target.value)
            resize()
          }}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault()
              submit()
            }
          }}
          placeholder="描述你想怎么布置…（Enter 发送，Shift+Enter 换行）"
          ref={textareaRef}
          rows={1}
          value={draft}
        />
        <div className="composer-actions">
          <span className="composer-hint">Enter 发送 · Shift+Enter 换行</span>
          {busy ? (
            <Button
              className="h-7 gap-1 px-2 text-[11px]"
              onClick={onCancel}
              size="sm"
              variant="ghost"
            >
              <Square className="h-3 w-3" />
              停止
            </Button>
          ) : null}
          <Button
            className="h-7 gap-1 px-2.5 text-[11px]"
            disabled={!draft.trim()}
            onClick={submit}
            size="sm"
          >
            <Send className="h-3 w-3" />
            {busy ? '排队' : '发送'}
          </Button>
        </div>
      </div>
    </div>
  )
}
