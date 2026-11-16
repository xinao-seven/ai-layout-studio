import { useSceneStore } from '../../store/sceneStore'

export function StatusBar({ perfMode }: { perfMode: boolean }) {
  const scene = useSceneStore((state) => state.scene)
  const fps = useSceneStore((state) => state.fps)
  const viewMode = useSceneStore((state) => state.viewMode)

  if (!scene) return null
  const area = scene.rooms.reduce((total, room) => {
    let sum = 0
    const points = room.polygon as [number, number][]
    for (let index = 0; index < points.length; index += 1) {
      const [x1, z1] = points[index] as [number, number]
      const [x2, z2] = points[(index + 1) % points.length] as [number, number]
      sum += x1 * z2 - x2 * z1
    }
    return total + Math.abs(sum / 2)
  }, 0)

  return (
    <div className="pointer-events-none absolute right-3 bottom-3 left-3 z-10 flex items-end justify-between gap-3">
      <div className="flex flex-wrap items-center gap-1.5 text-[10px] text-white/55">
        <span className="rounded-full border border-white/10 bg-black/40 px-2 py-1 backdrop-blur">
          {viewMode === '3d' ? '3D 透视' : '正交俯视'}
        </span>
        <span className="rounded-full border border-white/10 bg-black/40 px-2 py-1 backdrop-blur">
          {area.toFixed(1)}㎡
        </span>
        <span className="rounded-full border border-white/10 bg-black/40 px-2 py-1 backdrop-blur">
          {scene.rooms.length} 房间 · {scene.items.length} 家具
        </span>
        <span className="rounded-full border border-white/10 bg-black/40 px-2 py-1 backdrop-blur">
          v{scene.version}
        </span>
        {perfMode ? (
          <span className="rounded-full border border-sky-400/20 bg-sky-400/10 px-2 py-1 text-sky-300 backdrop-blur">
            {fps} fps
          </span>
        ) : null}
      </div>
      <span className="hidden rounded-full border border-white/10 bg-black/40 px-2 py-1 text-[10px] text-white/40 backdrop-blur sm:block">
        Tab 俯视 · 拖拽家具 · R 旋转 · 方向键微调 · 拖入目录放置
      </span>
    </div>
  )
}
