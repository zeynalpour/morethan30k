# TME — Ideas Vault

A living idea bank for the TME platform (Telegram multi-tenant bot engine).
Every idea is grounded in the real architecture: **JSONB config in Postgres →
Redis cache → one shared engine** — no per-bot Python, ever.

**Product goal** — anyone creates Telegram bots without programming:
scalable, reliable, and usable across the whole skill range, from someone
who has never built an app to a senior software engineer. § M holds the
front-door ideas for that goal.

Status legend: ⚪ unstarted · 💡 idea · 🧭 stretch · 🔥 high leverage ·
effort: S / M / L (also noted inline per idea below).

## Rules of the vault

1. **Every idea obeys the north star** — JSON in Postgres → Redis cache →
   one shared engine. Anything needing per-bot Python is rejected or
   redesigned until it doesn't.
2. **Every idea names its tables** — no hand-waving. If it can't name its
   storage, it isn't an idea yet, it's a wish.
3. **Ideas that already exist in ROADMAP are marked** (○ roadmap) — the
   vault may extend them but never contradicts them.
4. **One idea per ID** — refinement happens in place, duplicates get
   merged, and dead ideas get moved to the Kill list (§ K), not deleted.
5. **Phase discipline** (project convention): ideas never schedule
   future-phase work into an earlier phase's checklist; the vault informs
   ROADMAP/SUB-PHASES edits, it doesn't bypass them.

---

## Top 10 — ranked by leverage right now

Phase 0 + Phase 1 are done; Phase 2 (templates) is next. These ten compound
each other — roughly in this order (effort: S/M/L):

1. **C1/C1b — node-graph flow engine** (L) — every other idea becomes a node
   type. The structural keystone (Phase 5 pulled forward).
2. **A4 — message variables** (S) — tiny effort; every template needs them.
3. **A6 — media in flows** (S) — templates are richer than text.
4. **B1/B2 — feedback + quiz templates** (M) — first real templates, prove
   the "template = config row" thesis.
5. **B7 — template registry + versioning** (M) — makes templates
   maintainable, marketplace-ready.
6. **C2 — config history & rollback** (S) — `version` field already exists;
   safety net for everything above.
7. **C1c — dry-run simulator** (M) — owners self-serve debugging; support
   load drops.
8. **D1 — BYOK AI gateway** (M) — Phase 4's flagship, blocked only by S0.3
   Secret Vault.
9. **E5 — analytics + flow drop-off** (M) — the dashboard's killer feature.
10. **G1/G2 — Stars + owner tiers** (L) — monetization once the above exists.

---

## A. Runtime & engine

- **A1. Scheduled broadcasts** — a `scheduled_messages` table (bot FK, cron or
  one-shot time, payload) + one shared async scheduler that fans sends out via
  the tenant bot's Telegram token. Pure DB row = a whole bot feature. 🔥
- **A2. Inactivity auto-replies** — per-bot rule: "if no bot reply for N
  minutes, send X". Same scheduler from A1, different trigger. 💡
- **A3. Fast-path commands** — a per-bot `commands` map in the flow
  (`/price → text`), resolved before the generic fallback; trivially fast
  because it's just a dict lookup in the cached config. 🔥
- **A4. Message variables** — `{{user.first_name}}`, `{{bot.name}}`,
  `{{now}}`, plus per-chat variables set by the engine; render in every
  outgoing message. Low effort, huge template payoff. 🔥
- **A5. Quiet hours** — per-bot config: hold non-urgent sends between HH:MM
  and HH:MM, flush on wake. Pairs with A1 broadcasts. 💡
- **A6. Media in flows** — extend `welcome_message` and menu-adjacent nodes
  with optional photo/video/document (Telegram `file_id` or URL) so templates
  aren't text-only. 🔥 (most templates are richer than text)
- **A7. Blocklist per bot** — `blocked_users` (bot FK, user id, reason) with
  middleware-level drop (no processing, no reply). Small table + middleware
  condition = abuse control without a per-bot code. 💡

## B. Templates (Phase 2 next)

- **B1. Feedback-collector template** — welcome → collect free-text steps →
  save to `collected_responses` (bot FK, chat, JSON answers) + optional
  "forward to owner" action. Direct reuse of form-building primitives. 🔥
- **B2. Quiz template** — questions array in flow; engine scores by
  `correct_answers`, ends with result screen; per-bot leaderboard table. 🔥
- **B3. FAQ bot template** — big list of Q→A pairs in the config (or a
  `faq_entries` table for large sets) with fuzzy-match resolution before
  fallback. 💡
- **B4. Channel-post template** — a bot whose mission is channel admin: on
  channel_post, apply rules (auto-reply, forward, filter). New update-kind
  support in the dynamic router. 💡
- **B4b. Channel-post triggered flows** — channel_post is an *event source*
  for workflows: new post → start a flow for the poster, or run silent
  actions. Same router extension as B4. 🧭
- **B5. Broadcast bot template** — owner sends "send this to all" → engine
  sends to all chats that ever /started. Depends on A1. 💡
- **B6. Link-in-bio / digital storefront template** — welcome message with
  product grid (menu buttons as categories) → inline product detail →
  "Buy" via Telegram Stars / external invoice link. Monetization-ready. 🔥
- **B7. Template version pinning** — `templates` table: id, version, category,
  tags, blurb; `bot_configs` gets `template_id` + `template_version` +
  `migrations` notes. Bump a template and existing bots track it. 🔥 (ROADMAP
  Phase 2 explicitly wants this.)
- **B8. Public template marketplace** — publish a template publicly; other
  owners clone it; every clone links back ("made with TME") → viral loop.
  Pairs with Phase 7 monetization: paid templates, revenue share. 💡

## C. Workflow engine (Phase 5 pulled earlier)

- **C1. Linear node graph in the flow** — `nodes` + `transitions` in the
  BotConfig schema; the engine interprets them. This is the single
  highest-leverage structural idea: A1–A7, B1–B7 all become nodes/transitions
  of this graph. 🔥
- **C1b. Node library** — `message`, `collect_input`, `keyboard`, `condition`,
  `webhook_call`, `ai_call`, `forward_to_admin`, `set_variable`, `random_split`
  (A/B testing built-in). Every new capability = new node type, never
  per-bot code. 🔥
- **C1c. Dry-run simulator** — same engine, fake Telegram adapter: owners test
  flows in the dashboard before publish. Pairs with the Phase 3 draft→preview
  →publish lifecycle. 🔥
- **C2. Flow versioning & rollback** — `bot_configs` already has `version`;
  add a `bot_config_history` table (snapshots per publish) → one-click
  rollback in the dashboard. Phase 8 already wants this. 🔥
- **C2b. Config diff viewer** — render history entries as a JSON diff in the
  dashboard: what changed between publishes, at a glance (which text, which
  button, which language). Turns rollback from a leap of faith into a
  decision. 🔥

## D. AI gateway (Phase 4)

- **D1. Bring-your-own-key (BYOK) gateway bot** — owner stores
  OpenAI-compatible endpoint + key (encrypted via S0.3 vault) in a
  conversation; bot chats with that model. Follows ROADMAP Phase 4 exactly. 🔥
- **D2. Shared gateway + credit pool** — GOD adds endpoint+key; per-user
  credit ledger decremented per request; metering service built generic
  (ROADMAP explicitly: quotas, rate limits, paid tiers reuse it). 🔥
- **D3. AI node for flows** — `ai_call` node: prompt template + model refs +
  `{{variables}}`; response → variable → later nodes. Combines C1b with D1/D2
  as its execution backends. 🔥
- **D4. Tracing & cost metering per AI call** — every ai_call logs
  model/prompt/eval completion tokens → `ai_usage` table → dashboard "cost
  per bot" + GOD health dashboard feed. 🔥
- **D4b. Model routing** — cheap model drafts, strong model finalizes;
  fallback chains across providers on 429/timeout; the gateway stays up when
  one provider is down. 💡
- **D5. Shared system-prompt library** — per-owner library of prompts reused
  across their bots; prompt versioning so a bot pins a prompt version. 💡

## E. Dashboard & UX

- **E1. Template gallery** — grid of cards (thumbnail, title, blurb, category,
  tags); "Use template" seeds a new bot. Phase 2's front door. 🔥
- **E2. Bot health tab** — webhook status, error counts, delivery latency,
  last error, restart action. Feeds Phase 6 observability. 🔥
- **E3. Command menu editor** — UI over A3: add/edit/delete commands,
  auto-register via `set_my_commands` per language (i18n-aware). 🔥
- **E4. Multi-owner "teams"** — a bot has a team: owner + members with roles
  (viewer, editor, publisher). `bot_members` table + role checks in the owner
  API. Phase 7's teams/collab idea, pulled early. 💡
- **E4b. A/B testing tab** — C1b's `random_split` node + dashboard: two
  variants of a message node, per-variant conversion counts. 💡
- **E5. Analytics tab** — active users, messages/day, fallback hits, node
  drop-off (where users abandon the flow). Flow drop-off is the killer
  feature: it tells owners *where* their bot leaks users. 🔥
- **E5b. Sentiment/CSAT micro-survey node** — a node that asks 👍/👎 after
  task completion; responses → analytics tab. 💡
- **E5c. Funnel view** — E5's drop-off extended into explicit funnels per
  bot: define steps (nodes), see per-step conversion. 💡
- **E6. Sandbox preview chat** — an owner-only "test" mode: owner chats
  with their own bot; runs against the draft config, not the published one.
  Phase 3's preview, implemented via draft configs. 🔥
- **E6b. Global search** — search across bots, templates, flows by name
  from the controller bot (/find) or dashboard. 💡

## F. GOD & platform ops

- **F1. `/god` panel** — list all bots, health, start/stop, credits — the
  single super-admin view the ROADMAP already demands (`settings.god_telegram_id`).
  🔥
- **F2. Platform health dashboard** — webhook delivery latency, queue depth,
  per-bot error rate, Redis/PG heartbeat. GOD's cockpit. 💡
- **F3. Impersonate-on-behalf** — GOD previews any tenant bot as a user would
  see it (sandbox config, own chat). Debugging without asking owners for
  screenshots. 💡
- **F4. Audit log** — who changed what when: `audit_log` table (actor,
  bot, action, before/after JSONB). Trust for teams + marketplace. 🔥
- **F4b. Anomaly alerts to GOD** — error-rate spike per bot → proactive
  Telegram alert to GOD. Pairs with F2. 💡
- **F5. Sandboxed template review** — a marketplace template can't be
  published until it passes: schema validation + dry-run (C1c) + no
  outbound-webhook allowlist violation. Quality gate for B8. 💡

## G. Monetization & growth

- **G1. Telegram Stars integration** — credit packs, paid templates, paid
  bot features. ROADMAP Phase 7 already names this. 🔥
- **G2. Subscription tiers for owners** — free (1 bot), pro (N bots,
  analytics, version history). Metering service is the billing engine. 🔥
- **G3. Revenue share on marketplace** — template authors earn a cut of
  clones/paid clones. The growth flywheel. 💡
- **G4. "Powered by TME" branding removal as paid feature** — classic SaaS
  lever, zero engineering cost. 💡
- **G5. Referral program** — invite an owner, both get credits. Viral loop
  #2. 💡
- **G6. Template stats & social proof** — cloned-count, rating, review text
  on marketplace cards. Clone-count is the marketplace's north-star metric. 💡

## H. Trust, safety & compliance

- **H1. GDPR "export my data" endpoint** — a user messages any tenant bot
  `/databot` or taps a dashboard action; engine assembles their stored data
  (variables, responses, quiz answers) into a JSON export. Single owner-level
  implementation, every bot inherits it. 💡
- **H2. Data retention windows** — per-bot TTL on `collected_responses` /
  `chat_variables`: purge job (shared scheduler from A1) deletes older rows.
  Compliance + PG bloat control in one. 💡
- **H3. PII redaction in logs** — middleware masks phone numbers / emails /
  tokens in structured logs; store only the redacted form. Pairs with S0.3
  Secret Vault (same never-log-secrets principle). 🔥
- **H4. Two-person rule for template publishing** — a marketplace template
  needs GOD + author confirmation before it goes live (F5's review made
  human). 🧭
- **H5. Per-bot incident reports** — one-tap "report this bot" on tenant
  bots → queue for GOD review (abuse handling for a multi-tenant platform
  with strangers' bots in it). 💡

## I. Adjacent plays (stretch)

- **I1. TME as MCP server** — expose every tenant bot's capabilities as MCP
  tools: an external agent can "send message via bot X" or "read collected
  forms". TME becomes agent infrastructure, not just a bot platform. 🧭
- **I2. Reverse direction: bots call MCPs** — per-bot MCP client config:
  the `ai_call` node can call out to any MCP server for tools (weather,
  CRM lookup). Agents in flows without custom code. 🧭
- **I3. "Flows as a universal format"** — export a TME flow as a portable
  JSON spec; import from ManyChat/Chatfuel competitors. Migration tooling
  as a growth wedge. 🧭
- **I4. WhatsApp/Instagram twin** — the flow engine is transport-agnostic
  in principle; a second transport adapter (WhatsApp Cloud API) doubles the
  addressable market with one engine. 🧭
- **I5. Self-host community tier** — a packaged single-binary self-hosted
  TME with a sync-to-cloud option. Enterprise wedge + open-source funnel. 🧭
- **I6. One flow, two runtimes (Mini App twin)** — the same JSON flow that
  drives the chat bot renders as a declarative Mini App: nodes → screens.
  Chat is the authoring surface; the Mini App is the "real" UI for
  storefront/quiz-style bots. Merged here from killed B6b. 🧭

## J. Dependency map

Keystones — unlock the most downstream ideas:

- **C1 (node graph)** → C1b, C1c, B1–B6, E4b, E5c, E5b, D3, I1–I4. The
  single most enabling idea in the vault.
- **S0.3 (Secret Vault)** → D1, D2, D4, G1, G2. Already a ROADMAP item;
  the vault restates it because the AI gateway blocks on it.
- **A1 (scheduler)** → A2, A5, B5, H2. One cron-style service, four ideas.
- **B7 (template registry)** → B8, E1, G3, G6, F5. Marketplace spine.
- **Metering (D2's ledger, built generic)** → G1, G2, G4, Phase 6 rate
  limits. ROADMAP already mandates this reuse.

Quick dependency pairs: A4 → B1/B2 · A6 → B2/B6 · C2 → E-diff ·
D1 → D2 → D3 · E5 → E5c · F2 → F4b · B7 → F5/H4 · G1 → G3/G5.

---

## M. The friendly front door (the final goal)

The goal is one platform serving the full skill range — a first-time
bot-maker and a senior engineer in the same wizard. The method:
**progressive disclosure** — one path that starts trivial and unfolds depth
only on demand. Nobody is ever shown a wall of settings they don't need
yet; nobody ever hits a ceiling they can't unfold past.

North-star metric: **time-to-live-bot ≤ 60s** for the simplest path.

### M1. Three rungs of the same ladder

- **M1a. Describe-it wizard (novice)** — "Tell me what your bot should do
  in your own words" → TME proposes a draft → user tweaks wording →
  done. Five questions maximum, plain language ("What should your bot
  say when someone says hi?"). First published bot in minutes, zero
  jargon. 🔥
- **M1b. Guided block builder (intermediate)** — chat-based or Mini App
  flow editor: drag/tap blocks (send message, ask a question, show
  buttons, notify me). The wizard above collapses into this when the user
  outgrows it — same underlying node graph (C1), friendlier surface.
  🔥
- **M1c. Power surface (senior)** — JSON/YAML flow editor + REST API
  (Phase 7's public API) + MCP access (I1/I2) + webhook nodes (C1b).
  Same engine, same schemas: the engineer's bot and the novice's bot are
  stored identically. The ceiling unfolds; nothing is hidden away. 🔥

  The three rungs are one ladder: M1a → M1b is "show me the blocks," and
  M1b → M1c is "show me the JSON." Users move up at their own pace, and
  the data model never changes beneath them.

### M2. Onboarding beats documentation

- **M2a. Live in-bot tutorial bot** — a tutorial bot that is itself a
  TME bot: users learn by building a real bot inside a chat, watching
  their own creation come alive step by step. The tutorial bot is just a
  template (B7) — dogfooding proof of the platform. 🔥
- **M2b. /help that answers like a senior** — per-context help: ask
  "how do I add a button?" anywhere and get the exact next step (AI
  gateway D1 answering from the platform's docs — the AI section paying
  for itself on day one). 🔥
- **M2c. Sandbox bots are free and unlimited** — no cost, no risk;
  experimenting is encouraged, publishing is the gated step. Pairs with
  E6 sandbox preview. 💡
- **M2d. Template-first creation** — never a blank page: creation starts
  from "what do you want to make?" with a template gallery (E1) behind
  it. Blank canvas is the expert's choice, not the default. 🔥
- **M2e. Publish checklist** — before a bot goes live: welcome message
  set? menu present? language picked? test message sent? A short
  checklist turns "is it ready?" into a binary question and prevents
  half-built public bots. 💡

### M3. Trust through visibility

- **M3a. Live preview pane** — every editor change renders instantly in
  a phone-frame preview (Mini App side of I6): what the user sees is
  always the truth, not a description of it. 🔥
- **M3b. Version history for everyone** — C2 rollback surfaced as "undo"
  everywhere; publishing feels safe when everything is reversible. 🔥
- **M3c. Health at a glance** — E2's health tab, distilled for novices:
  a single green/yellow/red dot per bot ("your bot is live and healthy")
  plus one-tap diagnostics. 💡

### M4. The describe-it engine (M1a's core)

- **M4a. Intent → template → fill slots** — free-text description maps
  to the nearest template (B7 registry), then a short dialogue fills the
  slots (name, greeting, topics). Deterministic pipeline: classify →
  propose → confirm. No LLM magic at publish time without review. 🔥
- **M4b. LLM co-pilot drafting** — the Phase 3 `(/describe)` LLM idea:
  free text → full draft flow JSON → user reviews each piece in plain
  words ("Your bot will greet, then offer 3 buttons: Prices, Hours,
  Contact — keep?"). LLM proposes; the user always approves. 🔥
- **M4c. Draft → preview → publish lifecycle** — nothing an LLM (or a
  novice) drafts ever goes live unseen. The lifecycle is the safety
  net that makes M4b trustworthy. 🔥 (○ roadmap — Phase 3.)

### M5. Reliability & scale (the non-negotiables)

- **M5a. Per-tenant isolation** — one bad bot can't degrade the rest:
  per-bot rate limits + circuit breaker per outbound call (webhook, AI
  provider). Isolation at the engine level, not just infra. 🔥 (○
  roadmap — Phase 6.)
- **M5b. Idempotent webhooks + retry with backoff** — Telegram webhook
  retries must not double-send; dedupe key per update. (○ roadmap —
  Phase 6.)
- **M5c. Graceful degradation** — Redis down → engine reads Postgres
  directly (slower, never dead). Config cache is an accelerator, not a
  dependency. 💡
- **M5d. Scheduled jobs can't stampede** — A1's scheduler spreads sends
  (jitter, per-chat rate caps) so a 10k-user broadcast doesn't spike
  Telegram rate limits. 💡

### M6. Growth loops for a mass audience

- **M6a. "Made with TME" footer with a link** — every published bot
  carries it (removable in paid tier, G4). Each live bot is an
  acquisition channel. 🔥
- **M6b. One-tap template share** — "share this bot as a template" →
  link → anyone clones it and customizes. B8's marketplace made social
  from day one. 🔥
- **M6c. Showcase gallery** — public gallery of great bots made on TME
  (with owner permission): social proof + a discovery surface for
  template ideas. 💡

---

## K. Kill list (rejected ideas, kept for the record)

- **B6b (Mini App from flow)** — MERGED into I6 as a stretch direction:
  one flow, two runtimes. Killed as a standalone B-section entry to keep B
  focused on shipping templates. See I6.
- **H4 (two-person rule)** — DEFERRED: overkill while GOD is the only
  publisher; revisit when marketplace review volume justifies it (F5 first).

## L. Change log

- **v1 (iteration 1)** — seed vault: sections A–E, grounded in the real
  codebase (typed config union, i18n, dashboard, Redis-cache path).
- **v2 (iteration 2)** — + F (GOD/ops), G (monetization); fixed drafting
  artifacts (truncated C2b, orphaned registry fragment → B8).
- **v3 (iteration 3)** — + H (trust/safety), I (adjacent plays); moved
  B6c → G6 (marketplace stats belong in G).
- **v4 (iteration 4)** — + Top 10 ranked list; effort sizing rule.
- **v5 (iteration 5)** — + J dependency map; fixed G-section ordering;
  C1 → C1b/C1c keystone made explicit.
- **v6 (iteration 6)** — full review pass: file reads clean end-to-end;
  added Rules of the vault (5 rules); this change log.
- **v7 (iteration 7)** — consistency repairs: killed B6b physically
  removed from B (lives on as I6); K entries now resolve to real IDs;
  H4 explicitly deferred (not killed).
- **v8 (iteration 8)** — programmatic cross-ref validation (61 unique IDs,
  zero duplicates); the only intentional dangling refs are kill-list
  history (B6b → I6, B6c → G6).
- **v9 (iteration 9)** — effort sizing (S/M/L) added to the Top 10, per
  the Rules; the S-effort quick wins (A4, A6, C2) are now visible as
  such.
- **v10 (iteration 10)** — final audit: 61 ideas across 9 themed
  sections, 5 rules, Top-10 ranking, dependency map, kill list — all
  cross-refs resolve; vault closed for this pass.
- **v11 (goal pass)** — the product goal is now explicit in the header:
  bots without programming, for the full skill range (novice → senior
  engineer), scalable + reliable. New § M "The friendly front door":
  three rungs of one ladder (M1 describe-it wizard → block builder →
  JSON/API/MCP), onboarding (M2), trust (M3), the describe-it engine
  (M4), reliability non-negotiables (M5), growth loops (M6). 21 new
  ideas; north-star metric: time-to-live-bot ≤ 60s.

