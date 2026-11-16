# Asset credits

No third-party assets have been added yet (M0). Every asset added later must be
listed here with name, author, license and source URL (PRD §5.3, §13).

Asset sources planned:

| Source | License | Usage |
|---|---|---|
| Kenney Furniture Kit | CC0 | main furniture line |
| Quaternius | CC0 | main furniture line |
| Poly Haven | CC0 | sparing accents |
| Sketchfab (selected glTF) | CC-BY | accents, attribution required |

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
