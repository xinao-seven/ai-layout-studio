import { create } from 'zustand'

export type ViewMode = '3d' | 'top'

interface SceneUIState {
  viewMode: ViewMode
  selectedItemId: string | null
  setViewMode: (mode: ViewMode) => void
  toggleViewMode: () => void
  selectItem: (itemId: string | null) => void
}

export const useSceneUIStore = create<SceneUIState>()((set) => ({
  viewMode: '3d',
  selectedItemId: null,
  setViewMode: (viewMode) => set({ viewMode }),
  toggleViewMode: () => set((state) => ({ viewMode: state.viewMode === '3d' ? 'top' : '3d' })),
  selectItem: (selectedItemId) => set({ selectedItemId }),
}))
