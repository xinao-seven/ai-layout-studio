import { Check, Copy } from 'lucide-react'
import { memo, useState } from 'react'
import type { ChatEntry } from '../../store/sceneStore'
import { MarkdownContent } from './MarkdownContent'
import { ThinkingBlock } from './ThinkingBlock'

function Screenshots({ entry }: { entry: ChatEntry }) {
  if (!entry.screenshotBefore && !entry.screenshotAfter) return null
  return (
    <div className="message-shots">
      {entry.screenshotBefore ? (
        <a href={entry.screenshotBefore} rel="noreferrer" target="_blank">
          <img alt="操作前" src={entry.screenshotBefore} />
        </a>
      ) : null}
      {entry.screenshotAfter ? (
        <a href={entry.screenshotAfter} rel="noreferrer" target="_blank">
          <img alt="操作后" src={entry.screenshotAfter} />
        </a>
      ) : null}
    </div>
  )
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <button
      aria-label="复制回复"
      className="message-copy"
      onClick={() => {
        void navigator.clipboard.writeText(text).then(() => {
          setCopied(true)
          window.setTimeout(() => setCopied(false), 1200)
        })
      }}
      type="button"
    >
      {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
    </button>
  )
}

export const MessageView = memo(function MessageView({ entry }: { entry: ChatEntry }) {
  if (entry.role === 'system') {
    return <p className="message-system">{entry.content}</p>
  }

  if (entry.role === 'user') {
    return (
      <article className="message message--user">
        <div className="message-body message-user-body">
          <p>{entry.content}</p>
          <Screenshots entry={entry} />
        </div>
      </article>
    )
  }

  const streaming = Boolean(entry.streaming)
  const hasContent = Boolean(entry.content)

  return (
    <article className="message message--assistant">
      <div className="message-body">
        <ThinkingBlock content={entry.reasoning ?? ''} streaming={streaming} />
        {hasContent ? <MarkdownContent content={entry.content} /> : null}
        {streaming && hasContent ? <span aria-hidden="true" className="stream-cursor" /> : null}
        {!hasContent && !entry.reasoning && streaming ? (
          <span className="message-placeholder" />
        ) : null}
        <Screenshots entry={entry} />
      </div>
      {hasContent && !streaming ? <CopyButton text={entry.content} /> : null}
    </article>
  )
})
