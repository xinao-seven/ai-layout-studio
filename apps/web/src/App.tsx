import { useEffect } from 'react'
import { EditorShell } from './components/editor/EditorShell'
import { ErrorBanner } from './components/editor/ErrorBanner'
import { HomeScreen } from './components/HomeScreen'
import { TooltipProvider } from './components/ui/tooltip'
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
      if (event.key === 'Tab') {
        event.preventDefault()
        useSceneStore.getState().toggleViewMode()
      } else if (event.key === 'Escape') {
        useSceneStore.getState().selectItem(null)
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
