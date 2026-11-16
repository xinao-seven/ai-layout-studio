import type { WsEnvelope } from '../contracts/generated/ws'

export type ConnectionStatus = 'idle' | 'connecting' | 'open' | 'reconnecting' | 'closed'

export interface SceneSocketHandlers {
  onEnvelope: (envelope: WsEnvelope) => void
  onStatus: (status: ConnectionStatus) => void
}

const PING_INTERVAL_MS = 15_000
const MAX_RECONNECT_DELAY_MS = 5_000

function websocketUrl(sceneId: string): string {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.host}/ws?sceneId=${encodeURIComponent(sceneId)}`
}

export class SceneSocket {
  private readonly sceneId: string
  private readonly handlers: SceneSocketHandlers
  private socket: WebSocket | null = null
  private reconnectTimer: number | null = null
  private pingTimer: number | null = null
  private reconnectAttempt = 0
  private resumeStateSeq = 0
  private resumeTurnSeq = 0
  private queuedHost: Array<{
    type: string
    payload: Record<string, unknown>
    turnId?: string | null
    requestId?: string | null
  }> = []
  private disposed = false

  constructor(sceneId: string, handlers: SceneSocketHandlers, lastStateSeq = 0, lastTurnSeq = 0) {
    this.sceneId = sceneId
    this.handlers = handlers
    this.resumeStateSeq = lastStateSeq
    this.resumeTurnSeq = lastTurnSeq
  }

  connect(): void {
    if (this.disposed) return
    this.handlers.onStatus(this.reconnectAttempt === 0 ? 'connecting' : 'reconnecting')
    try {
      this.socket = new WebSocket(websocketUrl(this.sceneId))
    } catch {
      this.scheduleReconnect()
      return
    }

    this.socket.addEventListener('open', () => {
      this.reconnectAttempt = 0
      this.handlers.onStatus('open')
      this.sendResume()
      this.flushQueued()
      this.startPing()
    })

    this.socket.addEventListener('message', (event) => {
      if (typeof event.data !== 'string') return
      try {
        const envelope = JSON.parse(event.data) as WsEnvelope
        this.handlers.onEnvelope(envelope)
      } catch {
        // Ignore malformed frames; the server owns the contract.
      }
    })

    this.socket.addEventListener('close', () => {
      this.stopPing()
      this.socket = null
      if (this.disposed) {
        this.handlers.onStatus('closed')
        return
      }
      this.scheduleReconnect()
    })

    this.socket.addEventListener('error', () => {
      // `close` follows and drives the reconnect loop.
    })
  }

  private sendResume(): void {
    if (this.resumeStateSeq <= 0 && this.resumeTurnSeq <= 0) return
    this.send({
      v: 1,
      ch: 'host',
      type: 'resume',
      seq: 0,
      ts: Date.now(),
      payload: { lastSeqs: { state: this.resumeStateSeq, turn: this.resumeTurnSeq } },
    } as WsEnvelope)
  }

  private startPing(): void {
    this.stopPing()
    this.pingTimer = window.setInterval(() => {
      this.send({
        v: 1,
        ch: 'host',
        type: 'ping',
        seq: 0,
        ts: Date.now(),
        payload: {},
      } as WsEnvelope)
    }, PING_INTERVAL_MS)
  }

  private stopPing(): void {
    if (this.pingTimer !== null) {
      window.clearInterval(this.pingTimer)
      this.pingTimer = null
    }
  }

  private send(envelope: WsEnvelope): void {
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify(envelope))
    }
  }

  private scheduleReconnect(): void {
    this.reconnectAttempt += 1
    const delay = Math.min(500 * 2 ** (this.reconnectAttempt - 1), MAX_RECONNECT_DELAY_MS)
    this.handlers.onStatus('reconnecting')
    if (this.reconnectTimer !== null) window.clearTimeout(this.reconnectTimer)
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null
      this.connect()
    }, delay)
  }

  setResumeSequence(seq: number): void {
    this.resumeStateSeq = seq
  }

  setResumeTurnSequence(seq: number): void {
    this.resumeTurnSeq = seq
  }

  sendHost(
    type: string,
    payload: Record<string, unknown>,
    turnId?: string | null,
    requestId?: string | null,
  ): void {
    if (this.socket?.readyState !== WebSocket.OPEN) {
      this.queuedHost.push({ type, payload, turnId, requestId })
      return
    }
    this.send({
      v: 1,
      ch: 'host',
      type,
      seq: 0,
      ts: Date.now(),
      turnId: turnId ?? null,
      requestId: requestId ?? null,
      payload,
    } as WsEnvelope)
  }

  private flushQueued(): void {
    const queued = this.queuedHost
    this.queuedHost = []
    for (const message of queued) {
      this.sendHost(message.type, message.payload, message.turnId, message.requestId)
    }
  }

  close(): void {
    this.disposed = true
    this.stopPing()
    if (this.reconnectTimer !== null) {
      window.clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    this.socket?.close()
    this.socket = null
    this.handlers.onStatus('closed')
  }
}
