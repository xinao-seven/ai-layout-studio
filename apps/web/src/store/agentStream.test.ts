import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { WsEnvelope } from '../contracts/generated/ws'
import { useSceneStore } from './sceneStore'

function turnEnvelope(type: string, payload: Record<string, unknown>, seq: number): WsEnvelope {
  return { v: 1, ch: 'turn', type, seq, ts: 0, payload } as WsEnvelope
}

describe('agent streaming state', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    useSceneStore.setState({
      chatMessages: [],
      lastTurnSeq: 0,
      lastStateSeq: 0,
      turnStatus: null,
      proposals: {},
      waitingUser: null,
    })
  })

  afterEach(() => {
    vi.advanceTimersByTime(50)
    vi.useRealTimers()
  })

  it('appends coalesced text deltas into one streaming bubble', () => {
    const { handleWsEnvelope } = useSceneStore.getState()
    handleWsEnvelope(turnEnvelope('assistant.delta', { text: '你' }, 1))
    handleWsEnvelope(turnEnvelope('assistant.delta', { text: '好' }, 2))
    vi.advanceTimersByTime(20)

    const entries = useSceneStore.getState().chatMessages
    expect(entries).toHaveLength(1)
    expect(entries[0]?.content).toBe('你好')
    expect(entries[0]?.streaming).toBe(true)
  })

  it('keeps reasoning separate from the answer', () => {
    const { handleWsEnvelope } = useSceneStore.getState()
    handleWsEnvelope(turnEnvelope('assistant.reasoning_delta', { text: '先看房间' }, 1))
    vi.advanceTimersByTime(20)
    handleWsEnvelope(turnEnvelope('assistant.delta', { text: '好的' }, 2))
    vi.advanceTimersByTime(20)

    const entry = useSceneStore.getState().chatMessages[0]
    expect(entry?.reasoning).toBe('先看房间')
    expect(entry?.content).toBe('好的')
  })

  it('replaces streamed text with the authoritative assistant message', () => {
    const { handleWsEnvelope } = useSceneStore.getState()
    handleWsEnvelope(turnEnvelope('assistant.delta', { text: '部分' }, 1))
    vi.advanceTimersByTime(20)
    handleWsEnvelope(
      turnEnvelope(
        'assistant.message',
        { content: '完整回答', reasoning: '完整思考', toolCalls: [], finishReason: 'stop' },
        2,
      ),
    )

    const entry = useSceneStore.getState().chatMessages[0]
    expect(entry?.content).toBe('完整回答')
    expect(entry?.reasoning).toBe('完整思考')
    expect(entry?.streaming).toBe(false)
  })

  it('finalizes a dangling streaming bubble when the turn is cancelled', () => {
    const { handleWsEnvelope } = useSceneStore.getState()
    handleWsEnvelope(turnEnvelope('assistant.delta', { text: '半句' }, 1))
    vi.advanceTimersByTime(20)
    handleWsEnvelope(turnEnvelope('turn.cancelled', {}, 2))

    const entries = useSceneStore.getState().chatMessages
    expect(entries[0]?.streaming).toBe(false)
    expect(entries.at(-1)?.role).toBe('system')
  })
})
