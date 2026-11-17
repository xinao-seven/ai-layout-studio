// biome-ignore-all lint/a11y/useSemanticElements: an interactive, focusable splitter is a separator, not an <hr>.
import { useCallback, useEffect, useRef } from 'react'

interface ResizeHandleProps {
  width: number
  min: number
  max: number
  /** Convert a pointer X coordinate into the desired dock width. */
  computeWidth: (clientX: number) => number
  onResize: (width: number) => void
  onReset: () => void
  label: string
}

/**
 * Thin vertical splitter for the agent dock. Pointer moves are rAF-throttled so
 * dragging does not re-render the React tree (and resize the 3D canvas) more
 * than once per frame.
 */
export function ResizeHandle({
  width,
  min,
  max,
  computeWidth,
  onResize,
  onReset,
  label,
}: ResizeHandleProps) {
  const dragging = useRef(false)
  const frame = useRef(0)
  const pending = useRef(0)

  const commit = useCallback(() => {
    frame.current = 0
    onResize(pending.current)
  }, [onResize])

  useEffect(
    () => () => {
      if (frame.current) cancelAnimationFrame(frame.current)
      document.body.classList.remove('resizing')
    },
    [],
  )

  const stop = () => {
    dragging.current = false
    document.body.classList.remove('resizing')
    if (frame.current) {
      cancelAnimationFrame(frame.current)
      frame.current = 0
      onResize(pending.current)
    }
  }

  return (
    <div
      aria-label={label}
      aria-orientation="vertical"
      aria-valuemax={max}
      aria-valuemin={min}
      aria-valuenow={width}
      className="resize-handle"
      onDoubleClick={onReset}
      onKeyDown={(event) => {
        if (event.key === 'ArrowLeft') {
          event.preventDefault()
          onResize(width + 16)
        }
        if (event.key === 'ArrowRight') {
          event.preventDefault()
          onResize(width - 16)
        }
      }}
      onPointerDown={(event) => {
        event.preventDefault()
        dragging.current = true
        pending.current = width
        event.currentTarget.setPointerCapture(event.pointerId)
        document.body.classList.add('resizing')
      }}
      onPointerMove={(event) => {
        if (!dragging.current) return
        pending.current = computeWidth(event.clientX)
        if (!frame.current) frame.current = requestAnimationFrame(commit)
      }}
      onPointerUp={stop}
      onPointerCancel={stop}
      role="separator"
      tabIndex={0}
    />
  )
}
