# TME — Sub-Phases (Working Plan)

A granular, living work plan that breaks each roadmap phase
([ROADMAP.md](ROADMAP.md)) into **shippable sub-phases** with checklists,
context, and acceptance criteria. Statuses are updated as we work; this is the
file we keep aligned with our goals.

## Legend

- `[ ]` — not started
- `[x]` — done
- `→` — next focus

---

## Phase 0 — Foundation & tech-debt cleanup

### S0.1 — Remove the legacy raw-dict `managed_bot` path

**Context.** `src/tme/main.py` still intercepts `"managed_bot" in data` and
calls `managed_bots.handle_managed_bot(...)` directly, returning **before**
`main_dp` ever sees the update. The native typed
`@main_router.managed_bot()` handler therefore never runs in production.
`services/managed_bots.py` also carries a hand-rolled `GetManagedBotToken`
that duplicates the native `aiogram.methods.GetManagedBotToken`.

**Checklist**

- [x] `src/tme/main.py`: remove the `"managed_bot" in data` interception so all
      controller updates flow through `main_dp.feed_update`; refresh stale
      docstring.
- [x] `src/tme/services/managed_bots.py`: delete `handle_managed_bot` and the
      custom `GetManagedBotToken`; keep `provision_managed_bot` /
      `register_webhook`; rewrite the "API caveat" docstring; drop unused
      imports.
- [x] `src/tme/routers/main_bot.py`: refresh module docstring (typed `Update`
      now carries `managed_bot`).
- [x] `tests/test_main_bot.py`: replace raw-path test with a typed-handler test
      (mock `GetManagedBotToken` + `provision_managed_bot`).
- [x] `tests/test_webhook.py`: route a real `Update` payload through the
      dispatcher; update the error-200 test so it no longer patches
      `handle_managed_bot`.

**Acceptance criteria**

- No `"managed_bot"` special-case remains in `src/tme/main.py`.
- `tme.services.managed_bots` exposes only provisioning/webhook helpers.
- `uv run pytest` and `uv run ruff check --fix .` are green.

### S0.2 — `BotType` enum + per-type config union

**Context.** One `Bot` table, typed configs: `generic | hello | echo | bridge |
ai_gateway | …`. Foundational for every new bot kind.

**Checklist**

- [ ] Add `bot_type` column / enum to the `Bot` model + migration.
- [ ] Define the per-type config union in `schemas/bot_config.py`.
- [ ] Wire `BotType` into provisioning + dynamic router dispatch.

**Acceptance criteria**

- A provisioned bot carries a type; the flow engine dispatches on it.
- Alembic migration is generated and applied.

### S0.3 — Secret Vault (tokens + API keys at rest)

**Context.** Encrypt bot tokens & API keys at rest (`pgcrypto` or app-level
envelope encryption). AI gateway bots need key storage immediately.

**Checklist**

- [ ] Choose and implement the encryption layer.
- [ ] Migrate `bots.token` / future key columns to encrypted storage.
- [ ] Decrypt lazily in the cache/provisioning path; never in logs.

**Acceptance criteria**

- Tokens/keys are not plaintext in the DB.
- Read/write paths handle legacy plaintext rows once.

### S0.4 — Sync ROADMAP.md statuses as sub-phases land

**Checklist**

- [ ] ROADMAP Phase 0 items flip 🟡/⚪ → 🟢 as S0.x completes.
- [ ] SUB-PHASES stays the canonical checklist for the current phase.

---

## Next milestones (brief)

- **Phase 1 — Multilanguage platform** — per-bot `translations`, user language
  preference, `I18nMiddleware`, main bot i18n.
- **Phase 2 — Starter bots & template library** — Hello World, Echo, Feedback,
  Quiz; templates seed configs from a registry.

Full checklists for these phases are written here when we start them.

---

## Current focus

**→ S0.2** (`BotType` enum + per-type config union)
