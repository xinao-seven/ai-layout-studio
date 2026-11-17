# ADR-0012: Agent dock, streaming markdown and reasoning levels

- Status: Accepted
- Date: 2026-11-17
- References: ADR-0005 (frontend visual language), PRD §6.2/§6.7
- Deciders: project owner

## Context

The agent was a tab inside the fixed 320 px scene sidebar. Assistant text was
rendered as `whitespace-pre-wrap` plain text at 11 px, tool calls were a flat
monospace log, and every WebSocket delta copied the message array and
re-rendered the whole list. The protocol already streamed token deltas
(`assistant.delta`), but the UI could not show markdown, code, a caret or a
thinking block, and the panel was too narrow for any of it.

Reasoning was absent end to end: the OpenAI adapter ignored
`delta.reasoning_content`, the turn contract had no reasoning field, and there
was no way to ask a provider for a specific effort.

## Decision

1. **Layout**: the agent moves out of the sidebar into a right-hand dock with
   its own drag splitter (`role="separator"`, keyboard arrows, double-click
   reset). Width is clamped to 360–720 px and to leave at least 420 px of
   canvas; below 1180 px viewport it floats over the scene instead of squeezing
   it. Width/open state/level persist in `localStorage` (`ai-layout.ui`), apart
   from the undo-tracked scene store. The left rail keeps only scene tabs; the
   TopBar pill and the PointContext button open the dock.
2. **Rendering**: `marked` + `highlight.js` + `DOMPurify`, with code-block copy
   via one delegated listener. Assistant messages are full-width editorial text
   (14 px/1.75, no bubbles); user messages are right-aligned. Thinking and tool
   calls are `<details>` blocks with a coloured 3 px left rule. The streaming
   caret sits outside the markdown output.
3. **Streaming performance**: deltas are coalesced per animation frame on the
   client and into ≥24-character/50 ms frames on the server (`_DeltaCoalescer`),
   so a token-per-event provider does not cause quadratic markdown parsing or
   one SQLite transaction per token. Non-delta events flush the buffer first.
4. **Reasoning**: `thinkingLevel` is per turn (`off|low|medium|high`) and never
   stored on the adapter, because scenes can run turns concurrently. A provider
   profile maps it to `reasoning_effort`, `thinking.budget_tokens`,
   `thinking.type` or `enable_thinking`; `LLM_REASONING_STYLE` overrides
   auto-detection. `GET /api/agent/capabilities` tells the UI what is actually
   supported (including `granular=false` for on/off-only endpoints), and the
   adapter retries once without the parameter if the provider rejects it.
   Deltas flow as `assistant.reasoning_delta`, the final text rides on
   `assistant.message.reasoning`, and `TurnMessage.reasoning` persists it for
   reconnect history.

## Consequences

- The scene canvas and the conversation can be balanced by dragging; the
  default 480 px dock shows markdown, tables and code without horizontal
  scrolling.
- Provider differences stay server-side; the client renders whatever
  reasoning arrives and hides the selector when the model has no channel.
- `assistant.message` remains the authoritative replacement for a model
  iteration, so reconnects and REST history never depend on delta replay.
- Removing the AI tab from the left rail changes one entry point; the TopBar
  toggle and the PointContext shortcut keep discovery intact.