import { describe, expect, it } from 'vitest'
import type { SceneDocument } from '../contracts/generated/client/rest'
import { buildCaptureLabels, openingPoint } from './capture'

const scene = {
  id: 'scene_test',
  rooms: [
    {
      id: 'room_a',
      polygon: [
        [0, 0],
        [4, 0],
        [4, 3],
        [0, 3],
      ],
    },
  ],
  openinings: [],
  items: [
    { id: 'it_b', kindId: 'bed_double' },
    { id: 'it_a', kindId: 'nightstand' },
  ],
  openings: [
    { id: 'op_z', type: 'window', roomId: 'room_a', edgeIndex: 2, offset: 2 },
    { id: 'op_a', type: 'door', roomId: 'room_a', edgeIndex: 0, offset: 1 },
    { id: 'op_b', type: 'door', roomId: 'room_a', edgeIndex: 1, offset: 1.5 },
  ],
} as unknown as SceneDocument

describe('capture labels', () => {
  it('sorts ids and prefixes furniture/doors/windows like the server', () => {
    const labels = buildCaptureLabels(scene)
    expect(labels.itemLabels).toEqual([
      { itemId: 'it_a', label: 'A1' },
      { itemId: 'it_b', label: 'A2' },
    ])
    expect(labels.openingLabels.map((entry) => entry.label)).toEqual(['D1', 'D2', 'W1'])
    expect(labels.legend.openings).toEqual([
      { label: 'D1', openingId: 'op_a', type: 'door' },
      { label: 'D2', openingId: 'op_b', type: 'door' },
      { label: 'W1', openingId: 'op_z', type: 'window' },
    ])
  })

  it('resolves an opening offset on its wall segment', () => {
    expect(
      openingPoint(
        [
          [0, 0],
          [4, 0],
        ],
        { edgeIndex: 0, offset: 1 },
      ),
    ).toEqual([1, 0])
  })
})
