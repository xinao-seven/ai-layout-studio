import { useSceneStore } from '../../store/sceneStore'

export function PlacementHint() {
  const tool = useSceneStore((state) => state.tool)
  const draftRoom = useSceneStore((state) => state.draftRoom)
  const drag = useSceneStore((state) => state.drag)
  const ghost = useSceneStore((state) => state.ghost)

  let tone = 'border-white/10 bg-black/55 text-white/70'
  let text: string | null = null
  if (tool === 'draw-room') {
    tone = 'border-primary/30 bg-primary/20 text-white/85'
    text =
      draftRoom.length === 0
        ? '画房间：点击地面添加墙中心线（0.1m 吸附），点击绿色起点闭合，Esc 取消'
        : `画房间：已添加 ${draftRoom.length} 个点，点击绿色起点闭合，Esc 取消`
  } else if (drag?.moved) {
    if (drag.valid) {
      tone = 'border-emerald-400/30 bg-emerald-950/70 text-emerald-100'
      text = '松开鼠标放置（0.1m 吸附，一次拖拽一次 undo）'
    } else {
      tone = 'border-red-400/35 bg-red-950/75 text-red-100'
      text = drag.issue?.message ?? '当前位置不可放置'
    }
  } else if (ghost) {
    if (ghost.valid) {
      tone = 'border-emerald-400/30 bg-emerald-950/70 text-emerald-100'
      text = '松开鼠标放置到房间'
    } else {
      tone = 'border-red-400/35 bg-red-950/75 text-red-100'
      text = ghost.issue?.message ?? '当前位置不可放置'
    }
  }

  if (!text) return null
  return (
    <div className="pointer-events-none absolute bottom-12 left-1/2 z-30 -translate-x-1/2">
      <span
        className={`rounded-full border px-3 py-1.5 text-[11px] shadow-xl backdrop-blur ${tone}`}
      >
        {text}
      </span>
    </div>
  )
}
