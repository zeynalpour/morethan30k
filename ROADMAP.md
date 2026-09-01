# TME — Roadmap

A living, prioritized feature roadmap for the TME platform. Statuses are updated
as work progresses; PRs should reference the phase they belong to.

## Status legend

| Mark | Meaning          |
| ---- | ---------------- |
| 🟢   | Done             |
| 🟡   | In progress      |
| ⚪   | Planned          |
| 💡   | New idea         |
| 🧭   | Stretch / future |

---

## North-star principle

> **No per-bot Python.** Every bot — regardless of type — is a JSON workflow
> saved in Postgres, cached in Redis, and executed by one shared engine. There
> is no per-bot process, no per-bot polling loop, and no per-bot code to
> deploy.

---

## Phase 0 — Foundation & tech-debt cleanup

- 🟢 **All bots saved in DB** — already true today:
  - `bots` / `bot_configs` tables (owner FK, JSONB `flow`, webhook flag).
  - Config lives in Postgres → cached in Redis → executed by the shared
    dispatcher.
- 🟡 **Remove the raw-dict `managed_bot` interception** in
  `src/tme/main.py` so the native, typed `@main_router.managed_bot()` handler
  actually runs in production.
- ⚪ **`BotType` enum + per-type config union** — one `Bot` table holding typed
  configs: `generic | hello | echo | bridge | ai_gateway | …`. 💡
  *Foundational for every new bot kind below.*
- ⚪ **Secret Vault** — encrypt bot tokens & API keys at rest (e.g. `pgcrypto`
  or app-level envelope encryption) from day one. AI gateway bots need key
  storage immediately. 💡

---

## Phase 1 — Multilanguage platform  *(your #1)*

- ⚪ Per-bot translations in `BotConfig.flow`:
  `translations: {en, fa, ru, ar, de, es, fr, tr, zh, hi, id, pt, …}`
- ⚪ Per-user language preference — default from Telegram's `language_code`;
  `/language` inline-flag picker to switch.
- ⚪ `I18nMiddleware` on the tenant router so every handler reads the user's
  language. The **main bot (control plane) translates too** — so GOD works the
  platform in GOD's language. 💡
- ⚪ "Copy-from-base" editor helper — seed a new language from an existing one
  instead of translating from scratch.
- ⚪ Fallback chain: user language → bot default → English.

---

## Phase 2 — Starter bots & template library  *(your #2)*

- ⚪ Bot creation flow asks "pick a template":
  - **Hello World bot** — literally just says hello when you `/start` it.
  - Echo, Feedback collector, Quiz, Simple form, AI gateway (later).
- ⚪ Template = seed a `BotConfig` from a registry — **no new code per
  template**. A new bot is a new row, not a new handler.
- ⚪ **Versioned templates + clone** — bump a template, re-clone into existing
  bots. 💡 *Hello World today, marketplace tomorrow.*

---

## Phase 3 — GOD super-admin & conversational builder  *(your #5)*

- ⚪ **GOD role** — `settings.god_telegram_id` (single super-admin) + `/god`
  panel: list all bots, health/status, start/stop, view credits.
- ⚪ **`/describe` wizard** — GOD explains a bot in plain words, and the main
  bot walks through it step-by-step (name → type → welcome message → buttons →
  language) until a **draft** is deployable.
- ⚪ **Draft → Preview → Publish** lifecycle — every bot passes a sandbox
  preview chat before going live. 💡
- 🧭 **LLM co-pilot** — GOD describes the bot freely; an LLM drafts the whole
  JSON flow; GOD approves. 💡

---

## Phase 4 — GOD bot types & AI gateway  *(your #3)*

- ⚪ **Bridge / Direct Unknown Messenger** — personal-use bot: strangers message
  it; GOD reads and replies privately (anonymous contact bot).
- ⚪ **Personal AI gateway** — user enters an `endpoint` + `api_key` in a
  conversation; the bot then chats with that model (OpenAI-compatible, Ollama,
  etc.). Key stored encrypted in the Secret Vault.
- ⚪ **Shared AI gateway + credits** — GOD adds endpoint + key + a credit pool;
  per-user credit ledger, decremented on each request.
- ⚪ **Metering service built generic** — the credit ledger is designed so
  per-tenant rate limits, quotas, and paid tiers can reuse it later. 💡

---

## Phase 5 — Workflow engine (the real product)  *(your #6)*

- ⚪ **v1 — linear flows** — nodes (`message`, `collect-input`, `keyboard`),
  transitions, variables, `/start` routing.
- ⚪ **v2 — branching & actions** — conditions; actions: webhook/API call, AI
  call, forward-to-admin.
- ⚪ **v3 — visual editor** — web app, flow versioning, dry-run simulation.
- Everything stays `DB → Redis → engine` — **no per-bot Python, ever.**

---

## Phase 6 — Ops & hardening

- ⚪ Per-tenant rate limits & quotas (reuse metering), abuse protection.
- ⚪ Observability: per-bot metrics, error tracker, **GOD health dashboard**. 💡
- ⚪ Horizontal scaling, webhook retry policy.

---

## Phase 7 — Ecosystem & monetization

- ⚪ Template marketplace + clone/export/import configs.
- ⚪ Per-bot analytics (active users, messages) — also feeds the dashboard.
- ⚪ Credit packs via Telegram Stars, paid templates, teams/collab. 💡
  *Plugs straight into the metering service.*
- ⚪ Public REST API for config CRUD.

---

## Priority policy

- Ranked by **value / effort** — not a strict sequence.
- PRs must reference the phase they belong to (e.g. "Closes Phase 3: `BotType`").
- Statuses are maintained in this file as the platform evolves.