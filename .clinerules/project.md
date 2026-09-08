# TME — Agent Rules

Cline is the VS Code coding agent for this repo. Follow these rules whenever
working inside the **TME** (Telegram Multi-Tenant Bot Engine) codebase.

## Project intent

A single service that serves **30,000+ telegram bots** from one webhook + one
codebase. Every bot is a **JSON workflow** saved in Postgres, cached in Redis,
executed by one shared dispatcher. There is **no per-bot Python, process, or
polling loop** — a new bot is a new DB row, not new code.

> **North star (from ROADMAP.md):** "No per-bot Python. Every bot — regardless
> of type — is a JSON workflow saved in Postgres, cached in Redis, and executed
> by one shared engine."

## Stack (don't guess)

- **Language:** Python `>=3.12` (`requires-python` in `pyproject.toml`).
- **Env / deps:** `uv` (see `pyproject.toml`, `uv.lock`). Always `uv sync` after
  changing dependencies; never hand-edit `uv.lock`.
- **Web:** FastAPI (`src/tme/main.py`, single universal endpoint
  `POST /webhook/{bot_token}`).
- **Bot framework:** aiogram 3.x — two dispatchers: `main_dp` (controller bot)
  and `tenant_dp` (shared by all cloned bots).
- **DB:** SQLAlchemy 2.x async + `asyncpg`; models in `src/tme/database/models.py`.
- **Cache/FMS:** Redis (`src/tme/core/cache.py`, `src/tme/core/storage.py`).
- **Migrations:** Alembic (`migrations/versions/`).

## Commands

Run from the repo root:

```bash
uv sync --extra dev          # install deps incl. dev tooling
uv run pytest                  # run the whole test suite (tests/)
uv run pytest tests/<file>     # run one test file
uv run ruff check src/ tests/  # lint
uv run ruff check --fix .      # lint + autofix
uv run ruff format --check .   # formatting check
uv run alembic upgrade head    # apply migrations
```

## Code conventions (match the existing code)

- **Type annotations everywhere** (including return types). Use
  `from __future__ import annotations` at the top of modules.
- **Docstrings** on every module, class, function, and non-trivial helper.
- **No per-bot Python.** Behaviour lives in config JSON (`BotConfig.flow`),
  not in per-bot handlers. Dispatch on `bot_type` (see `schemas/bot_config.py`).
- Respect `ruff.toml` and `pyproject.toml` (ruff/pytest config). Keep
  `pythonpath = ["src"]` semantics — imports are `from tme...`.
- Distinguish the **controller** router (`routers/main_bot.py`) from the
  shared **tenant** router (`routers/dynamic.py`).
- Multi-tenancy correctness is paramount: never share FSM/cache state across
  bots or users (see `core/storage.py` key isolation).

## Workflow discipline

- Reference the phase you're working on from `ROADMAP.md` / `SUB-PHASES.md`
  (e.g. `S0.2`), and keep those docs in sync when sub-phases land.
- Before finishing: tests green (`uv run pytest`) **and** ruff clean
  (`uv run ruff check src/ tests/`).
- Only touch files required by the task; avoid unrelated reformatting or API
  churn.
- Commit messages follow conventional style referencing the phase, e.g.
  `feat(db): add BotType enum + per-type config union (S0.2)`.
- Keep secrets out of code/commits — they live in env / `.env` / GitHub
  environment secrets only.
