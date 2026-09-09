# TME — Architecture

Target architecture for a platform where **anyone** creates Telegram bots
without programming — scalable to the 30k-bots promise, reliable, and
serving the full skill range (IDEAS.md § M). This file is the index and
the bird's-eye view; deep dives live in `docs/architecture/`.

Statuses: **[NOW]** shipped today · **[T1–T3]** scaling tiers ·
**[P2–P8]** roadmap phase that introduces it.

## Principles

1. **No per-bot Python, ever.** A bot is a JSON config row → Redis → one
   shared engine. Every new capability is a node type, a table, or a
   service — never per-tenant code. (ROADMAP north star.)
2. **Postgres + Redis only.** Queues are Redis Streams, locks are Redis,
   cache is Redis. No Kafka/NATS/Celery. Boring, operable on one box.
3. **The engine is pure; side effects live in adapters.** Interpreter is
   `(flow, state, event) → (effects, new_state)`. Telegram is one adapter;
   dry-run, Mini App, and WhatsApp are others. (Enables C1c, I6, I4.)
4. **Append-only for money and truth.** Credit ledger, config history,
   audit log: never updated, only appended. Rollback = a new append.
5. **Invalidate, don't synchronize.** Redis is an accelerator with TTLs
   and explicit invalidation; Postgres is the single source of truth
   (the S0.4 decision, kept forever).
6. **Progressive disclosure applies to the system too.** The novice wizard
   and the public REST API drive the same schemas — no privileged paths.

## System overview (target, Tier 1)

```
                        ┌──────────────────────── Cloudflare ────────────────────────┐
                        │  TLS · DDoS · (later) cache dashboard statics               │
                        └───────────────────────────┬───────────────────────────────┘
                                                    │
                                             nginx (host)
                                                    │ 127.0.0.1:8080
                    ┌───────────────────────────────┴─────────────────────────────┐
                    │                    TME app containers (N ≥ 1)                │
                    │  ┌──────────────┐ ┌──────────────┐ ┌──────────────────────┐ │
 Telegram ──────────┼─►│ Webhook GW   │ │ Owner API    │ │ Mini App statics     │ │
 (30k webhooks)     │  │ /webhook/{t} │ │ /api/bots*   │ │ /dashboard (SPA)     │ │
                    │  └──────┬───────┘ └──────┬───────┘ └──────────────────────┘ │
                    │         │  tenant_dp      │ initData HMAC                   │
                    │  ┌──────▼─────────────────▼──────────────────────────────┐ │
                    │  │  Flow engine (pure interpreter, node graph C1)         │ │
                    │  │  effects → Telegram adapter (aiogram, LRU registry)    │ │
                    │  └──────┬────────────────────────────────────────────────┘ │
                    └─────────┼─────────────────────────────────┼─────────────────┘
                              │                                 │ enqueue jobs
                    ┌─────────▼────────┐             ┌──────────▼────────────────┐
                    │  Redis            │             │  TME worker containers    │
                    │  · botcfg cache   │◄────────────►│  · scheduler (A1)         │
                    │  · chat state     │  streams     │  · broadcasts, webhooks   │
                    │  · rate limits    │  + results   │  · AI gateway calls (D)  │
                    │  · queues (XADD)  │              │  · rollups, retention     │
                    └─────────┬────────┘             └──────────┬────────────────┘
                              │                                 │
                    ┌─────────▼─────────────────────────────────▼────────────────┐
                    │  Postgres 16                                             │
                    │  users·bots·bot_configs·history·templates·jobs·ledger·    │
                    │  chat_events (partitioned)·audit·ai_usage·secrets        │
                    └───────────────────────────────────────────────────────────┘
```

`app` and `worker` are the **same image, different entrypoints** —
`uvicorn tme.main:app` vs `tme-worker`. No new services, no new
dependencies.

## Request paths

| Path | Today | Target |
|---|---|---|
| Tenant update | `POST /webhook/{token}` → Redis config → tenant_dp → handler [NOW] | same, but handler = engine interpreter; long work → stream [P5] |
| Controller update | same endpoint, main_dp [NOW] | + `/describe` wizard, `/god` panel [P3] |
| Owner edit | `/api/bots*` initData → PG write → cache invalidate [NOW] | + draft→publish transaction, history append [P3/P8] |
| Scheduled send | — | worker claims job (SKIP LOCKED) → stream → fan-out [P5→A1] |
| AI call | — | engine emits `ai_call` effect → worker executes, meters, circuit-breaks [P4] |
| Analytics | — | events → Redis stream → rollup worker → daily stats table [P7] |

## Scaling tiers

| Tier | Bots | What changes | Infra |
|---|---|---|---|
| **T0 [NOW]** | ~100s | single app container | compose as-is |
| **T1 [P5]** | 1–5k | worker entrypoint split out; Redis Streams queues; app ×2 | same compose, +worker service |
| **T2** | ~30k | app ×N behind LB; per-chat ordering locks; per-bot rate limits; partitioned event tables; PG read replica for analytics; Redis HA (Sentinel/managed) | still PG+Redis only |
| **T3 (future)** | 100k+ | cell sharding by bot_id — documented as an extension, not designed | out of scope |

Honest constraints: at T2, multi-replica webhook processing needs the
per-chat lock (§03) because Telegram may deliver a bot's updates on
parallel connections; a single replica keeps today's await-before-200
ordering for free.

## Deep dives

| Doc | Covers |
|---|---|
| [01-data-model.md](docs/architecture/01-data-model.md) | Full table inventory, ERD, drafts/versions, partitioning |
| [02-flow-engine.md](docs/architecture/02-flow-engine.md) | Node graph schema, pure interpreter, effects, adapters, dry-run |
| [03-runtime-and-scaling.md](docs/architecture/03-runtime-and-scaling.md) | Webhook path, queues, scheduler, worker, ordering, limits, degradation |
| [04-security.md](docs/architecture/04-security.md) | Secret vault, auth, audit, redaction, outbound allowlist |
| [05-evolution-map.md](docs/architecture/05-evolution-map.md) | Phase→component map, decision log (ADRs), extension points (MCP, WhatsApp, Mini App) |

## Feature coverage map (vault → architecture)

- § A runtime (A1 scheduler, A3 commands, A4 variables, A6 media, A7
  blocklist) → 02 nodes + 03 scheduler
- § B templates → 01 `templates`/`template_versions`, draft→publish
- § C engine → 02 (whole doc)
- § D AI gateway → 03 worker/outbound + 04 vault + 01 ledger/ai_usage
- § E dashboard → 01 history + 02 preview API (M3a)
- § F GOD/ops → 03 observability + 01 audit
- § G monetization → 01 ledger/purchases (metering is the spine)
- § M front door → 02 (wizard targets the same schemas), 05 evolution
