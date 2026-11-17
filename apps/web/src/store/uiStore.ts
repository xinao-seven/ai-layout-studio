import { create } from 'zustand'
import { createJSONStorage, persist } from 'zustand/middleware'
import type { AgentCapabilitiesResponse, ThinkingLevel } from '../contracts/generated/client/rest'

export const DOCK_MIN_WIDTH = 360
export const DOCK_MAX_WIDTH = 720
export const DOCK_DEFAULT_WIDTH = 480
export const RAIL_WIDTH = 56
export const SCENE_PANEL_WIDTH = 320
/** The 3D canvas never shrinks below this when the dock is dragged. */
export const CANVAS_MIN_WIDTH = 420

export function clampDockWidth(width: number, viewportWidth?: number): number {
  const available =
    viewportWidth !== undefined
      ? viewportWidth - RAIL_WIDTH - SCENE_PANEL_WIDTH - CANVAS_MIN_WIDTH
      : DOCK_MAX_WIDTH
  const ceiling = Math.max(DOCK_MIN_WIDTH, Math.min(DOCK_MAX_WIDTH, available))
  return Math.round(Math.min(ceiling, Math.max(DOCK_MIN_WIDTH, width)))
}

interface UiState {
  dockOpen: boolean
  dockWidth: number
  thinkingLevel: ThinkingLevel
  /** Fetched from the server; not persisted because it is runtime config. */
  capabilities: AgentCapabilitiesResponse | null
  setDockOpen: (open: boolean) => void
  toggleDock: () => void
  setDockWidth: (width: number) => void
  setThinkingLevel: (level: ThinkingLevel) => void
  setCapabilities: (capabilities: AgentCapabilitiesResponse | null) => void
}

export const useUiStore = create<UiState>()(
  persist(
    (set) => ({
      dockOpen: true,
      dockWidth: DOCK_DEFAULT_WIDTH,
      thinkingLevel: 'off',
      capabilities: null,
      setDockOpen: (open) => set({ dockOpen: open }),
      toggleDock: () => set((state) => ({ dockOpen: !state.dockOpen })),
      setDockWidth: (width) => set({ dockWidth: clampDockWidth(width) }),
      setThinkingLevel: (level) => set({ thinkingLevel: level }),
      setCapabilities: (capabilities) => set({ capabilities }),
    }),
    {
      name: 'ai-layout.ui',
      storage: createJSONStorage(() => localStorage),
      partialize: (state) => ({
        dockOpen: state.dockOpen,
        dockWidth: state.dockWidth,
        thinkingLevel: state.thinkingLevel,
      }),
    },
  ),
)
