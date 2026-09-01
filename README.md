# TME — Telegram Multi-Tenant Bot Engine

An enterprise-grade platform that serves **30,000+ custom Telegram bots from a
single codebase and a single webhook** — no per-bot process, no per-bot polling
loop, no memory bloat.

A **Main Bot** (controller) lets users create and manage their own **cloned
bots** (tenants). Every tenant's behaviour is driven by a JSON config that lives
in Postgres, is cached in Redis, and is executed by one shared aiogram
dispatcher.

➡️ See the **[ROADMAP.md](ROADMAP.md)** for the prioritized, phased feature plan.

---

## Architecture at a glance

```
                    ┌──────────────────────────────────────────┐
   Telegram  ─────► │  POST /webhook/{bot_token}   (FastAPI)     │
   (all bots)       └───────────────┬──────────────────────────┘
                                    │  verify secret • route by token
                 ┌──────────────────┴───────────────────┐
                 │                                       │
        token == MAIN_BOT_TOKEN                  any tenant token
                 │                                       │
                 ▼                                       ▼
         main_dp (controller)                    tenant_dp  (ONE dispatcher
         /start, Create Bot,                      for ALL tenant bots)
         managed_bot                           │
                                          ConfigMiddleware injects config
                                                        │
                                                        ▼
                                         dynamic_router (JSON flow engine)
                                          reads welcome_message / menu_buttons

   Config path:   Postgres ──(cache miss)──► Redis ──(cache hit)──► handler
   FSM state:     Redis, keyed  state:{bot_id}:{chat_id}:{user_id}  (isolated)
```

**Why it scales:** a `Bot` object is just a token + a *shared* HTTP session;
handlers and FSM live in one dispatcher. Per-bot cost is a few hundred bytes of
JSON in Redis — not a process or a resident object graph.

---

## Tech stack

| Concern              | Choice                                        |
| -------------------- | --------------------------------------------- |
| Package management   | [`uv`](https://docs.astral.sh/uv/)            |
| Lint / format        | [`ruff`](https://docs.astral.sh/ruff/) (strict) |
| Telegram framework   | `aiogram` 3.x (async)                         |
| Webhook gateway      | `FastAPI` + `uvicorn`                         |
| Database             | PostgreSQL + SQLAlchemy 2.0 (async) + Alembic |
| Cache / FSM state    | Redis (`redis-py` async)                      |
| Language             | Python 3.12+                                  |

---

## Prerequisites

- **Python 3.12+** — `uv` will fetch it automatically if your system Python is older.
- **`uv`** — https://docs.astral.sh/uv/getting-started/installation/
- **Docker** (for local Postgres + Redis).
- A public HTTPS URL for Telegram webhooks (e.g. [ngrok](https://ngrok.com/) or
  [cloudflared](https://developers.cloudflare.com/cloudflare-tunnel/)).

---

## Quick start (local)

### 1. Configure

```bash
cp .env.example .env
# Edit .env: set MAIN_BOT_TOKEN, WEBHOOK_BASE_URL (your public HTTPS URL),
# and WEBHOOK_SECRET (any long random string).
```

### 2. Start Postgres + Redis

```bash
docker compose up -d          # postgres on :5432, redis on :6379
docker compose ps             # both should be "healthy"
```

### 3. Install dependencies

```bash
uv sync --extra dev           # creates .venv (Python 3.12) and installs everything
```

### 4. Create the database schema

```bash
# Generate the initial migration from the models, then apply it:
uv run alembic revision --autogenerate -m "initial schema"
uv run alembic upgrade head
```

### 5. Expose your local port to the internet

Telegram must reach your machine over HTTPS. In a separate terminal:

```bash
ngrok http 8000
# Copy the https URL it prints into WEBHOOK_BASE_URL in .env, then restart the app.
```

### 6. Run the gateway

```bash
uv run tme-api                # serves on http://0.0.0.0:8000
# On startup it auto-registers the Main Bot's webhook at
#   {WEBHOOK_BASE_URL}/webhook/{MAIN_BOT_TOKEN}
```

Message your Main Bot with `/start` — you should get a greeting and a
**➕ Create New Bot** button.

---

## How it works

### The universal webhook (`src/tme/main.py`)
`POST /webhook/{bot_token}` verifies the `X-Telegram-Bot-Api-Secret-Token`
header, parses the raw JSON into an aiogram `Update`, and dispatches it to the
controller or tenant dispatcher based on the token. It always returns `200`
(logging errors) so Telegram never retry-storms a broken update, and processes
updates inline so per-chat ordering is preserved for FSM correctness.

### DB → RAM caching (`src/tme/core/cache.py`)
`get_bot_config(token)` is a **read-through cache**: Redis hit → parse & return;
miss → load from Postgres, validate, write to Redis with a TTL. Unknown tokens
are *negatively* cached briefly to prevent cache-penetration hammering the DB.
Edit a config → call `invalidate_bot_config(token)`.

### Isolated FSM state (`src/tme/core/storage.py`)
Uses aiogram's `DefaultKeyBuilder(prefix="state", with_bot_id=True)`, so every
FSM key embeds **both** the bot id and the user/chat id
(`state:{bot_id}:{chat_id}:{user_id}`). Millions of users across thousands of
bots can never collide.

### Dynamic router (`src/tme/routers/dynamic.py`)
One router for all tenants. `ConfigMiddleware` injects each bot's `bot_config`;
handlers render `welcome_message` and `menu_buttons` straight from it — no
hard-coded per-bot text.

---

## Managed Bots — official Bot API support

Managed Bots are an **official, documented** part of the Telegram Bot API,
added in **Bot API 9.6 on April 3, 2026**, and are **fully supported by
aiogram 3.x** — this project runs aiogram 3.30.0, which ships native types and
methods for them.

### The update — `ManagedBotUpdated`

> This object contains information about the creation, token update, or owner
> update of a bot that is managed by the current bot.

It's delivered as `Update.managed_bot` and carries just two fields:

| Field      | Type   | Description                                       |
| ---------- | ------ | ------------------------------------------------- |
| `user`     | `User` | The user who created / now owns the managed bot.  |
| `bot_user` | `User` | The bot managed by the current bot.               |

Subscribe to it via `allowed_updates=["message", "callback_query", "managed_bot"]`
(the gateway already does this in `src/tme/main.py`).

### The methods

| Method                                                       | Description                                                     |
| ------------------------------------------------------------ | --------------------------------------------------------------- |
| `getManagedBotToken(user_id)`                                | Return the token of a managed bot → `String`.                   |
| `replaceManagedBotToken(user_id)`                            | Revoke the current token and generate a new one → new `String`. |
| `getManagedBotAccessSettings(user_id)`                       | Return access settings → `BotAccessSettings`.                   |
| `setManagedBotAccessSettings(user_id, is_access_restricted, added_user_ids)` | Change access settings → `True`.                    |

Reference: <https://core.telegram.org/bots/api#managedbotupdated> ·
<https://core.telegram.org/bots/api#getmanagedbottoken>

### Implementation status

The platform uses the **native aiogram path** end-to-end:

- `src/tme/routers/main_bot.py` handles `managed_bot` updates via the typed
  `@main_router.managed_bot()` decorator (`ManagedBotUpdated`) and fetches the
  token with the native `aiogram.methods.GetManagedBotToken`.
- `src/tme/services/managed_bots.py` contains only the provisioning helpers
  (`provision_managed_bot`, `register_webhook`) — the legacy raw-dict
  `handle_managed_bot` and the hand-rolled method were removed (see
  `SUB-PHASES.md` → S0.1).
- `Update.managed_bot` (`ManagedBotUpdated | None`) is parsed directly by
  aiogram's typed model in the gateway.

If a Bot API build lacks these methods, the calls raise
`TelegramBadRequest`; they're caught and logged so the gateway stays up. The
provisioning pipeline (persist bot → prime cache → register webhook) works
independently and can be driven directly via `provision_managed_bot(...)`.
---

## Project structure

```
tme/
├── pyproject.toml            # uv project + dependencies
├── ruff.toml                 # strict lint/format config
├── docker-compose.yml        # local Postgres + Redis
├── alembic.ini               # migrations config (URL injected from settings)
├── .env.example              # copy to .env
├── migrations/               # Alembic (async env.py)
└── src/tme/
    ├── config.py             # pydantic-settings
    ├── main.py               # FastAPI universal webhook gateway  ← entrypoint
    ├── core/
    │   ├── redis_client.py   # shared async Redis pool
    │   ├── cache.py          # DB → RAM config cache (read-through)
    │   ├── storage.py        # RedisStorage with per-(bot,user) key isolation
    │   ├── bot_registry.py   # shared-session Bot instances (LRU)
    │   ├── dispatchers.py    # main_dp + tenant_dp assembly
    │   └── logging.py
    ├── database/
    │   ├── base.py           # DeclarativeBase + timestamps
    │   ├── engine.py         # async engine + session_scope
    │   └── models.py         # User, Bot, BotConfig
    ├── middlewares/
    │   └── config_middleware.py   # injects bot_config into every tenant update
    ├── routers/
    │   ├── main_bot.py       # controller: /start, Create New Bot
    │   └── dynamic.py        # JSON flow engine for tenants
    ├── services/
    │   └── managed_bots.py   # provisioning + webhook registration
    └── schemas/
        └── bot_config.py     # validated JSON flow schema
```

---

## Development

```bash
uv run ruff format .          # format
uv run ruff check --fix .     # lint (autofix)
uv run alembic revision --autogenerate -m "msg"   # new migration after model changes
uv run alembic upgrade head   # apply migrations
```

---

## Production notes (beyond the MVP)

- **Encrypt bot tokens at rest** (they're plaintext in the MVP) — e.g. app-level
  envelope encryption or `pgcrypto`.
- Put the gateway behind a reverse proxy / multiple uvicorn workers; the design
  is stateless per-request, so it scales horizontally.
- Consider offloading heavy handlers to a task queue and acking Telegram fast.
- Add rate-limiting and per-tenant quotas.
