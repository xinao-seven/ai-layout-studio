import { useEffect, useRef } from 'react'

interface ThinkingBlockProps {
  content: string
  streaming: boolean
}

/**
 * Collapsible reasoning trace. It opens itself while the model is thinking and
 * folds away when the turn moves on, so the answer stays the focus.
 */
export function ThinkingBlock({ content, streaming }: ThinkingBlockProps) {
  const ref = useRef<HTMLDetailsElement | null>(null)

  useEffect(() => {
    if (ref.current) ref.current.open = streaming
  }, [streaming])

  if (!content.trim()) return null

  return (
    <details className="thinking-block" ref={ref}>
      <summary>
        <span className="thinking-orbit" aria-hidden="true" />
        <span>思考过程</span>
        {streaming ? <span className="thinking-live">LIVE</span> : null}
      </summary>
      <div className="thinking-content">{content}</div>
    </details>
  )
}
