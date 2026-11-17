import { describe, expect, it } from 'vitest'
import { prettyPayload, toolHint } from './toolHint'

describe('tool hints', () => {
  it('summarises the most identifying argument per tool', () => {
    expect(toolHint('add_item', { kindId: 'sofa', variantId: 'two-seat' })).toBe('sofa · two-seat')
    expect(toolHint('move_item', { itemId: 'it_1', position: [0, 0, 0] })).toBe('it_1')
    expect(toolHint('search_catalog', { query: '书桌', category: 'table' })).toBe('书桌')
    expect(toolHint('ask_user', { question: '放哪面墙？' })).toBe('放哪面墙？')
    expect(toolHint('query_scene', { includePending: true })).toBe('含待确认提案')
    expect(toolHint('auto_layout', {})).toBe('整屋')
  })

  it('handles missing arguments', () => {
    expect(toolHint('add_item', null)).toBe('')
    expect(toolHint('unknown_tool', {})).toBe('')
  })
})

describe('pretty payload', () => {
  it('pretty prints objects and truncates long strings', () => {
    expect(prettyPayload({ ok: true })).toBe('{\n  "ok": true\n}')
    expect(prettyPayload('abc', 2)).toBe('ab')
    expect(prettyPayload(null)).toBe('')
  })
})
