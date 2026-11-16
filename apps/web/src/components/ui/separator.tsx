import * as SeparatorPrimitive from '@radix-ui/react-separator'
import type { ComponentProps } from 'react'
import { cn } from '../../lib/cn'

export function Separator({
  className,
  orientation = 'horizontal',
  decorative = true,
  ...props
}: ComponentProps<typeof SeparatorPrimitive.Root>) {
  return (
    <SeparatorPrimitive.Root
      className={cn(
        'shrink-0 bg-border data-[orientation=horizontal]:h-px data-[orientation=horizontal]:w-full data-[orientation=vertical]:h-full data-[orientation=vertical]:w-px',
        className,
      )}
      decorative={decorative}
      orientation={orientation}
      {...props}
    />
  )
}
