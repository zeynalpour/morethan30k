# 04 — Security

Deep dive of [ARCHITECTURE.md](../../ARCHITECTURE.md). A multi-tenant
platform hosting strangers' bots is a target: secrets, outbound calls,
and noisy/abusive tenants are the three threat surfaces.

Statuses: **[NOW]** shipped today · **[P x]** lands in phase x ·
**[S0.3]** the vault sub-phase.

## Threat model (short)

1. **Token/key theft at rest or in logs** — bot tokens, AI keys.
2. **Cross-tenant access** — owner A reading/editing bot of owner B via
   the API; tenant users reaching another tenant's data.
3. **SSRF via outbound nodes** — `webhook_call` to internal addresses.
4. **Abuse & spam** — a tenant bot flooding Telegram, scraping users,
   or hosting prohibited content.
5. **Injection via config** — crafted flow JSON becoming code (ReDoS,
   template injection, billion-laughs style blowups).

## Secrets (S0.3)

- **Envelope encryption:** per-row AES-256-GCM DEK, wrapped by a
  master key from env (`TME_MASTER_KEY`, 32 bytes, base64) — rotate the
  KEK without re-encrypting every row; PG never sees plaintext.
- `secrets` table (01): `ciphertext BYTEA, kind, ref_id, last_four,
  rotated_at`. `bots.token` migrates to `token_hash` (HMAC-SHA256 +
  server pepper) — the webhook hot path looks up by hash, never
  decrypts; decryption happens only in the adapter at send time.
- **Legacy migration:** one worker job converts plaintext rows, keeps a
  `migrated_at` marker; webhook accepts token → hash lookup before the
  old direct path, so cutover is atomic per row.
- **Never in logs:** secret_token header, bot tokens, AI keys are
  filtered by the logging config (pattern redaction, plus the PII
  redaction middleware below). `…last6` display convention stays [NOW].
- **Frontend:** zero secrets in the SPA — initData HMAC only [NOW];
  dashboard never sees a token (S0.4 acceptance criterion, kept).

## AuthN/AuthZ matrix

| Surface | AuthN | AuthZ |
|---|---|---|
| Telegram webhooks | secret-token header [NOW] | routing by token; unknown → negative cache [NOW] |
| Owner API `/api/bots*` | Telegram initData HMAC (per request) [NOW] | ownership: `bots.owner_id == user.telegram_id`; teams join `bot_members` [P7] |
| Dashboard statics | none (public) | none — SPA, all data via Owner API [NOW] |
| Worker internal | compose network isolation | no external exposure; no ports [NOW] |
| GOD actions | `god_telegram_id` allowlist | platform-wide scope, every action audited [P3] |
| Public REST API (P7) | per-owner API keys (hashed) | scoped to owner's bots; rate-limited |

- initData validation: verify hash + auth_date freshness window
  (replay protection) [NOW]; add `cross-origin` check for Mini App
  embedding.
- Teams (E4): role checks live in one service function
  (`require_role(bot_id, user, role)`) — never inline in route handlers;
  the webhook path never reads team tables.

## Outbound call safety (SSRF)

- `webhook_call` targets: https only, DNS-resolve then validate IP
  against denylist (RFC1918, link-local, loopback, metadata endpoints
  169.254.169.254), redirect chains re-validated per hop; per-bot
  allowlist option (F5 review enforces it for marketplace templates).
- No globbing: exact host[:port]; timeouts + size caps on responses.
- Circuit breaker + per-bot outbound budget (03) is also a DoS guard.

## Config injection defenses

- Pydantic validation at every boundary [NOW] — unknown keys tolerated
  (`extra=allow`) but unknown NODE types are rejected at publish.
- `validate` on collect_input is a named whitelist (`nonempty`,
  `email`, `phone`, `number`, `len<=N`) — never user-supplied regex.
- Template rendering (`{{…}}`): a safe renderer (no eval, no attribute
  chains past a fixed allowlist like `user.first_name`, `bot.title`,
  `vars.*`, `now`), length caps on the output; recursion depth capped.
- Effect budgets + node/hop caps (02) bound the blast radius of any
  flow bug.

## Abuse controls

- Per-bot blocklists (A7) enforced as a Redis set checked in middleware
  — hot path never touches PG.
- `report this bot` (H5) → `incident_reports` → GOD queue; one-click
  disable = `is_active=false` [NOW] + cache drop [NOW].
- Broadcast anti-stampede (M5d) doubles as an anti-spam measure —
  per-chat rate caps mean a hostile owner's blast is spread over hours,
  not a burst Telegram would ban the platform for.
- Platform-level: GOD can pause ALL sends for a bot mid-flight
  (kill-switch flag checked by the effects executor).

## Audit & compliance

- `audit_log` append-only, same-transaction as the change (01) [P3].
- GDPR data export (H1): worker job assembles a user's data from
  `chat_events`, `user_languages`, collected vars → JSON file, expiring
  link. Retention (H2) prunes by per-bot TTL windows.
- PII redaction middleware [P6]: structured logs mask phone/email
  patterns before persistence.
- Stars payments: `purchases` idempotent on telegram_payment_id;
  reconciliation job (ledger sum vs. purchase sum) flags drift to GOD.

## Ops hardening

- Postgres/Redis: no host ports, compose-network only [NOW]; PG over
  TLS inside the network, password auth with rotated secrets in `.env`
  (never in the repo).
- Image: non-root user, pinned base digests, CI scan (the repo already
  runs ruff+pytest; add trivy to the pipeline later).
- Backups: nightly `pg_dump` to the host (outside compose volumes) +
  WAL if T2; restore drill documented in runbook.
- Cloudflare in front (existing tme-dev.izhex.com setup [NOW]) gives
  TLS, DDoS, and IP rate limiting at the edge before nginx.
