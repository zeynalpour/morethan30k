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

- [x] Add `bot_type` column / enum to the `Bot` model + migration.
- [x] Define the per-type config union in `schemas/bot_config.py`.
- [x] Wire `BotType` into provisioning + dynamic router dispatch.

**Acceptance criteria**

- A provisioned bot carries a type; the flow engine dispatches on it. ✅
- Alembic migration is generated and applied. ✅ (`0002_bot_type`)
- ✅ Landed: `0cf9f50` (enum + union) and `aa918f4` (sibling config variants);
  the controller bot's type picker ships Generic/Hello/Echo (`5c2e1f9`).

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

### S0.4 — Owner bot-settings dashboard (BotFather-style mini app)

**Context.** Every bot owner should manage their own bots from a settings UI
(BotFather-style) instead of raw JSON. A React frontend scaffold is already
in-tree (bolt-new PR: `src/App.tsx`, `BotList`, `ConfigEditor`,
`MenuButtonsEditor`, `BotHeader`, `src/lib/supabase.ts`) and must be wired to
the backend. **Decision (resolved):** management plane is the FastAPI CRUD API
(`/api/bots*`), not direct Supabase writes — single source of truth + Redis
cache invalidation; Supabase drops out of the data path.

**Checklist**

- [x] `dashboard_auth_tokens` table (migration 0003) + issue/validate service.
- [x] Owner-only access: auth binds to `users.telegram_id`; users list and
      edit only their own bots.
- [x] Settings editor: bot type, welcome message, menu buttons, fallback text,
      active modules → write `bot_configs.flow`, invalidate the cache.
- [x] Mini App delivery + auth: WebApp buttons, initData (hash-delivered,
      HMAC-validated per request), dashboard served at `/dashboard` AND the
      domain root (profile Mini App / menu button), `/dashboard` command +
      auto-registered menu.
- [x] Bot status view: webhook state + created date shown; enable/disable
      toggle (`PATCH /api/bots/{id}`) — disabling is a hard stop at the
      runtime layer (cache dropped, only ACTIVE bots resolve).
- [x] Bot type switching in the dashboard (`PATCH /api/bots/{id}` with
      `bot_type`) — resets flow to that type's defaults.
- [x] Full-stack integration tests (`tests/test_integration.py`): real
      Postgres (`tme_test` DB, Alembic-migrated) + Redis db 15 — provision →
      signed-initData API → config patch through cache → disable/enable →
      type switch. Skipped when the host infra stack is down.

**Acceptance criteria**

- A user sees only bots they own; edits go live within seconds (cache
  invalidated).
- No token or secret is ever exposed to the frontend.

### S0.5 — Sync ROADMAP.md statuses as sub-phases land

**Checklist**

- [x] ROADMAP Phase 0 items flip 🟡/⚪ → 🟢 as S0.x completes.
- [x] SUB-PHASES stays the canonical checklist for the current phase.

---

## Phase 1 — Multilanguage platform

### S1.1 — Per-bot translations: schema + runtime + dashboard editor  *(done)*

**Checklist**

- [x] `translations` schema (`schemas/bot_config.py`): per-language overrides
      (welcome, fallback, greeting, echo prefix, menu buttons) keyed by
      ISO-639-1 code; validated at write time through the existing flow union.
- [x] `tme/core/i18n.py`: `normalize_language()` (region codes → ISO-639-1)
      and `localize()` with the fallback chain **user language → English →
      base flow**, applied per field (a partial translation keeps the
      English/base fields).
- [x] Tenant router (`routers/dynamic.py`) reads `from_user.language_code` and
      renders every user-facing text through `localize()`: welcome/greeting,
      menu labels, fallback, echo prefix. No DB or middleware needed for the
      auto-detection half of S1.2.
- [x] Dashboard editor: per-language chips, per-field overrides, 📋
      Copy-from-base helper (seeds a language from the base copy).
- [x] Tests: resolver fallback chain (11 unit tests in `tests/test_i18n.py`);
      the full-stack integration test PATCHes translations through the API and
      asserts `localize()` reads them back from Redis.

**Acceptance criteria**

- A Persian user messaging a tenant bot with an `fa` translation gets Persian
  copy; users of untranslated languages get English, then the base flow.
- Translation edits go live within seconds (same Redis-cache invalidation
  path as any config edit).

### S1.2 — Per-user language preference  *(done)*

**Checklist**

- [x] `user_languages` table (migration 0004) — explicit preference keyed by
      Telegram id, covering both owners and tenant-bot users.
- [x] `services/user_language.py`: get/set with Redis read-through cache.
- [x] `/language` (+ `/lang`) command on tenant bots with an inline flag
      picker that lists ONLY the bot's actual translation languages (+
      "Auto (Telegram)" to clear the choice); refused entirely on
      single-language bots (command and callback both guarded).
- [x] `I18nMiddleware` on the tenant dispatcher: resolves stored preference →
      Telegram `language_code` and injects `language_code` into handlers;
      skips the lookup for single-language bots.
- [x] Owner toggle **single-language mode** (`single_language` in the flow):
      the bot speaks ONLY its base copy — translations and user language are
      ignored; the dashboard hides the translation editor while it's on and
      the picker refuses to switch.
- [x] Tests: `effective_language` ordering, single-language bypass, picker
      handlers, middleware (preference wins / telegram fallback / skip), and a
      real-DB+Redis round-trip in the integration suite. 91 total green.

**Acceptance criteria**

- Changing Telegram's UI language re-locates the bot only when it has that
  translation; a user can force any of the 12 picker languages per-account.
- A single-language bot never localizes, regardless of user preference.

### S1.3 — Controller-bot i18n *(done)*

The controller bot's own copy speaks the owner's language — same chain as
tenant bots (stored `/language` preference → Telegram UI `language_code` →
English). Built-in table (`src/tme/core/main_i18n.py`): add a language by
extending `MAIN_BOT_STRINGS`; the controller's `/language` picker offers
exactly the shipped languages (+ Auto reset) and writes the SAME store
tenant bots read — one pick localizes the whole platform for that owner.

**Checklist**

- [x] Built-in `en` + `fa` copy tables; `tr()` falls back to English;
      key drift between languages is a test failure.
- [x] `I18nMiddleware` on `main_dp`; the `ManagedBotUpdated` handler
      resolves the owner's language explicitly (middleware can't see inside
      that wrapper).
- [x] Every controller message localized: /start welcome, creation intro,
      token-fetch failure, type picker + captions, provision failure,
      live-confirmation, /mybots list + empty state, /dashboard intro,
      Mini App button labels, reply-keyboard labels.
- [x] Localized reply-keyboard "🤖 ربات‌های من" accepted by the `/mybots`
      filter (en + emoji + fa variants).
- [x] `/language` on the controller with picker of shipped languages + Auto.
- [x] `setMyCommands` registers per-language menus (`language_code` scope).

---

## Next milestones (brief)

- **Phase 2 — Starter bots & template library** — Hello World, Echo, Feedback,
  Quiz; templates seed configs from a registry; versioned templates + clone.
- **Phase 8 — Per-user bot settings** — extended vision beyond the S0.4 MVP:
  config history/rollback, template gallery, dashboard analytics.

Full checklists for these phases are written here when we start them.

---

## Current focus

**→ S1.1 + S1.2 + S1.3 are done** (per-bot translations, per-user `/language`
preference, single-language mode, controller copy in the owner's language)
— S0.1–S0.4 complete. **Phase 1 is done.** Next: Phase 2 (template gallery
etc. — see ROADMAP).
