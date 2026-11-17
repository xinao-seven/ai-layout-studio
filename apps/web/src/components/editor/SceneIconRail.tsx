import { Box, Home, Layers3, Ruler, Sofa } from 'lucide-react'
import type { ComponentType } from 'react'
import { cn } from '../../lib/cn'
import { type SidebarTab, useSceneStore } from '../../store/sceneStore'

const TABS: Array<{
  id: SidebarTab
  label: string
  icon: ComponentType<{ className?: string }>
}> = [
  { id: 'scene', label: '场景', icon: Layers3 },
  { id: 'rooms', label: '房间', icon: Home },
  { id: 'items', label: '家具', icon: Sofa },
]

export function SceneIconRail() {
  const sidebarTab = useSceneStore((state) => state.sidebarTab)
  const setSidebarTab = useSceneStore((state) => state.setSidebarTab)

  return (
    <nav className="flex h-full w-14 shrink-0 flex-col items-center gap-1 border-border/50 border-r bg-sidebar py-2">
      {TABS.map((tab) => {
        const Icon = tab.icon
        const active = sidebarTab === tab.id
        return (
          <button
            aria-label={tab.label}
            aria-pressed={active}
            className={cn(
              'flex h-11 w-11 items-center justify-center rounded-xl transition-all duration-200',
              active
                ? 'bg-sidebar-accent text-foreground shadow-sm'
                : 'text-muted-foreground hover:bg-sidebar-accent/50 hover:text-foreground',
            )}
            key={tab.id}
            onClick={() => setSidebarTab(tab.id)}
            title={tab.label}
            type="button"
          >
            <Icon className="h-[18px] w-[18px]" />
          </button>
        )
      })}
      <div className="mt-auto flex flex-col items-center gap-2 pb-1 text-muted-foreground">
        <span className="h-px w-6 bg-border/60" />
        <Ruler className="h-3.5 w-3.5" />
        <Box className="h-3.5 w-3.5" />
      </div>
    </nav>
  )
}
