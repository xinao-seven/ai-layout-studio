import { AlertTriangle, X } from 'lucide-react'
import { useSceneStore } from '../../store/sceneStore'

export function ErrorBanner() {
  const error = useSceneStore((state) => state.error)
  const setError = useSceneStore((state) => state.setError)

  if (!error) return null

  return (
    <div className="pointer-events-auto fixed top-16 left-1/2 z-40 w-[min(92vw,520px)] -translate-x-1/2 rounded-xl border border-red-400/25 bg-red-950/85 p-3 shadow-2xl backdrop-blur-xl">
      <div className="flex items-start gap-2.5">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-red-300" />
        <div className="min-w-0 flex-1">
          <p className="font-medium text-[12px] text-red-100">{error.message}</p>
          <p className="mt-0.5 font-mono text-[10px] text-red-300/80">{error.code}</p>
          {error.suggest ? (
            <p className="mt-1 text-[11px] text-red-200/70">{error.suggest}</p>
          ) : null}
        </div>
        <button
          aria-label="关闭错误提示"
          className="rounded-md p-1 text-red-200/60 transition-colors hover:bg-white/10 hover:text-red-100"
          onClick={() => setError(null)}
          type="button"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
    </div>
  )
}
