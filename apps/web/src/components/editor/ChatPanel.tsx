import { Check, CircleAlert, Loader2, Send, Sparkles, Square, Wrench, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { Proposal } from '../../contracts/generated/client/rest'
import { pendingProposalList } from '../../scene/proposals'
import { useSceneStore } from '../../store/sceneStore'
import { Badge } from '../ui/badge'
import { Button } from '../ui/button'

function proposalTitle(proposal: Proposal): string {
  const op = proposal.ops[0]
  if (!op) return '空提案'
  if (op.op === 'item.add') return `添加 ${op.item.kindId}`
  if (op.op === 'item.remove') return `移除 ${op.itemId}`
  if (op.op === 'item.update') return `更新 ${op.itemId}`
  return op.op
}

function proposalDetail(proposal: Proposal): string {
  const op = proposal.ops[0]
  if (op?.op === 'item.add') {
    const [x, , z] = op.item.transform.position
    return `${op.item.variantId} · (${x.toFixed(2)}, ${z.toFixed(2)}) · ${Math.round(
      (op.item.transform.rotationY * 180) / Math.PI,
    )}°`
  }
  return proposal.ops.map((candidate) => candidate.op).join(' · ')
}

function statusLabel(status: string | null): string {
  if (status === 'running') return '运行中'
  if (status === 'queued') return '排队中'
  if (status === 'waiting_user') return '等待回答'
  if (status === 'done') return '已完成'
  if (status === 'failed') return '失败'
  if (status === 'cancelled') return '已取消'
  return '空闲'
}

export function ChatPanel() {
  const chatMessages = useSceneStore((state) => state.chatMessages)
  const proposals = useSceneStore((state) => state.proposals)
  const waitingUser = useSceneStore((state) => state.waitingUser)
  const turnStatus = useSceneStore((state) => state.turnStatus)
  const activeTurnId = useSceneStore((state) => state.activeTurnId)
  const connection = useSceneStore((state) => state.connection)
  const pointContext = useSceneStore((state) => state.pointContext)
  const sendChatMessage = useSceneStore((state) => state.sendChatMessage)
  const cancelActiveTurn = useSceneStore((state) => state.cancelActiveTurn)
  const confirmProposal = useSceneStore((state) => state.confirmProposal)
  const rejectProposal = useSceneStore((state) => state.rejectProposal)
  const answerUser = useSceneStore((state) => state.answerUser)

  const [draft, setDraft] = useState('')
  const [selectedOptions, setSelectedOptions] = useState<string[]>([])
  const [waitingText, setWaitingText] = useState('')
  const bottomRef = useRef<HTMLDivElement | null>(null)
  const pending = useMemo(() => pendingProposalList(proposals), [proposals])
  const busy = turnStatus === 'running' || turnStatus === 'queued' || turnStatus === 'waiting_user'

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: 'end' })
  }, [])

  useEffect(() => {
    setSelectedOptions([])
  }, [])

  const submit = () => {
    const text = draft.trim()
    if (!text) return
    setDraft('')
    void sendChatMessage(text)
  }

  const submitAnswer = () => {
    const answers = waitingUser?.options.length
      ? selectedOptions
      : waitingText.trim()
        ? [waitingText.trim()]
        : []
    if (answers.length === 0) return
    answerUser(answers)
    setSelectedOptions([])
    setWaitingText('')
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center justify-between border-b border-border/60 px-3 py-2">
        <div className="flex items-center gap-2">
          <span className="flex h-6 w-6 items-center justify-center rounded-lg border border-primary/25 bg-primary/10">
            <Sparkles className="h-3.5 w-3.5" />
          </span>
          <div>
            <p className="font-medium text-[12px] leading-4">AI 布局助手</p>
            <p className="text-[9px] text-muted-foreground">
              {statusLabel(turnStatus)} · {connection}
            </p>
          </div>
        </div>
        {busy && activeTurnId ? (
          <Button
            className="h-7 gap-1 px-2 text-[10px]"
            onClick={() => void cancelActiveTurn()}
            size="sm"
            variant="ghost"
          >
            <Square className="h-3 w-3" />
            取消
          </Button>
        ) : null}
      </div>

      <div className="min-h-0 flex-1 space-y-2 overflow-y-auto px-3 py-3">
        {chatMessages.length === 0 ? (
          <div className="rounded-xl border border-primary/20 bg-primary/5 p-3 text-center">
            <p className="text-[11px] font-medium">用一句话布置房间</p>
            <p className="mt-1 text-[10px] text-muted-foreground leading-4">
              例如“在客厅放一个双人沙发，靠电视墙，再配个茶几”。写操作会先生成幽灵提案，
              确认后才落库。
            </p>
            {pointContext ? (
              <p className="mt-2 truncate rounded-md border border-border/60 bg-background/40 px-2 py-1 font-mono text-[9px] text-muted-foreground">
                上下文：{pointContext.itemId} · {pointContext.kindId}
              </p>
            ) : null}
          </div>
        ) : null}

        {chatMessages.map((entry) => {
          if (entry.role === 'tool') {
            const failed = entry.status === 'failed'
            const running = entry.status === 'running'
            return (
              <div
                className={`rounded-lg border px-2 py-1.5 font-mono text-[9px] ${
                  failed
                    ? 'border-destructive/40 bg-destructive/10 text-destructive'
                    : 'border-border/60 bg-background/40 text-muted-foreground'
                }`}
                key={entry.id}
              >
                <div className="flex items-center gap-1.5">
                  {running ? (
                    <Loader2 className="h-3 w-3 animate-spin" />
                  ) : failed ? (
                    <CircleAlert className="h-3 w-3" />
                  ) : (
                    <Wrench className="h-3 w-3" />
                  )}
                  <span className="font-semibold">{entry.name ?? 'tool'}</span>
                  {entry.arguments ? (
                    <span className="truncate opacity-70">{JSON.stringify(entry.arguments)}</span>
                  ) : null}
                </div>
                {entry.content ? <p className="mt-1 break-words">{entry.content}</p> : null}
              </div>
            )
          }
          if (entry.role === 'system') {
            return (
              <p
                className="rounded-lg bg-muted/40 px-2 py-1 text-center text-[9px] text-muted-foreground"
                key={entry.id}
              >
                {entry.content}
              </p>
            )
          }
          const user = entry.role === 'user'
          return (
            <div className={`flex ${user ? 'justify-end' : 'justify-start'}`} key={entry.id}>
              <div
                className={`max-w-[92%] whitespace-pre-wrap rounded-xl px-2.5 py-1.5 text-[11px] leading-5 ${
                  user
                    ? 'bg-primary/15 text-foreground'
                    : 'border border-border/60 bg-background/50'
                }`}
              >
                {entry.content || (entry.streaming ? '…' : '')}
                {entry.screenshotBefore || entry.screenshotAfter ? (
                  <div className="mt-1.5 flex gap-1.5">
                    {entry.screenshotBefore ? (
                      <a href={entry.screenshotBefore} rel="noreferrer" target="_blank">
                        <img
                          alt="操作前"
                          className="h-14 w-20 rounded border border-border/60 object-cover"
                          src={entry.screenshotBefore}
                        />
                      </a>
                    ) : null}
                    {entry.screenshotAfter ? (
                      <a href={entry.screenshotAfter} rel="noreferrer" target="_blank">
                        <img
                          alt="操作后"
                          className="h-14 w-20 rounded border border-border/60 object-cover"
                          src={entry.screenshotAfter}
                        />
                      </a>
                    ) : null}
                  </div>
                ) : null}
              </div>
            </div>
          )
        })}

        {pending.length > 0 ? (
          <div className="space-y-2 pt-1">
            <p className="text-[9px] font-medium text-muted-foreground">待确认提案</p>
            {pending.map((proposal) => (
              <div
                className="rounded-xl border border-primary/30 bg-primary/5 p-2.5"
                key={proposal.id}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="truncate text-[11px] font-medium">{proposalTitle(proposal)}</p>
                    <p className="mt-0.5 truncate font-mono text-[9px] text-muted-foreground">
                      {proposalDetail(proposal)}
                    </p>
                  </div>
                  <Badge tone="accent">待确认</Badge>
                </div>
                {(proposal.dependsOn ?? []).length > 0 ? (
                  <p className="mt-1 text-[9px] text-muted-foreground">
                    依赖 {(proposal.dependsOn ?? []).length} 个前序提案，需按顺序确认
                  </p>
                ) : null}
                <div className="mt-2 flex gap-1.5">
                  <Button
                    className="h-7 flex-1 gap-1 text-[10px]"
                    onClick={() => confirmProposal(proposal.id)}
                    size="sm"
                  >
                    <Check className="h-3 w-3" />
                    确认
                  </Button>
                  <Button
                    className="h-7 gap-1 text-[10px]"
                    onClick={() => rejectProposal(proposal.id)}
                    size="sm"
                    variant="ghost"
                  >
                    <X className="h-3 w-3" />
                    拒绝
                  </Button>
                </div>
              </div>
            ))}
          </div>
        ) : null}

        {waitingUser ? (
          <div className="rounded-xl border border-warning/40 bg-warning/10 p-2.5">
            <p className="text-[11px] font-medium">{waitingUser.question}</p>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {waitingUser.options.map((option) => {
                const selected = selectedOptions.includes(option)
                return (
                  <button
                    className={`rounded-md border px-2 py-1 text-[10px] ${
                      selected
                        ? 'border-primary/60 bg-primary/20'
                        : 'border-border/70 bg-background/40 hover:bg-muted/40'
                    }`}
                    key={option}
                    onClick={() =>
                      setSelectedOptions((current) =>
                        waitingUser.allowMultiple
                          ? current.includes(option)
                            ? current.filter((value) => value !== option)
                            : [...current, option]
                          : [option],
                      )
                    }
                    type="button"
                  >
                    {option}
                  </button>
                )
              })}
            </div>
            {waitingUser.options.length === 0 ? (
              <input
                className="mt-2 w-full rounded-md border border-border/70 bg-background/60 px-2 py-1 text-[10px] outline-none focus:border-primary/50"
                onChange={(event) => setWaitingText(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') submitAnswer()
                }}
                placeholder="输入回答…"
                value={waitingText}
              />
            ) : null}
            <Button
              className="mt-2 h-7 w-full text-[10px]"
              disabled={
                waitingUser.options.length > 0 ? selectedOptions.length === 0 : !waitingText.trim()
              }
              onClick={submitAnswer}
              size="sm"
            >
              提交回答
            </Button>
          </div>
        ) : null}
        <div ref={bottomRef} />
      </div>

      <div className="border-t border-border/60 p-2">
        <textarea
          className="h-16 w-full resize-none rounded-lg border border-border/70 bg-background/60 px-2 py-1.5 text-[11px] outline-none placeholder:text-muted-foreground focus:border-primary/50"
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault()
              submit()
            }
          }}
          placeholder="描述你想怎么布置…（Enter 发送，Shift+Enter 换行）"
          value={draft}
        />
        <div className="mt-1.5 flex items-center justify-between">
          <p className="text-[9px] text-muted-foreground">
            {pointContext ? `已引用 ${pointContext.kindId}` : '写操作需确认后落库'}
          </p>
          <Button
            className="h-7 gap-1 px-2.5 text-[10px]"
            disabled={!draft.trim()}
            onClick={submit}
            size="sm"
          >
            <Send className="h-3 w-3" />
            发送
          </Button>
        </div>
      </div>
    </div>
  )
}
