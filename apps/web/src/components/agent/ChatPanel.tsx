import { ArrowDown, Sparkles } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import * as api from '../../api/client'
import { pendingProposalList } from '../../scene/proposals'
import { useSceneStore } from '../../store/sceneStore'
import { useUiStore } from '../../store/uiStore'
import { AskUserCard } from './AskUserCard'
import { Composer } from './Composer'
import { MessageView } from './MessageView'
import { ProposalCard } from './ProposalCard'
import { ToolCallCard } from './ToolCallCard'

const EXAMPLES = [
  '在客厅放一个双人沙发，靠电视墙，再配个茶几',
  '把主卧布置成睡眠 + 书桌的复合空间',
  '检查一下现在的走道是不是都够 0.9 米',
]

function statusText(
  turnStatus: string | null,
  busy: boolean,
  runningTool: boolean,
  streaming: boolean,
): string {
  if (turnStatus === 'queued') return '排队中'
  if (turnStatus === 'waiting_user') return '等待你的回答'
  if (turnStatus === 'failed') return '回合失败'
  if (turnStatus === 'cancelled') return '已取消'
  if (turnStatus === 'done' && !busy) return ''
  if (runningTool) return '正在执行工具'
  if (streaming) return '正在生成回复'
  return busy ? '正在等待模型' : ''
}

export function ChatPanel() {
  const chatMessages = useSceneStore((state) => state.chatMessages)
  const proposals = useSceneStore((state) => state.proposals)
  const waitingUser = useSceneStore((state) => state.waitingUser)
  const turnStatus = useSceneStore((state) => state.turnStatus)
  const pointContext = useSceneStore((state) => state.pointContext)
  const sendChatMessage = useSceneStore((state) => state.sendChatMessage)
  const cancelActiveTurn = useSceneStore((state) => state.cancelActiveTurn)
  const confirmProposal = useSceneStore((state) => state.confirmProposal)
  const rejectProposal = useSceneStore((state) => state.rejectProposal)
  const answerUser = useSceneStore((state) => state.answerUser)
  const capabilities = useUiStore((state) => state.capabilities)
  const setCapabilities = useUiStore((state) => state.setCapabilities)
  const thinkingLevel = useUiStore((state) => state.thinkingLevel)
  const setThinkingLevel = useUiStore((state) => state.setThinkingLevel)

  const scrollRef = useRef<HTMLDivElement | null>(null)
  const columnRef = useRef<HTMLDivElement | null>(null)
  const bottomRef = useRef<HTMLDivElement | null>(null)
  const [stuck, setStuck] = useState(true)
  const busy = turnStatus === 'running' || turnStatus === 'queued'
  const pending = useMemo(() => pendingProposalList(proposals), [proposals])

  // Load once per session; the server owns provider detection.
  useEffect(() => {
    if (capabilities) return
    let alive = true
    void api
      .getAgentCapabilities()
      .then((next) => {
        if (alive) setCapabilities(next)
      })
      .catch(() => {
        // The chat still works without the selector.
      })
    return () => {
      alive = false
    }
  }, [capabilities, setCapabilities])

  useEffect(() => {
    if (!capabilities?.thinkingLevels) return
    if (!capabilities.thinkingLevels.includes(thinkingLevel)) {
      setThinkingLevel(capabilities.defaultThinkingLevel ?? 'off')
    }
  }, [capabilities, thinkingLevel, setThinkingLevel])

  useEffect(() => {
    const element = scrollRef.current
    if (!element) return
    const onScroll = () => {
      setStuck(element.scrollHeight - element.scrollTop - element.clientHeight < 120)
    }
    element.addEventListener('scroll', onScroll, { passive: true })
    return () => element.removeEventListener('scroll', onScroll)
  }, [])

  const last = chatMessages[chatMessages.length - 1]
  const streaming = Boolean(last?.streaming)
  const watchKey = `${chatMessages.length}:${last?.content.length ?? 0}:${
    last?.reasoning?.length ?? 0
  }:${pending.length}:${waitingUser ? 1 : 0}`

  // biome-ignore lint/correctness/useExhaustiveDependencies: watchKey is the streaming scroll trigger.
  useEffect(() => {
    if (!stuck) return
    bottomRef.current?.scrollIntoView({ block: 'end', behavior: streaming ? 'auto' : 'smooth' })
  }, [watchKey, stuck, streaming])

  // Markdown, screenshots and expanded tool cards change the content height
  // without a new message; keep following when the user was already at the end.
  useEffect(() => {
    const column = columnRef.current
    if (!column || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(() => {
      if (!stuck) return
      bottomRef.current?.scrollIntoView({ block: 'end', behavior: 'auto' })
    })
    observer.observe(column)
    return () => observer.disconnect()
  }, [stuck])

  const runningTool = chatMessages.some(
    (entry) => entry.role === 'tool' && entry.status === 'running',
  )
  const label = statusText(turnStatus, busy, runningTool, streaming)

  return (
    <div className="chat-panel">
      <div className="chat-scroller subtle-scrollbar" ref={scrollRef}>
        <div className="chat-column" ref={columnRef}>
          {chatMessages.length === 0 ? (
            <div className="chat-empty">
              <span className="chat-empty-mark">
                <Sparkles className="h-4 w-4" />
              </span>
              <h2>把想法说出来，房间自己长出来</h2>
              <p>
                AI 只决定语义关系（靠墙、相邻、留出通道），服务端求解器负责坐标、碰撞与版本。
                写操作会先生成幽灵提案，确认后才落库。
              </p>
              <div className="chat-examples">
                {EXAMPLES.map((example) => (
                  <button
                    className="chat-example"
                    key={example}
                    onClick={() => void sendChatMessage(example)}
                    type="button"
                  >
                    {example}
                  </button>
                ))}
              </div>
            </div>
          ) : null}

          {chatMessages.map((entry) =>
            entry.role === 'tool' ? (
              <ToolCallCard entry={entry} key={entry.id} />
            ) : (
              <MessageView entry={entry} key={entry.id} />
            ),
          )}

          {pending.length > 0 ? (
            <div className="chat-pending">
              <p className="chat-section-label">待确认提案</p>
              {pending.map((proposal) => (
                <ProposalCard
                  key={proposal.id}
                  onConfirm={() => confirmProposal(proposal.id)}
                  onReject={() => rejectProposal(proposal.id)}
                  proposal={proposal}
                />
              ))}
            </div>
          ) : null}

          {waitingUser ? (
            <AskUserCard
              key={`${waitingUser.turnId}:${waitingUser.question}`}
              onAnswer={answerUser}
              request={waitingUser}
            />
          ) : null}

          {label ? (
            <div className="agent-status" role="status">
              <span className="status-pulse" aria-hidden="true" />
              {label}
            </div>
          ) : null}

          <div ref={bottomRef} />
        </div>

        {!stuck ? (
          <button
            className="jump-latest"
            onClick={() => {
              setStuck(true)
              bottomRef.current?.scrollIntoView({ block: 'end', behavior: 'smooth' })
            }}
            type="button"
          >
            <ArrowDown className="h-3 w-3" />
            回到最新
          </button>
        ) : null}
      </div>

      <Composer
        busy={busy}
        contextLabel={pointContext ? `已引用 ${pointContext.kindId}` : null}
        onCancel={() => void cancelActiveTurn()}
        onSend={(text) => void sendChatMessage(text)}
      />
    </div>
  )
}
