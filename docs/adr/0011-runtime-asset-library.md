# ADR-0011: Runtime parametric asset library

- Status: Accepted
- Date: 2026-11-17
- PRD: §5.3 (catalog), §6.1 (tools)
- Deciders: project owner

## Context

The shipped catalog has 60 curated CC0 entries. That is a hard ceiling on what
the agent can place: a request such as "a 2.4 m wide floor-to-ceiling bookshelf"
has no catalog answer, so the agent can only refuse. Generating assets with an
external text-to-3D service (T3) was considered and rejected for now: it adds an
async job system, mesh cleanup, external cost and latency for a capability the
deterministic core can cover for box-shaped furniture.

## Decision

### 1. Generated assets are first-class catalog entries

The agent gains one write tool, `model_item`, whose input is a *spec* (family +
dimensions), not a `kindId`. The server compiles the spec to a GLB, registers a
catalog entry, and then runs the existing semantic placement and L0–L3
validation unchanged. Users confirm generated items through the same
proposal/ghost flow as catalog items.

### 2. The library is a directory, not a table

Assets live under `<dataDir>/assets/{tmp,models}` with `manifest.json` as the
source of truth. No Alembic migration, and no second copy of catalog metadata in
SQLite: a scene already stores only `kindId`, so the manifest can evolve
independently of the scene schema.

The library is deliberately **not** `fixtures/catalog.json`:

- the fixture catalog is capped at 30–60 entries by the build validator;
- `assets/generated/assets-index.json` is a build artifact with SHA-256 checks,
  so runtime writes would leave it permanently stale;
- `POST /api/catalog/reload` re-validates live scenes, so a reload that omits a
  generated kind would break editing.

### 3. Two sources, one view

`CatalogService` merges the fixture catalog with the library at read time. Every
consumer (scene validation, `search_catalog`, the solver, the agent system
prompt) sees one catalog. Built-in kinds always win a `kindId` collision, and
library ids are namespaced `lib_<family>_<specHash8>`.

### 4. T1 (rescaling) is a degenerate T2 family

Rather than adding a render-time `scale` factor to catalog variants — which
would break the single invariant "declared `sizeM` equals the GLB AABB" — scaling
is expressed as a `scale` family whose output is a real GLB. One code path, one
invariant, no renderer change.

### 5. Determinism is the identity

The spec hash is both the cache key and the asset id. The same spec always
compiles to the same bytes, so generation is idempotent and testable without a
renderer.

## Consequences

- The agent can satisfy dimension-exact requests without a new planning model.
- Assets accumulate as a second memory layer: `search_catalog(source="library")`
  finds previously generated furniture, and the manifest keeps the generator
  params so a spec can be reused or revised later.
- The prompt grows with the library, so the system prompt lists library entries
  separately and caps them; full lookup stays behind `search_catalog`.
- Promoted assets must never be garbage collected: a scene references them by
  `kindId`, so deleting the file would make that scene fail validation on load.
  The same is true for an *unpromoted* asset that a user added manually from the
  catalog UI before confirming its proposal, so `gc_tmp` also takes the set of
  kind ids referenced by persisted scenes and skips those.
- Hosts must learn about a new kind *before* the proposal that uses it; hence
  `catalog.updated` is emitted first on the same state channel. On confirmation
  the promotion (and its `catalog.updated`) happens before `scene.patch`, so the
  first render of the item never uses a URL that has just moved.
- Promotion moves files and rewrites the entry URLs in one manifest save, and
  load reconciles the record against what is actually on disk, so a crash in the
  middle cannot leave a record pointing at a namespace whose file is gone.
- Only the `models`, `thumbnails` and `tmp` subdirectories are served; the
  manifest is internal state.
- Visual quality of parametric output is modest by design. Materials are a small
  fixed palette; there is no texturing. T3 can be added later behind the same
  tool contract, changing only the operation layer.

## Alternatives considered

- **DB table for generated assets** — rejected: the scene schema needs no
  change, and a directory keeps files and metadata together.
- **Putting generated entries into `fixtures/catalog.json`** — rejected for the
  three reasons above.
- **Variant-level scale factor** — rejected: it introduces a second notion of
  size and forces validator and renderer changes.
- **External text-to-3D (T3)** — deferred, not rejected: it needs an async job
  system and a cleanup pipeline that the current turn budget and tool model do
  not support.