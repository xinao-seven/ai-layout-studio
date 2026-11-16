# ADR-0005: Frontend visual language follows the aedifex / shadcn editor style

- Status: Accepted
- Date: 2026-11-16
- References: [TangSY/aedifex](https://github.com/TangSY/aedifex) (MIT, built on
  Pascal Editor, MIT), [shadcn/ui](https://ui.shadcn.com) (MIT),
  [lucide](https://lucide.dev) (ISC)
- PRD: §4.1, FR-03/04/09
- Deciders: project owner

## Context

The PRD originally described aedifex as an architecture reference only. The
project owner asked the editor to feel close to that product: a neutral dark
workspace, an icon rail with a resizable surface panel, floating pill toolbars,
muted borders and compact typography.

## Decision

Adopt the **visual language and interaction layout** of that editor family while
keeping the implementation independent:

- dark neutral tokens (`oklch` background `0.205`, white/10 borders, 0.625 rem
  radius), Barlow UI font, `lucide` icons;
- left 56 px icon rail + 320 px panel, floating view toolbar, compass HUD and
  bottom status chips;
- shadcn-style component primitives (`Button`, `Badge`, `Tooltip`,
  `Separator`) implemented locally with Radix + CVA; no aedifex source files are
  copied;
- our own R3F scene stack (rooms, merged wall lines, openings, furniture
  fallbacks) and our own Zustand/Zundo store.

## Consequences

- The UI matches the requested reference style without creating a license or
  provenance ambiguity; `assets/CREDITS.md` records the references.
- We own every line of application code and can evolve it with our contracts.
- shadcn/ui is a copy-in component pattern (MIT); Radix, lucide and Barlow are
  dependencies recorded in `docs/DEPENDENCIES.md`.
- If a future component is copied verbatim from aedifex, it must be listed in
  `assets/CREDITS.md` with its MIT notice and the PRD's "independent
  implementation" statement updated.