// biome-ignore-all lint/suspicious/noArrayIndexKey: draft vertices and polygon edges are positional identities in this editor.
import { Edges, Html, Line, OrbitControls, useGLTF } from '@react-three/drei'
import { Canvas, type ThreeEvent, useFrame, useThree } from '@react-three/fiber'
import { Component, type ReactNode, Suspense, useEffect, useMemo, useRef } from 'react'
import * as THREE from 'three'
import type { CatalogEntry, Item, Room, SceneDocument } from '../../contracts/generated/client/rest'
import { getCatalogDragKind, setCatalogDragKind } from '../../scene/catalogDrag'
import { distance, edgeInfo, type Vec2 } from '../../scene/geometry'
import { pendingOverlay } from '../../scene/proposals'
import { buildWalls } from '../../scene/walls'
import {
  type CameraState,
  type CaptureRequestState,
  type CatalogGhostState,
  type ItemDragState,
  type PngExportRequestState,
  useSceneStore,
  type ViewMode,
} from '../../store/sceneStore'
import { CaptureGrab, CaptureLabels, PngExportGrab } from './CaptureOverlay'
import { WalkRig } from './WalkRig'

const WALL_COLOR = '#d9d5cc'
const WALL_SELECTED = '#8fa9e8'
const DOOR_COLOR = '#8a6337'
const WINDOW_COLOR = '#8ec5e8'
const SELECTION_COLOR = '#7aa2ff'
const VALID_COLOR = '#57d18b'
const INVALID_COLOR = '#ef6464'
const PENDING_ROOM_COLOR = '#67e8f9'
const FLOOR_COLORS: Record<string, string> = {
  living: '#544636',
  bedroom: '#4c4034',
  kitchen: '#3d4447',
  bathroom: '#37444b',
  study: '#4b443a',
  balcony: '#3e453a',
  other: '#46423d',
}
const CATEGORY_COLORS: Record<string, string> = {
  sofa: '#62719a',
  bed: '#876a58',
  table: '#76634c',
  storage: '#647268',
  lighting: '#a98f58',
  decor: '#756787',
  other: '#686868',
}

interface SceneCanvasProps {
  scene: SceneDocument
  catalog: CatalogEntry[]
  viewMode: ViewMode
  selectedItemId: string | null
  perfMode: boolean
  cameraResetToken: number
  onSelectItem: (itemId: string | null) => void
  onFps: (fps: number) => void
}

interface Bounds {
  center: Vec2
  width: number
  depth: number
  maxDim: number
}

function sceneBounds(scene: SceneDocument): Bounds {
  if (scene.rooms.length === 0) {
    return { center: [0, 0], width: 10, depth: 8, maxDim: 10 }
  }
  const xs: number[] = []
  const zs: number[] = []
  for (const room of scene.rooms) {
    for (const point of room.polygon as Vec2[]) {
      xs.push(point[0])
      zs.push(point[1])
    }
  }
  const minX = Math.min(...xs)
  const maxX = Math.max(...xs)
  const minZ = Math.min(...zs)
  const maxZ = Math.max(...zs)
  const width = Math.max(maxX - minX, 1)
  const depth = Math.max(maxZ - minZ, 1)
  return {
    center: [(minX + maxX) / 2, (minZ + maxZ) / 2],
    width,
    depth,
    maxDim: Math.max(width, depth, 6),
  }
}

function CameraFit({
  bounds,
  orthographic,
  fitToken,
  restore,
  saved,
  walkMode,
  onRestored,
}: {
  bounds: Bounds
  orthographic: boolean
  fitToken: string
  restore: boolean
  saved: CameraState | null
  walkMode: boolean
  onRestored: () => void
}) {
  const camera = useThree((state) => state.camera)
  const size = useThree((state) => state.size)
  const [cx, cz] = bounds.center
  const savedRef = useRef(saved)
  const restoreRef = useRef(restore)
  savedRef.current = saved
  restoreRef.current = restore

  useEffect(() => {
    // The first-person rig owns the camera while walking; refits would snap it.
    if (walkMode) return
    const savedState = savedRef.current
    if (restoreRef.current && savedState) {
      camera.up.set(0, 1, 0)
      camera.position.set(...savedState.position)
      camera.lookAt(...savedState.target)
      if (camera instanceof THREE.OrthographicCamera) {
        camera.zoom = savedState.zoom
      } else if (camera instanceof THREE.PerspectiveCamera) {
        camera.zoom = 1
      }
      camera.updateProjectionMatrix()
      onRestored()
      return
    }
    // fitToken/orthographic are intentional triggers for refitting.
    void fitToken
    void orthographic
    if (camera instanceof THREE.OrthographicCamera) {
      camera.up.set(0, 1, 0)
      camera.zoom = Math.min(size.width, size.height) / (bounds.maxDim * 1.15)
      // A small z offset avoids the degenerate lookAt when the camera is
      // exactly above the target (view direction parallel to the up vector).
      camera.position.set(cx, bounds.maxDim * 2, cz + bounds.maxDim * 0.05)
      camera.lookAt(cx, 0, cz)
      camera.updateProjectionMatrix()
    } else if (camera instanceof THREE.PerspectiveCamera) {
      camera.up.set(0, 1, 0)
      camera.position.set(cx + bounds.maxDim * 0.85, bounds.maxDim * 1.15, cz + bounds.maxDim * 1.0)
      camera.lookAt(cx, 0, cz)
      camera.updateProjectionMatrix()
    }
    // Fit only when the scene identity changes or the user resets the camera.
    // Item commits update `scene` but must not move the camera.
  }, [
    bounds.maxDim,
    camera,
    cx,
    cz,
    fitToken,
    onRestored,
    orthographic,
    size.height,
    size.width,
    walkMode,
  ])

  return null
}

function CameraKeeper({ enabled }: { enabled: boolean }) {
  const camera = useThree((state) => state.camera)
  const controls = useThree((state) => state.controls) as { target?: THREE.Vector3 } | null
  const saveCameraState = useSceneStore((state) => state.saveCameraState)
  const lastSave = useRef(0)
  const lastSignature = useRef('')

  useFrame(() => {
    if (!enabled) return
    const now = performance.now()
    if (now - lastSave.current < 1000) return
    lastSave.current = now
    const target = controls?.target
      ? ([controls.target.x, controls.target.y, controls.target.z] as [number, number, number])
      : ([camera.position.x, 0, camera.position.z] as [number, number, number])
    const signature = [...camera.position.toArray(), ...target, camera.zoom]
      .map((value) => value.toFixed(2))
      .join(',')
    if (signature === lastSignature.current) return
    lastSignature.current = signature
    saveCameraState({
      position: [camera.position.x, camera.position.y, camera.position.z],
      target,
      zoom: camera.zoom,
    })
  })
  return null
}

function RoomFloor({ room }: { room: Room }) {
  const selectedRoomId = useSceneStore((state) => state.selectedRoomId)
  const selectRoom = useSceneStore((state) => state.selectRoom)
  const selected = selectedRoomId === room.id
  const shape = useMemo(() => {
    const points = room.polygon as Vec2[]
    const value = new THREE.Shape()
    points.forEach(([x, z], index) => {
      if (index === 0) value.moveTo(x, -z)
      else value.lineTo(x, -z)
    })
    value.closePath()
    return value
  }, [room.polygon])

  const base = FLOOR_COLORS[room.type ?? 'other'] ?? FLOOR_COLORS.other
  return (
    // biome-ignore lint/a11y/noStaticElementInteractions: R3F mesh is a 3D pick target
    <mesh
      onClick={(event) => {
        if (useSceneStore.getState().tool !== 'select') return
        const itemHit = event.intersections.some((intersection) => {
          let object: THREE.Object3D | null = intersection.object
          while (object) {
            if (object.userData?.itemId) return true
            object = object.parent
          }
          return false
        })
        if (itemHit) return
        event.stopPropagation()
        selectRoom(room.id)
      }}
      receiveShadow
      rotation={[-Math.PI / 2, 0, 0]}
    >
      <shapeGeometry args={[shape]} />
      <meshStandardMaterial
        color={selected ? '#6b5b45' : base}
        emissive={selected ? '#3a3226' : '#000000'}
        metalness={0.02}
        roughness={0.92}
      />
    </mesh>
  )
}

function Walls({ scene, topView }: { scene: SceneDocument; topView: boolean }) {
  const geometry = useMemo(() => buildWalls(scene, { topView }), [scene, topView])
  const selectedWall = useSceneStore((state) => state.selectedWall)
  const selectWall = useSceneStore((state) => state.selectWall)
  const camera = useThree((state) => state.camera)

  return (
    <group>
      {geometry.walls.map((wall) => {
        const selected =
          selectedWall !== null &&
          wall.edges.some(
            (edge) =>
              edge.roomId === selectedWall.roomId && edge.edgeIndex === selectedWall.edgeIndex,
          )
        return (
          // biome-ignore lint/a11y/noStaticElementInteractions: R3F mesh is a 3D pick target
          <mesh
            castShadow={wall.part === 'wall'}
            key={wall.key}
            onClick={(event) => {
              if (useSceneStore.getState().tool !== 'select') return
              let chosen = wall.edges[0]
              let best = Number.NEGATIVE_INFINITY
              for (const edge of wall.edges) {
                const room = scene.rooms.find((candidate) => candidate.id === edge.roomId)
                if (!room) continue
                const info = edgeInfo(room.polygon as Vec2[], edge.edgeIndex)
                if (!info) continue
                const dx = info.b[0] - info.a[0]
                const dz = info.b[1] - info.a[1]
                const lengthSq = dx * dx + dz * dz
                if (lengthSq <= 1e-9) continue
                const t =
                  ((event.point.x - info.a[0]) * dx + (event.point.z - info.a[1]) * dz) / lengthSq
                // Disjoint collinear edges share a line but not a span.
                if (t < -0.02 || t > 1.02) continue
                // Pick the room side the camera is looking from, so a shared
                // wall selects the edge belonging to the visible room.
                const score =
                  (camera.position.x - event.point.x) * info.normal[0] +
                  (camera.position.z - event.point.z) * info.normal[1]
                if (score > best) {
                  best = score
                  chosen = edge
                }
              }
              if (!chosen) return
              event.stopPropagation()
              selectWall({ roomId: chosen.roomId, edgeIndex: chosen.edgeIndex })
            }}
            position={[wall.center[0], wall.y, wall.center[1]]}
            receiveShadow
            rotation={[0, wall.rotationY, 0]}
          >
            <boxGeometry args={[wall.length, wall.height, wall.thickness]} />
            <meshStandardMaterial
              color={selected ? WALL_SELECTED : WALL_COLOR}
              emissive={selected ? '#22314f' : '#000000'}
              emissiveIntensity={selected ? 0.5 : 0}
              roughness={0.82}
            />
          </mesh>
        )
      })}

      {geometry.doors.map((door) => (
        <group
          key={door.key}
          position={[door.center[0], 0, door.center[1]]}
          rotation={[0, door.rotationY, 0]}
        >
          <mesh castShadow position={[0, door.height / 2, 0]}>
            <boxGeometry args={[door.width * 0.92, door.height * 0.97, 0.05]} />
            <meshStandardMaterial color={DOOR_COLOR} roughness={0.6} />
            <Edges color="#3a2a18" threshold={15} />
          </mesh>
          <mesh position={[door.width * 0.32, 1.0, 0.04]}>
            <sphereGeometry args={[0.035, 12, 12]} />
            <meshStandardMaterial color="#d6c7a8" metalness={0.6} roughness={0.3} />
          </mesh>
        </group>
      ))}

      {geometry.windows.map((window) => (
        <mesh
          key={window.key}
          position={[window.center[0], window.sill + window.height / 2, window.center[1]]}
          rotation={[0, window.rotationY, 0]}
        >
          <boxGeometry args={[window.width * 0.96, window.height * 0.96, 0.035]} />
          <meshStandardMaterial
            color={WINDOW_COLOR}
            metalness={0.15}
            opacity={0.4}
            roughness={0.08}
            transparent
          />
          <Edges color="#cfe8f7" threshold={15} />
        </mesh>
      ))}
    </group>
  )
}

interface VariantInfo {
  width: number
  depth: number
  height: number
  category: string
  modelPath: string | null
  thumbnailPath: string | null
}

function itemVariant(item: Item, catalog: CatalogEntry[]): VariantInfo {
  const entry = catalog.find((candidate) => candidate.kindId === item.kindId)
  const variant =
    entry?.variants.find((candidate) => candidate.id === item.variantId) ?? entry?.variants[0]
  const size = variant?.sizeM ?? [1, 1, 1]
  return {
    width: size[0],
    depth: size[1],
    height: size[2],
    category: entry?.category ?? 'other',
    modelPath: variant?.modelPath ?? null,
    thumbnailPath: variant?.thumbnailPath ?? null,
  }
}

function FallbackBox({
  width,
  depth,
  height,
  color,
  selected,
}: {
  width: number
  depth: number
  height: number
  color: string
  selected: boolean
}) {
  return (
    <mesh castShadow position={[0, height / 2, 0]} receiveShadow>
      <boxGeometry args={[Math.max(width, 0.05), Math.max(height, 0.05), Math.max(depth, 0.05)]} />
      <meshStandardMaterial
        color={selected ? SELECTION_COLOR : color}
        emissive={selected ? SELECTION_COLOR : '#000000'}
        emissiveIntensity={selected ? 0.18 : 0}
        metalness={0.05}
        opacity={0.94}
        roughness={0.72}
        transparent
        wireframe={false}
      />
      <Edges color={selected ? '#dbe6ff' : '#181818'} threshold={20} />
    </mesh>
  )
}

function LoadedModel({ url }: { url: string }) {
  const { scene } = useGLTF(url)
  const cloned = useMemo(() => {
    const copy = scene.clone(true)
    copy.traverse((object) => {
      if (object instanceof THREE.Mesh) {
        object.castShadow = true
        object.receiveShadow = true
      }
    })
    return copy
  }, [scene])
  return <primitive object={cloned} />
}

class ModelErrorBoundary extends Component<
  { fallback: ReactNode; children?: ReactNode },
  { failed: boolean }
> {
  state = { failed: false }

  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true }
  }

  render(): ReactNode {
    return this.state.failed ? this.props.fallback : this.props.children
  }
}

function ModelOrFallback({ variant, selected }: { variant: VariantInfo; selected: boolean }) {
  const fallback = (
    <FallbackBox
      color={CATEGORY_COLORS[variant.category] ?? '#686868'}
      depth={variant.depth}
      height={variant.height}
      selected={selected}
      width={variant.width}
    />
  )
  if (!variant.modelPath) return fallback
  return (
    <ModelErrorBoundary key={variant.modelPath} fallback={fallback}>
      <Suspense fallback={fallback}>
        <LoadedModel url={variant.modelPath} />
      </Suspense>
    </ModelErrorBoundary>
  )
}

function ItemMesh({
  item,
  catalog,
  selected,
  dragState,
}: {
  item: Item
  catalog: CatalogEntry[]
  selected: boolean
  dragState: ItemDragState | null
}) {
  const variant = itemVariant(item, catalog)
  const dragging = dragState?.itemId === item.id
  const invalid = Boolean(dragging && dragState?.moved && !dragState.valid)
  const [baseX, baseY, baseZ] = item.transform.position
  const position: [number, number, number] =
    dragging && dragState ? dragState.position : [baseX, baseY, baseZ]
  const ringColor = invalid ? INVALID_COLOR : SELECTION_COLOR

  const handlePointerDown = (event: ThreeEvent<PointerEvent>) => {
    if (event.button !== 0) return
    if (useSceneStore.getState().tool !== 'select') return
    if (useSceneStore.getState().viewMode === 'walk') return
    event.stopPropagation()
    // Use the floor intersection, not the (possibly raised) mesh surface, so
    // tall furniture does not jump on the first pointermove.
    const plane = new THREE.Plane(new THREE.Vector3(0, 1, 0), 0)
    const ground = new THREE.Vector3()
    const grab = event.ray.intersectPlane(plane, ground) ? ground : event.point
    useSceneStore
      .getState()
      .startItemDrag(item.id, item.transform.position, item.transform.rotationY, [
        baseX - grab.x,
        baseZ - grab.z,
      ])
  }

  const handleClick = (event: ThreeEvent<MouseEvent>) => {
    if (useSceneStore.getState().tool !== 'select') return
    if (useSceneStore.getState().viewMode === 'walk') return
    event.stopPropagation()
    useSceneStore.getState().selectItem(item.id)
  }

  return (
    // biome-ignore lint/a11y/noStaticElementInteractions: R3F group is a 3D pick target
    <group
      onClick={handleClick}
      onPointerDown={handlePointerDown}
      onPointerOut={() => {
        document.body.style.cursor = ''
      }}
      onPointerOver={(event) => {
        event.stopPropagation()
        document.body.style.cursor = dragging ? 'grabbing' : 'grab'
      }}
      position={position}
      rotation={[0, item.transform.rotationY, 0]}
      userData={{ itemId: item.id }}
    >
      {selected || dragging ? (
        <mesh position={[0, 0.012, 0]} rotation={[-Math.PI / 2, 0, 0]}>
          <ringGeometry
            args={[
              Math.max(variant.width, variant.depth) / 2 + 0.12,
              Math.max(variant.width, variant.depth) / 2 + 0.19,
              40,
            ]}
          />
          <meshBasicMaterial color={ringColor} opacity={0.92} side={THREE.DoubleSide} transparent />
        </mesh>
      ) : null}
      <ModelOrFallback selected={selected || dragging} variant={variant} />
      <mesh position={[0, 0.03, variant.depth / 2 + 0.12]}>
        <boxGeometry args={[Math.min(variant.width * 0.4, 0.4), 0.02, 0.12]} />
        <meshBasicMaterial color={invalid ? INVALID_COLOR : '#9aa4b2'} />
      </mesh>
    </group>
  )
}

function PendingItemGhost({ item, catalog }: { item: Item; catalog: CatalogEntry[] }) {
  const variant = itemVariant(item, catalog)
  const color = '#67e8f9'
  const [x, , z] = item.transform.position
  return (
    <group position={[x, 0, z]} rotation={[0, item.transform.rotationY, 0]}>
      <mesh position={[0, variant.height / 2, 0]}>
        <boxGeometry args={[variant.width, variant.height, variant.depth]} />
        <meshStandardMaterial
          color={color}
          emissive={color}
          emissiveIntensity={0.35}
          opacity={0.3}
          transparent
        />
        <Edges color={color} threshold={20} />
      </mesh>
      <mesh position={[0, 0.015, 0]} rotation={[-Math.PI / 2, 0, 0]}>
        <ringGeometry
          args={[
            Math.max(variant.width, variant.depth) / 2 + 0.1,
            Math.max(variant.width, variant.depth) / 2 + 0.15,
            40,
          ]}
        />
        <meshBasicMaterial color={color} opacity={0.9} side={THREE.DoubleSide} transparent />
      </mesh>
    </group>
  )
}

function GhostItem({ ghost, catalog }: { ghost: CatalogGhostState; catalog: CatalogEntry[] }) {
  const variant = itemVariant(
    {
      id: 'ghost',
      kindId: ghost.kindId,
      variantId: ghost.variantId,
      transform: {
        position: [ghost.position[0], 0, ghost.position[1]],
        rotationY: ghost.rotationY,
      },
    },
    catalog,
  )
  const color = ghost.valid ? VALID_COLOR : INVALID_COLOR
  return (
    <group position={[ghost.position[0], 0, ghost.position[1]]} rotation={[0, ghost.rotationY, 0]}>
      <mesh position={[0, variant.height / 2, 0]}>
        <boxGeometry args={[variant.width, variant.height, variant.depth]} />
        <meshStandardMaterial
          color={color}
          emissive={color}
          emissiveIntensity={0.25}
          opacity={0.38}
          transparent
        />
        <Edges color={color} threshold={20} />
      </mesh>
      <mesh position={[0, 0.014, 0]} rotation={[-Math.PI / 2, 0, 0]}>
        <ringGeometry
          args={[
            Math.max(variant.width, variant.depth) / 2 + 0.12,
            Math.max(variant.width, variant.depth) / 2 + 0.16,
            40,
          ]}
        />
        <meshBasicMaterial color={color} opacity={0.85} side={THREE.DoubleSide} transparent />
      </mesh>
    </group>
  )
}

function DraftOverlay() {
  const draftRoom = useSceneStore((state) => state.draftRoom)
  const draftCursor = useSceneStore((state) => state.draftCursor)
  const points = draftCursor ? [...draftRoom, draftCursor] : draftRoom
  if (points.length === 0) return null

  const first = points[0]
  const last = points[points.length - 1]
  const canClose = points.length >= 3
  const anchor = draftRoom[draftRoom.length - 1]
  return (
    <group>
      {points.length >= 2 ? (
        <Line
          color="#7aa2ff"
          lineWidth={2.5}
          points={points.map(([x, z]): [number, number, number] => [x, 0.035, z])}
        />
      ) : null}
      {canClose && first && last ? (
        <Line
          color="#7aa2ff"
          dashed
          dashSize={0.12}
          gapSize={0.08}
          lineWidth={1}
          opacity={0.55}
          points={[
            [last[0], 0.035, last[1]],
            [first[0], 0.035, first[1]],
          ]}
          transparent
        />
      ) : null}
      {draftRoom.map(([x, z], index) => (
        <mesh key={`${x}-${z}-${index}`} position={[x, 0.06, z]}>
          <sphereGeometry args={[index === 0 ? 0.11 : 0.075, 16, 16]} />
          <meshBasicMaterial color={index === 0 ? VALID_COLOR : '#dbe6ff'} />
        </mesh>
      ))}
      {draftCursor ? (
        <mesh position={[draftCursor[0], 0.04, draftCursor[1]]}>
          <ringGeometry args={[0.08, 0.13, 24]} />
          <meshBasicMaterial color="#dbe6ff" opacity={0.8} side={THREE.DoubleSide} transparent />
        </mesh>
      ) : null}
      {anchor && draftCursor ? (
        <Html
          center
          position={[(anchor[0] + draftCursor[0]) / 2, 0.14, (anchor[1] + draftCursor[1]) / 2]}
          style={{ pointerEvents: 'none' }}
        >
          <div className="rounded bg-slate-900/80 px-1.5 py-0.5 text-[10px] leading-tight text-white select-none tabular-nums">
            {distance(anchor, draftCursor).toFixed(2)} m
          </div>
        </Html>
      ) : null}
    </group>
  )
}

function PendingRoomGhost({ room }: { room: Room }) {
  const shape = useMemo(() => {
    const value = new THREE.Shape()
    const points = room.polygon as Vec2[]
    points.forEach(([x, z], index) => {
      if (index === 0) value.moveTo(x, -z)
      else value.lineTo(x, -z)
    })
    value.closePath()
    return value
  }, [room.polygon])
  const outline = useMemo(() => {
    const points = room.polygon as Vec2[]
    const first = points[0]
    const closed = first ? [...points, first] : []
    return closed.map(([x, z]): [number, number, number] => [x, 0.04, z])
  }, [room.polygon])
  const label = useMemo(() => {
    const points = room.polygon as Vec2[]
    const sum = points.reduce<Vec2>(
      (accumulator, [x, z]) => [accumulator[0] + x, accumulator[1] + z],
      [0, 0],
    )
    return [sum[0] / points.length, sum[1] / points.length] as Vec2
  }, [room.polygon])
  return (
    <group>
      <mesh position={[0, 0.012, 0]} rotation={[-Math.PI / 2, 0, 0]}>
        <shapeGeometry args={[shape]} />
        <meshBasicMaterial
          color={PENDING_ROOM_COLOR}
          depthWrite={false}
          opacity={0.14}
          transparent
        />
      </mesh>
      <Line
        color={PENDING_ROOM_COLOR}
        dashed
        dashSize={0.14}
        gapSize={0.09}
        lineWidth={2}
        points={outline}
      />
      <Html center position={[label[0], 0.16, label[1]]} style={{ pointerEvents: 'none' }}>
        <div className="rounded border border-cyan-300/40 bg-cyan-950/80 px-1.5 py-0.5 text-[10px] leading-tight text-cyan-100 select-none tabular-nums">
          {room.name}
        </div>
      </Html>
    </group>
  )
}

function DrawingPlane() {
  const addDraftPoint = useSceneStore((state) => state.addDraftPoint)
  const setDraftCursor = useSceneStore((state) => state.setDraftCursor)
  return (
    // biome-ignore lint/a11y/noStaticElementInteractions: R3F mesh is the draw surface
    <mesh
      onClick={(event) => {
        event.stopPropagation()
        addDraftPoint([event.point.x, event.point.z], { free: event.nativeEvent.shiftKey })
      }}
      onPointerMove={(event) =>
        setDraftCursor([event.point.x, event.point.z], { free: event.nativeEvent.shiftKey })
      }
      onPointerOut={() => setDraftCursor(null)}
      position={[0, 0.004, 0]}
      rotation={[-Math.PI / 2, 0, 0]}
    >
      <planeGeometry args={[300, 300]} />
      <meshBasicMaterial depthWrite={false} opacity={0} transparent />
    </mesh>
  )
}

function VertexEditor({ scene }: { scene: SceneDocument }) {
  const selectedRoomId = useSceneStore((state) => state.selectedRoomId)
  const vertexDrag = useSceneStore((state) => state.vertexDrag)
  const startVertexDrag = useSceneStore((state) => state.startVertexDrag)
  const tool = useSceneStore((state) => state.tool)
  const room = scene.rooms.find((candidate) => candidate.id === selectedRoomId)
  if (!room || tool !== 'select') return null

  const polygon = (room.polygon as Vec2[]).map((point, index) =>
    vertexDrag && vertexDrag.roomId === room.id && vertexDrag.vertexIndex === index
      ? vertexDrag.position
      : point,
  )
  return (
    <group>
      {vertexDrag && vertexDrag.roomId === room.id ? (
        <Line
          color="#f3c969"
          lineWidth={2}
          points={[...polygon, polygon[0] ?? [0, 0]].map(([x, z]): [number, number, number] => [
            x,
            0.045,
            z,
          ])}
        />
      ) : null}
      {room.polygon.map((point, index) => {
        const [x, z] = point as Vec2
        const active =
          vertexDrag && vertexDrag.roomId === room.id && vertexDrag.vertexIndex === index
        return (
          <mesh
            key={`${room.id}-vertex-${index}`}
            onPointerDown={(event) => {
              if (event.button !== 0) return
              event.stopPropagation()
              startVertexDrag(room.id, index, [x, z])
            }}
            onPointerOut={() => {
              document.body.style.cursor = ''
            }}
            onPointerOver={(event) => {
              event.stopPropagation()
              document.body.style.cursor = 'move'
            }}
            position={[x, 0.24, z]}
            renderOrder={10}
          >
            <sphereGeometry args={[active ? 0.16 : 0.12, 18, 18]} />
            <meshBasicMaterial color={active ? '#f3c969' : '#dbe6ff'} depthTest={false} />
          </mesh>
        )
      })}
    </group>
  )
}

function ProxyBoxes({ count, bounds }: { count: number; bounds: Bounds }) {
  const boxes = useMemo(() => {
    const columns = Math.ceil(Math.sqrt(count))
    const spacing = 0.65
    const startX = bounds.center[0] - (columns * spacing) / 2
    const startZ = bounds.center[1] - (columns * spacing) / 2
    return Array.from({ length: count }, (_, index) => {
      const column = index % columns
      const row = Math.floor(index / columns)
      return {
        id: `proxy-${index}`,
        position: [startX + column * spacing, 0.25, startZ + row * spacing] as [
          number,
          number,
          number,
        ],
      }
    })
  }, [bounds.center, count])

  return (
    <group>
      {boxes.map((box) => (
        <mesh key={box.id} position={box.position}>
          <boxGeometry args={[0.35, 0.5, 0.35]} />
          <meshStandardMaterial color="#5b8cff" opacity={0.5} transparent />
        </mesh>
      ))}
    </group>
  )
}

function FpsMeter({ onFps }: { onFps: (fps: number) => void }) {
  const frames = useRef(0)
  const last = useRef(0)
  useFrame(() => {
    const now = performance.now()
    if (last.current === 0) last.current = now
    frames.current += 1
    const elapsed = now - last.current
    if (elapsed >= 500) {
      onFps(Math.round((frames.current * 1000) / elapsed))
      frames.current = 0
      last.current = now
    }
  })
  return null
}

function SceneInteractions() {
  const camera = useThree((state) => state.camera)
  const gl = useThree((state) => state.gl)

  useEffect(() => {
    const element = gl.domElement
    const raycaster = new THREE.Raycaster()
    const plane = new THREE.Plane(new THREE.Vector3(0, 1, 0), 0)
    const point = new THREE.Vector3()

    const ground = (clientX: number, clientY: number): Vec2 | null => {
      const rect = element.getBoundingClientRect()
      if (rect.width <= 0 || rect.height <= 0) return null
      const ndc = new THREE.Vector2(
        ((clientX - rect.left) / rect.width) * 2 - 1,
        -((clientY - rect.top) / rect.height) * 2 + 1,
      )
      raycaster.setFromCamera(ndc, camera)
      if (!raycaster.ray.intersectPlane(plane, point)) return null
      return [point.x, point.z]
    }

    const onPointerMove = (event: PointerEvent) => {
      const state = useSceneStore.getState()
      if (state.viewMode === 'walk') return
      if (state.drag) {
        const groundPoint = ground(event.clientX, event.clientY)
        if (groundPoint) {
          state.updateItemDrag([
            groundPoint[0] + state.drag.offset[0],
            groundPoint[1] + state.drag.offset[1],
          ])
        }
      } else if (state.vertexDrag) {
        const groundPoint = ground(event.clientX, event.clientY)
        if (groundPoint) state.updateVertexDrag(groundPoint, { free: event.shiftKey })
      }
    }

    const onPointerUp = () => {
      const state = useSceneStore.getState()
      if (state.drag) void state.endItemDrag()
      else if (state.vertexDrag) void state.endVertexDrag()
    }

    const onDragOver = (event: DragEvent) => {
      if (useSceneStore.getState().viewMode === 'walk') return
      const kindId = getCatalogDragKind()
      if (!kindId) return
      event.preventDefault()
      if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy'
      const groundPoint = ground(event.clientX, event.clientY)
      if (groundPoint) {
        useSceneStore.getState().setCatalogGhost(kindId, groundPoint, 0)
      }
    }

    const onDrop = (event: DragEvent) => {
      event.preventDefault()
      const kindId = getCatalogDragKind()
      const groundPoint = ground(event.clientX, event.clientY)
      setCatalogDragKind(null)
      const state = useSceneStore.getState()
      state.clearCatalogGhost()
      if (kindId && groundPoint) void state.placeCatalogItem(kindId, groundPoint, 0)
    }

    const onDragLeave = (event: DragEvent) => {
      if (event.relatedTarget === null) useSceneStore.getState().clearCatalogGhost()
    }

    element.addEventListener('pointermove', onPointerMove)
    window.addEventListener('pointerup', onPointerUp)
    element.addEventListener('dragover', onDragOver)
    element.addEventListener('drop', onDrop)
    element.addEventListener('dragleave', onDragLeave)
    return () => {
      element.removeEventListener('pointermove', onPointerMove)
      window.removeEventListener('pointerup', onPointerUp)
      element.removeEventListener('dragover', onDragOver)
      element.removeEventListener('drop', onDrop)
      element.removeEventListener('dragleave', onDragLeave)
    }
  }, [camera, gl])

  return null
}

function ViewControls({
  bounds,
  target,
}: {
  bounds: Bounds
  target?: [number, number, number] | null
}) {
  const interacting = useSceneStore((state) => Boolean(state.drag || state.vertexDrag))
  return (
    <OrbitControls
      enableDamping
      enabled={!interacting}
      makeDefault
      maxDistance={80}
      maxPolarAngle={Math.PI / 2 - 0.03}
      minDistance={2}
      target={target ?? [bounds.center[0], 0, bounds.center[1]]}
    />
  )
}

function TopControls({
  bounds,
  target,
}: {
  bounds: Bounds
  target?: [number, number, number] | null
}) {
  const interacting = useSceneStore((state) => Boolean(state.drag || state.vertexDrag))
  return (
    <OrbitControls
      enableDamping
      enableRotate={false}
      enabled={!interacting}
      makeDefault
      maxZoom={200}
      minZoom={10}
      target={target ?? [bounds.center[0], 0, bounds.center[1]]}
    />
  )
}

function SceneContents({
  scene,
  catalog,
  viewMode,
  selectedItemId,
  perfMode,
  cameraResetToken,
  onFps,
  captureRequest,
  pngExportRequest,
  cameraState,
  restoreCamera,
  onCameraRestored,
}: Omit<SceneCanvasProps, 'onSelectItem'> & {
  captureRequest?: CaptureRequestState | null
  pngExportRequest?: PngExportRequestState | null
  cameraState: CameraState | null
  restoreCamera: boolean
  onCameraRestored: () => void
}) {
  const bounds = useMemo(() => sceneBounds(scene), [scene])
  const topView = viewMode === 'top'
  const walkMode = viewMode === 'walk'
  const tool = useSceneStore((state) => state.tool)
  const dragState = useSceneStore((state) => state.drag)
  const ghost = useSceneStore((state) => state.ghost)
  const proposals = useSceneStore((state) => state.proposals)
  const overlay = useMemo(() => pendingOverlay(scene, proposals), [scene, proposals])

  return (
    <>
      <color attach="background" args={['#151515']} />
      <ambientLight intensity={0.72} />
      <directionalLight
        castShadow={!topView}
        intensity={2.1}
        position={[bounds.center[0] + 8, 12, bounds.center[1] + 6]}
        shadow-camera-bottom={-16}
        shadow-camera-left={-16}
        shadow-camera-right={16}
        shadow-camera-top={16}
        shadow-mapSize-height={2048}
        shadow-mapSize-width={2048}
      />
      <directionalLight
        intensity={0.55}
        position={[bounds.center[0] - 6, 8, bounds.center[1] - 8]}
      />
      <CameraFit
        bounds={bounds}
        fitToken={`${scene.id}:${cameraResetToken}`}
        onRestored={onCameraRestored}
        orthographic={topView}
        restore={restoreCamera}
        saved={cameraState}
        walkMode={walkMode}
      />
      <CameraKeeper enabled={!captureRequest && !walkMode} />
      <FpsMeter onFps={onFps} />
      <SceneInteractions />
      {walkMode ? <WalkRig catalog={catalog} scene={scene} /> : null}

      <gridHelper
        args={[60, 120, '#303030', '#202020']}
        position={[bounds.center[0], 0.002, bounds.center[1]]}
      />

      {scene.rooms.map((room) => (
        <RoomFloor key={room.id} room={room} />
      ))}
      <Walls scene={scene} topView={topView} />

      {scene.items.map((item) =>
        !captureRequest &&
        (overlay.removedItemIds.has(item.id) || overlay.updatedItemIds.has(item.id)) ? null : (
          <ItemMesh
            catalog={catalog}
            dragState={captureRequest ? null : dragState}
            item={item}
            key={item.id}
            selected={!captureRequest && !walkMode && item.id === selectedItemId}
          />
        ),
      )}

      {captureRequest
        ? null
        : [...overlay.addedItems, ...overlay.updatedItems].map((item) => (
            <PendingItemGhost catalog={catalog} item={item} key={`pending-${item.id}`} />
          ))}

      {captureRequest
        ? null
        : overlay.addedRooms.map((room) => (
            <PendingRoomGhost key={`pending-room-${room.id}`} room={room} />
          ))}

      {!captureRequest && ghost ? <GhostItem catalog={catalog} ghost={ghost} /> : null}
      {!captureRequest && !walkMode && tool === 'draw-room' ? <DrawingPlane /> : null}
      {!captureRequest && !walkMode && tool === 'draw-room' ? <DraftOverlay /> : null}
      {!captureRequest && !walkMode ? <VertexEditor scene={scene} /> : null}

      {!captureRequest && perfMode ? <ProxyBoxes bounds={bounds} count={200} /> : null}

      {captureRequest?.legend ? (
        <>
          <CaptureLabels
            legend={captureRequest.legend}
            scene={scene}
            size={Math.max(bounds.maxDim * 0.07, 0.22)}
          />
          <CaptureGrab
            maxEdge={Math.min(1536, Math.max(captureRequest.width, captureRequest.height))}
            scene={scene}
          />
        </>
      ) : null}
      {captureRequest && !captureRequest.legend ? (
        <CaptureGrab maxEdge={1536} scene={scene} />
      ) : null}
      {pngExportRequest ? <PngExportGrab maxEdge={pngExportRequest.maxEdge} /> : null}

      {captureRequest ? null : walkMode ? null : topView ? (
        <TopControls bounds={bounds} target={cameraState?.target} />
      ) : (
        <ViewControls bounds={bounds} target={cameraState?.target} />
      )}
    </>
  )
}

export function SceneCanvas({
  scene,
  catalog,
  viewMode,
  selectedItemId,
  perfMode,
  cameraResetToken,
  onSelectItem,
  onFps,
}: SceneCanvasProps) {
  const bounds = useMemo(() => sceneBounds(scene), [scene])
  const captureRequest = useSceneStore((state) => state.captureRequest)
  const pngExportRequest = useSceneStore((state) => state.pngExportRequest)
  const cameraState = useSceneStore((state) => state.cameraState)
  const restoreCamera = useSceneStore((state) => state.restoreCamera)
  const consumeCameraRestore = useSceneStore((state) => state.consumeCameraRestore)
  const effectiveView = captureRequest ? 'top' : viewMode
  const topView = effectiveView === 'top'
  const [cx, cz] = bounds.center

  return (
    <div className="viewport absolute inset-0">
      <Canvas
        camera={
          topView
            ? {
                far: 200,
                near: 0.1,
                position: [cx, bounds.maxDim * 2, cz + bounds.maxDim * 0.05],
                zoom: 50,
              }
            : {
                far: 300,
                fov: 45,
                near: 0.1,
                position: [cx + bounds.maxDim * 0.85, bounds.maxDim * 1.15, cz + bounds.maxDim],
              }
        }
        dpr={[1, 2]}
        gl={{ antialias: true, preserveDrawingBuffer: Boolean(captureRequest || pngExportRequest) }}
        key={`${scene.id}-${effectiveView}-${cameraResetToken}-${captureRequest?.requestId ?? ''}-${pngExportRequest?.requestId ?? ''}`}
        onPointerMissed={() => onSelectItem(null)}
        orthographic={topView}
        shadows={!topView}
      >
        <SceneContents
          cameraResetToken={cameraResetToken}
          cameraState={cameraState}
          captureRequest={captureRequest}
          pngExportRequest={pngExportRequest}
          catalog={catalog}
          onCameraRestored={consumeCameraRestore}
          onFps={onFps}
          perfMode={perfMode}
          restoreCamera={restoreCamera}
          scene={scene}
          selectedItemId={captureRequest ? null : selectedItemId}
          viewMode={effectiveView}
        />
      </Canvas>
    </div>
  )
}
