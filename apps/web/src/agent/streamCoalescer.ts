/**
 * Coalesces WebSocket streaming deltas into at most one store update per frame.
 *
 * Providers emit one `assistant.delta` per token. Writing each of them into
 * Zustand (which copies the message array and re-renders markdown) makes long
 * answers quadratic. Buffering until the next animation frame keeps the text
 * visually continuous without the churn.
 */

export interface StreamDelta {
  text: string
  reasoning: string
}

type Flush = (delta: StreamDelta) => void

function scheduleFrame(callback: () => void): () => void {
  if (typeof requestAnimationFrame === 'function') {
    const handle = requestAnimationFrame(callback)
    return () => cancelAnimationFrame(handle)
  }
  const handle = setTimeout(callback, 16) as unknown as number
  return () => clearTimeout(handle)
}

export interface StreamCoalescer {
  pushText: (text: string) => void
  pushReasoning: (text: string) => void
  /** Apply everything buffered immediately (call before non-delta events). */
  flushNow: () => void
  dispose: () => void
}

export function createStreamCoalescer(flush: Flush): StreamCoalescer {
  let text = ''
  let reasoning = ''
  let cancelFrame: (() => void) | null = null

  const run = () => {
    cancelFrame = null
    if (!text && !reasoning) return
    const delta = { text, reasoning }
    text = ''
    reasoning = ''
    flush(delta)
  }

  const schedule = () => {
    if (cancelFrame) return
    cancelFrame = scheduleFrame(run)
  }

  return {
    pushText(part: string) {
      if (!part) return
      text += part
      schedule()
    },
    pushReasoning(part: string) {
      if (!part) return
      reasoning += part
      schedule()
    },
    flushNow() {
      if (cancelFrame) {
        cancelFrame()
        cancelFrame = null
      }
      run()
    },
    dispose() {
      if (cancelFrame) {
        cancelFrame()
        cancelFrame = null
      }
      text = ''
      reasoning = ''
    },
  }
}
