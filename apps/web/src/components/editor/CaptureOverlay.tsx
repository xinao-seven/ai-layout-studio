import { useFrame, useThree } from '@react-three/fiber'
import { useEffect, useMemo, useRef } from 'react'
import * as THREE from 'three'
import type { SceneDocument } from '../../contracts/generated/client/rest'
import { type CaptureLegend, openingPoint } from '../../scene/capture'
import { pendingOverlay } from '../../scene/proposals'
import { useSceneStore } from '../../store/sceneStore'

function CaptureLabel({
  text,
  position,
  size,
}: {
  text: string
  position: [number, number, number]
  size: number
}) {
  const texture = useMemo(() => {
    const canvas = document.createElement('canvas')
    canvas.width = 256
    canvas.height = 128
    const context = canvas.getContext('2d')
    if (context) {
      context.fillStyle = 'rgba(12, 14, 18, 0.82)'
      context.fillRect(0, 0, canvas.width, canvas.height)
      context.strokeStyle = 'rgba(255,255,255,0.8)'
      context.lineWidth = 6
      context.strokeRect(3, 3, canvas.width - 6, canvas.height - 6)
      context.fillStyle = '#ffffff'
      context.font = 'bold 64px sans-serif'
      context.textAlign = 'center'
      context.textBaseline = 'middle'
      context.fillText(text, canvas.width / 2, canvas.height / 2 + 4)
    }
    const result = new THREE.CanvasTexture(canvas)
    result.needsUpdate = true
    return result
  }, [text])
  useEffect(() => () => texture.dispose(), [texture])
  return (
    <sprite position={position} renderOrder={1000} scale={[size * 2, size, 1]}>
      <spriteMaterial depthTest={false} depthWrite={false} map={texture} transparent />
    </sprite>
  )
}

export function CaptureLabels({
  scene,
  legend,
  size,
}: {
  scene: SceneDocument
  legend: CaptureLegend
  size: number
}) {
  const proposals = useSceneStore((state) => state.proposals)
  const items = useMemo(() => {
    const overlay = pendingOverlay(scene, proposals)
    const merged = new Map<string, { position: [number, number, number] }>()
    for (const item of [...scene.items, ...overlay.addedItems, ...overlay.updatedItems]) {
      merged.set(item.id, { position: item.transform.position })
    }
    return merged
  }, [scene, proposals])
  const openings = useMemo(
    () => new Map(scene.openings.map((opening) => [opening.id, opening])),
    [scene.openings],
  )
  const rooms = useMemo(
    () => new Map(scene.rooms.map((room) => [room.id, room.polygon as [number, number][]])),
    [scene.rooms],
  )
  return (
    <group>
      {legend.items.map((entry) => {
        if (!entry.itemId) return null
        const item = items.get(entry.itemId)
        if (!item) return null
        return (
          <CaptureLabel
            key={entry.itemId}
            position={[item.position[0], 1.4, item.position[2]]}
            size={size}
            text={entry.label}
          />
        )
      })}
      {legend.openings.map((entry) => {
        if (!entry.openingId) return null
        const opening = openings.get(entry.openingId)
        if (!opening) return null
        const point = openingPoint(rooms.get(opening.roomId), opening)
        return (
          <CaptureLabel
            key={entry.openingId}
            position={[point[0], 0.5, point[1]]}
            size={size}
            text={entry.label}
          />
        )
      })}
    </group>
  )
}

function downscale(source: HTMLCanvasElement, maxEdge: number, maxBytes: number): string {
  let scale = Math.min(1, maxEdge / Math.max(source.width, source.height, 1))
  let dataUrl = ''
  for (let attempt = 0; attempt < 7; attempt += 1) {
    const canvas = document.createElement('canvas')
    canvas.width = Math.max(1, Math.round(source.width * scale))
    canvas.height = Math.max(1, Math.round(source.height * scale))
    const context = canvas.getContext('2d')
    if (!context) return ''
    context.drawImage(source, 0, 0, canvas.width, canvas.height)
    dataUrl = canvas.toDataURL('image/png')
    const base64 = dataUrl.split(',')[1] ?? ''
    if (base64.length * 0.75 <= maxBytes * 0.98) return dataUrl
    scale *= 0.8
  }
  return dataUrl
}

export function CaptureGrab({ scene, maxEdge }: { scene: SceneDocument; maxEdge: number }) {
  const request = useSceneStore((state) => state.captureRequest)
  const cancelCapture = useSceneStore((state) => state.cancelCapture)
  const gl = useThree((state) => state.gl)
  const frames = useRef(0)
  const sent = useRef(false)

  useEffect(() => {
    if (!request) return
    const timer = window.setTimeout(() => cancelCapture(request.requestId), 8000)
    return () => window.clearTimeout(timer)
  }, [cancelCapture, request])

  useFrame(() => {
    if (!request || sent.current) return
    frames.current += 1
    if (frames.current < 6) return
    sent.current = true
    try {
      const dataUrl = downscale(gl.domElement, maxEdge, request.maxBytes)
      const pngBase64 = dataUrl.split(',')[1] ?? ''
      if (!pngBase64) {
        cancelCapture(request.requestId)
        return
      }
      const legend = (request.legend ?? {
        items: [],
        openings: [],
      }) as unknown as Record<string, unknown>
      window.setTimeout(() => {
        useSceneStore.getState().completeCapture(request.requestId, pngBase64, legend)
      }, 0)
    } catch {
      cancelCapture(request.requestId)
    }
  })
  void scene
  return null
}

export function PngExportGrab({ maxEdge }: { maxEdge: number }) {
  const request = useSceneStore((state) => state.pngExportRequest)
  const cancelPngExport = useSceneStore((state) => state.cancelPngExport)
  const gl = useThree((state) => state.gl)
  const frames = useRef(0)
  const sent = useRef(false)

  useEffect(() => {
    if (!request) return
    const timer = window.setTimeout(() => cancelPngExport(request.requestId), 8000)
    return () => window.clearTimeout(timer)
  }, [cancelPngExport, request])

  useFrame(() => {
    if (!request || sent.current) return
    frames.current += 1
    if (frames.current < 6) return
    sent.current = true
    try {
      const dataUrl = downscale(gl.domElement, maxEdge, Number.POSITIVE_INFINITY)
      if (!dataUrl.includes(',')) {
        cancelPngExport(request.requestId)
        return
      }
      window.setTimeout(() => {
        useSceneStore.getState().completePngExport(request.requestId, dataUrl)
      }, 0)
    } catch {
      cancelPngExport(request.requestId)
    }
  })
  return null
}
