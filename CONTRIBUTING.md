# Contributing to TME

Thanks for helping build a platform where **anyone** can create Telegram
bots without programming. This guide tells you how to contribute, how we
avoid stepping on each other, and how the repo guarantees bad changes
don't land.

## Read this first

- [ROADMAP.md](ROADMAP.md) — what's planned and in which phase.
- [SUB-PHASES.md](SUB-PHASES.md) — the current phase's detailed checklist.
- [ARCHITECTURE.md](ARCHITECTURE.md) + [docs/architecture/](docs/architecture/) —
  how the system works and why (data model, engine, scaling, security).
- [IDEAS.md](IDEAS.md) — the idea vault (things NOT yet scheduled).

**The north star:** no per-bot Python. Every bot is a JSON config in
Postgres → Redis cache → one shared engine. If your change needs
per-tenant code, it will be rejected — propose a node type, a table, or
a service instead (see [docs/architecture/02-flow-engine.md]).

## The two rules that prevent chaos

1. **Nothing lands on `main` without a green PR.** Every push to `main`
   auto-deploys to the test environment minutes later — `main` is
   always deployable, enforced by CI, not by trust.
2. **One issue → one branch → one PR.** Small, focused, linked. If your
   change needs two unrelated commits landing, it's two PRs.

## How to contribute (the loop)

### 1. Find or open an issue

Check [open issues](https://github.com/zeynalpour/morethan30k/issues)
first — duplicates get closed. None fits? Open one using the bug or
feature template. For features: check ROADMAP/IDEAS first and say which
phase/idea it belongs to; out-of-roadmap features need a maintainer's
`accepted` label before you build them.

### 2. Claim it

Assign yourself (or comment "I'm taking this"). One contributor per
issue — this is how we don't interrupt each other. If an issue has an
active assignee and no activity for 14 days, ask before taking over.

### 3. Branch from `main`

```bash
git checkout main && git pull
git checkout -b feat/<short-slug>   # fix/…, docs/…, ci/…, refactor/…
```

Branch naming: `feat/`, `fix/`, `docs/`, `ci/`, `refactor/` + slug.
The prefix tells reviewers what kind of review to expect.

### 4. Make the change

- **Phase discipline:** build what the issue scopes — no drive-by
  future-phase work, no refactors "while you're in there."
- **Dependencies:** if you touch `pyproject.toml`, run `uv lock` and
  commit `uv.lock` — CI installs with `--frozen` and fails on a stale
  lock.
- **Migrations:** generate with Alembic (`uv run alembic revision
  -m "…"`); NEVER edit an already-applied migration — add a new one.
  CI runs `alembic upgrade head` from scratch, so a broken migration
  fails the build.
- **Tests:** behavior change → test change. The suite runs against real
  Postgres + Redis in CI; locally see "Running the full suite" below.
- **Secrets/tokens:** never commit real tokens or keys; tests use the
  dummies from `tests/conftest.py` / CI env.
- **Commits:** Conventional Commits — `feat(scope): …`, `fix(scope): …`,
  `docs: …`, `ci: …`, `refactor: …`, `test: …`, `chore: …`. Reference
  the phase or sub-phase when it has one (e.g. `feat(phase2): …`).

### 5. Verify locally before pushing

```bash
uv run ruff check --fix . && uv run ruff format .
uv run pytest
```

Both must pass — CI runs exactly this, plus `alembic upgrade head`.

### 6. Open the PR

Push your branch, open a PR against `main` using the PR template
(summary, what/why, test plan, issue link). Then:

- CI runs automatically on every PR (lint + format + tests on real
  services + migration check).
- **Red CI = fix it or the PR can't merge.** Update your branch and
  push; CI re-runs.
- A maintainer reviews. Expect review on: correctness, phase scope,
  adherence to the architecture docs, and test coverage.
- Merge is **squash** — your branch's history becomes one commit on
  `main`; keep commits reasonably clean but don't sweat rebasing.

### 7. After merge

Your change auto-deploys to the **test** stack (`tme-test`, port 8081).
If your feature is user-visible, verify it there before opening a
release issue for `tme-dev`/`tme-prod`.

## How we don't make wrong changes (the gates)

| Gate | What it catches | Where |
|---|---|---|
| Issue + claim | duplicate/contradictory work | before you write code |
| Scope in issue | phase discipline violations | PR review |
| ruff lint + format | style, dead code, import errors | CI `lint` job |
| pytest (real PG + Redis) | behavior regressions, i18n drift, schema breakage | CI `test` job |
| `alembic upgrade head` | broken/stale migrations | CI `test` job |
| `uv sync --frozen` | stale lockfile, undeclared deps | CI (both jobs) |
| Maintainer review | architecture violations, security, scope | PR |
| Test-env deploy | anything CI can't catch | after merge |

None of these rely on anyone being careful. That's the point.

## Running the full suite locally

Unit tests run anywhere; the integration tests want real Postgres +
Redis. Easiest path — the compose stack (it's what CI mirrors):

```bash
docker compose -p tme-test --env-file .env -f docker-compose.prod.yml up -d postgres redis
uv sync --extra dev
uv run pytest
```

(The integration suite self-skips when the infra is unreachable —
don't be fooled by a green local run if it skipped.)

## Project layout

```
src/tme/
  main.py            webhook gateway (single entrypoint)
  core/              dispatchers, cache, registry, i18n, logging
  routers/           main_bot (controller), dynamic (tenant engine)
  middlewares/       config + i18n injection
  schemas/           bot_config — the typed config union
  services/          managed bots, auth, dashboard, user language
  database/          models, engine, migrations via alembic/
tests/               unit + integration (real PG/Redis)
docs/architecture/   deep dives (start: ARCHITECTURE.md)
```

## Questions / stuck?

Open a `question` discussion or comment on the issue you're working.
For architecture "why" questions, the ADR table in
[docs/architecture/05-evolution-map.md](docs/architecture/05-evolution-map.md)
is the source of truth — if reality disagrees with it, that's a bug in
one of them, and we fix the doc (or the code) in the same PR.
