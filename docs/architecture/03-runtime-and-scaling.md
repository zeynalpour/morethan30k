# 03 — Runtime & Scaling

Deep dive of [ARCHITECTURE.md](../../ARCHITECTURE.md). How updates flow,
how work is queued, how the system stays correct at 30k bots — with
today's single-container deployment as the starting point.

Statuses: **[NOW]** shipped today · **[T x]** scaling tier ·
**[P x]** roadmap phase.

## Process topology

One image, two entrypoints (T1+):

```
tme app      : uvicorn tme.main:app     — webhook GW, owner API, engine
tme worker   : tme-worker               — scheduler, queues, AI calls,
                                          rollups, retention, retries
```

[NOW] everything runs in the single `app` container; the worker is
split out at T1 by adding a `worker:` service to the same compose file —
same image, same env, different command. Zero code forks: the worker
entrypoint imports the same services the app uses.

## The update path (hot loop)

```
Telegram → POST /webhook/{token}
  → verify secret header (constant-time)          [NOW]
  → resolve bot: main token? → main_dp            [NOW]
               else tenant_dp                      [NOW]
  → ConfigMiddleware: Redis get botcfg:{token}     [NOW]
      miss → PG select bots+config, validate,
             cache (TTL) — negative-cache unknowns [NOW]
  → I18nMiddleware: stored pref → tg language       [NOW]
  → engine.step(flow, chat_state, event)          [P5] (pure, ~µs)
  → adapter executes send/answer_callback inline   [P5]
  → long effects (ai/webhook/broadcast) → Redis Stream
  → await processing; 200                          [NOW]
```

**Why await-before-200 stays:** Telegram serializes per-chat delivery
on our ack — the FSM/chat-state correctness depends on it. In exchange
the handler must stay light: everything slower than ~a send goes to a
stream. [NOW — preserved as invariant]

**T2 change:** with N app replicas Telegram may open parallel
connections per bot; per-chat ordering needs a short-lived Redis lock
(`chat:{bot}:{chat}`, TTL ≲ 5s) acquired before `engine.step` —
serializes same-chat updates across replicas without a global queue.
Single-replica keeps it free. A replica that can't get the lock in
~2s acks 200 and re-enqueues the update (idempotent via update_id).

## Queues: Redis Streams (T1+)

No Kafka/NATS. Streams chosen: consumer groups (competing consumers),
at-least-once with `XACK`, pending-entry reclaim for dead workers
(`XAUTOCLAIM`), and it's already in the stack. Layout:

| Stream | Contents | Consumer |
|---|---|---|
| `sq:effects` | send-jobs from engine (broadcast chunks, delayed, ai_call, webhook_out) | worker `effects` group |
| `sq:events` | chat_events analytics batches | worker `analytics` group |
| `sq:retry` | failed jobs with backoff metadata | worker `retry` group |

- **Idempotency:** every stream entry carries `idempotency_key`
  (`update_id` or `job_id:chat_id`); executors keep a short Redis
  `done:{key}` set — retries never double-send (M5b).
- **Backpressure:** each stream has a max length (approximate trim);
  if `sq:effects` is full, producers fall back to writing the job row
  to PG (`jobs`, status=pending) — the stream is a fast lane, PG the
  durable spine. Workers drain PG backlog on idle.
- **Poison handling:** attempts++ per reclaim; `max_attempts` →
  status=dead + GOD alert (F4b). Dead jobs are inspectable + replayable
  from PG.

## Scheduler (A1 spine) [P5]

- `jobs` table is the source of truth (01); the worker runs a ticker:
  claim `WHERE status=pending AND run_at <= now()` with
  `FOR UPDATE SKIP LOCKED` (multiple workers coexist safely).
- Kinds: `scheduled_send`, `broadcast` (parent → per-chat children with
  jitter — M5d), `inactivity` sweep (A2), `retention` (H2, also prunes
  `chat_events` partitions + creates future ones), `rollup` (nightly
  `daily_bot_stats`), `partition_maintenance`.
- Clock skew: jobs carry `run_at` in UTC; ticker leases (claimed_by +
  claimed_at with expiry) so a crashed worker's claims return to
  pending automatically.
- At-least-once + idempotency keys ⇒ a send may be attempted twice,
  never delivered twice.

## Rate limits & isolation (M5a, P6)

- **Per-bot outbound budget:** token-bucket in Redis
  (`rl:{bot}`, refill ~25/s ~ matching Telegram's per-bot limit of
  ~30 msg/s). Engine effects acquire or the job delays itself
  (jitter) — a hot bot never eats the shared session.
- **Per-tenant (owner) quotas:** the credit-ledger metering service
  doubles as the quota check (roadmap: built generic, reused for
  billing AND limits).
- **Circuit breakers** per outbound dependency (AI provider, webhook
  targets): consecutive-failure counters in Redis → half-open probes.
  One dead provider slows its own lane; the platform stays up.
- **Inbound:** webhook secret failures are rate-limited per IP
  (Cloudflare + nginx layer); unknown-token floods die at the
  negative cache [NOW].

## Degradation ladder (M5c)

| Failure | Behavior |
|---|---|
| Redis down | configs read straight from PG (validated, un-cached); chat state falls back to a PG `chat_state` mirror (T2); analytics buffered in-process rings, flushed on recovery. Slower, never dead. |
| PG down | cached configs keep serving (TTL refresh fails gracefully); new bot resolution 503s; owner API returns 503; tenant traffic on known bots continues. |
| Stream full | spill-to-PG fallback (above); GOD alert. |
| Worker dead | leases expire → jobs return to pending; streams reclaim via XAUTOCLAIM. No lost work. |
| Telegram 5xx | adapter retries with backoff + jitter; persistent failures land in dead-letter with cause; per-bot disable flag if token revoked. |

Design rule: **every dependency is either a fast lane with a slow
fallback (Redis) or a source of truth with a cache (PG).** Nothing is
both.

## Observability (F2, P6)

- Structured logs [NOW] + request-scoped `update_id`/`bot_id` fields.
- Metrics exported per bot: msgs in/out, engine step latency, effect
  queue depth, breaker states, negative-cache hit rate, cache hit
  ratio, PG/Redis pool saturation. GOD health dashboard (F2) reads
  these; anomaly alerts (F4b) are thresholds on the same series.
- Health endpoints: `/health` liveness [NOW] + `/health/ready`
  (checks PG + Redis round-trips) for compose/LB.

## Capacity notes (30k promise, honest math)

- 30k bots ≠ 30k rps: only a fraction of bots are hot. Sustained
  target ~200–500 updates/s total, bursts ×10.
- Config cache: 30k × ~4 KB ≈ 120 MB Redis — comfortable.
- Bot registry LRU (2048 [NOW]) covers hot bots; misses are cheap
  (thin object, shared session) — no change needed at T2.
- `chat_events` at 500/s ≈ 43M rows/day worst-case → partitioned
  monthly + BRIN + rollups; raw retention 90 days (H2 default).
- App replica sizing: uvicorn workers × containers; scale horizontally
  — statelessness is the invariant that makes that safe (chat-state
  lock above is the only coordination).
