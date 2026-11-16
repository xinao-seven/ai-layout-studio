import rules from '@ai-layout/contracts/rules.json'
import { describe, expect, it } from 'vitest'

describe('shared spatial rules', () => {
  it('exposes the PRD §6.3 defaults', () => {
    expect(rules.WALL_HEIGHT).toBe(2.8)
    expect(rules.WALL_THICKNESS).toBe(0.12)
    expect(rules.SOLVER_ROTATIONS).toEqual([0, 90, 180, 270])
  })

  it('keeps ROOM_INSET derived from wall thickness', () => {
    expect(rules.ROOM_INSET).toBeCloseTo(rules.WALL_THICKNESS / 2 + 0.02, 6)
  })
})
