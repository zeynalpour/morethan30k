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

## ERD (target)

```
users ──1:N── bots ──1:1── bot_configs ──N:1── bot_config_history
  │             │ 1:N                                       (per publish)
  │             ├── template_versions (nullable FK, pin B7)
  │             ├── bot_members (teams E4, roles)
  │             ├── blocked_users (A7)
  │             └── jobs (scheduled/broadcast/rollup, A1)
  ├── user_languages [NOW]
  ├── purchases (Stars G1) ──> credit_ledger (D2)
  └── templates ──1:N── template_versions (B7)
                      └── template_reviews (F5)

chat_events (partitioned by month, append-only — analytics E5, funnels)
ai_usage (append-only — D4)          audit_log (append-only — F4)
secrets (encrypted — S0.3)          webhook_deliveries (retry bookkeeping)
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

### Config history (C2, rollback) [P3]

- `bot_config_history` — append-only. Columns: bot_id, version,
  flow JSONB, published_by (user id), published_at, change_note,
  is_rollback_of. Publish = `INSERT` here + flip `bot_configs` in one
  transaction. Rollback = insert a NEW row (copy of old), never UPDATE.
  Cap retention: keep last N=50 per bot, prune in the worker (H2 reuse).

### Templates (B7, B8) [P2]

- `templates` — slug, owner_id (author), category, blurb, icon,
  is_public, install_count, rating_sum, rating_count. Marketplace card
  reads only this table. (G6)
- `template_versions` — template_id, version, flow JSONB (a complete
  seed config), changelog, is_reviewed (F5), review_state. Bots PIN a
  version; bump propagation is opt-in per bot ("update available").
- `template_reviews` — version_id, reviewer (GOD), verdict, notes. (F5)

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

- `secrets` — kind (`bot_token|api_key|vault`), ref_id (bot/owner),
  ciphertext BYTEA (envelope: AES-GCM, DEK wrapped by a master key from
  env/KMS), last_four (for UI "…ab12"), rotated_at. `bots.token`
  migrates here; webhook routing keeps a `token_hash` (HMAC-SHA256 with
  a server pepper) in `bots` so the hot lookup never decrypts anything.
  Plaintext legacy rows are migrated once, in place.

### GOD & audit (F1, F4) [P3]

- `audit_log` — append-only: actor (user id or `system`), bot_id,
  action enum, before/after JSONB, created_at. Written in the same
  transaction as the change it describes (same conn, same commit) — an
  audit row can never be lost to a crash between change and log.
- `settings` (god_telegram_id etc.) stays in env/config, not a table —
  one GOD, no UI to edit it.

## Migration policy

- Alembic, one migration per sub-phase, [NOW] pattern continues.
- Never `DROP` data columns in the same release that stops writing
  them; deprecate → prune next release (e.g. `dashboard_auth_tokens`,
  already retired logically [NOW]).
- `chat_events` partitions are created ahead by the retention job
  (keeps 12 months, prunes older) — partition management is a worker
  task, not a migration.
