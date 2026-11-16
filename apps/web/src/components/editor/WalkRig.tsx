import { useFrame, useThree } from '@react-three/fiber'
import { useEffect, useMemo, useRef } from 'react'
import * as THREE from 'three'
import type { CatalogEntry, SceneDocument } from '../../contracts/generated/client/rest'
import type { Vec2 } from '../../scene/geometry'
import {
  buildWalkColliders,
  resolveWalkMove,
  WALK_RUN_SPEED,
  WALK_SPEED,
  WALKER_HEIGHT,
  walkSpawn,
} from '../../scene/walk'
import { useSceneStore } from '../../store/sceneStore'

const LOOK_SENSITIVITY = 0.0022
const MAX_PITCH = 1.45
/** Per-substep move cap so point sampling cannot tunnel through thin walls. */
const MAX_SUBSTEP = 0.2

const KEY_FORWARD = new Set(['KeyW', 'ArrowUp'])
const KEY_BACK = new Set(['KeyS', 'ArrowDown'])
const KEY_LEFT = new Set(['KeyA', 'ArrowLeft'])
const KEY_RIGHT = new Set(['KeyD', 'ArrowRight'])

interface WalkState {
  position: Vec2
  yaw: number
  pitch: number
  keys: Set<string>
}

export function WalkRig({ scene, catalog }: { scene: SceneDocument; catalog: CatalogEntry[] }) {
  const camera = useThree((state) => state.camera)
  const gl = useThree((state) => state.gl)
  const colliders = useMemo(() => buildWalkColliders(scene, catalog), [scene, catalog])
  const spawn = useMemo(() => walkSpawn(scene), [scene])
  const walk = useRef<WalkState>({
    position: spawn.position,
    yaw: spawn.yaw,
    pitch: 0,
    keys: new Set<string>(),
  })
  const collidersRef = useRef(colliders)
  collidersRef.current = colliders

  // Initialise the camera once on mount; editing continues over WS while the
  // user walks, and collider updates arrive through the useMemo above.
  useEffect(() => {
    if (!(camera instanceof THREE.PerspectiveCamera)) return
    camera.rotation.order = 'YXZ'
    const current = walk.current
    camera.position.set(current.position[0], WALKER_HEIGHT, current.position[1])
    camera.rotation.set(current.pitch, current.yaw, 0)
    camera.updateProjectionMatrix()
  }, [camera])

  useEffect(() => {
    const element = gl.domElement
    const state = walk.current
    let disposed = false

    const requestLock = () => {
      if (disposed || document.pointerLockElement === element) return
      try {
        const result = element.requestPointerLock() as unknown as Promise<void> | undefined
        if (result && typeof result.catch === 'function') result.catch(() => undefined)
      } catch {
        // Pointer lock can be rejected right after an ESC exit; ignore.
      }
    }

    const onLockChange = () => {
      useSceneStore.getState().setWalkLocked(document.pointerLockElement === element)
    }

    const onMouseMove = (event: MouseEvent) => {
      if (document.pointerLockElement !== element) return
      state.yaw -= event.movementX * LOOK_SENSITIVITY
      state.pitch = Math.max(
        -MAX_PITCH,
        Math.min(MAX_PITCH, state.pitch - event.movementY * LOOK_SENSITIVITY),
      )
    }

    const onKeyDown = (event: KeyboardEvent) => {
      state.keys.add(event.code)
    }
    const onKeyUp = (event: KeyboardEvent) => {
      state.keys.delete(event.code)
    }
    const onBlur = () => {
      state.keys.clear()
    }

    element.addEventListener('pointerdown', requestLock)
    document.addEventListener('pointerlockchange', onLockChange)
    document.addEventListener('mousemove', onMouseMove)
    window.addEventListener('keydown', onKeyDown)
    window.addEventListener('keyup', onKeyUp)
    window.addEventListener('blur', onBlur)
    return () => {
      disposed = true
      element.removeEventListener('pointerdown', requestLock)
      document.removeEventListener('pointerlockchange', onLockChange)
      document.removeEventListener('mousemove', onMouseMove)
      window.removeEventListener('keydown', onKeyDown)
      window.removeEventListener('keyup', onKeyUp)
      window.removeEventListener('blur', onBlur)
      if (document.pointerLockElement === element) document.exitPointerLock()
      useSceneStore.getState().setWalkLocked(false)
    }
  }, [gl])

  useFrame((_, frameDelta) => {
    if (!(camera instanceof THREE.PerspectiveCamera)) return
    const state = walk.current
    const forwardInput =
      ([...KEY_FORWARD].some((code) => state.keys.has(code)) ? 1 : 0) -
      ([...KEY_BACK].some((code) => state.keys.has(code)) ? 1 : 0)
    const strafeInput =
      ([...KEY_RIGHT].some((code) => state.keys.has(code)) ? 1 : 0) -
      ([...KEY_LEFT].some((code) => state.keys.has(code)) ? 1 : 0)
    const running = state.keys.has('ShiftLeft') || state.keys.has('ShiftRight')
    const speed = running ? WALK_RUN_SPEED : WALK_SPEED

    let delta: Vec2 = [0, 0]
    if (forwardInput !== 0 || strafeInput !== 0) {
      // Ground-plane basis for camera yaw: forward = (-sin, -cos), right = (cos, -sin).
      const sin = Math.sin(state.yaw)
      const cos = Math.cos(state.yaw)
      let dx = -sin * forwardInput + cos * strafeInput
      let dz = -cos * forwardInput - sin * strafeInput
      const length = Math.hypot(dx, dz)
      if (length > 1e-6) {
        dx = (dx / length) * speed
        dz = (dz / length) * speed
        delta = [dx * frameDelta, dz * frameDelta]
      }
    }

    const distance = Math.hypot(delta[0], delta[1])
    if (distance > 0) {
      const steps = Math.max(1, Math.ceil(distance / MAX_SUBSTEP))
      const stepDelta: Vec2 = [delta[0] / steps, delta[1] / steps]
      let position = state.position
      for (let index = 0; index < steps; index += 1) {
        position = resolveWalkMove(collidersRef.current, position, stepDelta)
      }
      state.position = position
    }

    camera.position.set(state.position[0], WALKER_HEIGHT, state.position[1])
    camera.rotation.set(state.pitch, state.yaw, 0)
  })

  return null
}
