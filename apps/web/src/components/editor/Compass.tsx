import { Navigation } from 'lucide-react'
import type { Vec2 } from '../../scene/geometry'

export function Compass({ north }: { north: Vec2 }) {
  const radians = Math.atan2(north[0], -north[1])
  const degrees = (radians * 180) / Math.PI

  return (
    <div className="pointer-events-none absolute right-3 bottom-12 z-10 flex flex-col items-center gap-1">
      <div className="relative flex h-12 w-12 items-center justify-center rounded-full border border-white/12 bg-black/45 shadow-xl backdrop-blur-md">
        <span className="absolute top-1 font-medium text-[8px] text-white/60">N</span>
        <Navigation
          className="h-4 w-4 text-white/85"
          style={{ transform: `rotate(${degrees}deg)` }}
        />
      </div>
      <span className="text-[8px] text-white/35">north</span>
    </div>
  )
}
