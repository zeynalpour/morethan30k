## Summary

<!-- 1–3 sentences: what this PR does and why. -->

## Linked issue

Closes #N <!-- or "Part of #N" / "Relates to #N". PRs without an issue get sent back. -->

## Phase / scope

<!-- Which ROADMAP phase (and sub-phase) this belongs to; "none" for docs. -->

## What changed

<!-- Key changes by area (engine, api, dashboard, schema, CI…). Note any
migration, new table, or lockfile change. -->

## How it was tested

- [ ] `uv run ruff check . && uv run ruff format --check .` green
- [ ] `uv run pytest` green locally (integration suite not skipped)
- [ ] New/changed behavior covered by tests (list them)
- [ ] Migrations: `uv run alembic upgrade head` applies cleanly from scratch

## Architecture check

<!-- For anything touching the engine, schema, or security surface:
which ADR (docs/architecture/05-evolution-map.md) does this follow or add? -->

- [ ] No per-bot Python introduced (north star)
- [ ] Follows the data model (docs/architecture/01-data-model.md) for new tables

## Screenshots / demo

<!-- For dashboard or bot-facing changes: before/after on tme-test. -->
