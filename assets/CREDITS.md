# Asset credits

Every committed asset is listed here with author, license and source URL
(PRD §5.3, §13). The runtime catalog is `fixtures/catalog.json`; the per-model
sizes, triangle counts and hashes are in `assets/generated/assets-index.json`.

## Furniture models and thumbnails

| Asset set | Author | License | Source | Modifications |
|---|---|---|---|---|
| Kenney Furniture Kit (60 selected models + isometric thumbnails) | Kenney | CC0-1.0 (public domain) | <https://kenney.nl/assets/furniture-kit> | Uniform scale to realistic metric dimensions, baked bottom-center pivot, Y-up, front `+Z`; thumbnails padded to 256×256 on a uniform dark background |

- Original archive: `kenney_furniture-kit.zip` (not committed).
- Selected source models: `assets/source/catalog-manifest.json` records the
  60 kind→model mappings and target sizes.
- Build/validation: `scripts/build_catalog_assets.py` (rebuild) and
  `pnpm`-independent check `uv run --directory apps/server python
  ../../scripts/build_catalog_assets.py --check`.
- License text: <https://creativecommons.org/publicdomain/zero/1.0/>.
  CC0 requires no attribution; the credit is kept for provenance.

Asset budgets (checked by the build script and pytest):

- ≤1 MiB and ≤15k triangles per GLB.
- 256×256 thumbnail per kind.
- Catalog declares `frontAxis: "+Z"` and `pivot: "bottom-center"`; the validator
  re-measures every GLB AABB and fails on drift.

## UI / visual references (no source code copied)

| Reference | License | How it is used |
|---|---|---|
| [TangSY/aedifex](https://github.com/TangSY/aedifex) | MIT | Editor layout, neutral dark visual language and interaction patterns as a reference; application code is an independent implementation |
| [Pascal Editor](https://github.com/pascalorg/editor) | MIT | Upstream design lineage of aedifex; referenced for the same visual study |
| [shadcn/ui](https://ui.shadcn.com) | MIT | Component patterns (Button/Badge/Tooltip/Separator) reimplemented locally with Radix + CVA |
| [lucide](https://lucide.dev) | ISC | Icon set |
| [Barlow](https://fonts.google.com/specimen/Barlow) via @fontsource | OFL-1.1 | UI typeface |

No aedifex or Pascal Editor source files are vendored. If a component is copied
verbatim in the future, record the file, commit and MIT notice here.
