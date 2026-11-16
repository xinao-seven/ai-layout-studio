import { beforeEach, describe, expect, it } from 'vitest'
import type { SceneDocument } from '../contracts/generated/client/rest'
import { useSceneStore } from './sceneStore'

beforeEach(() => {
  useSceneStore.setState({
    viewMode: '3d',
    sidebarTab: 'scene',
    selectedItemId: null,
    cameraResetToken: 0,
  })
  useSceneStore.temporal.getState().clear()
})

function minimalScene(): SceneDocument {
  return {
    schemaVersion: 1,
    id: 'scene_test',
    name: 'Test',
    version: 1,
    units: 'metric',
    north: [0, -1],
    defaults: { wallHeight: 2.8, wallThickness: 0.12, wallMaterial: 'default' },
    rooms: [],
    openings: [],
    items: [],
    createdAt: '2026-11-16T00:00:00Z',
    updatedAt: '2026-11-16T00:00:00Z',
  }
}

describe('scene store UI state', () => {
  it('starts in 3D view with nothing selected', () => {
    expect(useSceneStore.getState().viewMode).toBe('3d')
    expect(useSceneStore.getState().selectedItemId).toBeNull()
  })

  it('toggles between 3D and top view', () => {
    useSceneStore.getState().toggleViewMode()
    expect(useSceneStore.getState().viewMode).toBe('top')
    useSceneStore.getState().toggleViewMode()
    expect(useSceneStore.getState().viewMode).toBe('3d')
  })

  it('stores selection and sidebar tab', () => {
    const scene = minimalScene()
    scene.items = [
      {
        id: 'it_123',
        kindId: 'plant',
        variantId: 'default',
        transform: { position: [0, 0, 0], rotationY: 0 },
      },
    ]
    useSceneStore.setState({ scene })
    useSceneStore.getState().selectItem('it_123')
    useSceneStore.getState().setSidebarTab('items')
    expect(useSceneStore.getState().selectedItemId).toBe('it_123')
    expect(useSceneStore.getState().pointContext?.itemId).toBe('it_123')
    expect(useSceneStore.getState().sidebarTab).toBe('items')
  })

  it('bumps the camera reset token', () => {
    useSceneStore.getState().resetCamera()
    expect(useSceneStore.getState().cameraResetToken).toBe(1)
  })
})

describe('zundo mirror', () => {
  it('records committed scene states for the local mirror', () => {
    useSceneStore.setState({ scene: minimalScene() })
    expect(useSceneStore.temporal.getState().pastStates.length).toBe(1)
  })
})

describe('room draft snapping', () => {
  it('snaps the draft cursor to the grid before the first point', () => {
    useSceneStore.setState({ draftRoom: [], draftCursor: null })
    useSceneStore.getState().setDraftCursor([0.34, 1.27])
    expect(useSceneStore.getState().draftCursor).toEqual([0.3, 1.3])
  })

  it('keeps draw segments axis-aligned relative to the last point', () => {
    useSceneStore.setState({ draftRoom: [[1, 1]], draftCursor: null })
    useSceneStore.getState().setDraftCursor([3.42, 1.31])
    expect(useSceneStore.getState().draftCursor).toEqual([3.4, 1])
    useSceneStore.getState().setDraftCursor([1.08, 4.26])
    expect(useSceneStore.getState().draftCursor).toEqual([1, 4.3])
  })

  it('lets Shift skip the right-angle snap but keep the grid snap', () => {
    useSceneStore.setState({ draftRoom: [[1, 1]], draftCursor: null })
    useSceneStore.getState().setDraftCursor([3.42, 1.31], { free: true })
    expect(useSceneStore.getState().draftCursor).toEqual([3.4, 1.3])
  })

  it('commits ortho-snapped draw points', () => {
    useSceneStore.setState({ draftRoom: [[1, 1]], draftCursor: null })
    useSceneStore.getState().addDraftPoint([3.42, 1.31])
    expect(useSceneStore.getState().draftRoom).toEqual([
      [1, 1],
      [3.4, 1],
    ])
  })

  it('treats a click near the first point as close instead of adding a vertex', () => {
    useSceneStore.setState({
      draftRoom: [
        [1, 1],
        [4, 1],
        [4, 3],
      ],
      draftCursor: null,
    })
    // 直角吸附会把该点击拉到 [1.1, 3];若按吸附后的距离判定闭合,这里会误加点。
    useSceneStore.getState().addDraftPoint([1.12, 1.08])
    expect(useSceneStore.getState().draftRoom).toEqual([
      [1, 1],
      [4, 1],
      [4, 3],
    ])
  })
})

describe('vertex drag alignment', () => {
  function sceneWithRoom(polygon: [number, number][]): SceneDocument {
    const scene = minimalScene()
    scene.rooms = [{ id: 'room_1', name: 'R', type: 'other', polygon }]
    return scene
  }

  it('grid-snaps the drag start', () => {
    useSceneStore.setState({
      scene: sceneWithRoom([
        [0, 0],
        [4, 0],
        [4, 3],
      ]),
    })
    useSceneStore.getState().startVertexDrag('room_1', 1, [4.04, 0.14])
    expect(useSceneStore.getState().vertexDrag?.position).toEqual([4, 0.1])
    useSceneStore.getState().cancelVertexDrag()
  })

  it('aligns a dragged vertex with its neighbors while dragging', () => {
    useSceneStore.setState({
      scene: sceneWithRoom([
        [0, 0],
        [4, 0],
        [4, 3],
      ]),
    })
    useSceneStore.getState().startVertexDrag('room_1', 1, [4, 0])
    useSceneStore.getState().updateVertexDrag([4.08, 1.2])
    // x 距相邻顶点 0.08(≤ 0.15) → 锁到 4;z 远离相邻顶点 → 网格吸附
    expect(useSceneStore.getState().vertexDrag?.position).toEqual([4, 1.2])
    useSceneStore.getState().updateVertexDrag([4.08, 1.2], { free: true })
    expect(useSceneStore.getState().vertexDrag?.position).toEqual([4.1, 1.2])
    useSceneStore.getState().cancelVertexDrag()
  })
})

describe('room draft snapping (review P2-1)', () => {
  it('drops diagonal clicks that snap back onto the last vertex', () => {
    useSceneStore.setState({ draftRoom: [[1, 1]], draftCursor: null })
    // 原始距离 0.057 > 0.05,但直角吸附把点拉回 [1, 1]:不得追加重复顶点。
    useSceneStore.getState().addDraftPoint([1.04, 1.04])
    expect(useSceneStore.getState().draftRoom).toEqual([[1, 1]])
  })
})
