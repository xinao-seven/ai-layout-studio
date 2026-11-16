import { Sun } from 'lucide-react'
import { useEffect, useState } from 'react'
import { formatSunTime } from '../../scene/sun'
import { useSceneStore } from '../../store/sceneStore'
import { Button } from '../ui/button'

function CoordinateInput({
  label,
  value,
  min,
  max,
  onCommit,
}: {
  label: string
  value: number | null
  min: number
  max: number
  onCommit: (next: number) => void
}) {
  const [draft, setDraft] = useState(value == null ? '' : String(value))
  useEffect(() => {
    setDraft(value == null ? '' : String(value))
  }, [value])

  const commit = () => {
    const parsed = Number(draft)
    if (!Number.isFinite(parsed) || parsed < min || parsed > max) {
      setDraft(value == null ? '' : String(value))
      return
    }
    onCommit(Number(parsed.toFixed(4)))
  }

  return (
    <label className="flex flex-1 flex-col gap-1">
      <span className="text-[10px] text-muted-foreground">{label}</span>
      <input
        className="h-7 rounded-md border border-border bg-background/80 px-2 text-[11px] tabular-nums outline-none focus:border-white/30"
        onBlur={commit}
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Enter') {
            event.currentTarget.blur()
          }
        }}
        placeholder={`(${min}..${max})`}
        type="number"
        value={draft}
      />
    </label>
  )
}

export function SunPanel() {
  const scene = useSceneStore((state) => state.scene)
  const sunEnabled = useSceneStore((state) => state.sunEnabled)
  const sunTimeHours = useSceneStore((state) => state.sunTimeHours)
  const setSunEnabled = useSceneStore((state) => state.setSunEnabled)
  const setSunTime = useSceneStore((state) => state.setSunTime)
  const updateEnvironment = useSceneStore((state) => state.updateEnvironment)

  if (!scene) return null
  const latitude = scene.environment?.latitude ?? null
  const longitude = scene.environment?.longitude ?? null

  return (
    <div className="pointer-events-auto absolute bottom-3 left-3 z-20 w-64 rounded-xl border border-border bg-background/92 p-3 shadow-2xl backdrop-blur-md">
      <div className="mb-2 flex items-center justify-between">
        <span className="flex items-center gap-1.5 font-medium text-[11px]">
          <Sun className="h-3.5 w-3.5" />
          日照环境层
        </span>
        <label className="flex cursor-pointer items-center gap-1.5 text-[10px] text-muted-foreground">
          <input
            checked={sunEnabled}
            onChange={(event) => setSunEnabled(event.target.checked)}
            type="checkbox"
          />
          启用
        </label>
      </div>

      <div className={sunEnabled ? '' : 'pointer-events-none opacity-45'}>
        <div className="mb-2">
          <div className="mb-1 flex items-center justify-between text-[10px] text-muted-foreground">
            <span>时间轴（太阳时）</span>
            <span className="tabular-nums text-foreground">{formatSunTime(sunTimeHours)}</span>
          </div>
          <input
            className="w-full accent-sky-400"
            max={24}
            min={0}
            onChange={(event) => setSunTime(Number(event.target.value))}
            step={0.25}
            type="range"
            value={sunTimeHours}
          />
        </div>

        <div className="flex gap-2">
          <CoordinateInput
            label="纬度"
            max={90}
            min={-90}
            onCommit={(next) => void updateEnvironment(next, longitude ?? 0)}
            value={latitude}
          />
          <CoordinateInput
            label="经度"
            max={180}
            min={-180}
            onCommit={(next) => void updateEnvironment(latitude ?? 0, next)}
            value={longitude}
          />
        </div>
        <p className="mt-2 text-[10px] leading-4 text-muted-foreground">
          朝北由罗盘（场景 north）控制；经纬度随场景保存，可撤销。
        </p>
        {latitude == null || longitude == null ? (
          <p className="mt-1 text-[10px] text-amber-300/90">先填写经纬度才能看到阴影变化。</p>
        ) : null}
        <Button
          className="mt-2 h-7 w-full text-[11px]"
          onClick={() =>
            void updateEnvironment(latitude ?? 31.2304, longitude ?? 121.4737).then(() =>
              setSunEnabled(true),
            )
          }
          size="sm"
          variant="outline"
        >
          使用上海坐标并启用
        </Button>
      </div>
    </div>
  )
}
