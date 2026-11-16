import { Footprints, Loader2 } from 'lucide-react'
import { useSceneStore } from '../../store/sceneStore'
import { Compass } from './Compass'
import { FloatingToolbar } from './FloatingToolbar'
import { PlacementHint } from './PlacementHint'
import { SceneCanvas } from './SceneCanvas'
import { SceneIconRail } from './SceneIconRail'
import { Sidebar } from './Sidebar'
import { StatusBar } from './StatusBar'
import { SunPanel } from './SunPanel'
import { TopBar } from './TopBar'
import { WarningToast } from './WarningToast'

function WalkHint() {
  const walkLocked = useSceneStore((state) => state.walkLocked)
  return (
    <div className="pointer-events-none absolute bottom-14 left-1/2 z-20 -translate-x-1/2">
      <span className="flex items-center gap-2 rounded-full border border-white/10 bg-black/70 px-3.5 py-1.5 text-[11px] text-white/85 backdrop-blur">
        <Footprints className="h-3.5 w-3.5" />
        {walkLocked
          ? 'WASD/方向键移动 · Shift 加速 · ESC 退出指针锁定'
          : '点击画面进入第一人称漫游 · 再次点击「漫游」按钮退出'}
      </span>
    </div>
  )
}

export function EditorShell({ perfMode }: { perfMode: boolean }) {
  const scene = useSceneStore((state) => state.scene)
  const viewMode = useSceneStore((state) => state.viewMode)
  const sunPanelOpen = useSceneStore((state) => state.sunPanelOpen)
  const selectedItemId = useSceneStore((state) => state.selectedItemId)
  const cameraResetToken = useSceneStore((state) => state.cameraResetToken)
  const catalog = useSceneStore((state) => state.catalog)
  const loading = useSceneStore((state) => state.loading)
  const selectItem = useSceneStore((state) => state.selectItem)
  const setFps = useSceneStore((state) => state.setFps)

  return (
    <div className="flex h-full flex-col bg-background">
      <TopBar />
      <div className="flex min-h-0 flex-1">
        <SceneIconRail />
        <Sidebar />
        <main className="relative min-w-0 flex-1 overflow-hidden bg-[#151515]">
          {scene ? (
            <>
              <SceneCanvas
                cameraResetToken={cameraResetToken}
                catalog={catalog}
                onFps={setFps}
                onSelectItem={selectItem}
                perfMode={perfMode}
                scene={scene}
                selectedItemId={selectedItemId}
                viewMode={viewMode}
              />
              <FloatingToolbar perfMode={perfMode} />
              {viewMode === 'walk' ? <WalkHint /> : null}
              {sunPanelOpen && viewMode !== 'walk' ? <SunPanel /> : null}
              <PlacementHint />
              <Compass north={scene.north as [number, number]} />
              <StatusBar perfMode={perfMode} />
            </>
          ) : (
            <div className="flex h-full flex-col items-center justify-center gap-3 text-muted-foreground">
              <Loader2 className="h-5 w-5 animate-spin" />
              <p className="text-xs">正在载入场景…</p>
            </div>
          )}
          {loading && scene ? (
            <div className="pointer-events-none absolute inset-0 z-30 flex items-start justify-center bg-black/20 pt-20 backdrop-blur-[1px]">
              <span className="flex items-center gap-2 rounded-full border border-white/10 bg-black/70 px-3 py-1.5 text-[11px] text-white/80">
                <Loader2 className="h-3 w-3 animate-spin" />
                同步中…
              </span>
            </div>
          ) : null}
        </main>
      </div>
      <WarningToast />
    </div>
  )
}
