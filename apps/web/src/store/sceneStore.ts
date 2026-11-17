import rules from '@ai-layout/contracts/rules.json'
import { temporal } from 'zundo'
import { create } from 'zustand'
import { createStreamCoalescer } from '../agent/streamCoalescer'
import * as api from '../api/client'
import { ApiError } from '../api/client'
import type {
  CatalogEntry,
  FixtureSummary,
  Item,
  Opening,
  OpeningUpdateFields,
  Proposal,
  SceneDocument,
  ScenePatch,
  SceneResult,
  SceneSummary,
  Turn,
  TurnStatus,
} from '../contracts/generated/client/rest'
import type { WsEnvelope } from '../contracts/generated/ws'
import type { CaptureLegend } from '../scene/capture'
import {
  type AlignPlanes,
  alignValue,
  collectAlignPlanes,
  distance,
  edgeInfo,
  insetPolygon,
  isSimplePolygon,
  obbCornersInsidePolygon,
  obbFromBox,
  pointInPolygon,
  polygonArea,
  signedArea,
  snapDraftPoint,
  snapPoint,
  snapValue,
  type Vec2,
  vertexAlignSnap,
} from '../scene/geometry'
import { applyPatchToDocument } from '../scene/patch'
import { checkPlacement, itemFootprint, type PlacementIssue, variantFor } from '../scene/placement'
import { buildPointContext, type PointContext } from '../scene/pointContext'
import { resolveSnap } from '../scene/snapping'
import { type ConnectionStatus, SceneSocket } from '../ws/client'
import { useUiStore } from './uiStore'

export type ViewMode = '3d' | 'top' | 'walk'
export type SidebarTab = 'scene' | 'rooms' | 'items'
export type EditorTool = 'select' | 'draw-room'

export interface UiError {
  code: string
  message: string
  suggest?: string | null
}

export interface UiWarning {
  code: string
  message: string
  details?: Record<string, unknown> | null
}

export interface ChatEntry {
  id: string
  role: 'user' | 'assistant' | 'tool' | 'system'
  content: string
  /** Provider reasoning streamed before the answer (M7). */
  reasoning?: string
  name?: string
  status?: 'running' | 'done' | 'failed'
  arguments?: Record<string, unknown> | null
  result?: unknown
  streaming?: boolean
  screenshotBefore?: string | null
  screenshotAfter?: string | null
}

export interface CaptureRequestState {
  requestId: string
  turnId: string | null
  width: number
  height: number
  maxBytes: number
  legend: CaptureLegend | null
}

export interface PngExportRequestState {
  requestId: string
  maxEdge: number
}

export interface CameraState {
  position: [number, number, number]
  target: [number, number, number]
  zoom: number
}

export interface WaitingUserState {
  turnId: string
  question: string
  options: string[]
  allowMultiple: boolean
}

export interface WallSelection {
  roomId: string
  edgeIndex: number
}

export interface ItemDragState {
  itemId: string
  kindId: string
  position: [number, number, number]
  rotationY: number
  scale: number
  offset: [number, number]
  moved: boolean
  valid: boolean
  issue?: PlacementIssue
}

export interface CatalogGhostState {
  kindId: string
  variantId: string
  position: Vec2
  rotationY: number
  valid: boolean
  issue?: PlacementIssue
}

export interface VertexDragState {
  roomId: string
  vertexIndex: number
  position: Vec2
  valid: boolean
  issue?: PlacementIssue
}

/** Live uniform-scale drag on a selected item (corner grip). */
export interface ScaleDragState {
  itemId: string
  /** Item centre on the floor plane, used as the drag pivot. */
  center: Vec2
  /** Half-diagonal of the unscaled footprint; distance ratio gives the scale. */
  baseHalf: number
  scale: number
  valid: boolean
  issue?: PlacementIssue
}

export interface WallEdgeRef {
  roomId: string
  edgeIndex: number
  aIndex: number
  bIndex: number
}

/**
 * Dragging one wall translates both of its vertices along the wall normal.
 * Edges in other rooms that are geometrically the same wall move with it, so a
 * shared partition stays shared.
 */
export interface WallDragState {
  edges: WallEdgeRef[]
  normal: Vec2
  start: Vec2
  offset: number
  moved: boolean
  originals: Record<string, Vec2[]>
  polygons: Record<string, Vec2[]>
  valid: boolean
  issue?: PlacementIssue
}

/** 手绘房间/顶点拖拽的吸附选项:free=true 时跳过直角对齐,只保留网格吸附。 */
export interface DraftPointOptions {
  free?: boolean
}

type SceneResultWithWarnings = SceneResult & { warnings?: UiWarning[] }
type PatchOps = SceneResult['patch']['ops']

function resolveDraftPoint(
  draftRoom: Vec2[],
  point: Vec2,
  free: boolean,
  planes: AlignPlanes,
): Vec2 {
  if (free) return snapPoint(point)
  return snapDraftPoint(draftRoom[draftRoom.length - 1], point, planes)
}

function resolveVertexPosition(
  polygon: Vec2[],
  index: number,
  position: Vec2,
  free: boolean,
  planes: AlignPlanes,
): Vec2 {
  return free
    ? snapPoint(position)
    : vertexAlignSnap(polygon, index, position, undefined, undefined, planes)
}

function samePoint(first: Vec2, second: Vec2): boolean {
  return Math.abs(first[0] - second[0]) <= 1e-6 && Math.abs(first[1] - second[1]) <= 1e-6
}

/**
 * The dragged edge plus every other room edge that is geometrically the same
 * wall, so a shared partition moves as one.
 */
function coincidentEdges(scene: SceneDocument, roomId: string, edgeIndex: number): WallEdgeRef[] {
  const room = scene.rooms.find((candidate) => candidate.id === roomId)
  if (!room) return []
  const polygon = room.polygon as Vec2[]
  const a = polygon[edgeIndex]
  const b = polygon[(edgeIndex + 1) % polygon.length]
  if (!a || !b) return []
  const edges: WallEdgeRef[] = [
    { roomId, edgeIndex, aIndex: edgeIndex, bIndex: (edgeIndex + 1) % polygon.length },
  ]
  for (const other of scene.rooms) {
    if (other.id === roomId) continue
    const points = other.polygon as Vec2[]
    for (let index = 0; index < points.length; index += 1) {
      const next = (index + 1) % points.length
      const p = points[index]
      const q = points[next]
      if (!p || !q) continue
      const forward = samePoint(p, a) && samePoint(q, b)
      const reverse = samePoint(p, b) && samePoint(q, a)
      if (forward || reverse) {
        edges.push({ roomId: other.id, edgeIndex: index, aIndex: index, bIndex: next })
      }
    }
  }
  return edges
}

/** Client-side mirror of the room-change checks the commit will run. */
function validateRoomMove(
  scene: SceneDocument,
  catalog: CatalogEntry[],
  polygon: Vec2[],
  previous: Vec2[],
): PlacementIssue | null {
  if (polygon.length < 3 || !isSimplePolygon(polygon)) {
    return {
      code: 'invalid_polygon',
      message: '移动后房间轮廓会自相交。',
      suggest: '往回拖一点，或先把相邻的墙调整好。',
    }
  }
  if (polygonArea(polygon) < rules.MIN_ROOM_AREA) {
    return {
      code: 'room_too_small',
      message: '移动后房间面积太小。',
      suggest: `至少保留 ${rules.MIN_ROOM_AREA} ㎡。`,
    }
  }
  const inset = insetPolygon(polygon, rules.ROOM_INSET)
  if (!inset) {
    return {
      code: 'room_too_small',
      message: '移动后房间放不下墙体。',
      suggest: '往回收一点。',
    }
  }
  for (const item of scene.items) {
    const centre: Vec2 = [item.transform.position[0], item.transform.position[2]]
    if (!pointInPolygon(centre, polygon) && !pointInPolygon(centre, previous)) continue
    const box = obbFromBox(centre, itemFootprint(item, catalog), item.transform.rotationY)
    if (!obbCornersInsidePolygon(box, inset)) {
      return {
        code: 'out_of_room',
        message: '移动后会有家具跑到墙外。',
        suggest: '先把那件家具挪开，或不要把墙推得这么深。',
      }
    }
  }
  return null
}

const SCALE_MIN = rules.ITEM_SCALE_MIN
const SCALE_MAX = rules.ITEM_SCALE_MAX
const SCALE_STEP = rules.ITEM_SCALE_STEP

/** Snap to the scale step and clamp to the server-enforced envelope. */
export function clampScale(raw: number): number {
  if (!Number.isFinite(raw)) return 1
  const snapped = Math.round(raw / SCALE_STEP) * SCALE_STEP
  const clamped = Math.min(SCALE_MAX, Math.max(SCALE_MIN, snapped))
  return Math.round(clamped * 1000) / 1000
}

interface SceneState {
  scenes: SceneSummary[]
  fixtures: FixtureSummary[]
  catalog: CatalogEntry[]
  scene: SceneDocument | null
  activeSceneId: string | null
  viewMode: ViewMode
  sidebarTab: SidebarTab
  tool: EditorTool
  selectedItemId: string | null
  selectedRoomId: string | null
  selectedWall: WallSelection | null
  selectedOpeningId: string | null
  pointContext: PointContext | null
  draftRoom: Vec2[]
  draftCursor: Vec2 | null
  drag: ItemDragState | null
  ghost: CatalogGhostState | null
  vertexDrag: VertexDragState | null
  scaleDrag: ScaleDragState | null
  wallDrag: WallDragState | null
  warnings: UiWarning[]
  loading: boolean
  error: UiError | null
  connection: ConnectionStatus
  lastStateSeq: number
  lastTurnSeq: number
  serverEpoch: string | null
  undoDepth: number
  redoDepth: number
  cameraResetToken: number
  fps: number
  chatMessages: ChatEntry[]
  turns: Turn[]
  activeTurnId: string | null
  turnStatus: TurnStatus | null
  proposals: Record<string, Proposal>
  waitingUser: WaitingUserState | null
  captureRequest: CaptureRequestState | null
  pngExportRequest: PngExportRequestState | null
  cameraState: CameraState | null
  restoreCamera: boolean
  walkLocked: boolean
  sunEnabled: boolean
  sunTimeHours: number
  sunPanelOpen: boolean

  bootstrap: () => Promise<void>
  refreshScenes: () => Promise<void>
  reloadCatalog: () => Promise<void>
  createFromFixture: (fixtureId: string) => Promise<void>
  createBlank: () => Promise<void>
  openScene: (sceneId: string) => Promise<void>
  closeScene: () => void
  setViewMode: (mode: ViewMode) => void
  toggleViewMode: () => void
  setSidebarTab: (tab: SidebarTab) => void
  setTool: (tool: EditorTool) => void
  selectItem: (itemId: string | null) => void
  selectRoom: (roomId: string | null) => void
  selectWall: (selection: WallSelection | null) => void
  selectOpening: (openingId: string | null) => void
  setError: (error: UiError | null) => void
  dismissWarnings: () => void
  resetCamera: () => void
  setFps: (fps: number) => void
  undo: () => Promise<void>
  redo: () => Promise<void>
  addItemFromCatalog: (kindId: string) => Promise<void>
  placeCatalogItem: (kindId: string, position: Vec2, rotationY?: number) => Promise<void>
  setCatalogGhost: (kindId: string, position: Vec2, rotationY: number) => void
  clearCatalogGhost: () => void
  startItemDrag: (
    itemId: string,
    position: [number, number, number],
    rotationY: number,
    offset: [number, number],
  ) => void
  updateItemDrag: (position: Vec2) => void
  endItemDrag: () => Promise<void>
  cancelItemDrag: () => void
  moveSelectedItem: (position: [number, number, number], rotationY?: number) => Promise<void>
  updateItemTo: (
    itemId: string,
    position: [number, number, number],
    rotationY: number,
  ) => Promise<void>
  rotateSelectedItem: (deltaRadians: number) => Promise<void>
  deleteSelectedItem: () => Promise<void>
  setDraftCursor: (point: Vec2 | null, options?: DraftPointOptions) => void
  addDraftPoint: (point: Vec2, options?: DraftPointOptions) => void
  cancelDraft: () => void
  createRoomFromDraft: () => Promise<void>
  updateRoomPolygon: (roomId: string, polygon: Vec2[]) => Promise<void>
  deleteRoom: (roomId: string) => Promise<void>
  startVertexDrag: (roomId: string, vertexIndex: number, position: Vec2) => void
  updateVertexDrag: (position: Vec2, options?: DraftPointOptions) => void
  cancelVertexDrag: () => void
  endVertexDrag: () => Promise<void>
  startScaleDrag: (itemId: string) => void
  updateScaleDrag: (position: Vec2) => void
  endScaleDrag: () => Promise<void>
  cancelScaleDrag: () => void
  startWallDrag: (roomId: string, edgeIndex: number, point: Vec2) => void
  updateWallDrag: (point: Vec2) => void
  endWallDrag: () => Promise<void>
  cancelWallDrag: () => void
  setItemScale: (itemId: string, scale: number) => Promise<void>
  addOpening: (roomId: string, edgeIndex: number, type: 'door' | 'window') => Promise<void>
  updateOpening: (openingId: string, after: OpeningUpdateFields) => Promise<void>
  deleteOpening: (openingId: string) => Promise<void>
  commitOps: (ops: PatchOps, failureMessage: string) => Promise<SceneResultWithWarnings | null>
  sendChatMessage: (text: string) => Promise<void>
  cancelActiveTurn: () => Promise<void>
  completeCapture: (requestId: string, pngBase64: string, legend: Record<string, unknown>) => void
  cancelCapture: (requestId: string) => void
  exportSceneJson: () => Promise<void>
  exportScenePng: () => void
  completePngExport: (requestId: string, dataUrl: string) => void
  cancelPngExport: (requestId: string) => void
  importSceneFile: (file: File) => Promise<void>
  saveCameraState: (state: CameraState) => void
  consumeCameraRestore: () => void
  setWalkLocked: (locked: boolean) => void
  setSunEnabled: (enabled: boolean) => void
  setSunTime: (hours: number) => void
  toggleSunPanel: () => void
  updateEnvironment: (latitude: number, longitude: number) => Promise<void>
  confirmProposal: (proposalId: string) => void
  rejectProposal: (proposalId: string) => void
  answerUser: (answers: string[]) => void
  refreshTurnState: () => Promise<void>
  handleWsEnvelope: (envelope: WsEnvelope) => void
}

let socket: SceneSocket | null = null
let localMutations = 0
const countedUndoVersions = new Set<string>()

function bumpUndoDepth(state: { undoDepth: number }, sceneId: string, version: number): number {
  const key = `${sceneId}:${version}`
  if (countedUndoVersions.has(key)) return state.undoDepth
  countedUndoVersions.add(key)
  if (countedUndoVersions.size > 500) countedUndoVersions.clear()
  return state.undoDepth + 1
}

async function withLocalMutation<T>(operation: () => Promise<T>): Promise<T> {
  localMutations += 1
  try {
    return await operation()
  } finally {
    localMutations -= 1
  }
}

function newId(prefix: string): string {
  const suffix =
    typeof crypto !== 'undefined' && 'randomUUID' in crypto
      ? crypto.randomUUID().replace(/-/g, '').slice(0, 12)
      : Math.random().toString(16).slice(2, 14)
  return `${prefix}_${suffix}`
}

function isCurrentResult(current: SceneDocument | null, result: { scene: SceneDocument }): boolean {
  // The WebSocket patch may reach us before the REST response for the same
  // version. Treat an equal version as current so local metadata (undo depth,
  // selection, warnings) still updates; only discard genuinely stale results.
  return Boolean(
    current && result.scene.id === current.id && result.scene.version >= current.version,
  )
}

function downloadBlob(filename: string, blob: Blob): void {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

function safeFilename(raw: string): string {
  const cleaned = raw.replace(/[\\/:*?"<>|]+/g, '_').trim()
  return cleaned.length > 0 ? cleaned.slice(0, 60) : 'scene'
}

function dataUrlToBlob(dataUrl: string): Blob | null {
  const match = /^data:([^;,]+)?(;base64)?,(.*)$/.exec(dataUrl)
  if (!match) return null
  const mime = match[1] ?? 'application/octet-stream'
  const payload = match[3] ?? ''
  if (!match[2]) return new Blob([payload], { type: mime })
  const binary = atob(payload)
  const bytes = new Uint8Array(binary.length)
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index)
  }
  return new Blob([bytes], { type: mime })
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

/** Merge catalog entries by kindId, preferring the incoming (authoritative) copy. */
function mergeCatalog(current: CatalogEntry[], incoming: CatalogEntry[]): CatalogEntry[] {
  const byKind = new Map(current.map((entry) => [entry.kindId, entry]))
  for (const entry of incoming) byKind.set(entry.kindId, entry)
  return [...byKind.values()]
}

function syncSceneSummary(scenes: SceneSummary[], document: SceneDocument): SceneSummary[] {
  return scenes.map((summary) =>
    summary.id === document.id
      ? {
          ...summary,
          name: document.name,
          version: document.version,
          roomCount: document.rooms.length,
          areaM2: document.rooms.reduce(
            (total, room) => total + polygonArea(room.polygon as Vec2[]),
            0,
          ),
        }
      : summary,
  )
}

interface SelectionSnapshot {
  selectedItemId: string | null
  selectedRoomId: string | null
  selectedWall: WallSelection | null
  selectedOpeningId: string | null
  pointContext: PointContext | null
}

function reconcileSelection(
  scene: SceneDocument,
  catalog: CatalogEntry[],
  state: {
    selectedItemId: string | null
    selectedRoomId: string | null
    selectedWall: WallSelection | null
    selectedOpeningId: string | null
  },
): SelectionSnapshot {
  const selectedItemId = selectedItemAfter(scene, state.selectedItemId)
  const selectedRoomId =
    state.selectedRoomId && scene.rooms.some((room) => room.id === state.selectedRoomId)
      ? state.selectedRoomId
      : null
  const selectedWall =
    state.selectedWall &&
    scene.rooms.some(
      (room) =>
        room.id === state.selectedWall?.roomId &&
        state.selectedWall.edgeIndex >= 0 &&
        state.selectedWall.edgeIndex < room.polygon.length,
    )
      ? state.selectedWall
      : null
  const selectedOpeningId =
    state.selectedOpeningId &&
    scene.openings.some((opening) => opening.id === state.selectedOpeningId)
      ? state.selectedOpeningId
      : null
  const item = selectedItemId
    ? scene.items.find((candidate) => candidate.id === selectedItemId)
    : undefined
  return {
    selectedItemId,
    selectedRoomId,
    selectedWall,
    selectedOpeningId,
    pointContext: item ? buildPointContext(scene, catalog, item) : null,
  }
}

function rotatePointToCcw(points: Vec2[]): Vec2[] {
  return signedArea(points) > 0 ? points : [...points].reverse()
}

function chatFromTurn(turn: Turn): ChatEntry[] {
  const entries: ChatEntry[] = []
  turn.messages?.forEach((message, index) => {
    if (message.role === 'tool') {
      entries.push({
        id: `history-tool-${turn.id}-${index}`,
        role: 'tool',
        content: message.content ?? '',
        status: 'done',
        result: message.toolResult ?? null,
      })
      return
    }
    entries.push({
      id: `history-${message.role}-${turn.id}-${index}`,
      role: message.role === 'assistant' ? 'assistant' : 'user',
      content: message.content ?? '',
      reasoning: message.reasoning ?? undefined,
      screenshotBefore: message.screenshotBefore ?? null,
      screenshotAfter: message.screenshotAfter ?? null,
    })
  })
  return entries
}

function toolSummary(result: unknown): string {
  if (!result || typeof result !== 'object') return ''
  const payload = result as { ok?: boolean; refusals?: { message?: string }[] }
  if (payload.ok === false && payload.refusals?.length) {
    return payload.refusals.map((refusal) => refusal.message ?? 'refused').join('; ')
  }
  return payload.ok === true ? 'ok' : ''
}

/** Close any dangling streaming bubble so a later turn cannot append into it. */
function finalizeStreaming(entries: ChatEntry[]): ChatEntry[] {
  if (!entries.some((entry) => entry.streaming)) return entries
  return entries.map((entry) => (entry.streaming ? { ...entry, streaming: false } : entry))
}

function lastStreamingIndex(entries: ChatEntry[]): number {
  for (let index = entries.length - 1; index >= 0; index -= 1) {
    const entry = entries[index]
    if (entry && entry.role === 'assistant' && entry.streaming) return index
  }
  return -1
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
      tool: 'select',
      selectedItemId: null,
      selectedRoomId: null,
      selectedWall: null,
      selectedOpeningId: null,
      pointContext: null,
      draftRoom: [],
      draftCursor: null,
      drag: null,
      ghost: null,
      vertexDrag: null,
      scaleDrag: null,
      wallDrag: null,
      warnings: [],
      loading: false,
      error: null,
      connection: 'idle',
      lastStateSeq: 0,
      lastTurnSeq: 0,
      serverEpoch: null,
      undoDepth: 0,
      redoDepth: 0,
      cameraResetToken: 0,
      fps: 0,
      chatMessages: [],
      turns: [],
      activeTurnId: null,
      turnStatus: null,
      proposals: {},
      waitingUser: null,
      captureRequest: null,
      pngExportRequest: null,
      cameraState: null,
      restoreCamera: false,
      walkLocked: false,
      sunEnabled: false,
      sunTimeHours: 12,
      sunPanelOpen: false,

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

      reloadCatalog: async () => {
        try {
          const catalog = await api.reloadCatalog()
          set({ catalog })
        } catch (error) {
          set({ error: toUiError(error, 'Failed to reload the catalog.') })
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
        streamCoalescer.dispose()
        set({
          loading: true,
          error: null,
          activeSceneId: sceneId,
          selectedItemId: null,
          selectedRoomId: null,
          selectedWall: null,
          selectedOpeningId: null,
          pointContext: null,
          tool: 'select',
          draftRoom: [],
          draftCursor: null,
          drag: null,
          ghost: null,
          vertexDrag: null,
          scaleDrag: null,
          wallDrag: null,
          warnings: [],
          lastStateSeq: 0,
          lastTurnSeq: 0,
          serverEpoch: null,
          undoDepth: 0,
          redoDepth: 0,
          chatMessages: [],
          turns: [],
          activeTurnId: null,
          turnStatus: null,
          proposals: {},
          waitingUser: null,
          captureRequest: null,
          pngExportRequest: null,
          restoreCamera: false,
        })
        try {
          const [scene, turns, pendingProposals] = await Promise.all([
            api.getScene(sceneId),
            api.listTurns(sceneId).catch(() => []),
            api.listProposals(sceneId, 'pending').catch(() => []),
          ])
          if (get().activeSceneId !== sceneId) return
          const latest = turns[0] as Turn | undefined
          const isActive =
            latest?.status === 'running' ||
            latest?.status === 'waiting_user' ||
            latest?.status === 'queued'
          withSceneUpdate(() =>
            set({
              scene,
              loading: false,
              sidebarTab: 'scene',
              turns,
              chatMessages: latest ? chatFromTurn(latest) : [],
              activeTurnId: isActive && latest ? (latest.id ?? null) : null,
              turnStatus: latest?.status ?? null,
              proposals: Object.fromEntries(
                pendingProposals.map((proposal) => [proposal.id, proposal]),
              ),
              ...reconcileSelection(scene, get().catalog, {
                selectedItemId: null,
                selectedRoomId: null,
                selectedWall: null,
                selectedOpeningId: null,
              }),
            }),
          )
          socket = new SceneSocket(
            sceneId,
            {
              onStatus: (connection) => set({ connection }),
              onEnvelope: (envelope) => get().handleWsEnvelope(envelope),
            },
            get().lastStateSeq,
            get().lastTurnSeq,
          )
          socket.connect()
          if (isActive && latest?.id) {
            socket.sendHost('turn.subscribe', { turnId: latest.id, lastSeq: 0 }, latest.id)
          }
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
        streamCoalescer.dispose()
        withSceneUpdate(() =>
          set({
            scene: null,
            activeSceneId: null,
            selectedItemId: null,
            selectedRoomId: null,
            selectedWall: null,
            selectedOpeningId: null,
            pointContext: null,
            tool: 'select',
            draftRoom: [],
            draftCursor: null,
            drag: null,
            ghost: null,
            vertexDrag: null,
            scaleDrag: null,
            wallDrag: null,
            warnings: [],
            lastStateSeq: 0,
            lastTurnSeq: 0,
            serverEpoch: null,
            connection: 'idle',
            undoDepth: 0,
            redoDepth: 0,
            chatMessages: [],
            turns: [],
            activeTurnId: null,
            turnStatus: null,
            proposals: {},
            waitingUser: null,
            captureRequest: null,
            pngExportRequest: null,
            restoreCamera: false,
          }),
        )
      },

      setViewMode: (viewMode) => set({ viewMode }),
      toggleViewMode: () => set((state) => ({ viewMode: state.viewMode === '3d' ? 'top' : '3d' })),
      setSidebarTab: (sidebarTab) => set({ sidebarTab }),
      setTool: (tool) =>
        set({
          tool,
          draftRoom: [],
          draftCursor: null,
          selectedItemId: tool === 'draw-room' ? null : get().selectedItemId,
        }),

      selectItem: (itemId) =>
        set((state) => {
          if (!itemId) {
            return {
              selectedItemId: null,
              pointContext: null,
              selectedRoomId: null,
              selectedWall: null,
              selectedOpeningId: null,
            }
          }
          const scene = state.scene
          const item = scene?.items.find((candidate) => candidate.id === itemId)
          if (!scene || !item) return { selectedItemId: null, pointContext: null }
          return {
            selectedItemId: itemId,
            selectedRoomId: null,
            selectedWall: null,
            selectedOpeningId: null,
            pointContext: buildPointContext(scene, state.catalog, item),
          }
        }),

      selectRoom: (roomId) =>
        set({
          selectedRoomId: roomId,
          selectedItemId: null,
          selectedWall: null,
          selectedOpeningId: null,
          pointContext: null,
        }),

      selectWall: (selection) =>
        set({
          selectedWall: selection,
          selectedRoomId: selection?.roomId ?? null,
          selectedItemId: null,
          selectedOpeningId: null,
          pointContext: null,
        }),

      selectOpening: (openingId) =>
        set((state) => {
          const opening = state.scene?.openings.find((candidate) => candidate.id === openingId)
          return {
            selectedOpeningId: openingId,
            selectedWall: opening
              ? { roomId: opening.roomId, edgeIndex: opening.edgeIndex }
              : state.selectedWall,
            selectedRoomId: opening?.roomId ?? state.selectedRoomId,
          }
        }),

      setError: (error) => set({ error }),
      dismissWarnings: () => set({ warnings: [] }),
      resetCamera: () => set((state) => ({ cameraResetToken: state.cameraResetToken + 1 })),
      setFps: (fps) => set({ fps }),

      undo: async () => {
        const { scene } = get()
        if (!scene) return
        const targetKey = `${scene.id}:${scene.version + 1}`
        countedUndoVersions.add(targetKey)
        try {
          const result = await withLocalMutation(() => api.undoScene(scene.id, scene.version))
          if (!isCurrentResult(get().scene, result)) return
          countedUndoVersions.add(`${result.scene.id}:${result.scene.version}`)
          withSceneUpdate(() =>
            set((state) => ({
              scene: result.scene,
              scenes: syncSceneSummary(state.scenes, result.scene),
              undoDepth: Math.max(0, state.undoDepth - 1),
              redoDepth: state.redoDepth + 1,
              ...reconcileSelection(result.scene, state.catalog, state),
            })),
          )
        } catch (error) {
          countedUndoVersions.delete(targetKey)
          set({ error: toUiError(error, 'Undo failed.') })
        }
      },

      redo: async () => {
        const { scene } = get()
        if (!scene) return
        const targetKey = `${scene.id}:${scene.version + 1}`
        countedUndoVersions.add(targetKey)
        try {
          const result = await withLocalMutation(() => api.redoScene(scene.id, scene.version))
          if (!isCurrentResult(get().scene, result)) return
          countedUndoVersions.add(`${result.scene.id}:${result.scene.version}`)
          withSceneUpdate(() =>
            set((state) => ({
              scene: result.scene,
              scenes: syncSceneSummary(state.scenes, result.scene),
              undoDepth: state.undoDepth + 1,
              redoDepth: Math.max(0, state.redoDepth - 1),
              ...reconcileSelection(result.scene, state.catalog, state),
            })),
          )
        } catch (error) {
          countedUndoVersions.delete(targetKey)
          set({ error: toUiError(error, 'Redo failed.') })
        }
      },

      commitOps: async (ops, failureMessage) => {
        const { scene } = get()
        if (!scene || ops.length === 0) return null
        try {
          const result = (await withLocalMutation(() =>
            api.commitScene(scene.id, { baseVersion: scene.version, ops }),
          )) as SceneResultWithWarnings
          if (!isCurrentResult(get().scene, result)) return null
          withSceneUpdate(() =>
            set((state) => ({
              scene: result.scene,
              scenes: syncSceneSummary(state.scenes, result.scene),
              warnings: result.warnings ?? [],
              undoDepth: bumpUndoDepth(state, result.scene.id, result.scene.version),
              redoDepth: 0,
              ...reconcileSelection(result.scene, state.catalog, state),
            })),
          )
          return result
        } catch (error) {
          set({ error: toUiError(error, failureMessage) })
          return null
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
            set((state) => ({
              scene: result.scene,
              scenes: syncSceneSummary(state.scenes, result.scene),
              selectedItemId: added && added.op === 'item.add' ? added.item.id : null,
              pointContext:
                added && added.op === 'item.add'
                  ? buildPointContext(result.scene, state.catalog, added.item)
                  : null,
              selectedRoomId: null,
              selectedWall: null,
              selectedOpeningId: null,
              undoDepth: bumpUndoDepth(state, result.scene.id, result.scene.version),
              redoDepth: 0,
              sidebarTab: 'items',
            })),
          )
        } catch (error) {
          set({ error: toUiError(error, 'Failed to add furniture.') })
        }
      },

      placeCatalogItem: async (kindId, position, rotationY = 0) => {
        const { scene, catalog } = get()
        if (!scene) return
        const snapped = resolveSnap({ scene, catalog, kindId, rotationY }, position)
        const check = checkPlacement(scene, catalog, { kindId, position: snapped, rotationY })
        if (!check.valid) {
          set({
            error: {
              code: check.issue?.code ?? 'collision',
              message: check.issue?.message ?? '无法放置家具。',
              suggest: check.issue?.suggest ?? null,
            },
          })
          return
        }
        try {
          const result = await withLocalMutation(() =>
            api.addItem(scene.id, { kindId, position: snapped, rotationY }),
          )
          if (!isCurrentResult(get().scene, result)) return
          const added = result.patch.ops.find((op) => op.op === 'item.add')
          withSceneUpdate(() =>
            set((state) => ({
              scene: result.scene,
              scenes: syncSceneSummary(state.scenes, result.scene),
              selectedItemId: added && added.op === 'item.add' ? added.item.id : null,
              pointContext:
                added && added.op === 'item.add'
                  ? buildPointContext(result.scene, state.catalog, added.item)
                  : null,
              selectedRoomId: null,
              selectedWall: null,
              selectedOpeningId: null,
              undoDepth: bumpUndoDepth(state, result.scene.id, result.scene.version),
              redoDepth: 0,
              ghost: null,
            })),
          )
        } catch (error) {
          set({ error: toUiError(error, 'Failed to place furniture.') })
        }
      },

      setCatalogGhost: (kindId, position, rotationY) => {
        const { scene, catalog } = get()
        if (!scene) return
        const snapped = resolveSnap({ scene, catalog, kindId, rotationY }, position)
        const check = checkPlacement(scene, catalog, {
          kindId,
          position: snapped,
          rotationY,
        })
        const variant = variantFor(catalog, kindId)
        set({
          ghost: {
            kindId,
            variantId: variant?.id ?? 'default',
            position: snapped,
            rotationY,
            valid: check.valid,
            issue: check.issue,
          },
        })
      },

      clearCatalogGhost: () => set({ ghost: null }),

      startItemDrag: (itemId, position, rotationY, offset) => {
        const { scene, catalog } = get()
        if (!scene) return
        const item = scene.items.find((candidate) => candidate.id === itemId)
        if (!item) return
        const check = checkPlacement(scene, catalog, {
          kindId: item.kindId,
          variantId: item.variantId,
          position: [position[0], position[2]],
          rotationY,
          scale: item.transform.scale ?? 1,
          ignoreItemId: itemId,
        })
        set({
          selectedItemId: itemId,
          selectedRoomId: null,
          selectedWall: null,
          selectedOpeningId: null,
          pointContext: buildPointContext(scene, catalog, item),
          drag: {
            itemId,
            kindId: item.kindId,
            position,
            rotationY,
            scale: item.transform.scale ?? 1,
            offset,
            moved: false,
            valid: check.valid,
            issue: check.issue,
          },
        })
      },

      updateItemDrag: (position) => {
        const { scene, catalog, drag } = get()
        if (!scene || !drag) return
        const snapped = resolveSnap(
          {
            scene,
            catalog,
            kindId: drag.kindId,
            scale: drag.scale,
            rotationY: drag.rotationY,
            ignoreItemId: drag.itemId,
          },
          position,
        )
        const check = checkPlacement(scene, catalog, {
          kindId: drag.kindId,
          position: snapped,
          rotationY: drag.rotationY,
          scale: drag.scale,
          ignoreItemId: drag.itemId,
        })
        set({
          drag: {
            ...drag,
            position: [snapped[0], 0, snapped[1]],
            moved: true,
            valid: check.valid,
            issue: check.issue,
          },
        })
      },

      endItemDrag: async () => {
        const { drag } = get()
        if (!drag) return
        set({ drag: null })
        if (!drag.moved) return
        if (!drag.valid) {
          set({
            error: {
              code: drag.issue?.code ?? 'collision',
              message: drag.issue?.message ?? '家具无法放置在这里。',
              suggest: drag.issue?.suggest ?? null,
            },
          })
          return
        }
        await get().updateItemTo(drag.itemId, drag.position, drag.rotationY)
      },

      cancelItemDrag: () => set({ drag: null }),

      updateItemTo: async (itemId, position, rotationY) => {
        const { scene, catalog } = get()
        if (!scene) return
        const item = scene.items.find((candidate) => candidate.id === itemId)
        if (!item) return
        // Callers resolve snapping (edge or grid) before committing; re-snapping
        // here would undo an edge snap and can move a rotated item.
        const target: Vec2 = [position[0], position[2]]
        if (
          Math.abs(target[0] - item.transform.position[0]) <= 1e-9 &&
          Math.abs(target[1] - item.transform.position[2]) <= 1e-9 &&
          Math.abs(rotationY - item.transform.rotationY) <= 1e-9
        ) {
          return
        }
        const check = checkPlacement(scene, catalog, {
          kindId: item.kindId,
          variantId: item.variantId,
          position: target,
          rotationY,
          scale: item.transform.scale ?? 1,
          ignoreItemId: itemId,
        })
        if (!check.valid) {
          set({
            error: {
              code: check.issue?.code ?? 'collision',
              message: check.issue?.message ?? '家具无法放置在这里。',
              suggest: check.issue?.suggest ?? null,
            },
          })
          return
        }
        const after: {
          transform: { position: [number, number, number]; rotationY: number }
          constraint?: NonNullable<Item['constraint']>
        } = {
          transform: { position: [target[0], position[1] ?? 0, target[1]], rotationY },
        }
        if (item.constraint) {
          after.constraint = {
            ...item.constraint,
            roomId: check.roomId ?? item.constraint.roomId,
          }
        } else if (check.roomId) {
          after.constraint = { roomId: check.roomId }
        }
        await get().commitOps([{ op: 'item.update', itemId, after }], 'Failed to move the item.')
      },

      moveSelectedItem: async (position, rotationY) => {
        const { scene, selectedItemId } = get()
        if (!scene || !selectedItemId) return
        const item = scene.items.find((candidate) => candidate.id === selectedItemId)
        if (!item) return
        await get().updateItemTo(selectedItemId, position, rotationY ?? item.transform.rotationY)
      },

      rotateSelectedItem: async (deltaRadians) => {
        const { scene, selectedItemId } = get()
        if (!scene || !selectedItemId) return
        const item = scene.items.find((candidate) => candidate.id === selectedItemId)
        if (!item) return
        await get().updateItemTo(
          selectedItemId,
          item.transform.position,
          item.transform.rotationY + deltaRadians,
        )
      },

      deleteSelectedItem: async () => {
        const { selectedItemId } = get()
        if (!selectedItemId) return
        const result = await get().commitOps(
          [{ op: 'item.remove', itemId: selectedItemId }],
          'Failed to delete the item.',
        )
        if (!result) return
        set({
          selectedItemId: null,
          pointContext: null,
        })
      },

      setDraftCursor: (draftCursor, options) =>
        set({
          draftCursor: draftCursor
            ? resolveDraftPoint(
                get().draftRoom,
                draftCursor,
                options?.free === true,
                collectAlignPlanes(get().scene?.rooms ?? []),
              )
            : null,
        }),

      addDraftPoint: (point, options) => {
        const { draftRoom, scene } = get()
        if (draftRoom.length >= 3) {
          const first = draftRoom[0]
          // 闭合判定用原始光标:直角吸附可能把提交点拉离起点,但用户意图是回到起点。
          if (first && distance(point, first) <= 0.3) {
            void get().createRoomFromDraft()
            return
          }
        }
        const snapped = resolveDraftPoint(
          draftRoom,
          point,
          options?.free === true,
          collectAlignPlanes(scene?.rooms ?? []),
        )
        const last = draftRoom[draftRoom.length - 1]
        // 吸附可能把 0.05–0.07m 的斜向点击拉回上一点,两种情况都要拦住。
        if (last && (distance(point, last) <= 0.05 || distance(snapped, last) <= 1e-9)) return
        set({ draftRoom: [...draftRoom, snapped] })
      },

      cancelDraft: () => set({ draftRoom: [], draftCursor: null }),

      createRoomFromDraft: async () => {
        const { draftRoom, scene } = get()
        if (!scene || draftRoom.length < 3) return
        const polygon = rotatePointToCcw(draftRoom)
        if (!isSimplePolygon(polygon)) {
          set({
            error: {
              code: 'invalid_polygon',
              message: '房间轮廓自相交或存在重复顶点。',
              suggest: '撤销最后几个点，重新绘制一个简单多边形。',
            },
          })
          return
        }
        if (polygonArea(polygon) < rules.MIN_ROOM_AREA) {
          set({
            error: {
              code: 'room_too_small',
              message: '房间面积太小。',
              suggest: `至少画出一个 ${rules.MIN_ROOM_AREA} ㎡ 的房间。`,
            },
          })
          return
        }
        const roomId = newId('room')
        const result = await get().commitOps(
          [
            {
              op: 'room.add',
              room: {
                id: roomId,
                name: `房间 ${scene.rooms.length + 1}`,
                type: 'other',
                polygon,
              },
            },
          ],
          'Failed to create the room.',
        )
        if (!result) return
        set({
          tool: 'select',
          draftRoom: [],
          draftCursor: null,
          selectedRoomId: roomId,
          selectedItemId: null,
          selectedWall: null,
          selectedOpeningId: null,
          sidebarTab: 'rooms',
        })
      },

      updateRoomPolygon: async (roomId, polygon) => {
        const room = get().scene?.rooms.find((candidate) => candidate.id === roomId)
        if (
          room &&
          polygon.length === room.polygon.length &&
          polygon.every(
            (point, index) =>
              Math.abs(point[0] - (room.polygon[index]?.[0] ?? Infinity)) <= 1e-9 &&
              Math.abs(point[1] - (room.polygon[index]?.[1] ?? Infinity)) <= 1e-9,
          )
        ) {
          return
        }
        if (
          polygon.length < 3 ||
          !isSimplePolygon(polygon) ||
          polygonArea(polygon) < rules.MIN_ROOM_AREA
        ) {
          set({
            error: {
              code: 'invalid_polygon',
              message: '房间轮廓无效（自相交、重复顶点或面积过小）。',
              suggest: '把顶点拖回合理位置。',
            },
          })
          return
        }
        await get().commitOps(
          [
            {
              op: 'room.update',
              roomId,
              after: { polygon: rotatePointToCcw(polygon) },
            },
          ],
          'Failed to update the room.',
        )
      },

      deleteRoom: async (roomId) => {
        const result = await get().commitOps(
          [{ op: 'room.remove', roomId }],
          'Failed to delete the room.',
        )
        if (!result) return
        set({
          selectedRoomId: null,
          selectedWall: null,
          selectedOpeningId: null,
        })
      },

      startVertexDrag: (roomId, vertexIndex, position) => {
        if (!get().scene) return
        set({
          selectedRoomId: roomId,
          selectedItemId: null,
          selectedWall: null,
          selectedOpeningId: null,
          pointContext: null,
          wallDrag: null,
          // 拖拽起点只做网格吸附:对齐吸附交给移动过程,避免“点一下就跳位”。
          vertexDrag: { roomId, vertexIndex, position: snapPoint(position), valid: true },
        })
      },

      updateVertexDrag: (position, options) => {
        const { vertexDrag, scene } = get()
        if (!vertexDrag) return
        const room = scene?.rooms.find((candidate) => candidate.id === vertexDrag.roomId)
        const polygon = (room?.polygon as Vec2[] | undefined) ?? []
        set({
          vertexDrag: {
            ...vertexDrag,
            position: resolveVertexPosition(
              polygon,
              vertexDrag.vertexIndex,
              position,
              options?.free === true,
              // Other rooms contribute walls to align with; this room's own
              // vertices are added inside vertexAlignSnap.
              collectAlignPlanes(
                (scene?.rooms ?? []).filter((candidate) => candidate.id !== vertexDrag.roomId),
              ),
            ),
            valid: true,
          },
        })
      },

      cancelVertexDrag: () => set({ vertexDrag: null }),

      endVertexDrag: async () => {
        const { vertexDrag, scene } = get()
        if (!vertexDrag || !scene) return
        const room = scene.rooms.find((candidate) => candidate.id === vertexDrag.roomId)
        set({ vertexDrag: null })
        if (!room) return
        const polygon = (room.polygon as Vec2[]).map((point, index) =>
          index === vertexDrag.vertexIndex ? vertexDrag.position : point,
        )
        const previous = room.polygon[vertexDrag.vertexIndex] as Vec2 | undefined
        if (
          previous &&
          Math.abs(previous[0] - vertexDrag.position[0]) <= 1e-9 &&
          Math.abs(previous[1] - vertexDrag.position[1]) <= 1e-9
        ) {
          return
        }
        await get().updateRoomPolygon(room.id, polygon)
      },

      startScaleDrag: (itemId) => {
        const { scene, catalog } = get()
        if (!scene) return
        const item = scene.items.find((candidate) => candidate.id === itemId)
        if (!item) return
        const variant = variantFor(catalog, item.kindId, item.variantId)
        if (!variant) return
        const baseHalf = Math.hypot(variant.sizeM[0], variant.sizeM[1]) / 2
        if (baseHalf <= 1e-6) return
        set({
          scaleDrag: {
            itemId,
            center: [item.transform.position[0], item.transform.position[2]],
            baseHalf,
            scale: item.transform.scale ?? 1,
            valid: true,
          },
        })
      },

      updateScaleDrag: (position) => {
        const { scaleDrag, scene, catalog } = get()
        if (!scaleDrag || !scene) return
        const item = scene.items.find((candidate) => candidate.id === scaleDrag.itemId)
        if (!item) return
        const distance = Math.hypot(
          position[0] - scaleDrag.center[0],
          position[1] - scaleDrag.center[1],
        )
        const scale = clampScale(distance / scaleDrag.baseHalf)
        const check = checkPlacement(scene, catalog, {
          kindId: item.kindId,
          variantId: item.variantId,
          position: scaleDrag.center,
          rotationY: item.transform.rotationY,
          scale,
          ignoreItemId: item.id,
        })
        set({ scaleDrag: { ...scaleDrag, scale, valid: check.valid, issue: check.issue } })
      },

      endScaleDrag: async () => {
        const { scaleDrag, scene } = get()
        if (!scaleDrag) return
        set({ scaleDrag: null })
        if (!scaleDrag.valid) {
          set({
            error: {
              code: scaleDrag.issue?.code ?? 'collision',
              message: scaleDrag.issue?.message ?? '无法缩放到这个尺寸。',
              suggest: scaleDrag.issue?.suggest ?? null,
            },
          })
          return
        }
        const item = scene?.items.find((candidate) => candidate.id === scaleDrag.itemId)
        if (!item) return
        if (Math.abs((item.transform.scale ?? 1) - scaleDrag.scale) <= 1e-9) return
        await get().setItemScale(item.id, scaleDrag.scale)
      },

      cancelScaleDrag: () => set({ scaleDrag: null }),

      startWallDrag: (roomId, edgeIndex, point) => {
        const { scene } = get()
        if (!scene) return
        const room = scene.rooms.find((candidate) => candidate.id === roomId)
        if (!room) return
        const info = edgeInfo(room.polygon as Vec2[], edgeIndex)
        if (!info) return
        const edges = coincidentEdges(scene, roomId, edgeIndex)
        const originals: Record<string, Vec2[]> = {}
        for (const edge of edges) {
          const target = scene.rooms.find((candidate) => candidate.id === edge.roomId)
          if (target) originals[edge.roomId] = [...(target.polygon as Vec2[])]
        }
        set({
          vertexDrag: null,
          wallDrag: {
            edges,
            normal: info.normal,
            start: point,
            offset: 0,
            moved: false,
            originals,
            polygons: {},
            valid: true,
          },
        })
      },

      updateWallDrag: (point) => {
        const { wallDrag, scene, catalog } = get()
        if (!wallDrag || !scene) return
        const primaryRef = wallDrag.edges[0]
        const primary = primaryRef
          ? scene.rooms.find((candidate) => candidate.id === primaryRef.roomId)
          : undefined
        const info =
          primaryRef && primary ? edgeInfo(primary.polygon as Vec2[], primaryRef.edgeIndex) : null

        const dx = point[0] - wallDrag.start[0]
        const dz = point[1] - wallDrag.start[1]
        const raw = dx * wallDrag.normal[0] + dz * wallDrag.normal[1]
        const moved = wallDrag.moved || Math.hypot(dx, dz) > 0.02

        let offset = snapValue(raw)
        if (info) {
          // Snap the moved wall line onto another wall's coordinate so two rooms
          // can share a partition exactly.
          const excluded = wallDrag.edges.flatMap((edge) => [
            { roomId: edge.roomId, vertexIndex: edge.aIndex },
            { roomId: edge.roomId, vertexIndex: edge.bIndex },
          ])
          const planes = collectAlignPlanes(scene.rooms, excluded)
          if (Math.abs(info.a[0] - info.b[0]) <= 1e-6 && Math.abs(wallDrag.normal[0]) > 1e-9) {
            const target = info.a[0] + wallDrag.normal[0] * raw
            offset = (alignValue(target, planes.xs) - info.a[0]) / wallDrag.normal[0]
          } else if (
            Math.abs(info.a[1] - info.b[1]) <= 1e-6 &&
            Math.abs(wallDrag.normal[1]) > 1e-9
          ) {
            const target = info.a[1] + wallDrag.normal[1] * raw
            offset = (alignValue(target, planes.zs) - info.a[1]) / wallDrag.normal[1]
          }
        }

        const delta: Vec2 = [wallDrag.normal[0] * offset, wallDrag.normal[1] * offset]
        const polygons: Record<string, Vec2[]> = {}
        let issue: PlacementIssue | null = null
        for (const edge of wallDrag.edges) {
          const original = wallDrag.originals[edge.roomId]
          if (!original) continue
          const next = original.map((vertex, index) =>
            index === edge.aIndex || index === edge.bIndex
              ? ([vertex[0] + delta[0], vertex[1] + delta[1]] as Vec2)
              : vertex,
          )
          polygons[edge.roomId] = next
          if (!issue) issue = validateRoomMove(scene, catalog, next, original)
        }
        set({
          wallDrag: {
            ...wallDrag,
            offset,
            moved,
            polygons,
            valid: issue === null,
            issue: issue ?? undefined,
          },
        })
      },

      endWallDrag: async () => {
        const { wallDrag } = get()
        if (!wallDrag) return
        if (!wallDrag.moved || Math.abs(wallDrag.offset) <= 1e-9) {
          set({ wallDrag: null })
          return
        }
        if (!wallDrag.valid) {
          set({
            wallDrag: null,
            error: {
              code: wallDrag.issue?.code ?? 'invalid_polygon',
              message: wallDrag.issue?.message ?? '墙体无法移动到此处。',
              suggest: wallDrag.issue?.suggest ?? null,
            },
          })
          return
        }
        const ops: PatchOps = Object.entries(wallDrag.polygons).map(([roomId, polygon]) => ({
          op: 'room.update',
          roomId,
          after: { polygon: rotatePointToCcw(polygon) },
        }))
        set({ wallDrag: null })
        await get().commitOps(ops, '移动墙体失败。')
      },

      cancelWallDrag: () => set({ wallDrag: null }),

      setItemScale: async (itemId, scale) => {
        const { scene, catalog } = get()
        if (!scene) return
        const item = scene.items.find((candidate) => candidate.id === itemId)
        if (!item) return
        const next = clampScale(scale)
        const check = checkPlacement(scene, catalog, {
          kindId: item.kindId,
          variantId: item.variantId,
          position: [item.transform.position[0], item.transform.position[2]],
          rotationY: item.transform.rotationY,
          scale: next,
          ignoreItemId: item.id,
        })
        if (!check.valid) {
          set({
            error: {
              code: check.issue?.code ?? 'collision',
              message: check.issue?.message ?? '无法缩放到这个尺寸。',
              suggest: check.issue?.suggest ?? null,
            },
          })
          return
        }
        await get().commitOps(
          [{ op: 'item.update', itemId, after: { transform: { scale: next } } }],
          'Failed to resize the item.',
        )
      },

      addOpening: async (roomId, edgeIndex, type) => {
        const { scene } = get()
        const room = scene?.rooms.find((candidate) => candidate.id === roomId)
        if (!room) return
        const points = room.polygon as Vec2[]
        const a = points[edgeIndex]
        const b = points[(edgeIndex + 1) % points.length]
        if (!a || !b) return
        const edgeLength = Math.hypot(b[0] - a[0], b[1] - a[1])
        const width = type === 'door' ? 0.9 : 1.2
        const height = type === 'door' ? 2.1 : 1.5
        if (width + 0.2 > edgeLength) {
          set({
            error: {
              code: 'opening_off_wall',
              message: '这段墙太短，放不下门窗。',
              suggest: '换一段更长的墙，或先编辑房间顶点。',
            },
          })
          return
        }
        const opening: Opening = {
          id: newId('op'),
          type,
          roomId,
          edgeIndex,
          offsetFrom: 'start',
          offset: edgeLength / 2,
          width,
          height,
          sillHeight: type === 'window' ? 0.9 : null,
          door: type === 'door' ? { swing: 'single', hinge: 'start', opensTo: 'in' } : null,
        }
        const result = await get().commitOps(
          [{ op: 'opening.add', opening }],
          'Failed to add the opening.',
        )
        if (!result) return
        set({
          selectedWall: { roomId, edgeIndex },
          selectedRoomId: roomId,
          selectedOpeningId: opening.id,
          selectedItemId: null,
          sidebarTab: 'rooms',
        })
      },

      updateOpening: async (openingId, after) => {
        await get().commitOps(
          [{ op: 'opening.update', openingId, after }],
          'Failed to update the opening.',
        )
      },

      deleteOpening: async (openingId) => {
        const result = await get().commitOps(
          [{ op: 'opening.remove', openingId }],
          'Failed to delete the opening.',
        )
        if (!result) return
        set({ selectedOpeningId: null })
      },

      sendChatMessage: async (text) => {
        const message = text.trim()
        const sceneId = get().activeSceneId
        if (!message || !sceneId) return
        set((state) => ({
          chatMessages: [
            ...state.chatMessages,
            { id: `user-${Date.now()}`, role: 'user', content: message },
          ],
          turnStatus: 'queued',
        }))
        try {
          const turn = await api.createTurn(sceneId, {
            message,
            pointContext: get().pointContext
              ? (get().pointContext as unknown as Record<string, unknown>)
              : null,
            thinkingLevel: useUiStore.getState().thinkingLevel,
          })
          set((state) => ({
            turns: [turn, ...state.turns.filter((candidate) => candidate.id !== turn.id)],
            activeTurnId: turn.id ?? null,
            // The turn can finish before this HTTP response arrives (failed in
            // milliseconds); WS events are authoritative once they moved past
            // the optimistic "queued" state, so never overwrite them.
            turnStatus: state.turnStatus === 'queued' ? (turn.status ?? null) : state.turnStatus,
          }))
        } catch (error) {
          set((state) => ({
            error: toUiError(error, 'Failed to start the turn.'),
            turnStatus: null,
            chatMessages: [
              ...state.chatMessages,
              {
                id: `system-${Date.now()}`,
                role: 'system',
                content: toUiError(error, 'Failed to start the turn.').message,
              },
            ],
          }))
        }
      },

      cancelActiveTurn: async () => {
        const turnId = get().activeTurnId
        if (!turnId) return
        try {
          const status = await api.cancelTurn(turnId)
          // `turn.cancelled` appends the visible system note; adding one here
          // as well produced two "cancelled" bubbles per cancel.
          set({ turnStatus: status, waitingUser: null })
        } catch (error) {
          set({ error: toUiError(error, 'Failed to cancel the turn.') })
        }
      },

      confirmProposal: (proposalId) => {
        socket?.sendHost('proposal.confirm', { proposalId })
      },

      rejectProposal: (proposalId) => {
        socket?.sendHost('proposal.reject', { proposalId, reason: 'user_rejected' })
      },

      answerUser: (answers) => {
        const waiting = get().waitingUser
        if (!waiting) return
        socket?.sendHost(
          'ask_user.answer',
          { turnId: waiting.turnId, answers: { [waiting.question]: answers } },
          waiting.turnId,
        )
        set((state) => ({
          waitingUser: null,
          chatMessages: [
            ...state.chatMessages,
            {
              id: `user-${Date.now()}`,
              role: 'user',
              content: answers.join(', '),
            },
          ],
        }))
      },

      refreshTurnState: async () => {
        const sceneId = get().activeSceneId
        if (!sceneId) return
        try {
          const [turns, pending, catalog] = await Promise.all([
            api.listTurns(sceneId),
            api.listProposals(sceneId, 'pending'),
            // Recover generated asset entries missed while disconnected.
            api.listCatalog().catch(() => []),
          ])
          if (get().activeSceneId !== sceneId) return
          const latest = turns[0]
          const active =
            latest?.status === 'running' ||
            latest?.status === 'waiting_user' ||
            latest?.status === 'queued'
          set((state) => ({
            turns,
            // Merge rather than replace: a catalog.updated event may have landed
            // while this request was in flight.
            catalog: catalog.length > 0 ? mergeCatalog(state.catalog, catalog) : state.catalog,
            proposals: Object.fromEntries(pending.map((proposal) => [proposal.id, proposal])),
            activeTurnId: active && latest ? (latest.id ?? null) : state.activeTurnId,
            turnStatus: latest?.status ?? state.turnStatus,
            chatMessages:
              state.chatMessages.length === 0 && latest ? chatFromTurn(latest) : state.chatMessages,
            waitingUser: latest?.status === 'waiting_user' ? state.waitingUser : null,
          }))
          if (active && latest?.id) {
            socket?.sendHost('turn.subscribe', { turnId: latest.id, lastSeq: 0 }, latest.id)
          }
        } catch (error) {
          set({ error: toUiError(error, 'Failed to refresh the agent state.') })
        }
      },

      completeCapture: (requestId, pngBase64, legend) => {
        const request = get().captureRequest
        if (!request || request.requestId !== requestId) return
        socket?.sendHost(
          'capture.result',
          {
            turnId: request.turnId ?? '',
            pngBase64,
            legend,
          },
          request.turnId,
          request.requestId,
        )
        set({ captureRequest: null, restoreCamera: true })
      },

      cancelCapture: (requestId) => {
        const request = get().captureRequest
        if (!request || request.requestId !== requestId) return
        set({ captureRequest: null, restoreCamera: true })
      },

      exportSceneJson: async () => {
        const scene = get().scene
        if (!scene) return
        try {
          const document = await api.exportScene(scene.id)
          const payload = JSON.stringify(document, null, 2)
          downloadBlob(
            `${safeFilename(scene.name)}.json`,
            new Blob([payload], { type: 'application/json' }),
          )
        } catch (error) {
          set({ error: toUiError(error, 'Failed to export the scene.') })
        }
      },

      exportScenePng: () => {
        if (!get().scene) return
        set({ pngExportRequest: { requestId: newId('png'), maxEdge: 2048 } })
      },

      completePngExport: (requestId, dataUrl) => {
        const request = get().pngExportRequest
        if (!request || request.requestId !== requestId) return
        set({ pngExportRequest: null })
        const scene = get().scene
        try {
          const blob = dataUrlToBlob(dataUrl)
          if (blob) {
            downloadBlob(`${safeFilename(scene?.name ?? 'scene')}.png`, blob)
          }
        } catch {
          set({
            error: { code: 'png_export_failed', message: 'PNG 导出失败。', suggest: null },
          })
        }
      },

      cancelPngExport: (requestId) => {
        const request = get().pngExportRequest
        if (!request || request.requestId !== requestId) return
        set({ pngExportRequest: null })
      },

      importSceneFile: async (file) => {
        set({ loading: true, error: null })
        try {
          const text = await file.text()
          const parsed = JSON.parse(text) as SceneDocument
          const imported = await api.importScene(parsed)
          await get().refreshScenes()
          await get().openScene(imported.id)
        } catch (error) {
          set({ loading: false })
          if (error instanceof SyntaxError) {
            set({
              error: {
                code: 'import_invalid',
                message: '文件不是有效的 JSON。',
                suggest: '导出文件应来自「导出 JSON」按钮。',
              },
            })
            return
          }
          set({ error: toUiError(error, 'Failed to import the scene.') })
        }
      },

      saveCameraState: (cameraState) => set({ cameraState }),
      consumeCameraRestore: () => set({ restoreCamera: false }),
      setWalkLocked: (walkLocked) => set({ walkLocked }),

      setSunEnabled: (sunEnabled) => set({ sunEnabled }),
      setSunTime: (sunTimeHours) => set({ sunTimeHours: Math.max(0, Math.min(24, sunTimeHours)) }),
      toggleSunPanel: () => set((state) => ({ sunPanelOpen: !state.sunPanelOpen })),

      updateEnvironment: async (latitude, longitude) => {
        const scene = get().scene
        if (!scene) return
        const previous = scene.environment
        if (
          previous &&
          Math.abs((previous.latitude ?? 0) - latitude) <= 1e-9 &&
          Math.abs((previous.longitude ?? 0) - longitude) <= 1e-9
        ) {
          return
        }
        await get().commitOps(
          [
            {
              op: 'scene.update',
              after: { environment: { latitude, longitude } },
            },
          ],
          'Failed to update the environment.',
        )
      },

      handleWsEnvelope: (envelope) => {
        if (envelope.ch === 'host') {
          if (envelope.type === 'capture.request') {
            const payload = envelope.payload as
              | {
                  width?: number
                  height?: number
                  maxBytes?: number
                  legend?: CaptureLegend | null
                }
              | undefined
            set({
              captureRequest: {
                requestId: envelope.requestId ?? '',
                turnId: envelope.turnId ?? null,
                width: payload?.width ?? 1536,
                height: payload?.height ?? 1536,
                maxBytes: payload?.maxBytes ?? 1_000_000,
                legend: (payload?.legend as CaptureLegend | null | undefined) ?? null,
              },
            })
            return
          }
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
            return
          }
          if (envelope.type === 'host.ack') {
            const payload = envelope.payload as { warnings?: UiWarning[] } | undefined
            if (payload?.warnings?.length) {
              set({ warnings: payload.warnings })
            }
          }
          return
        }
        if (envelope.ch === 'turn') {
          const fresh = envelope.seq > get().lastTurnSeq
          if (fresh) {
            set({ lastTurnSeq: envelope.seq })
            socket?.setResumeTurnSequence(envelope.seq)
          } else if (envelope.type === 'turn.status') {
            // Status is authoritative after a server restart whose sequence
            // counters reset; adopt its cursor so later events are accepted.
            set({ lastTurnSeq: envelope.seq })
            socket?.setResumeTurnSequence(envelope.seq)
          } else {
            return
          }
          const payload = envelope.payload as Record<string, unknown> | undefined
          // Any non-delta event must see the text streamed so far, otherwise the
          // final message could be replaced before its buffered tokens land.
          if (
            envelope.type !== 'assistant.delta' &&
            envelope.type !== 'assistant.reasoning_delta'
          ) {
            streamCoalescer.flushNow()
          }
          if (envelope.type === 'turn.started' || envelope.type === 'turn.status') {
            const turn = payload?.turn as Turn | undefined
            if (!turn) return
            const waiting = payload?.waiting as
              | { question?: string; options?: string[]; allowMultiple?: boolean }
              | undefined
            const history = chatFromTurn(turn)
            const shouldSyncHistory =
              envelope.type === 'turn.status' && (turn.messages?.length ?? 0) > 1
            set((state) => ({
              activeTurnId: turn.id ?? state.activeTurnId,
              turnStatus: turn.status ?? state.turnStatus,
              turns: [turn, ...state.turns.filter((candidate) => candidate.id !== turn.id)],
              chatMessages: shouldSyncHistory
                ? history
                : state.chatMessages.length === 0
                  ? history
                  : envelope.type === 'turn.started'
                    ? finalizeStreaming(state.chatMessages)
                    : state.chatMessages,
              waitingUser: waiting
                ? {
                    turnId: turn.id ?? state.activeTurnId ?? '',
                    question: waiting.question ?? '',
                    options: waiting.options ?? [],
                    allowMultiple: Boolean(waiting.allowMultiple),
                  }
                : envelope.type === 'turn.started'
                  ? null
                  : state.waitingUser,
            }))
            return
          }
          if (envelope.type === 'assistant.delta') {
            streamCoalescer.pushText(String(payload?.text ?? ''))
            return
          }
          if (envelope.type === 'assistant.reasoning_delta') {
            streamCoalescer.pushReasoning(String(payload?.text ?? ''))
            return
          }
          if (envelope.type === 'assistant.message') {
            const content = String(payload?.content ?? '')
            const reasoning = String(payload?.reasoning ?? '')
            set((state) => {
              const index = lastStreamingIndex(state.chatMessages)
              if (index >= 0) {
                const next = [...state.chatMessages]
                const current = next[index]
                if (current) {
                  next[index] = {
                    ...current,
                    content,
                    reasoning: reasoning || current.reasoning,
                    streaming: false,
                  }
                }
                return { chatMessages: next }
              }
              if (!content && !reasoning) return state
              return {
                chatMessages: [
                  ...state.chatMessages,
                  {
                    id: `assistant-${Date.now()}`,
                    role: 'assistant',
                    content,
                    reasoning: reasoning || undefined,
                  },
                ],
              }
            })
            return
          }
          if (envelope.type === 'tool.started') {
            const id = String(payload?.toolCallId ?? '')
            const name = String(payload?.name ?? 'tool')
            set((state) => ({
              chatMessages: [
                ...state.chatMessages,
                { id, role: 'tool', name, content: '', status: 'running' },
              ],
            }))
            return
          }
          if (envelope.type === 'tool.args') {
            const id = String(payload?.toolCallId ?? '')
            const args = payload?.arguments as Record<string, unknown> | undefined
            set((state) => ({
              chatMessages: state.chatMessages.map((entry) =>
                entry.id === id ? { ...entry, arguments: args ?? null } : entry,
              ),
            }))
            return
          }
          if (envelope.type === 'tool.result') {
            const id = String(payload?.toolCallId ?? '')
            const result = payload?.result
            const ok = (result as { ok?: boolean } | undefined)?.ok !== false
            set((state) => ({
              chatMessages: state.chatMessages.map((entry) =>
                entry.id === id
                  ? {
                      ...entry,
                      content: toolSummary(result),
                      status: ok ? 'done' : 'failed',
                      result,
                    }
                  : entry,
              ),
            }))
            return
          }
          if (envelope.type === 'proposal.created') {
            const proposal = payload?.proposal as Proposal | undefined
            if (!proposal) return
            set((state) => ({
              proposals: { ...state.proposals, [proposal.id]: proposal },
            }))
            return
          }
          if (envelope.type === 'turn.waiting_user') {
            const turnId = envelope.turnId ?? get().activeTurnId
            if (!turnId) return
            set({
              turnStatus: 'waiting_user',
              waitingUser: {
                turnId,
                question: String(payload?.question ?? ''),
                options: (payload?.options as string[] | undefined) ?? [],
                allowMultiple: Boolean(payload?.allowMultiple),
              },
            })
            return
          }
          if (envelope.type === 'turn.done') {
            const turn = payload?.turn as Turn | undefined
            set((state) => ({
              turnStatus: 'done',
              waitingUser: null,
              activeTurnId: turn?.id ?? state.activeTurnId,
              chatMessages: finalizeStreaming(state.chatMessages),
              turns: turn
                ? [turn, ...state.turns.filter((candidate) => candidate.id !== turn.id)]
                : state.turns,
            }))
            return
          }
          if (envelope.type === 'turn.failed') {
            const error = payload?.error as
              | { code?: string; message?: string; suggest?: string }
              | undefined
            set((state) => ({
              turnStatus: 'failed',
              waitingUser: null,
              chatMessages: finalizeStreaming([
                ...state.chatMessages,
                {
                  id: `system-${Date.now()}`,
                  role: 'system',
                  content: [error?.message, error?.suggest].filter(Boolean).join(' '),
                },
              ]),
            }))
            return
          }
          if (envelope.type === 'turn.cancelled') {
            set((state) => ({
              turnStatus: 'cancelled',
              waitingUser: null,
              chatMessages: finalizeStreaming([
                ...state.chatMessages,
                { id: `system-${Date.now()}`, role: 'system', content: '回合已取消。' },
              ]),
            }))
            return
          }
          return
        }
        if (envelope.ch !== 'state') return

        if (envelope.type === 'proposal.updated') {
          if (envelope.seq <= get().lastStateSeq) return
          const proposal = (envelope.payload as { proposal?: Proposal } | undefined)?.proposal
          if (!proposal) return
          set((state) => ({
            lastStateSeq: envelope.seq,
            proposals: { ...state.proposals, [proposal.id]: proposal },
          }))
          socket?.setResumeSequence(envelope.seq)
          return
        }

        if (envelope.type === 'catalog.updated') {
          if (envelope.seq <= get().lastStateSeq) return
          const entry = (envelope.payload as { entry?: CatalogEntry } | undefined)?.entry
          if (!entry) return
          // Generated assets arrive before the proposal that references them, so
          // a ghost can always resolve its model URL.
          set((state) => ({
            lastStateSeq: envelope.seq,
            catalog: [
              ...state.catalog.filter((candidate) => candidate.kindId !== entry.kindId),
              entry,
            ],
          }))
          socket?.setResumeSequence(envelope.seq)
          return
        }

        if (envelope.type === 'scene.snapshot') {
          const payload = envelope.payload as
            | { scene?: SceneDocument; serverEpoch?: string | null }
            | undefined
          const scene = payload?.scene
          if (!scene) return
          if (get().activeSceneId && get().activeSceneId !== scene.id) return
          const epoch = payload?.serverEpoch ?? null
          const epochChanged = Boolean(epoch && epoch !== get().serverEpoch)
          if (epochChanged) {
            // A restarted server resets per-channel sequence numbers; discard
            // stale turn cursors and rebuild turn/proposal state from REST.
            set({
              serverEpoch: epoch,
              lastTurnSeq: 0,
              chatMessages: [],
              proposals: {},
              waitingUser: null,
            })
            socket?.setResumeTurnSequence(0)
          } else if (epoch) {
            set({ serverEpoch: epoch })
          }
          const current = get().scene
          if (current && current.id === scene.id && current.version === scene.version) {
            set({ lastStateSeq: envelope.seq })
            socket?.setResumeSequence(envelope.seq)
            void get().refreshTurnState()
            return
          }
          withSceneUpdate(() =>
            set((state) => ({
              lastStateSeq: envelope.seq,
              scene,
              scenes: syncSceneSummary(state.scenes, scene),
              ...reconcileSelection(scene, state.catalog, state),
            })),
          )
          socket?.setResumeSequence(envelope.seq)
          void get().refreshTurnState()
          return
        }

        if (envelope.type !== 'scene.patch') return
        if (envelope.seq <= get().lastStateSeq) return
        const payload = envelope.payload as { patch?: ScenePatch } | undefined
        const patch = payload?.patch
        if (!patch) return
        if (get().activeSceneId && get().activeSceneId !== patch.sceneId) return
        if (patch.actor === 'user') {
          // Manual edits expire every pending proposal server-side.
          set({ proposals: {} })
        }

        const current = get().scene
        if (current && patch.toVersion <= current.version) {
          set((state) => ({
            lastStateSeq: envelope.seq,
            scenes: syncSceneSummary(state.scenes, current),
          }))
          socket?.setResumeSequence(envelope.seq)
          return
        }
        if (current && patch.fromVersion === current.version) {
          try {
            const next = applyPatchToDocument(current, patch.ops)
            next.version = patch.toVersion
            withSceneUpdate(() =>
              set((state) => ({
                scene: next,
                scenes: syncSceneSummary(state.scenes, next),
                lastStateSeq: envelope.seq,
                undoDepth:
                  localMutations > 0
                    ? state.undoDepth
                    : bumpUndoDepth(state, patch.sceneId, patch.toVersion),
                redoDepth: 0,
                ...reconcileSelection(next, state.catalog, state),
              })),
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
            .then((scene) => {
              if (get().activeSceneId !== scene.id) return
              set((state) => ({
                scene,
                scenes: syncSceneSummary(state.scenes, scene),
                lastStateSeq: envelope.seq,
                ...reconcileSelection(scene, state.catalog, state),
              }))
            })
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

/**
 * Streaming deltas are buffered per animation frame so a token-per-event
 * provider does not copy the message array (and re-parse markdown) 60+ times a
 * second. `assistant.message` and tool events flush it first, keeping order.
 */
const streamCoalescer = createStreamCoalescer((delta) => {
  useSceneStore.setState((state) => {
    const index = lastStreamingIndex(state.chatMessages)
    if (index >= 0) {
      const current = state.chatMessages[index]
      if (!current) return state
      const next = [...state.chatMessages]
      next[index] = {
        ...current,
        content: current.content + delta.text,
        reasoning: delta.reasoning
          ? `${current.reasoning ?? ''}${delta.reasoning}`
          : current.reasoning,
      }
      return { chatMessages: next }
    }
    if (!delta.text && !delta.reasoning) return state
    return {
      chatMessages: [
        ...state.chatMessages,
        {
          id: `assistant-${Date.now()}`,
          role: 'assistant',
          content: delta.text,
          reasoning: delta.reasoning || undefined,
          streaming: true,
        },
      ],
    }
  })
})
