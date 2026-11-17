import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createStreamCoalescer, type StreamDelta } from './streamCoalescer'

describe('stream coalescer', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('batches tokens into one flush per timer turn', () => {
    const flushed: StreamDelta[] = []
    const coalescer = createStreamCoalescer((delta) => flushed.push(delta))

    coalescer.pushText('你')
    coalescer.pushText('好')
    coalescer.pushReasoning('先看')
    expect(flushed).toHaveLength(0)

    vi.advanceTimersByTime(20)
    expect(flushed).toEqual([{ text: '你好', reasoning: '先看' }])
    expect(flushed[0]).toBeDefined()
    expect(flushed.length).toBe(1)
  })

  it('flushes immediately before non-delta events', () => {
    const flushed: StreamDelta[] = []
    const coalescer = createStreamCoalescer((delta) => flushed.push(delta))

    coalescer.pushText('abc')
    coalescer.flushNow()
    expect(flushed).toEqual([{ text: 'abc', reasoning: '' }])

    coalescer.flushNow()
    expect(flushed).toHaveLength(1)
  })

  it('drops buffered text on dispose', () => {
    const flushed: StreamDelta[] = []
    const coalescer = createStreamCoalescer((delta) => flushed.push(delta))
    coalescer.pushText('abc')
    coalescer.dispose()
    vi.advanceTimersByTime(50)
    expect(flushed).toHaveLength(0)
  })
})
