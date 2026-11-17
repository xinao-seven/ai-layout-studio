import { useState } from 'react'
import { cn } from '../../lib/cn'
import type { WaitingUserState } from '../../store/sceneStore'
import { Button } from '../ui/button'

export function AskUserCard({
  request,
  onAnswer,
}: {
  request: WaitingUserState
  onAnswer: (answers: string[]) => void
}) {
  const [selected, setSelected] = useState<string[]>([])
  const [text, setText] = useState('')

  const hasOptions = request.options.length > 0
  const answers = hasOptions ? selected : text.trim() ? [text.trim()] : []

  return (
    <div className="ask-card">
      <p className="ask-question">{request.question}</p>
      {hasOptions ? (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {request.options.map((option) => {
            const active = selected.includes(option)
            return (
              <button
                className={cn('ask-option', active && 'ask-option--active')}
                key={option}
                onClick={() =>
                  setSelected((current) =>
                    request.allowMultiple
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
      ) : (
        <input
          className="ask-input"
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.nativeEvent.isComposing) {
              event.preventDefault()
              if (answers.length > 0) onAnswer(answers)
            }
          }}
          placeholder="输入回答…"
          value={text}
        />
      )}
      <Button
        className="mt-2 h-7 w-full text-[11px]"
        disabled={answers.length === 0}
        onClick={() => onAnswer(answers)}
        size="sm"
      >
        提交回答
      </Button>
    </div>
  )
}
