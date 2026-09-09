# 02 — Flow Engine

Deep dive of [ARCHITECTURE.md](../../ARCHITECTURE.md). The engine is the
product: one pure interpreter executing JSON node graphs for every
tenant bot — 30k bots, zero per-bot code. This doc defines the graph
schema, the interpreter loop, and why it stays testable and transport-agnostic.

Statuses: **[NOW]** shipped today · **[P x]** lands in phase x.

## Current state → target

[NOW] The "engine" is the dynamic router: per-`bot_type` handlers
(welcome+menu, hello, echo) reading `bot_config` from Redis, all copy
through `localize()` (12 languages). It IS a flow engine in miniature —
the target generalizes it, not replaces it: the typed config union
(`BotConfigUnion`) keeps growing variants until `nodes`/`transitions`
absorb them all.

## Node graph schema (C1)

Lives inside `flow` JSONB (`BotConfigBase` gains `nodes`, `start_node`,
`transitions` — `extra=allow` already tolerates them today, so old rows
keep parsing [NOW]). Minimal v1 node:

```jsonc
{
  "bot_type": "generic",
  "version": 3,
  "start_node": "welcome",
  "nodes": {
    "welcome": {
      "type": "message",
      "text": "Hi {{user.first_name}} 👋",        // A4 variables
      "media": {"kind": "photo", "ref": "<file_id|url>"},  // A6
      "buttons": [{"text": "🛍 Shop", "goto": "shop"}]     // inline
    },
    "shop": {
      "type": "keyboard",
      "prompt": "Pick a category",
      "options": [{"label": "Prices", "goto": "prices"}]
    },
    "prices": {"type": "message", "text": "…"},
    "ask_name": {
      "type": "collect_input", "var": "name",
      "prompt": "What should I call you?", "validate": "nonempty"
    }
  }
}
```

v2 adds (roadmap P5): `condition` (branch on variables/language),
`random_split` (A/B, E4b), `webhook_call`, `ai_call` (D3),
`forward_to_admin` (bridge), `set_variable`, `delay`, `csat` (E5b).

## Node types (v1 ship list, C1b)

| Node | Behavior | Emits |
|---|---|---|
| `message` | render text+vars, attach media, inline buttons | `send` |
| `keyboard` | prompt + option buttons → `goto` on tap | `send` |
| `collect_input` | free text → `var`, validation, re-ask on fail | `send` ×2 |
| `condition` | if/else on expressions over `vars` + user/lang | — |
| `random_split` | hash(chat_id, salt) % N — deterministic A/B | — |
| `set_variable` | assign literal or template render | — |
| `delay` | hand off to scheduler, resume later (A5 quiet hours) | `schedule` |
`ai_call`/`webhook_call`/`forward_to_admin` are v2 effects executed by
the worker (never inline in the webhook process — see 03).

## The interpreter (pure core)

```python
def step(flow, chat_state, event) -> StepResult:
    """Pure: (graph, state, event) -> (effects, new_state).

    No I/O. No clock. No Telegram. Renders text (i18n + {{vars}}),
    picks the next node, returns effects. Adapters do the rest.
    """
```

- **State** is `{node_id, vars, history}` per chat, in Redis
  (`chat:{bot}:{chat}` hash, TTL 24h) — same home as aiogram FSM today.
- **Event** is normalized: `{kind: start|text|callback|postback…, text,
  data, user, chat, language_code}`.
- **Effects**: `send`, `answer_callback`, `schedule`, `ai_call`,
  `webhook_call`, `forward`. The engine RETURNS them; it never executes
  Telegram I/O itself — the adapter in the webhook process executes
  `send`/`answer_callback` immediately (fast, ordered), and enqueues the
  rest for the worker.
- **Determinism**: `random_split` hashes `(chat_id, salt)` — replays and
  dry-runs reproduce exactly. `{{now}}` renders at adapter time, so a
  dry-run never lies about send-time values.
- **Safety**: every `goto` validated against the node set at publish
  time (draft validation) — a published graph can't dead-end.
- **i18n**: node text may carry `translations` overlays exactly like
  today's `localize()` chain (user lang → en → base). One mechanism,
  extended to nodes.

## Adapters (why it's transport-agnostic)

1. **Telegram adapter** [NOW-ish]: aiogram `Bot` from the LRU registry,
   executes `send`/`answer_callback`, maps API exceptions to effects
   (e.g. blocked bot → `disable_bot` flag for GOD).
2. **Dry-run adapter** (C1c): fake executor records effects; dashboard
   replay shows exact messages, buttons, branching. Same engine —
   because the core is pure, testing needs no mocks.
3. **Mini App adapter** (I6, M3a): renders nodes → screens over the
   same `flow`; buttons → same `goto` graph. Author once, run anywhere.
4. **WhatsApp adapter** (I4, stretch): implements the same effect
   interface against WhatsApp Cloud API. No engine changes.

## Draft → preview → publish (M4c)

`bot_configs.status='draft'` → owner previews in dashboard (dry-run
adapter + E6 sandbox chat against the DRAFT config token-scoped) →
publish = atomic status flip + `bot_config_history` append (01) +
cache invalidation. The 60-second wizard (M1a) writes a draft, walks the
owner through 3–5 plain-language confirmations, and publishes — the
wizard is just a fast, guided path over the same lifecycle.

## Guardrails

- Node count cap per flow (e.g. 200) — template hygiene + blast radius.
- Text render length caps; media `ref` must be file_id or https URL.
- `validate` is a whitelist of named validators, never raw regex from
  users (ReDoS + comprehension).
- Effects budget per update (e.g. 10 sends) — a loop bug can't spam a
  chat; engine returns `budget_exceeded` → fallback node.
- Every graph validation error is i18n'd for the owner (the wizard's
  error copy comes from the same tables).
- Deep-graph recursion capped (max hops per update, e.g. 25) — cycles
  end at a terminal or the cap, whichever first.
