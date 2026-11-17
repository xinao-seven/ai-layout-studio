import {
  ArrowRight,
  Box,
  Home,
  ImageIcon,
  Layers3,
  Loader2,
  Plus,
  RefreshCw,
  Sparkles,
  Upload,
} from 'lucide-react'
import { useRef, useState } from 'react'
import { importPhoto, type PhotoImportResult } from '../api/client'
import { useSceneStore } from '../store/sceneStore'
import { PhotoDraftModal } from './PhotoDraftModal'
import { Badge } from './ui/badge'
import { Button } from './ui/button'
import { Separator } from './ui/separator'

function BrandMark() {
  return (
    <span className="relative flex h-8 w-8 items-center justify-center overflow-hidden rounded-[10px] bg-gradient-to-br from-sky-300 via-indigo-400 to-emerald-300 shadow-[0_0_24px_rgba(92,140,255,0.35)]">
      <span className="h-3 w-3 rounded-[3px] border border-black/40 bg-white/80" />
    </span>
  )
}

export function HomeScreen() {
  const fixtures = useSceneStore((state) => state.fixtures)
  const scenes = useSceneStore((state) => state.scenes)
  const loading = useSceneStore((state) => state.loading)
  const createFromFixture = useSceneStore((state) => state.createFromFixture)
  const createBlank = useSceneStore((state) => state.createBlank)
  const openScene = useSceneStore((state) => state.openScene)
  const bootstrap = useSceneStore((state) => state.bootstrap)
  const importSceneFile = useSceneStore((state) => state.importSceneFile)
  const error = useSceneStore((state) => state.error)
  const setError = useSceneStore((state) => state.setError)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const photoInputRef = useRef<HTMLInputElement>(null)
  const [importing, setImporting] = useState(false)
  const [photoLoading, setPhotoLoading] = useState(false)
  const [photoDraft, setPhotoDraft] = useState<PhotoImportResult | null>(null)

  const handleImportFile = (file: File | undefined) => {
    if (!file) return
    setImporting(true)
    void importSceneFile(file)
      .catch(() => undefined)
      .finally(() => setImporting(false))
  }

  const handlePhotoFile = (file: File | undefined) => {
    if (!file) return
    setPhotoLoading(true)
    setError(null)
    importPhoto(file)
      .then((result) => setPhotoDraft(result))
      .catch((error: unknown) => {
        setError({
          code:
            error instanceof Error && 'code' in error
              ? String((error as { code: string }).code)
              : 'photo_failed',
          message: error instanceof Error ? error.message : '照片解析失败。',
          suggest:
            error instanceof Error && 'suggest' in error
              ? ((error as { suggest?: string }).suggest ?? null)
              : null,
        })
      })
      .finally(() => setPhotoLoading(false))
  }

  return (
    <div className="subtle-scrollbar h-full overflow-y-auto bg-background">
      <div className="pointer-events-none fixed inset-0 bg-[radial-gradient(circle_at_50%_-10%,rgba(90,120,200,0.18),transparent_60%)]" />

      <header className="sticky top-0 z-20 border-border/50 border-b bg-background/80 backdrop-blur-xl">
        <div className="mx-auto flex h-14 max-w-6xl items-center justify-between px-6">
          <div className="flex items-center gap-2.5">
            <BrandMark />
            <div className="leading-tight">
              <p className="font-medium text-sm tracking-wide">AI Layout Studio</p>
              <p className="text-[11px] text-muted-foreground">对话式室内布局工作台</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Badge tone="accent">M6 · 参数化资产</Badge>
            <input
              accept="application/json,.json"
              className="hidden"
              onChange={(event) => {
                handleImportFile(event.target.files?.[0])
                event.target.value = ''
              }}
              ref={fileInputRef}
              type="file"
            />
            <input
              accept="image/jpeg,image/png"
              className="hidden"
              onChange={(event) => {
                handlePhotoFile(event.target.files?.[0])
                event.target.value = ''
              }}
              ref={photoInputRef}
              type="file"
            />
            <Button
              disabled={photoLoading}
              onClick={() => photoInputRef.current?.click()}
              size="sm"
              variant="outline"
            >
              {photoLoading ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <ImageIcon className="h-3.5 w-3.5" />
              )}
              户型图照片
            </Button>
            <Button
              disabled={importing}
              onClick={() => fileInputRef.current?.click()}
              size="sm"
              variant="outline"
            >
              {importing ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Upload className="h-3.5 w-3.5" />
              )}
              导入方案
            </Button>
            <Button onClick={() => void createBlank()} size="sm" variant="outline">
              <Plus className="h-3.5 w-3.5" />
              空白场景
            </Button>
          </div>
        </div>
      </header>

      <main className="relative mx-auto max-w-6xl px-6 pb-16">
        {error ? (
          <div className="mt-4 flex items-start justify-between gap-3 rounded-xl border border-red-400/30 bg-red-500/10 px-4 py-3 text-sm text-red-200">
            <span>
              <span className="font-medium">{error.message}</span>
              {error.suggest ? (
                <span className="block text-xs text-red-200/80">{error.suggest}</span>
              ) : null}
            </span>
            <button
              className="shrink-0 text-xs text-red-200/70 hover:text-red-100"
              onClick={() => setError(null)}
              type="button"
            >
              关闭
            </button>
          </div>
        ) : null}
        <section className="pt-14 pb-10">
          <Badge className="mb-5" tone="neutral">
            <Sparkles className="h-3 w-3" />
            对话式 · 约束求解 · 人机协同
          </Badge>
          <h1 className="max-w-3xl font-semibold text-4xl leading-[1.08] tracking-[-0.03em] sm:text-5xl">
            用一句话，布置你的家。
          </h1>
          <p className="mt-4 max-w-2xl text-[15px] text-muted-foreground leading-7">
            选择一个内置户型进入三维工作台：从 60 件目录拖放家具、绘制房间、 放置门窗，或用一句话让
            AI 生成幽灵提案，确认后落库。
          </p>
          <div className="mt-7 flex flex-wrap items-center gap-3">
            <Button
              disabled={loading || fixtures.length === 0}
              onClick={() => {
                const first = fixtures[0]
                if (first) void createFromFixture(first.id)
              }}
              size="lg"
            >
              {loading ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <ArrowRight className="h-4 w-4" />
              )}
              打开示例户型
            </Button>
            <Button onClick={() => void bootstrap()} size="lg" variant="outline">
              <RefreshCw className="h-4 w-4" />
              刷新
            </Button>
          </div>
        </section>

        <Separator />

        <section className="py-10">
          <div className="mb-5 flex items-end justify-between">
            <div>
              <h2 className="font-medium text-lg">内置户型</h2>
              <p className="mt-1 text-muted-foreground text-sm">
                4 个典型户型，含门窗与家具；目录资产缺失时自动退回线框盒。
              </p>
            </div>
            <Badge tone="neutral">{fixtures.length} 个</Badge>
          </div>

          <div className="grid gap-4 md:grid-cols-3">
            {fixtures.map((fixture) => (
              <button
                className="group relative overflow-hidden rounded-2xl border border-border/80 bg-card p-5 text-left transition-all duration-200 hover:-translate-y-0.5 hover:border-white/20 hover:bg-accent/60"
                key={fixture.id}
                onClick={() => void createFromFixture(fixture.id)}
                type="button"
              >
                <div className="mb-5 flex items-start justify-between">
                  <div className="relative flex h-24 w-full items-center justify-center overflow-hidden rounded-xl border border-white/5 bg-[linear-gradient(135deg,rgba(255,255,255,0.06),rgba(255,255,255,0.01))]">
                    <div className="absolute inset-0 opacity-40 [background-image:linear-gradient(rgba(255,255,255,0.12)_1px,transparent_1px),linear-gradient(90deg,rgba(255,255,255,0.12)_1px,transparent_1px)] [background-size:16px_16px]" />
                    <div className="relative flex h-14 w-20 items-center justify-center rounded-md border border-white/25 bg-white/5">
                      <Home className="h-5 w-5 text-white/70" />
                    </div>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <h3 className="font-medium text-[15px]">{fixture.name}</h3>
                  <ArrowRight className="h-3.5 w-3.5 -translate-x-1 text-muted-foreground opacity-0 transition-all group-hover:translate-x-0 group-hover:opacity-100" />
                </div>
                <p className="mt-1.5 line-clamp-2 text-muted-foreground text-xs leading-5">
                  {fixture.description}
                </p>
                <div className="mt-4 flex items-center gap-3 text-[11px] text-muted-foreground">
                  <span className="flex items-center gap-1">
                    <Layers3 className="h-3 w-3" />
                    {fixture.roomCount} 个房间
                  </span>
                  <span>{fixture.areaM2.toFixed(1)}㎡</span>
                </div>
              </button>
            ))}
          </div>
        </section>

        <section className="py-4">
          <div className="mb-4 flex items-center justify-between">
            <div>
              <h2 className="font-medium text-lg">最近场景</h2>
              <p className="mt-1 text-muted-foreground text-sm">场景状态与版本历史保存在服务端。</p>
            </div>
            <Badge tone="neutral">{scenes.length} 个</Badge>
          </div>

          <div className="overflow-hidden rounded-2xl border border-border/80 bg-card">
            {scenes.length === 0 ? (
              <div className="flex flex-col items-center justify-center gap-2 px-6 py-12 text-center">
                <Box className="h-7 w-7 text-muted-foreground" />
                <p className="font-medium text-sm">还没有场景</p>
                <p className="text-muted-foreground text-xs">
                  从上方选择一个户型，或创建一个空白场景。
                </p>
              </div>
            ) : (
              scenes.map((scene) => (
                <button
                  className="flex w-full items-center gap-4 border-border/50 border-b px-5 py-3.5 text-left transition-colors last:border-b-0 hover:bg-accent/60"
                  key={scene.id}
                  onClick={() => void openScene(scene.id)}
                  type="button"
                >
                  <span className="flex h-9 w-9 items-center justify-center rounded-lg border border-white/10 bg-white/5">
                    <Home className="h-4 w-4 text-muted-foreground" />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-medium text-sm">{scene.name}</span>
                    <span className="mt-0.5 block text-[11px] text-muted-foreground">
                      {scene.roomCount} 个房间 · {scene.areaM2.toFixed(1)}㎡ · v{scene.version}
                    </span>
                  </span>
                  <Badge tone="neutral">
                    {new Date(scene.updatedAt).toLocaleDateString('zh-CN')}
                  </Badge>
                  <ArrowRight className="h-4 w-4 text-muted-foreground" />
                </button>
              ))
            )}
          </div>
        </section>

        {loading ? (
          <div className="fixed right-5 bottom-5 flex items-center gap-2 rounded-full border border-white/10 bg-black/70 px-3 py-2 text-xs text-white shadow-xl backdrop-blur">
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
            正在加载…
          </div>
        ) : null}
      </main>
      {photoDraft ? (
        <PhotoDraftModal draft={photoDraft} onClose={() => setPhotoDraft(null)} />
      ) : null}
    </div>
  )
}
