## Summary

<!-- What does this PR change and why? Link the work package (e.g. M1-03). -->

## Checklist

- [ ] Pydantic contracts updated; `pnpm contracts:gen` ran; generated artifacts committed.
- [ ] New/updated unit tests for geometry, operations, validation or protocol changes.
- [ ] `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy app`, `uv run pytest -q` green.
- [ ] `pnpm typecheck`, `pnpm lint`, `pnpm test`, `pnpm build` green.
- [ ] No secrets, absolute local paths or debug logging; assets under the allowlist.
- [ ] ADR updated for architectural decisions; README/API docs updated.
- [ ] UI changes include a screenshot/GIF; performance-sensitive changes include measurements.