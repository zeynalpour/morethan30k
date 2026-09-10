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

### S0.3 — Secret Vault (tokens + API keys at rest)  *(done)*

**Context.** Encrypt bot tokens & API keys at rest (`pgcrypto` or app-level
envelope encryption). AI gateway bots need key storage immediately.

**Landed** (`734761f`): app-level envelope encryption — AES-256-GCM
per-secret DEK wrapped by `VAULT_MASTER_KEY`; `secrets` table keyed
`(kind, ref_id)`; peppered HMAC `bots.token_hash` for hot-path lookup
(no decryption when routing); provisioning vaults new tokens and sets the
hash. Works without a master key (loud warning, plaintext column stays
the source of truth) so existing stacks keep running during rollout —
set `VAULT_MASTER_KEY` + `VAULT_PEPPER` in `.env` to activate.

**Checklist**

- [x] Choose and implement the encryption layer. (AES-GCM envelope,
      `cryptography` package.)
- [x] Migrate `bots.token` / future key columns to encrypted storage.
      (`secrets` table via migration 0005; `bots.token` still populated —
      plaintext column is dropped in a follow-up once every row has a
      vault copy. AI-gateway keys will reuse `store_secret`.)
- [x] Decrypt lazily in the cache/provisioning path; never in logs.
      (Decryption only in the adapter path; `…last6` display convention
      unchanged; `last_four` on the vault row for UI.)

**Acceptance criteria**

- [x] Tokens/keys are not plaintext in the DB. (Vaulted rows are; the
      transitional `bots.token` column remains until the data migration —
      tracked below.)
- [x] Read/write paths handle legacy plaintext rows once. (No master key →
      provisioning skips vaulting with a warning; nothing breaks.)

**Follow-up (next session)** — backfill job: vault all existing
`bots.token` rows + null the plaintext column (separate migration 0006);
switch webhook resolution to `token_hash` lookup.

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

## Phase 2 — Starter bots & template library

### S2.1 — Template registry core (versioned, in-code)

**Context.** A template is **seed data for a `BotConfig`** — a named,
versioned flow snapshot (bot_type, welcome message, fallback, menu
buttons, …) held in an **in-code registry** under `src/tme/templates/`.
Instantiating a template adds **no new handler, no new `BotType`, no
per-template code**: a bot made from a template is a new `bot_configs`
row like any other (north-star: JSON in Postgres → Redis cache → one
shared engine). The registry is the single source that both the
creation-flow picker (S2.2) and the re-clone path (S2.3) read, and it
stays pure data — importable with no DB or Redis. A `templates` database
table only becomes interesting when a marketplace does; this phase does
not need one.

**Checklist**

- [ ] `src/tme/templates/` package: `TemplateSpec` (id, version, title,
      blurb, bot_type, seed flow dict) + the registry module holding the
      versioned entries.
- [ ] Launch set of five templates: **Hello World**, **Echo**, **Feedback
      collector**, **Quiz**, **Simple form**. Hello/Echo reuse the existing
      config variants; the conversational three are generic flows whose
      `steps` data the shared engine consumes (S2.2). AI gateway stays out
      — "later" per ROADMAP.
- [ ] Registry API: list (picker source), lookup by id, latest-version
      resolution — one source of truth, so picker UI and template data
      cannot drift.
- [ ] Every entry's seed flow validates against `BotConfigUnion`
      (round-trips through `parse_bot_config`); a malformed template fails
      its own test, never a live user.
- [ ] Template provenance recorded on the seeded config (`template_id` +
      `template_version`) so S2.3 knows which seed a bot came from; exact
      storage shape (flow rider vs. column) is the Architect's call.
- [ ] Tests: ids unique, versions monotonic per id, every seed parses and
      the engine can run it; provisioning from a template seeds exactly
      the template's flow.

**Acceptance criteria**

- Adding a template = adding a data entry to the registry — zero engine,
  router, or enum changes.
- The registry imports with no infrastructure (pure in-memory data);
  validation tests run without Postgres/Redis.
- No template introduces per-template runtime code of any kind.

### S2.2 — Template picker in the controller bot-creation flow

**Context.** After `ManagedBotUpdated` the controller offers a three-way
type picker (`_TYPE_CHOICES`: Generic/Hello/Echo) and provisions with
`_default_config_for(bot_type)`. Phase 2 turns that moment into the
template gallery's front door: one card per registry entry (title, blurb,
what you get) plus a "start from scratch" card that preserves today's
bare-type behaviour. Template-first creation means nobody faces a blank
page; the blank canvas stays the expert's choice.

**Checklist**

- [ ] Picker keyboard derives from the registry list (same single-source
      pattern as `_TYPE_CHOICES`); card callback data keyed by template
      id; a "start from scratch" card seeds today's generic default (the
      Hello World / Echo cards subsume the old type buttons).
- [ ] `provision_managed_bot` accepts a template id and seeds the chosen
      template's flow instead of the bare per-type default; cache priming
      mirrors exactly what was persisted; provenance written per S2.1.
- [ ] Shared conversational primitive for the Feedback/Quiz/Simple-form
      templates: a `steps` array in the generic flow (prompt → free-text
      or option-button answer → next step; quiz steps carry
      `correct_answers` + a result screen), driven by ONE shared path in
      the dynamic router keyed on config data — the only engine change
      this phase; no per-template handlers.
- [ ] Collected answers persisted to a single shared `collected_responses`
      table (bot FK, chat, JSON answers) + migration — one table serves
      feedback, forms, and quizzes.
- [ ] Controller copy for cards and every new message localized through
      `MAIN_BOT_STRINGS` (en + fa; key drift is a test failure);
      emoji-labelled buttons keep their filter variants covered.
- [ ] Tests: picker renders one card per registry template; provisioning
      seeds the chosen flow; each conversational template's seed drives
      the expected behaviour through the shared router; existing
      generic/hello/echo behaviour unchanged.

**Acceptance criteria**

- A new owner goes from "create bot" to a working Feedback/Quiz/Form bot
  without touching JSON: the picked bot is live and behaves as its card
  advertised, immediately.
- No per-template handler or `BotType` member is added — the three
  conversational templates run as generic flows on one shared steps path.
- Bots of every existing type keep their current behaviour (hello/echo
  regression tests stay green).
- The picker is localized (en + fa) like the rest of the controller.

### S2.3 — Versioned templates + re-clone into existing bots

**Context.** Templates version: bumping one is a data edit in the
registry, and an owner can push the latest seed into a bot they already
own. Re-clone replaces the **base copy only** — the owner's `translations`
and `single_language` mode survive the reset (the Phase 1 layer stays
theirs). This is ROADMAP's "Hello World today, marketplace tomorrow"
mechanic: provenance recorded at creation (S2.1) tells the dashboard
which template a bot came from and whether a newer version exists.

**Checklist**

- [ ] Version-bump mechanics in the registry: per-id versioning, latest
      resolution; a bump = one data edit (+ its tests).
- [ ] Re-clone service: apply a template's latest seed to an existing
      bot's base flow, preserve `translations` + `single_language`,
      update provenance to the new version; the flow is revalidated
      through `BotConfigUnion` before persisting.
- [ ] Dashboard "Reset to template" action: shows the bot's provenance
      (template + version, "update available" when the registry is ahead),
      asks for confirmation, then re-clones; owner-scoped like every
      `/api/bots*` route; cache invalidated on write.
- [ ] Bots without provenance can adopt a template through the same path
      (pre-Phase-2 and scratch bots are not stranded); `bot_type` follows
      the template, reusing the existing type-switch write path.
- [ ] No silent auto-updates — live bots change only on an explicit owner
      action.
- [ ] Tests: re-clone preserves translations/single-language and bumps
      provenance; cache invalidated; another owner's bot → 404; type
      follows template on adoption.

**Acceptance criteria**

- Bumping a template never mutates live bots by itself; every re-clone is
  an explicit, confirmed owner action.
- After a re-clone the base copy matches the latest seed while the
  owner's translations and language mode are intact.
- Changes are live within seconds (Redis cache invalidated) — the same
  invariant as every other settings write.
- Pre-Phase-2 bots can adopt a template later.

---

## Next milestones (brief)

- **Phase 8 — Per-user bot settings** — extended vision beyond the S0.4 MVP:
  config history/rollback, template gallery, dashboard analytics.

Full checklists for these phases are written here when we start them.

---

## Current focus

**→ Phase 2 — Starter bots & template library** (the active phase).
Phase 0 (S0.1–S0.4) and Phase 1 (S1.1–S1.3) are **done**; the S0.3
Secret Vault landed and its follow-up PR #4 is open awaiting owner
review, alongside PR #3 (E2E Telegram harness). The three Phase 2
sub-phases — S2.1 template registry core, S2.2 template picker in the
creation flow, S2.3 versioned templates + re-clone — are planned above;
S2.1 is the next focus.
