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
    useSceneStore.getState().selectItem('it_123')
    useSceneStore.getState().setSidebarTab('items')
    expect(useSceneStore.getState().selectedItemId).toBe('it_123')
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
