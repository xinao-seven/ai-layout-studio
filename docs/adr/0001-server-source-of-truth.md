# ADR-0001: Server owns the scene as the single source of truth

- Status: Accepted
- Date: 2026-11-16
- PRD: D1, §4.2, §5.5, §6.7
- Deciders: project owner

## Context

The product combines a browser 3D editor, a server-side AI agent, deterministic
validation/solving, and headless evaluation. Scene state must be consistent when
the agent proposes changes, when the user confirms them, and when a turn is
replayed or evaluated. Keys and geometry code must stay off the client.

## Decision

The server (FastAPI + SQLite) is the authoritative owner of the scene document,
its version history, proposals and turn state. The browser renders and collects
input; it keeps only a local mirror (Zustand/Zundo) and treats server patches as
authoritative. All writes are validated server-side before becoming a version.

## Consequences

- Undo/redo is a server version operation that produces a new version; Zundo is
  a UI mirror only.
- Reconnect can always recover from a snapshot + version, independent of client
  history.
- The agent, evaluation runners and future hosts (MCP) share the same operations.
- The API must be locally hosted or protected; the initial product has no auth.
- Client-side geometry is a fast pre-check only; conflicts resolve in favour of
  the server (PRD D16).