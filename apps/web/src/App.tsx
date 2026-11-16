import { useEffect } from 'react'
import { EditorShell } from './components/editor/EditorShell'
import { ErrorBanner } from './components/editor/ErrorBanner'
import { HomeScreen } from './components/HomeScreen'
import { TooltipProvider } from './components/ui/tooltip'
import { MANUAL_ROTATION_RADIANS } from './scene/geometry'
import { useSceneStore } from './store/sceneStore'

function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  const tag = target.tagName.toLowerCase()
  return tag === 'input' || tag === 'textarea' || tag === 'select' || target.isContentEditable
}

export default function App() {
  const activeSceneId = useSceneStore((state) => state.activeSceneId)
  const perfMode = new URLSearchParams(window.location.search).get('perf') === '200'

  useEffect(() => {
    void useSceneStore.getState().bootstrap()
  }, [])

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (isTypingTarget(event.target) || event.metaKey || event.ctrlKey) return
      const state = useSceneStore.getState()
      if (event.key === 'Tab') {
        event.preventDefault()
        state.toggleViewMode()
        return
      }
      if (event.key === 'Escape') {
        if (state.tool === 'draw-room') {
          state.cancelDraft()
          state.setTool('select')
        } else if (state.vertexDrag) {
          state.cancelVertexDrag()
        } else if (state.drag) {
          state.cancelItemDrag()
        } else {
          state.selectItem(null)
        }
        return
      }
      if (state.tool === 'draw-room' && event.key === 'Enter') {
        void state.createRoomFromDraft()
        return
      }
      if (
        (event.key === 'Delete' || event.key === 'Backspace') &&
        state.selectedItemId &&
        state.tool === 'select'
      ) {
        event.preventDefault()
        void state.deleteSelectedItem()
        return
      }
      if (!state.selectedItemId || state.tool !== 'select') return
      const item = state.scene?.items.find((candidate) => candidate.id === state.selectedItemId)
      if (!item) return
      if (event.key === 'r' || event.key === 'R') {
        event.preventDefault()
        void state.rotateSelectedItem((event.shiftKey ? -1 : 1) * MANUAL_ROTATION_RADIANS)
        return
      }
      const step = 0.1
      const [x, y, z] = item.transform.position
      const nudges: Record<string, [number, number]> = {
        ArrowUp: [0, -step],
        ArrowDown: [0, step],
        ArrowLeft: [-step, 0],
        ArrowRight: [step, 0],
      }
      const nudge = nudges[event.key]
      if (nudge) {
        event.preventDefault()
        void state.moveSelectedItem([x + nudge[0], y, z + nudge[1]], item.transform.rotationY)
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [])

  return (
    <TooltipProvider delayDuration={250}>
      <div className="h-full">
        {activeSceneId ? <EditorShell perfMode={perfMode} /> : <HomeScreen />}
        <ErrorBanner />
      </div>
    </TooltipProvider>
  )
}
