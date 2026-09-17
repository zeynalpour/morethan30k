# 01 — Data Model

Deep dive of [ARCHITECTURE.md](../../ARCHITECTURE.md). The Postgres schema
is the backbone: every roadmap feature (templates, history, credits,
analytics, audit) is a table, per the vault rule "if it can't name its
storage, it isn't an idea yet."

Statuses: **[NOW]** exists today · **[P x]** lands in phase x.

## Design rules

1. **JSONB config is per-bot, validated by Pydantic at every boundary**
   (write API, cache read, engine parse — `parse_bot_config` never trusts
   stored JSON). [NOW]
2. **Never update truth; append it.** Config history, credit ledger,
   audit log are append-only. Fixes are compensating appends. (C2, F4, D2)
3. **Drafts and published configs are the same row family.** A draft is a
   `bot_configs` row with `status='draft'`; publish is an atomic
   status flip + history append. No separate "drafts" table to drift out
   of sync. (M4c, E6)
4. **Money tables use integers.** Credits, token counts, prices are
   `BIGINT`/minor units — never floats, never `NUMERIC` where an int
   suffices. (D2, G1)
5. **Hot path tables are small; big tables are append-only and
   partitioned.** The webhook path touches only `bots`, `bot_configs`,
   Redis. Everything else is behind the worker. [NOW, extended T2]
6. **Schema-first rule.** No checklist item in `SUB-PHASES.md` may be
   ticked until its storage is named **in this file**. A roadmap or blueprint
   item with no table (or an explicit "no table needed, here's where the data
   lives instead") is a design gap, not an implementation detail — this is how
   S2.2's `collected_responses` came to be claimed in the checklist while no
   table, model, or migration ever existed (issue #21). When an item genuinely
   needs no table, this file still says so and says what owns the data (Redis
   structure, in-code registry, config).
7. **Design ahead, migrate on demand.** Tables for later phases are specified
   here before their phase starts (names, columns, keys, indexes, retention),
   but migrations land one per sub-phase as that phase ships — no speculative
   migrations, no retrofitted schema.

## Config-in-flow: named storage with no table

Rule 1 makes `bot_configs.flow` the storage for behaviour that is *data* —
Pydantic validates it on every write and the engine reads it per request, so
adding a behaviour is a schema field, not a migration. Recorded here so the
"name the storage, or state explicitly that no table is needed" rule stays
auditable:

- **Phase 2 `steps` flow [NOW]** — a multi-step flow (`id`, `prompt`,
  `options[{label,value}]`, `answer_type`, `correct_answers`) rides the flow
  as an `extra="allow"` rider, interpreted by one shared engine
  (`services/steps.py`). No table, no per-template code.
- **Per-step translations + `main_language` [NOW, issue #23]** —
  `translations[<code>].steps[<step id>]` carries only that step's localized
  `prompt` and option *labels* (the option `value` is the answer key and is
  never translated), and `BotConfigBase.main_language` (ISO-639-1, optional)
  records the language the base copy is written in — it becomes the middle
  layer of the fallback chain (**user language → main language → base copy**;
  the middle layer is English when unset). Both are config-in-flow: **no
  table, no migration.** Copy is not per-user state, it changes with every
  edit, and the flow already carries and caches it — a `translations` table
  would put copy on the request path and in Postgres for no gain. Unknown
  step ids are stored as given and never read; blank copy is "unset" at
  every layer (never sent).
- **Modules: `active_modules` is derived bookkeeping [NOW, IDEAS N step 0]** —
  the in-code module registry (`src/tme/modules/`, mirroring `TemplateSpec`)
  names every engine capability that exists and carries the detector that
  answers "does this flow contain it?". The flow key `active_modules` stays in
  the data (the engine reads it as its module gate) but is **never** a setting:
  every write path persists `normalize_flow(flow)`, i.e. the value derived from
  the flow's own content, so a bot cannot advertise a module it does not
  contain. No table — capabilities are code, and the per-bot enable state is the
  flow itself; `bot_modules` arrives only with the store and third-party
  modules (IDEAS N step 3).

## ERD (target)

```
users ──1:N── bots ──1:1── bot_configs ──N:1── bot_config_history
  │             │ 1:N                                       (per publish)
  │             ├── collected_responses (S2.x flows — feedback/forms/quiz)
  │             ├── chat_variables (A4 message variables)
  │             ├── bot_members (teams E4, roles)
  │             ├── blocked_users (A7)
  │             ├── faq_entries (B3, optional — small sets stay in flow)
  │             └── jobs (scheduled/broadcast/rollup, A1)
  ├── user_languages [NOW]
  ├── purchases (Stars G1) ──> credit_ledger (D2)
  ├── referrals (G5)
  └── templates ──1:N── template_versions (B8 marketplace only — see §Templates)
                      └── template_reviews (F5)

chat_events (partitioned by month, append-only — analytics E5, funnels)
ai_usage (append-only — D4)          audit_log (append-only — F4)
secrets (encrypted — S0.3)          webhook_deliveries (retry bookkeeping)
redis (not PG): flowstate:{bot}:{user} (30 min TTL), config cache, liveness
```

## Table inventory

### Identity & ownership [NOW]

- `users` — telegram_id unique, username, first_name, language_code,
  is_active. [NOW]
- `user_languages` — telegram_id unique, language_code. Covers owners AND
  tenant-bot users (tenant chatters are NOT `users` rows — they only get
  rows here when they pick a language). [NOW]
- `bots` — token (routing key), telegram_bot_id, bot_type enum,
  owner_id FK, is_active, webhook_registered. [NOW]
- `bot_configs` — bot_id unique, `flow` JSONB, description. [NOW]
  **Extends [P3/P8]:** + `status: draft|published|archived`,
  + `published_at`, + `template_version_id` FK (B7 pin),
  + `publish_checklist` JSONB (M2e).
  ⚠️ **Constraint change required for drafts (S3.2):** `bot_id` is unique
  today, so a bot cannot hold a draft row and a published row at once. When
  drafts land, replace `unique(bot_id)` with a **partial** unique index —
  `unique(bot_id) WHERE status = 'published'` — so exactly one published
  config exists per bot while drafts coexist. Decide this before S3.2, not
  during it.

### Conversational flows (S2.x: feedback / quiz / simple form) [NOW]

- `collected_responses` — **one table serves feedback, forms, and quizzes.**
  Columns: `bot_id` FK (CASCADE), `chat_id` BIGINT, `user_id` BIGINT,
  `flow_ref` (template id or flow version), `answers` JSONB (step id →
  value; labels preserved for display), `score` INT NULL / `graded` INT NULL
  (quizzes), `completed` BOOL, `created_at`. Index
  `(bot_id, created_at DESC)` for the owner's list; optional
  `(bot_id, user_id)`. Written **on completion** by the shared steps path —
  one write site, zero per-template code.
  ⚠️ **STATUS — not built (issue #21).** Today answers live only in Redis
  `flowstate:{bot_id}:{user_id}` (30 min TTL) and are delivered once to the
  owner's chat by `steps.deliver_to_owner()` (best-effort recap). That message
  is the only copy: no open owner chat or a failed send = answers dropped.
  Nothing reads this table yet, but E5 (analytics/drop-off), H1 (GDPR export)
  and H2 (retention) all index on it, so it is a prerequisite rather than a
  later add-on. Migration `0006`. Retention: owned by H2's purge job, never
  hardcoded here.

- **Redis `flowstate:{bot_id}:{user_id}`** — deliberately *not* a table: the
  live cursor of one user through one flow (`index`, `values`, `labels`),
  30-minute TTL, no durability requirement. Persisted answers are the copy
  that matters (above); the cursor is disposable by design.

- `chat_variables` (A4) — `bot_id`, `chat_id`, `key`, `value` JSONB,
  `updated_at`, unique `(bot_id, chat_id, key)`. Backs `{{user.x}}` /
  `{{var.x}}` interpolation in any outgoing copy. Read into the Redis config
  path per chat; small rows, owner-scoped export helper for H1.

- `faq_entries` (B3) — `bot_id`, `question`, `answer`, `keywords` TEXT[],
  `priority`, `is_active`. Only when the FAQ set outgrows the flow JSONB
  (small sets stay in `flow.faq` — no table needed; this is the escape hatch
  that keeps configs small).

### Templates (S2.x, B7/B8) — in-code registry [NOW], tables only for a marketplace

**Reality check.** Phase 2 deliberately did **not** create template tables:
S2.1 chose an in-code registry (`src/tme/templates/` — `TemplateSpec` + five
versioned seeds) because a template is *seed data for a config*, importable
with no DB, and "a `templates` table only becomes interesting when a
marketplace does." So today:

- **Registry (in code, source of truth)** — `TemplateSpec(id, version,
  display_name, description, bot_type, seed flow)`, `list_templates()` /
  `get_template()` / `resolve_template()`. Adding a template = one data entry.
- **Per-bot pin, in data** — the flow rider `template: {id, version}`
  (`STAMP_KEY = "template"`), exposed by the API as `current` +
  `latest_version` + `update_available`. No FK, no join, no table; the stamp
  is validated on read like everything else in `flow`.
- **Tables below are [P7/B8 marketplace]** — they exist for *user-authored,
  public* templates with ownership, install counts and reviews. Do not create
  them until the marketplace phase; when it lands, the registry seeds become
  the initial rows (migration copies them in, registry stays the authoring
  format for built-ins).

- `templates` — slug, owner_id (author), category, blurb, icon,
  is_public, install_count, rating_sum, rating_count. Marketplace card
  reads only this table. (G6)
- `template_versions` — template_id, version, flow JSONB (a complete
  seed config), changelog, is_reviewed (F5), review_state. Bots PIN a
  version; bump propagation is opt-in per bot ("update available").
- `template_reviews` — version_id, reviewer (GOD), verdict, notes. (F5)
- `referrals` (G5) — referrer_user_id, invited_user_id, code, state,
  credited_at; unique(invited_user_id) so a user is only ever credited once.

### Config history (C2, rollback) [P3]

- `bot_config_history` — append-only. Columns: bot_id, version,
  flow JSONB, published_by (user id), published_at, change_note,
  is_rollback_of. Publish = `INSERT` here + flip `bot_configs` in one
  transaction. Rollback = insert a NEW row (copy of old), never UPDATE.
  Cap retention: keep last N=50 per bot, prune in the worker (H2 reuse).

### Team access (E4) [P7-early]

- `bot_members` — bot_id, user_id, role enum `viewer|editor|publisher`,
  invited_by, unique(bot_id, user_id). Owner API joins against it; the
  webhook path never reads it.

### Abuse & safety (A7, H5) [P6]

- `blocked_users` — bot_id, telegram_id, reason, blocked_at. Read into
  Redis per-bot sets (small) by a middleware drop — PG never touched on
  the hot path.
- `incident_reports` — bot_id, reporter, category, details JSONB, state
  (GOD queue).

### Work queue (A1, A2, A5, H2, D) [P5]

- `jobs` — the durable spine of the scheduler. Columns: id, bot_id,
  kind (`broadcast|scheduled_send|inactivity|retention|rollup|webhook_out|ai_call`),
  run_at, payload JSONB, status (`pending|claimed|done|failed|dead`),
  attempts, max_attempts, claimed_by, claimed_at, last_error.
  Claim: `SELECT … FOR UPDATE SKIP LOCKED` in the worker. Broadcast
  fan-out: one parent job → per-chat child jobs (M5d jitter: each child's
  run_at gets a random offset inside the window) so a 10k-user broadcast
  never stampedes Telegram rate limits.

### Credits & monetization (D2, G1, G2) [P4/P7]

- `credit_ledger` — append-only, integer amounts. Columns: user_id,
  delta (+grant/−spend), reason enum (`purchase|grant|ai_call|quota|refund`),
  ref_id (job/usage id), balance_after, created_at. Balance is
  **derived** by the last row's `balance_after` (a snapshot column — the
  classic ledger-with-snapshot pattern; no `UPDATE users.balance` ever).
  This same table serves paid tiers, quotas, and rate limits (the
  "metering built generic" roadmap mandate).
- `purchases` — provider (`stars`), telegram_payment_id, user_id,
  stars_amount, granted_credits, state. Stars webhooks land here, append
  a ledger row on success — idempotent on telegram_payment_id.

### Events & analytics (E5, E5b/c, D4) [P7, worker-fed]

- `chat_events` — append-only, **declaratively partitioned by month**.
  Columns: bot_id, chat_id, user_id, kind (`message_in|message_out|
  node_enter|node_exit|fallback|button|csat|ai_call|drop`), node_id,
  payload JSONB, created_at. Index (bot_id, created_at brin). Written by
  the worker from a Redis stream — the webhook path NEVER writes PG
  analytics. Funnel/drop-off queries (E5c) are `kind='node_exit'` vs
  `node_enter` counts per node.
- `ai_usage` — append-only: bot_id, user_id, provider, model,
  prompt_tokens, completion_tokens, credits_charged, latency_ms,
  ok, created_at. "Cost per bot" (D4) is a GROUP BY here.
- `daily_bot_stats` — rollup (worker, nightly): bot_id, day, msgs_in,
  msgs_out, active_users, fallbacks, drops. The dashboard reads THIS,
  never raw events.

### Secrets (S0.3) [P0/S0.3]

- `secrets` — kind (`bot_token|api_key`), ref_id (bot id / owner id),
  ciphertext BYTEA (envelope: AES-256-GCM payload + one wrapped DEK per
  row, the DEK wrapped by `VAULT_MASTER_KEY` from env), last_four (for
  UI "…ab12"), rotated_at, created_at/updated_at.
- `bots.token_hash` — peppered HMAC-SHA256 of the routing token, unique.
  **The webhook resolver looks up by hash first** (`services/bot_lookup.py`):
  one indexed SELECT, no decryption on the hot path, and the Redis config
  cache is keyed `botcfg:{token_hash}`. The raw token is what callers hold
  (it is in the webhook URL) — hashing happens at that boundary.
- **`bots.token` is transitional, not the truth.** It is the *fallback* the
  resolver tries when the hash matches no row, so a stack that has not been
  backfilled yet keeps serving. Lifecycle: nullable ([NOW], migration
  `0006`) → populated until a stack is backfilled
  (`scripts/vault_backfill.py --apply`, per stack, by the owner) → cleared
  with `--clear-plaintext` once every row verified a decrypt round-trip →
  **column dropped in a later release**, after dev/test/prod are all done.
  No stack is switched over by a migration: data changes are per stack and
  verified, never applied to every stack by container start. **Provisioning is
  vault-only once the key is set** (S0.3 follow-up): a bot created while
  `VAULT_MASTER_KEY` is present is persisted with `bots.token = NULL` + the
  routing hash + its vault row, so activation is not undone by the next bot the
  owner creates. Without the key the vault cannot operate, so provisioning keeps
  writing the plaintext column (with its warning). Legacy plaintext is cleared
  only by the owner's verified per-stack `--clear-plaintext` run — never
  silently by re-provisioning.
- **Reading the real token** (liveness probes, adapter sends, cache
  invalidation keys) goes through ONE accessor —
  `services/vault.resolve_bot_token(s)` — vault first, plaintext column as
  the rollout fallback. Nothing else reads `Bot.token` directly.
- **Master-key rotation:** re-wrap DEKs only, never re-encrypt payloads.
  Keep the old key, decrypt each `wrapped_dek` with it, re-wrap the same DEK
  with the new key, write it back (same row, same ciphertext), then verify by
  decrypting one known row with the new key and keep the new key backed up
  offline *before* dropping the old one. Losing `VAULT_MASTER_KEY` makes every
  vaulted token unrecoverable — back it up per stack before switching over.
  Rotating `VAULT_PEPPER` is separate and cheaper: it invalidates every
  `token_hash` (derived data, safe to recompute), so re-run
  `scripts/vault_backfill.py --apply` in the same window — for a row whose
  plaintext is already cleared the script re-derives the hash from the
  **vaulted** token. Until that repair runs, no bot resolves by hash, and a
  cleared stack has no plaintext fallback.

### GOD & audit (F1, F4) [P3]

- `audit_log` — append-only: actor (user id or `system`), bot_id,
  action enum, before/after JSONB, created_at. Written in the same
  transaction as the change it describes (same conn, same commit) — an
  audit row can never be lost to a crash between change and log.
- `settings` (god_telegram_id etc.) stays in env/config, not a table —
  one GOD, no UI to edit it.

## Schema readiness by phase

Where each roadmap phase's storage stands, so nothing gets retrofitted. "Redis"
and "in code" are first-class answers — they mean *no table is needed*, stated
explicitly rather than left implicit.

| Phase | Needs | Status |
| ----- | ----- | ------ |
| P0 foundation | `users`, `bots`, `bot_configs`, `secrets`, `dashboard_auth_tokens` | ✅ exist |
| P1 multilanguage | `user_languages` | ✅ exists |
| P2 templates + flows | in-code registry (no table), flow rider `template:{id,version}`, Redis `flowstate` (**no table**), `collected_responses` | ⚠️ **`collected_responses` missing** — issue #21, migration `0007` |
| P3 GOD + builder | `audit_log`, `bot_config_history`, `bot_configs.status` + partial unique index | ⚠️ design fixed here; migrations with S3.x |
| P4 AI gateway | `secrets` (kind `api_key`), `ai_usage`, `credit_ledger`, `purchases` | design ready |
| P5 workflow engine | `jobs` (worker spine), `chat_variables`, `faq_entries` | design ready |
| P6 ops/hardening | `blocked_users`, `incident_reports`, `webhook_deliveries` | design ready |
| P7 ecosystem | `templates`/`template_versions`/`template_reviews` (marketplace), `referrals`, `bot_members`, `daily_bot_stats`, `chat_events` | design ready — **do not create before the phase** |
| P8 dashboard vision | reads P3's history; `publish_checklist` column on `bot_configs` | design ready |

## Migration sequence (declared ahead of time)

Numbering is sequential and one migration per sub-phase; a phase that needs
several tables may still ship one migration per sub-phase, never one per table
buried inside a feature PR.

- `0001` initial schema ✅ · `0002` bot_type ✅ · `0003` dashboard_auth_tokens ✅
  · `0004` user_languages ✅ · `0005` secret_vault ✅
- `0006` **vault activation** — `bots.token` nullable + the `secrets`
  timestamps 0005 forgot (S0.3, issue #28) ✅
- `0007` **`collected_responses`** (issue #21) ← the only *overdue* migration
- `0008` `bot_config_history` + `audit_log` (S3.x, with the config lifecycle)
- `0009` `bot_configs.status` + partial unique index (S3.2 drafts)
- `0010` `jobs` (worker spine) — first phase that needs a background worker
- `0011` credits/metering (`credit_ledger`, `purchases`) — Phase 4
- `0012` `chat_variables` (A4) · `0013` `ai_usage` (D4) · `0014` `chat_events`
  partitions + `daily_bot_stats` (E5) · `0015` `blocked_users` /
  `incident_reports` (P6) · `0016` marketplace tables (P7)
- later: **drop `bots.token`** — once dev/test/prod are backfilled and verified

The exact numbers may shift as phases land; the **order** is the contract.

## Working with the schema (runbook for agents and contributors)

### Adding a table — the order is not optional

1. **Model** — `src/tme/database/models.py`: SQLAlchemy 2.0 style, `Mapped[...]`
   annotations, `__tablename__` snake_case, FKs with an explicit `ondelete`.
2. **Migration** — `migrations/versions/000N_snake_name.py`, next free number.
   Alembic is applied automatically at container start
   (`CMD ["sh","-c","alembic upgrade head && exec uvicorn ..."]`) and in CI
   before tests, so there is no manual step — which also means a broken
   migration blocks the boot. Test the upgrade path against a fresh DB.
3. **Write path** — one write site, in the service that owns the data. Never
   on the webhook hot path (that touches only `bots`, `bot_configs`, Redis).
4. **Read path** — owner-scoped API route (`/api/bots/{bot_id}/...`) so
   ownership checks are inherited, never re-implemented per endpoint.
5. **Tests** — write-on-completion/correct-row-shape, owner-scoped read,
   another owner → 404, malformed input never persists.
6. **This file** — update it **in the same PR**: the table entry, the ERD, the
   readiness matrix, the migration sequence. A schema change that doesn't
   touch this file is an incomplete change.

### Where does this data live? (decide storage first)

| Nature of the data | Home | Why |
| --- | --- | --- |
| Durable, queryable, owner-facing, or needed for analytics/exports | **Postgres table** | survives restarts, joins, indexes |
| Live cursor / short-lived session state (a user's position in a flow, rate-limit counters, cache) | **Redis + TTL** | disposable by design; never a table |
| Static seed data, versioned in code (templates, built-in copy) | **In-code registry / constant** | importable with no infrastructure, reviewable in a PR |
| Platform configuration with a single value and no UI (GOD id, master key) | **Env / config** | one value, no rows |
| Per-bot behaviour the engine interprets | **`bot_configs.flow` JSONB** | the north star: no schema change per feature |

Rule of thumb: if losing it on restart is fine, it is Redis; if a human would
notice it's gone, it is a table.

### Conventions

- snake_case identifiers; plural table names (`bots`, `chat_events`).
- `BIGINT` surrogate ids (`id`), `timestamptz` for every timestamp, never naive.
- Money and token counts are **integers** (minor units) — never floats.
- FKs are explicit and cascade deliberately (`ondelete="CASCADE"` for
  owned data like a bot's config and responses).
- Order rows by `created_at DESC` with a matching composite index when a
  human will page through them.
- When a field describes *per-bot behaviour*, it belongs in `flow` JSONB; when
  it must be queried, joined, or aggregated by the platform, it earns a column.

### Do not

- **No per-bot tables, ever** — one table serves every tenant, discriminated by
  `bot_id`.
- **Never `UPDATE` truth** — history, ledgers, and audit are append-only;
  corrections are compensating rows.
- **Never write Postgres from the webhook hot path** — enqueue; the worker
  writes.
- **Never store what a recap message can lose** — if an answer matters, persist
  it (this is exactly the `collected_responses` gap, issue #21).
- **Never create a later phase's tables early** — specify them here, migrate
  when the phase lands.


---

## Retention & lifecycle (per data type — never one global window)

Retention is a property of **the data**, declared per table/record class, and
read by the purge job. A table with no declared policy is a design gap.

- **Built-in / public templates: no expiry.** They are versioned and must stay
  reproducible; nothing purges them.
- **Owner-private data whose owner is gone and which no one can reach: purge
  after a grace window.** Rows carry `deleted_at` + `purge_after`; the worker
  deletes once past the window. (A private template whose owner deleted their
  account and which nobody installed is unreachable, un-usable, and should not
  live forever.)
- **Collected/behavioural data (`collected_responses`, `chat_events`):
  configurable window per bot** (H2), default retained, owner can shorten.
- **Config history: capped per bot** (last N=50), pruned by the worker.
- Deletion is two-phase everywhere: soft-delete (invisible) → purge (gone) —
  never a hard `DELETE` at the moment of user intent.

## Drafts & versions (S3.2 and the version handling that follows)

- **Drafts must exist.** A professional designer may spend weeks preparing the
  next version while end users keep running the current one; end users must
  never see a change until publish. No auto-publish, no live edits.
- **Materialise the draft once.** The draft row is created when the feature is
  enabled / at the migration step — a copy of the published config — and is then
  edited **in place** for the whole drafting period. It is *not* regenerated on
  each draft change (that would mean a migration-like write per keystroke and a
  moving baseline to diff against).
- **Publish** = atomic status flip + history append (C2). **Rollback** = append
  a new row copying an older version, never an `UPDATE`.
- **Constraint:** `bot_configs.bot_id` is unique today, so drafts cannot
  coexist with the published row until it becomes a **partial** unique index
  (`unique(bot_id) WHERE status='published'`). Migration `0008`.
- Later version handling builds on the same shape: a version is a row, a draft
  is the editable head, publishing promotes it.

## Migration policy

- Alembic, one migration per sub-phase, [NOW] pattern continues.
- **A migration that adds a table must update this file in the same PR** —
  schema reality and this document never diverge (the S2.2 miss came from
  exactly that divergence).
- Never `DROP` data columns in the same release that stops writing
  them; deprecate → prune next release (e.g. `dashboard_auth_tokens`,
  already retired logically [NOW]).
- `chat_events` partitions are created ahead by the retention job
  (keeps 12 months, prunes older) — partition management is a worker
  task, not a migration.
