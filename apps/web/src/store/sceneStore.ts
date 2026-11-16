import { temporal } from 'zundo'
import { create } from 'zustand'
import * as api from '../api/client'
import { ApiError } from '../api/client'
import type {
  CatalogEntry,
  FixtureSummary,
  SceneDocument,
  ScenePatch,
  SceneSummary,
} from '../contracts/generated/client/rest'
import type { WsEnvelope } from '../contracts/generated/ws'
import { applyPatchToDocument } from '../scene/patch'
import { type ConnectionStatus, SceneSocket } from '../ws/client'

export type ViewMode = '3d' | 'top'
export type SidebarTab = 'scene' | 'rooms' | 'items' | 'ai'

export interface UiError {
  code: string
  message: string
  suggest?: string | null
}

interface SceneState {
  scenes: SceneSummary[]
  fixtures: FixtureSummary[]
  catalog: CatalogEntry[]
  scene: SceneDocument | null
  activeSceneId: string | null
  viewMode: ViewMode
  sidebarTab: SidebarTab
  selectedItemId: string | null
  loading: boolean
  error: UiError | null
  connection: ConnectionStatus
  lastStateSeq: number
  undoDepth: number
  redoDepth: number
  cameraResetToken: number
  fps: number

  bootstrap: () => Promise<void>
  refreshScenes: () => Promise<void>
  createFromFixture: (fixtureId: string) => Promise<void>
  createBlank: () => Promise<void>
  openScene: (sceneId: string) => Promise<void>
  closeScene: () => void
  setViewMode: (mode: ViewMode) => void
  toggleViewMode: () => void
  setSidebarTab: (tab: SidebarTab) => void
  selectItem: (itemId: string | null) => void
  setError: (error: UiError | null) => void
  resetCamera: () => void
  setFps: (fps: number) => void
  undo: () => Promise<void>
  redo: () => Promise<void>
  addItemFromCatalog: (kindId: string) => Promise<void>
  moveSelectedItem: (position: [number, number, number], rotationY?: number) => Promise<void>
  rotateSelectedItem: (deltaRadians: number) => Promise<void>
  deleteSelectedItem: () => Promise<void>
  handleWsEnvelope: (envelope: WsEnvelope) => void
}

let socket: SceneSocket | null = null
let localMutations = 0

async function withLocalMutation<T>(operation: () => Promise<T>): Promise<T> {
  localMutations += 1
  try {
    return await operation()
  } finally {
    localMutations -= 1
  }
}

function isCurrentResult(current: SceneDocument | null, result: { scene: SceneDocument }): boolean {
  return Boolean(
    current && result.scene.id === current.id && result.scene.version > current.version,
  )
}

function toUiError(error: unknown, fallback: string): UiError {
  if (error instanceof ApiError) {
    return { code: error.code, message: error.message, suggest: error.suggest }
  }
  return {
    code: 'unknown_error',
    message: error instanceof Error ? error.message : fallback,
  }
}

function withSceneUpdate(mutator: () => void): void {
  // Zundo mirrors committed scene states; undo itself is a server call.
  mutator()
}

function selectedItemAfter(document: SceneDocument, itemId: string | null): string | null {
  if (!itemId) return null
  return document.items.some((item) => item.id === itemId) ? itemId : null
}

function syncSceneSummary(scenes: SceneSummary[], document: SceneDocument): SceneSummary[] {
  return scenes.map((summary) =>
    summary.id === document.id
      ? {
          ...summary,
          name: document.name,
          version: document.version,
          roomCount: document.rooms.length,
        }
      : summary,
  )
}

export const useSceneStore = create<SceneState>()(
  temporal(
    (set, get) => ({
      scenes: [],
      fixtures: [],
      catalog: [],
      scene: null,
      activeSceneId: null,
      viewMode: '3d',
      sidebarTab: 'scene',
      selectedItemId: null,
      loading: false,
      error: null,
      connection: 'idle',
      lastStateSeq: 0,
      undoDepth: 0,
      redoDepth: 0,
      cameraResetToken: 0,
      fps: 0,

      bootstrap: async () => {
        set({ loading: true, error: null })
        try {
          const [scenes, fixtures, catalog] = await Promise.all([
            api.listScenes(),
            api.listFixtures(),
            api.listCatalog(),
          ])
          set({ scenes, fixtures, catalog, loading: false })
        } catch (error) {
          set({ error: toUiError(error, 'Failed to load scenes.'), loading: false })
        }
      },

      refreshScenes: async () => {
        try {
          set({ scenes: await api.listScenes() })
        } catch (error) {
          set({ error: toUiError(error, 'Failed to refresh scenes.') })
        }
      },

      createFromFixture: async (fixtureId) => {
        set({ loading: true, error: null })
        try {
          const document = await api.createScene({ fixtureId })
          await get().refreshScenes()
          await get().openScene(document.id)
        } catch (error) {
          set({
            error: toUiError(error, 'Failed to create the scene.'),
            loading: false,
          })
        }
      },

      createBlank: async () => {
        set({ loading: true, error: null })
        try {
          const document = await api.createScene({ name: '未命名场景' })
          await get().refreshScenes()
          await get().openScene(document.id)
        } catch (error) {
          set({
            error: toUiError(error, 'Failed to create the scene.'),
            loading: false,
          })
        }
      },

      openScene: async (sceneId) => {
        socket?.close()
        socket = null
        set({
          loading: true,
          error: null,
          activeSceneId: sceneId,
          selectedItemId: null,
          lastStateSeq: 0,
          undoDepth: 0,
          redoDepth: 0,
        })
        try {
          const scene = await api.getScene(sceneId)
          if (get().activeSceneId !== sceneId) return
          withSceneUpdate(() =>
            set({
              scene,
              loading: false,
              sidebarTab: 'scene',
            }),
          )
          socket = new SceneSocket(
            sceneId,
            {
              onStatus: (connection) => set({ connection }),
              onEnvelope: (envelope) => get().handleWsEnvelope(envelope),
            },
            get().lastStateSeq,
          )
          socket.connect()
        } catch (error) {
          set({
            error: toUiError(error, 'Failed to open the scene.'),
            loading: false,
            activeSceneId: null,
          })
        }
      },

      closeScene: () => {
        socket?.close()
        socket = null
        withSceneUpdate(() =>
          set({
            scene: null,
            activeSceneId: null,
            selectedItemId: null,
            lastStateSeq: 0,
            connection: 'idle',
            undoDepth: 0,
            redoDepth: 0,
          }),
        )
      },

      setViewMode: (viewMode) => set({ viewMode }),
      toggleViewMode: () => set((state) => ({ viewMode: state.viewMode === '3d' ? 'top' : '3d' })),
      setSidebarTab: (sidebarTab) => set({ sidebarTab }),
      selectItem: (selectedItemId) => set({ selectedItemId }),
      setError: (error) => set({ error }),
      resetCamera: () => set((state) => ({ cameraResetToken: state.cameraResetToken + 1 })),
      setFps: (fps) => set({ fps }),

      undo: async () => {
        const { scene } = get()
        if (!scene) return
        try {
          const result = await withLocalMutation(() => api.undoScene(scene.id, scene.version))
          if (!isCurrentResult(get().scene, result)) return
          withSceneUpdate(() =>
            set({
              scene: result.scene,
              undoDepth: Math.max(0, get().undoDepth - 1),
              redoDepth: get().redoDepth + 1,
              selectedItemId: selectedItemAfter(result.scene, get().selectedItemId),
            }),
          )
        } catch (error) {
          set({ error: toUiError(error, 'Undo failed.') })
        }
      },

      redo: async () => {
        const { scene } = get()
        if (!scene) return
        try {
          const result = await withLocalMutation(() => api.redoScene(scene.id, scene.version))
          if (!isCurrentResult(get().scene, result)) return
          withSceneUpdate(() =>
            set({
              scene: result.scene,
              undoDepth: get().undoDepth + 1,
              redoDepth: Math.max(0, get().redoDepth - 1),
              selectedItemId: selectedItemAfter(result.scene, get().selectedItemId),
            }),
          )
        } catch (error) {
          set({ error: toUiError(error, 'Redo failed.') })
        }
      },

      addItemFromCatalog: async (kindId) => {
        const { scene } = get()
        if (!scene) return
        const room = scene.rooms[0]
        if (!room) {
          set({
            error: {
              code: 'out_of_room',
              message: '当前场景还没有房间。',
              suggest: '先选择一个户型或绘制房间。',
            },
          })
          return
        }
        try {
          const result = await withLocalMutation(() =>
            api.addItem(scene.id, { kindId, roomId: room.id }),
          )
          if (!isCurrentResult(get().scene, result)) return
          const added = result.patch.ops.find((op) => op.op === 'item.add')
          withSceneUpdate(() =>
            set({
              scene: result.scene,
              selectedItemId: added && added.op === 'item.add' ? added.item.id : null,
              undoDepth: get().undoDepth + 1,
              redoDepth: 0,
              sidebarTab: 'items',
            }),
          )
        } catch (error) {
          set({ error: toUiError(error, 'Failed to add furniture.') })
        }
      },

      moveSelectedItem: async (position, rotationY) => {
        const { scene, selectedItemId } = get()
        if (!scene || !selectedItemId) return
        try {
          const result = await withLocalMutation(() =>
            api.updateItem(scene.id, selectedItemId, {
              position,
              rotationY,
            }),
          )
          if (!isCurrentResult(get().scene, result)) return
          withSceneUpdate(() =>
            set({
              scene: result.scene,
              scenes: syncSceneSummary(get().scenes, result.scene),
              undoDepth: get().undoDepth + 1,
              redoDepth: 0,
            }),
          )
        } catch (error) {
          set({ error: toUiError(error, 'Failed to move the item.') })
        }
      },

      rotateSelectedItem: async (deltaRadians) => {
        const { scene, selectedItemId } = get()
        if (!scene || !selectedItemId) return
        const item = scene.items.find((candidate) => candidate.id === selectedItemId)
        if (!item) return
        await get().moveSelectedItem(
          item.transform.position,
          item.transform.rotationY + deltaRadians,
        )
      },

      deleteSelectedItem: async () => {
        const { scene, selectedItemId } = get()
        if (!scene || !selectedItemId) return
        try {
          const result = await withLocalMutation(() => api.deleteItem(scene.id, selectedItemId))
          if (!isCurrentResult(get().scene, result)) return
          withSceneUpdate(() =>
            set({
              scene: result.scene,
              scenes: syncSceneSummary(get().scenes, result.scene),
              selectedItemId: null,
              undoDepth: get().undoDepth + 1,
              redoDepth: 0,
            }),
          )
        } catch (error) {
          set({ error: toUiError(error, 'Failed to delete the item.') })
        }
      },

      handleWsEnvelope: (envelope) => {
        if (envelope.ch === 'host') {
          if (envelope.type === 'host.error') {
            const payload = envelope.payload as
              | { error?: { code?: string; message?: string; suggest?: string } }
              | undefined
            const error = payload?.error
            if (error?.code && error.message) {
              set({
                error: {
                  code: error.code,
                  message: error.message,
                  suggest: error.suggest ?? null,
                },
              })
            }
          }
          return
        }
        if (envelope.ch !== 'state') return

        if (envelope.type === 'scene.snapshot') {
          const payload = envelope.payload as { scene?: SceneDocument } | undefined
          const scene = payload?.scene
          if (!scene) return
          if (get().activeSceneId && get().activeSceneId !== scene.id) return
          const current = get().scene
          if (current && current.id === scene.id && current.version === scene.version) {
            set({ lastStateSeq: envelope.seq })
            socket?.setResumeSequence(envelope.seq)
            return
          }
          set({
            lastStateSeq: envelope.seq,
            scene,
            scenes: syncSceneSummary(get().scenes, scene),
            selectedItemId: selectedItemAfter(scene, get().selectedItemId),
          })
          socket?.setResumeSequence(envelope.seq)
          return
        }

        if (envelope.type !== 'scene.patch') return
        if (envelope.seq <= get().lastStateSeq) return
        const payload = envelope.payload as { patch?: ScenePatch } | undefined
        const patch = payload?.patch
        if (!patch) return
        if (get().activeSceneId && get().activeSceneId !== patch.sceneId) return

        const current = get().scene
        if (current && patch.toVersion <= current.version) {
          set({
            lastStateSeq: envelope.seq,
            scenes: syncSceneSummary(get().scenes, current),
          })
          socket?.setResumeSequence(envelope.seq)
          return
        }
        if (current && patch.fromVersion === current.version) {
          try {
            const next = applyPatchToDocument(current, patch.ops)
            next.version = patch.toVersion
            withSceneUpdate(() =>
              set({
                scene: next,
                scenes: syncSceneSummary(get().scenes, next),
                lastStateSeq: envelope.seq,
                undoDepth: localMutations > 0 ? get().undoDepth : Math.max(1, get().undoDepth),
                redoDepth: 0,
                selectedItemId: selectedItemAfter(next, get().selectedItemId),
              }),
            )
            socket?.setResumeSequence(envelope.seq)
            return
          } catch {
            // Fall through to a full refetch.
          }
        }
        if (current) {
          void api
            .getScene(current.id)
            .then((scene) =>
              set({
                scene,
                scenes: syncSceneSummary(get().scenes, scene),
                lastStateSeq: envelope.seq,
                selectedItemId: selectedItemAfter(scene, get().selectedItemId),
              }),
            )
            .catch((error: unknown) =>
              set({ error: toUiError(error, 'Failed to resync the scene.') }),
            )
        }
      },
    }),
    {
      limit: 50,
      partialize: (state) => ({ scene: state.scene }),
    },
  ),
)
