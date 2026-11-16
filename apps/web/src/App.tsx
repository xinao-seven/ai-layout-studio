import { OrbitControls } from '@react-three/drei'
import { Canvas } from '@react-three/fiber'
import { useSceneUIStore } from './store/sceneStore'

function SceneContents() {
  return (
    <>
      <color attach="background" args={['#0e1116']} />
      <ambientLight intensity={0.55} />
      <directionalLight
        castShadow
        intensity={1.8}
        position={[6, 10, 4]}
        shadow-camera-bottom={-8}
        shadow-camera-left={-8}
        shadow-camera-right={8}
        shadow-camera-top={8}
        shadow-mapSize-height={1024}
        shadow-mapSize-width={1024}
      />
      <mesh receiveShadow rotation={[-Math.PI / 2, 0, 0]}>
        <planeGeometry args={[8, 6]} />
        <meshStandardMaterial color="#171c24" roughness={0.95} />
      </mesh>
      <gridHelper args={[20, 40, '#2c3442', '#1b2028']} position={[0, 0.002, 0]} />
      <OrbitControls
        enableDamping
        makeDefault
        maxPolarAngle={Math.PI / 2 - 0.02}
        target={[0, 0, 0]}
      />
    </>
  )
}

function Viewport() {
  const viewMode = useSceneUIStore((state) => state.viewMode)

  if (viewMode === 'top') {
    return (
      <Canvas
        camera={{ far: 100, near: 0.1, position: [0, 12, 0.01], zoom: 56 }}
        dpr={[1, 2]}
        key="top"
        orthographic
        shadows
      >
        <SceneContents />
      </Canvas>
    )
  }

  return (
    <Canvas
      camera={{ far: 200, fov: 45, near: 0.1, position: [7, 6, 9] }}
      dpr={[1, 2]}
      key="perspective"
      shadows
    >
      <SceneContents />
    </Canvas>
  )
}

export default function App() {
  const viewMode = useSceneUIStore((state) => state.viewMode)
  const toggleViewMode = useSceneUIStore((state) => state.toggleViewMode)

  return (
    <main className="app-shell">
      <div className="viewport">
        <Viewport />
      </div>

      <header className="hud hud-top">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true" />
          <div>
            <h1>AI Interior Layout Workbench</h1>
            <p>对话式三维室内布局工作台</p>
          </div>
        </div>
        <div className="hud-actions">
          <span className="badge">M0 · 脚手架</span>
          <button className="view-toggle" onClick={toggleViewMode} type="button">
            视图：{viewMode === '3d' ? '3D' : '俯视'}
          </button>
        </div>
      </header>

      <footer className="hud hud-bottom">
        <span>React 19 · R3F 9 · WebGL2</span>
        <span>拖拽/家具/Agent 能力将在 M1–M3 接入</span>
      </footer>
    </main>
  )
}
