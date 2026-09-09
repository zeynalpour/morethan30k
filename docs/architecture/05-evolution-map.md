# 05 — Evolution Map

Deep dive of [ARCHITECTURE.md](../../ARCHITECTURE.md). How the current
codebase evolves into the target — phase by phase, component by
component — plus the decision log and the extension points stretch ideas
plug into.

## Phase → component map

| Phase | Adds (components) | Tables | Touches |
|---|---|---|---|
| **P0 rest: S0.3** | `SecretVault` service (encrypt/decrypt/wrap); token_hash routing | `secrets` | `bots`, webhook resolver, adapters |
| **P2 templates** | `TemplateRegistry` (seed/clone/pin); type-picker gets "from template" | `templates`, `template_versions` | provisioning flow, dashboard E1 |
| **P3 GOD + /describe** | `GODPanel` router handlers; `DescribeWizard` (deterministic M4a); draft lifecycle service | `bot_config_history`, `audit_log` | main_bot router, dashboard |
| **P4 AI gateway** | `AIGatewayService` (BYOK + shared pool) in worker; `ai_call` effect executor; metering service | `credit_ledger`, `ai_usage`, (purchases later) | engine effects, worker, dashboard D4 |
| **P5 engine v1–v2** | pure `engine.step` + Telegram adapter (refactor of dynamic router); streams + worker split | `jobs` | tenant_dp, new `tme-worker` entrypoint |
| **P5 v3 visual editor** | block builder UI → same JSON; dry-run adapter | — | dashboard (M1b/M3a) |
| **P6 ops** | rate-limit service, circuit breakers, readiness, metrics, anomaly alerts | `blocked_users`, `incident_reports` | middlewares, worker, GOD panel |
| **P7 ecosystem** | public REST API (API keys), Stars payments, teams | `purchases`, `bot_members`, `api_keys` | owner API (same schemas!), billing |
| **P8 dashboard ext.** | tabs shell, history/rollback UI, analytics tab | — | dashboard only |
| **§ M front door** | wizard = guided draft + publish (no new components!) | — | main_bot + dashboard |

Note the shape: **P2–P4 add services and tables; P5 refactors the
router into the engine; § M adds no components at all** — it's flows
over what exists. That's the progressive-disclosure payoff.

## Decision log (ADR summary)

| # | Decision | Rationale | Revisit when |
|---|---|---|---|
| ADR-1 | Single webhook endpoint, token-routed | one origin, one Cloudflare config; registry makes per-bot objects free | never — scales to cells (T3) |
| ADR-2 | Redis read-through config cache [NOW] | ~all traffic never touches PG; negative cache kills floods | never |
| ADR-3 | FastAPI CRUD API = management plane (no direct Supabase) | S0.4 resolution — single source of truth + cache invalidation | never |
| ADR-4 | Node graph INSIDE `flow` JSONB (not a separate graph store) | configs stay atomic, versionable, cacheable units; templates clone with one copy | if per-flow realtime collaborative editing is needed |
| ADR-5 | Pure engine + adapters (effects) | dry-run/Mini App/WhatsApp for free; testable core | never |
| ADR-6 | Redis Streams, not Kafka/Celery | already in stack; consumer groups + reclaim cover the needs at ≤T2 | T3 cells or >10k jobs/s |
| ADR-7 | Append-only ledger/history/audit | money and truth never mutate; rollback = append | never |
| ADR-8 | Drafts are `status` on `bot_configs` | no drift between draft and published families | never |
| ADR-9 | Same image, worker entrypoint | no deploy fork; worker uses identical services | if resource profiles diverge hard |
| ADR-10 | `templates` separate from `template_versions` | marketplace card queries stay cheap; flow content is versioned | never |

## Extension points (stretch ideas)

The architecture reserves seams, not implementations:

- **MCP server (I1):** owner API surface (`/api/bots*`) is the same one
  MCP tools would wrap — expose `list_bots`, `patch_flow` as MCP tools
  and external agents manage bots through the audited path. No new
  core code; an adapter around the API.
- **Bots calling MCPs (I2):** `webhook_call` generalizes to a
  `tool_call` node — the outbound safety rules (04) apply unchanged.
- **WhatsApp twin (I4):** new adapter implementing the effect interface;
  `bots` gains a `transport` column + webhook route; flow JSON unchanged.
- **Mini App runtime (I6):** dry-run adapter IS the Mini App backend —
  nodes→screens; the dashboard preview and the shipped Mini App share
  one renderer.
- **Self-host tier (I5):** everything above already collapses to
  compose + 2 entrypoints; packaging is a docs task, not re-arch.

## Migration order (what to build first)

1. **S0.3 vault** — everything in P4 and G1 blocks on it (already
   scheduled [P0]).
2. **P5 engine v1 refactor** — before templates ship en masse, so
   templates seed node graphs, not legacy flow fields. (C1 pulled
   forward per the vault Top-10.)
3. **P2 templates + history** — registry, clone, pin; config history
   arrives with publish lifecycle (they're one transaction).
4. **P3 wizard (M4a deterministic first)** — LLM co-pilot (M4b) only
   after the draft→publish safety net exists.
5. **P4 AI gateway** — worker split lands here if not sooner.
6. Then P6 hardening → P7 ecosystem → § M polish.

Rationale: each step unlocks revenue or the next phase's foundation;
none requires rework of the previous (the ADRs guarantee it).

## Non-goals (explicit)

- No multi-region, no cells, no sharding at this horizon (T3 doc-only).
- No per-tenant custom domains (single webhook origin is the point).
- No custom code execution (sandboxed user Python etc.) — the north
  star forbids it; power users get API/MCP instead.
- No real-time collaborative flow editing.
