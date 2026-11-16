# ADR-0002: Three-layer tool paradigm (contract / operation / executor)

- Status: Accepted
- Date: 2026-11-16
- PRD: D2, §6.1, §6.3
- Deciders: project owner

## Context

Agent tools must be callable from the chat loop today and from other hosts later
(e.g. a minimal MCP server), while remaining testable without an LLM or a
browser. Validation and refusal behaviour also has to be identical for manual
edits and agent writes.

## Decision

Every tool has three separable layers:

1. **Contract** — a Pydantic input model (`extra="forbid"`) that generates the
   JSON Schema registered with the LLM and is reused for L0 validation.
2. **Operation** — a pure Python function performing semantic checks, geometry
   validation, solving and patch construction, returning structured refusals.
3. **Executor** — host-specific glue (the chat `ToolRegistry` now; optionally
   MCP later) that handles proposal overlays, events and persistence.

## Consequences

- Tools can be unit-tested without FastAPI, an LLM or a database.
- Chat and future hosts share one schema and one implementation.
- The contract layer is the only editable schema source; no hand-written second
  JSON Schema (see ADR-0004).
- Executors must not contain geometry rules; putting them there would break
  parity between manual edits and agent writes.