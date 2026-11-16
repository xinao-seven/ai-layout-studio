import { beforeEach, describe, expect, it } from 'vitest'
import { useSceneUIStore } from './sceneStore'

beforeEach(() => {
  useSceneUIStore.setState({ viewMode: '3d', selectedItemId: null })
})

describe('scene UI store', () => {
  it('starts in 3D view with nothing selected', () => {
    expect(useSceneUIStore.getState().viewMode).toBe('3d')
    expect(useSceneUIStore.getState().selectedItemId).toBeNull()
  })

  it('toggles between 3D and top view', () => {
    useSceneUIStore.getState().toggleViewMode()
    expect(useSceneUIStore.getState().viewMode).toBe('top')

    useSceneUIStore.getState().toggleViewMode()
    expect(useSceneUIStore.getState().viewMode).toBe('3d')
  })

  it('stores and clears the selected item', () => {
    useSceneUIStore.getState().selectItem('it_123')
    expect(useSceneUIStore.getState().selectedItemId).toBe('it_123')

    useSceneUIStore.getState().selectItem(null)
    expect(useSceneUIStore.getState().selectedItemId).toBeNull()
  })
})
