import type { ComponentProps } from 'react'
import { cn } from '../../lib/cn'

type BadgeTone = 'neutral' | 'accent' | 'success' | 'warning'

const toneClasses: Record<BadgeTone, string> = {
  neutral: 'border-border bg-secondary/60 text-muted-foreground',
  accent: 'border-primary/25 bg-primary/10 text-foreground',
  success: 'border-emerald-400/25 bg-emerald-400/10 text-emerald-300',
  warning: 'border-amber-400/25 bg-amber-400/10 text-amber-300',
}

export function Badge({
  className,
  tone = 'neutral',
  ...props
}: ComponentProps<'span'> & { tone?: BadgeTone }) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] leading-4 font-medium tracking-wide',
        toneClasses[tone],
        className,
      )}
      {...props}
    />
  )
}
