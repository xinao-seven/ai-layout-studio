import { describe, expect, it } from 'vitest'
import {
  CANVAS_MIN_WIDTH,
  clampDockWidth,
  DOCK_MAX_WIDTH,
  DOCK_MIN_WIDTH,
  RAIL_WIDTH,
  SCENE_PANEL_WIDTH,
} from './uiStore'

describe('dock width clamp', () => {
  it('keeps the canvas above its minimum on desktop', () => {
    const viewport = 1280
    const max = viewport - RAIL_WIDTH - SCENE_PANEL_WIDTH - CANVAS_MIN_WIDTH
    expect(clampDockWidth(DOCK_MAX_WIDTH, viewport)).toBe(max)
  })

  it('never goes below the dock minimum', () => {
    expect(clampDockWidth(10, 1280)).toBe(DOCK_MIN_WIDTH)
    expect(clampDockWidth(10)).toBe(DOCK_MIN_WIDTH)
  })

  it('allows the full range in overlay mode', () => {
    expect(clampDockWidth(900)).toBe(DOCK_MAX_WIDTH)
  })
})
