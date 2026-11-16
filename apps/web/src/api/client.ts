import type {
  AddItemRequest,
  CatalogEntry,
  CommitRequest,
  CreateSceneRequest,
  CreateTurnRequest,
  FixtureSummary,
  Proposal,
  ProposalStatus,
  SceneDocument,
  SceneResult,
  SceneSummary,
  Turn,
  TurnStatus,
  UpdateItemRequest,
} from '../contracts/generated/client/rest'
import {
  addSceneItemApiScenesSceneIdItemsPost,
  cancelTurnApiTurnsTurnIdCancelPost,
  commitSceneApiScenesSceneIdCommitPost,
  createSceneApiScenesPost,
  createTurnApiScenesSceneIdTurnsPost,
  deleteSceneItemApiScenesSceneIdItemsItemIdDelete,
  exportSceneApiScenesSceneIdExportGet,
  getSceneApiScenesSceneIdGet,
  importSceneApiScenesImportPost,
  listCatalogApiCatalogGet,
  listFixturesApiFixturesGet,
  listProposalsApiScenesSceneIdProposalsGet,
  listScenesApiScenesGet,
  listTurnsApiScenesSceneIdTurnsGet,
  redoSceneApiScenesSceneIdRedoPost,
  reloadCatalogApiCatalogReloadPost,
  undoSceneApiScenesSceneIdUndoPost,
  updateSceneItemApiScenesSceneIdItemsItemIdPatch,
} from '../contracts/generated/client/rest'
import type { ErrorBody } from '../contracts/generated/ws'

interface ErrorEnvelope {
  error: ErrorBody
}

export class ApiError extends Error {
  readonly code: string
  readonly suggest?: string | null
  readonly details?: Record<string, unknown> | null
  readonly status: number

  constructor(
    code: string,
    message: string,
    status: number,
    suggest?: string | null,
    details?: Record<string, unknown> | null,
  ) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.status = status
    this.suggest = suggest
    this.details = details
  }
}

type RawResponse = { data: unknown; status: number }

function unwrap<T>(result: RawResponse): T {
  if (result.status >= 200 && result.status < 300) {
    return result.data as T
  }
  const envelope = result.data as ErrorEnvelope | undefined
  const error = envelope?.error
  if (error) {
    throw new ApiError(
      error.code,
      error.message,
      result.status,
      error.suggest ?? undefined,
      error.details ?? undefined,
    )
  }
  throw new ApiError('http_error', `Request failed with HTTP ${result.status}.`, result.status)
}

async function call<T>(request: () => Promise<RawResponse>): Promise<T> {
  try {
    return unwrap<T>(await request())
  } catch (error) {
    if (error instanceof ApiError) throw error
    throw new ApiError(
      'network_error',
      error instanceof Error ? error.message : 'Network request failed.',
      0,
      'Check that the API server is running.',
    )
  }
}

export async function listScenes(): Promise<SceneSummary[]> {
  const response = await call<{ scenes: SceneSummary[] }>(() => listScenesApiScenesGet())
  return response.scenes
}

export async function createScene(request: CreateSceneRequest): Promise<SceneDocument> {
  return call<SceneDocument>(() => createSceneApiScenesPost(request))
}

export async function getScene(sceneId: string): Promise<SceneDocument> {
  return call<SceneDocument>(() => getSceneApiScenesSceneIdGet(sceneId))
}

export async function commitScene(sceneId: string, request: CommitRequest): Promise<SceneResult> {
  return call<SceneResult>(() => commitSceneApiScenesSceneIdCommitPost(sceneId, request))
}

export async function undoScene(sceneId: string, expectedVersion: number): Promise<SceneResult> {
  return call<SceneResult>(() => undoSceneApiScenesSceneIdUndoPost(sceneId, { expectedVersion }))
}

export async function redoScene(sceneId: string, expectedVersion: number): Promise<SceneResult> {
  return call<SceneResult>(() => redoSceneApiScenesSceneIdRedoPost(sceneId, { expectedVersion }))
}

export async function addItem(sceneId: string, request: AddItemRequest): Promise<SceneResult> {
  return call<SceneResult>(() => addSceneItemApiScenesSceneIdItemsPost(sceneId, request))
}

export async function updateItem(
  sceneId: string,
  itemId: string,
  request: UpdateItemRequest,
): Promise<SceneResult> {
  return call<SceneResult>(() =>
    updateSceneItemApiScenesSceneIdItemsItemIdPatch(sceneId, itemId, request),
  )
}

export async function deleteItem(sceneId: string, itemId: string): Promise<SceneResult> {
  return call<SceneResult>(() => deleteSceneItemApiScenesSceneIdItemsItemIdDelete(sceneId, itemId))
}

export async function listFixtures(): Promise<FixtureSummary[]> {
  const response = await call<{ fixtures: FixtureSummary[] }>(() => listFixturesApiFixturesGet())
  return response.fixtures
}

export async function listCatalog(): Promise<CatalogEntry[]> {
  const response = await call<{ entries: CatalogEntry[] }>(() => listCatalogApiCatalogGet())
  return response.entries
}

export async function reloadCatalog(): Promise<CatalogEntry[]> {
  const response = await call<{ entries: CatalogEntry[] }>(() =>
    reloadCatalogApiCatalogReloadPost(),
  )
  return response.entries
}

export async function exportScene(sceneId: string): Promise<SceneDocument> {
  return call<SceneDocument>(() => exportSceneApiScenesSceneIdExportGet(sceneId))
}

export async function importScene(document: SceneDocument): Promise<SceneDocument> {
  return call<SceneDocument>(() => importSceneApiScenesImportPost({ document }))
}

export async function listTurns(sceneId: string): Promise<Turn[]> {
  const response = await call<{ turns: Turn[] }>(() => listTurnsApiScenesSceneIdTurnsGet(sceneId))
  return response.turns
}

export async function createTurn(sceneId: string, request: CreateTurnRequest): Promise<Turn> {
  const response = await call<{ turn: Turn }>(() =>
    createTurnApiScenesSceneIdTurnsPost(sceneId, request),
  )
  return response.turn
}

export async function cancelTurn(turnId: string): Promise<TurnStatus> {
  const response = await call<{ status: TurnStatus }>(() =>
    cancelTurnApiTurnsTurnIdCancelPost(turnId),
  )
  return response.status
}

export async function listProposals(sceneId: string, status?: ProposalStatus): Promise<Proposal[]> {
  const response = await call<{ proposals: Proposal[] }>(() =>
    listProposalsApiScenesSceneIdProposalsGet(sceneId, status ? { status } : undefined),
  )
  return response.proposals
}
