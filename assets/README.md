# Assets

- `models/` — 60 normalized GLB furniture models (metric, Y-up, bottom-center
  pivot, front `+Z`).
- `thumbnails/` — 256×256 PNG thumbnails on a uniform dark background.
- `source/catalog-manifest.json` — curated Kenney source-model mapping, target
  sizes, categories, clearances and rules.
- `generated/assets-index.json` — measured sizes, triangle counts, byte sizes
  and SHA-256 for every committed model (used by the catalog validator).

## Rebuild

```bash
# Download the CC0 Kenney Furniture Kit (referer is required for the ZIP).
curl -L -A "Mozilla/5.0" -e "https://kenney.nl/assets/furniture-kit" \
  -o furniture-kit.zip \
  "https://kenney.nl/media/pages/assets/furniture-kit/440e0608a4-1677580847/kenney_furniture-kit.zip"
unzip furniture-kit.zip -d kenney

# Normalize selected models, crop thumbnails, write fixtures/catalog.json.
uv run --directory apps/server python ../../scripts/build_catalog_assets.py \
  --source kenney

# Validate committed artifacts without the source archive.
uv run --directory apps/server python ../../scripts/build_catalog_assets.py --check
```

The pipeline scales each source model uniformly to a realistic target
dimension, bakes the scale and bottom-center translation into a wrapper GLB
node, and validates that the resulting AABB matches the catalog `sizeM`
(`[width, depth, height]`) within 3 mm. Missing model files at runtime fall back
to a wireframe box in the editor.

Asset budgets: ≤1 MiB and ≤15k triangles per model. See `CREDITS.md` for
provenance and licensing.
