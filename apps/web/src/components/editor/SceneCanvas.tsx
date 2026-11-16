import { Edges, OrbitControls } from '@react-three/drei'
import { Canvas, useFrame, useThree } from '@react-three/fiber'
import { useEffect, useMemo, useRef } from 'react'
import * as THREE from 'three'
import type { CatalogEntry, Item, Room, SceneDocument } from '../../contracts/generated/client/rest'
import type { Vec2 } from '../../scene/geometry'
import { buildWalls } from '../../scene/walls'

const WALL_COLOR = '#d9d5cc'
const DOOR_COLOR = '#8a6337'
const WINDOW_COLOR = '#8ec5e8'
const SELECTION_COLOR = '#7aa2ff'
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
  viewMode: '3d' | 'top'
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
}: {
  bounds: Bounds
  orthographic: boolean
  fitToken: string
}) {
  const camera = useThree((state) => state.camera)
  const size = useThree((state) => state.size)
  const [cx, cz] = bounds.center

  useEffect(() => {
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
  }, [bounds.maxDim, camera, cx, cz, fitToken, orthographic, size.height, size.width])

  return null
}

function RoomFloor({ room }: { room: Room }) {
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

  const color = FLOOR_COLORS[room.type ?? 'other'] ?? FLOOR_COLORS.other

  return (
    <mesh receiveShadow rotation={[-Math.PI / 2, 0, 0]}>
      <shapeGeometry args={[shape]} />
      <meshStandardMaterial color={color} metalness={0.02} roughness={0.92} />
    </mesh>
  )
}

function Walls({ scene, topView }: { scene: SceneDocument; topView: boolean }) {
  const geometry = useMemo(() => buildWalls(scene, { topView }), [scene, topView])

  return (
    <group>
      {geometry.walls.map((wall) => (
        <mesh
          castShadow={wall.part === 'wall'}
          key={wall.key}
          position={[wall.center[0], wall.y, wall.center[1]]}
          receiveShadow
          rotation={[0, wall.rotationY, 0]}
        >
          <boxGeometry args={[wall.length, wall.height, wall.thickness]} />
          <meshStandardMaterial color={WALL_COLOR} roughness={0.82} />
        </mesh>
      ))}

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

function itemVariant(
  item: Item,
  catalog: CatalogEntry[],
): { width: number; depth: number; height: number; category: string } {
  const entry = catalog.find((candidate) => candidate.kindId === item.kindId)
  const variant =
    entry?.variants.find((candidate) => candidate.id === item.variantId) ?? entry?.variants[0]
  const size = variant?.sizeM ?? [1, 1, 1]
  return {
    width: size[0],
    depth: size[1],
    height: size[2],
    category: entry?.category ?? 'other',
  }
}

function ItemMesh({
  item,
  catalog,
  selected,
  onSelect,
}: {
  item: Item
  catalog: CatalogEntry[]
  selected: boolean
  onSelect: (itemId: string | null) => void
}) {
  const { width, depth, height, category } = itemVariant(item, catalog)
  const color = CATEGORY_COLORS[category] ?? CATEGORY_COLORS.other
  const [x, y, z] = item.transform.position
  const baseY = y || 0

  return (
    // biome-ignore lint/a11y/noStaticElementInteractions: R3F group is a 3D pick target
    <group
      onClick={(event) => {
        event.stopPropagation()
        onSelect(item.id)
      }}
      onPointerOut={() => {
        document.body.style.cursor = ''
      }}
      onPointerOver={(event) => {
        event.stopPropagation()
        document.body.style.cursor = 'pointer'
      }}
      position={[x, baseY, z]}
      rotation={[0, item.transform.rotationY, 0]}
    >
      {selected ? (
        <mesh position={[0, 0.012, 0]} rotation={[-Math.PI / 2, 0, 0]}>
          <ringGeometry
            args={[Math.max(width, depth) / 2 + 0.12, Math.max(width, depth) / 2 + 0.19, 40]}
          />
          <meshBasicMaterial
            color={SELECTION_COLOR}
            opacity={0.9}
            side={THREE.DoubleSide}
            transparent
          />
        </mesh>
      ) : null}
      <mesh castShadow position={[0, height / 2, 0]} receiveShadow>
        <boxGeometry args={[width, height, depth]} />
        <meshStandardMaterial
          color={selected ? SELECTION_COLOR : color}
          emissive={selected ? SELECTION_COLOR : '#000000'}
          emissiveIntensity={selected ? 0.18 : 0}
          metalness={0.05}
          opacity={0.94}
          roughness={0.72}
          transparent
        />
        <Edges color={selected ? '#dbe6ff' : '#181818'} threshold={20} />
      </mesh>
      <mesh position={[0, 0.03, depth / 2 + 0.12]}>
        <boxGeometry args={[Math.min(width * 0.4, 0.4), 0.02, 0.12]} />
        <meshBasicMaterial color={selected ? '#dbe6ff' : '#9aa4b2'} />
      </mesh>
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

function ViewControls({ bounds }: { bounds: Bounds }) {
  return (
    <OrbitControls
      enableDamping
      makeDefault
      maxDistance={80}
      maxPolarAngle={Math.PI / 2 - 0.03}
      minDistance={2}
      target={[bounds.center[0], 0, bounds.center[1]]}
    />
  )
}

function TopControls({ bounds }: { bounds: Bounds }) {
  return (
    <OrbitControls
      enableDamping
      enableRotate={false}
      makeDefault
      maxZoom={200}
      minZoom={10}
      target={[bounds.center[0], 0, bounds.center[1]]}
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
  onSelectItem,
  onFps,
}: SceneCanvasProps) {
  const bounds = useMemo(() => sceneBounds(scene), [scene])
  const topView = viewMode === 'top'

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
        orthographic={topView}
      />
      <FpsMeter onFps={onFps} />

      <gridHelper
        args={[60, 120, '#303030', '#202020']}
        position={[bounds.center[0], 0.002, bounds.center[1]]}
      />

      {scene.rooms.map((room) => (
        <RoomFloor key={room.id} room={room} />
      ))}
      <Walls scene={scene} topView={topView} />

      {scene.items.map((item) => (
        <ItemMesh
          catalog={catalog}
          item={item}
          key={item.id}
          onSelect={onSelectItem}
          selected={item.id === selectedItemId}
        />
      ))}

      {perfMode ? <ProxyBoxes bounds={bounds} count={200} /> : null}

      {topView ? <TopControls bounds={bounds} /> : <ViewControls bounds={bounds} />}
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
  const topView = viewMode === 'top'
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
        key={`${scene.id}-${viewMode}-${cameraResetToken}`}
        onPointerMissed={() => onSelectItem(null)}
        orthographic={topView}
        shadows={!topView}
      >
        <SceneContents
          cameraResetToken={cameraResetToken}
          catalog={catalog}
          onFps={onFps}
          onSelectItem={onSelectItem}
          perfMode={perfMode}
          scene={scene}
          selectedItemId={selectedItemId}
          viewMode={viewMode}
        />
      </Canvas>
    </div>
  )
}
