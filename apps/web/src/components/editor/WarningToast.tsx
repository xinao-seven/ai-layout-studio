import { Info, X } from 'lucide-react'
import { useEffect } from 'react'
import { useSceneStore } from '../../store/sceneStore'

export function WarningToast() {
  const warnings = useSceneStore((state) => state.warnings)
  const dismissWarnings = useSceneStore((state) => state.dismissWarnings)

  useEffect(() => {
    if (warnings.length === 0) return
    const timer = window.setTimeout(() => dismissWarnings(), 8000)
    return () => window.clearTimeout(timer)
  }, [dismissWarnings, warnings])

  if (warnings.length === 0) return null

  return (
    <div className="pointer-events-auto fixed bottom-16 left-1/2 z-40 w-[min(92vw,520px)] -translate-x-1/2 rounded-xl border border-amber-400/25 bg-amber-950/85 p-3 shadow-2xl backdrop-blur-xl">
      <div className="flex items-start gap-2.5">
        <Info className="mt-0.5 h-4 w-4 shrink-0 text-amber-300" />
        <div className="min-w-0 flex-1 space-y-1">
          {warnings.map((warning) => (
            <div key={`${warning.code}:${warning.message}`}>
              <p className="text-[11px] text-amber-100">{warning.message}</p>
              <p className="font-mono text-[9px] text-amber-300/70">{warning.code}</p>
            </div>
          ))}
        </div>
        <button
          aria-label="关闭提示"
          className="rounded-md p-1 text-amber-200/60 transition-colors hover:bg-white/10 hover:text-amber-100"
          onClick={dismissWarnings}
          type="button"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
    </div>
  )
}
