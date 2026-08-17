# TME — Deployment & CI/CD

This document describes how TME ships to your server and, crucially, **how it
stays fully isolated from the two existing 20k-user apps on the same host.**

## TL;DR model

```
 Pull request ──────────────► CI (lint + tests + migration check)
 Push to main ──────────────► CI ─► Deploy to  TEST  (test bot token,  tme-test)
 Push tag  vX.Y.Z ──────────► CI ─► [manual approval] ─► Deploy to PRODUCTION (tme-prod)
```

- **One repo, one `main` branch.** No forks, no long-lived parallel branches.
- Test vs prod is separated by **GitHub Environments**, each with its own scoped
  secrets (your two bot tokens live here, never in git).
- Production requires a **manual approval** (a required-reviewer rule on the
  `production` environment). You promote the *exact commit* already tested.

---

## Isolation guarantees (why your other apps are safe)

Every deploy runs Docker Compose with a **dedicated project name** (`tme-test` /
`tme-prod`). Compose only ever manages resources namespaced under that project:

| Resource   | How it's isolated                                                        |
| ---------- | ------------------------------------------------------------------------ |
| Containers | Named/labelled under the compose project; other stacks are invisible.    |
| Network    | A private per-project network (`tme-prod_default`, ...).                  |
| Volumes    | Auto-prefixed (`tme-prod_pgdata`, `tme-test_pgdata`); no sharing.         |
| Postgres/Redis | **No host ports published** — reachable only inside the project net. |
| App port   | Bound to `127.0.0.1:<APP_PORT>` for your existing reverse proxy.          |
| Cleanup    | `--remove-orphans` and `docker image prune` are **filtered to this project only**. No global prune, no `docker stop`/`down` of anything else. |

The deploy also lives under a **dedicated Linux user** and its own directory.
As long as `tme-test` and `tme-prod` use **different `APP_PORT`s** (defaults 8081
/ 8080) and different project names, the two TME environments are isolated from
each other too.

> The workflows never run `docker system prune`, `docker compose down` on other
> projects, or touch anything outside the TME project namespace.

---

## One-time setup

### 1. Server prerequisites (already met in your case)

- Docker Engine + Compose plugin.
- A dedicated deploy user (you created this) whose login shell can run `docker`
  (member of the `docker` group).
- Your reverse proxy (nginx/Traefik/…) forwarding the public HTTPS hostnames to
  `127.0.0.1:<APP_PORT>` for each environment.

### 2. GitHub Environments

Create two environments under **Settings → Environments**: `test` and
`production`. On **`production`**, enable **Required reviewers** (add yourself) —
this is the approval gate.

### 3. Secrets & variables (per environment)

These match the secrets you already created. Set them in **both** the `test`
and `production` environments (with per-environment values).

**Secrets** (Settings → Environments → *env* → Secrets):

| Secret            | What it is                                                            |
| ----------------- | --------------------------------------------------------------------- |
| `SSH_HOST`        | Server hostname or IP.                                                 |
| `SSH_USER`        | The dedicated deploy user.                                             |
| `SSH_PRIVATE_KEY` | Private key (PEM) for that user. Public key in its `authorized_keys`.  |
| `BOT_TOKEN`       | The **controller** bot token for this environment (test vs prod bot).  |
| `WEBHOOK_SECRET`  | Long random string; verified on every incoming Telegram update.        |
| `DATABASE_URL`    | `postgresql+asyncpg://tme:<pw>@postgres:5432/tme` — host **must** be `postgres`. |
| `REDIS_URL`       | `redis://redis:6379/0` — host **must** be `redis`.                     |
| `KNOWN_HOSTS`     | *(optional, recommended)* Output of `ssh-keyscan -H <host>`. If absent, the deploy falls back to `ssh-keyscan` (trust-on-first-use). |

**Variables** (Settings → Environments → *env* → Variables):

| Variable           | Required? | Default (test / prod)   | Purpose                             |
| ------------------ | --------- | ----------------------- | ----------------------------------- |
| `WEBHOOK_BASE_URL` | **YES**   | — (deploy fails if unset) | Public HTTPS origin Telegram calls back on, e.g. `https://tme-test.yourdomain.com`. Not secret, so it lives here. |
| `SSH_PORT`         | no        | `22`                    | SSH port.                           |
| `DEPLOY_PATH`      | no        | `tme-test` / `tme-prod` | Dir on server (relative to `$HOME`).|
| `COMPOSE_PROJECT`  | no        | `tme-test` / `tme-prod` | Compose project name.               |
| `APP_PORT`         | no        | `8081` / `8080`         | Loopback port for the reverse proxy. **Must differ per env & not clash with the other apps** (`sudo ss -ltnp \| grep :<port>`). |
| `APP_URL`          | no        | —                       | Shown as the deploy's environment URL in GitHub. |

> **You still need to add `WEBHOOK_BASE_URL`** as a variable in each environment
> — it's the one required value not in your current secret list.

### 4. How the `.env` is assembled (you don't write one)

Unlike a single blob secret, the deploy **builds `/…/.env` on the server** from
the individual secrets/variables above. It also:

- maps `BOT_TOKEN` → `MAIN_BOT_TOKEN` (what the app reads),
- normalizes `DATABASE_URL` to the `postgresql+asyncpg://` driver if you omitted it,
- **parses** `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` out of
  `DATABASE_URL` to configure the dedicated Postgres container (so they always
  match — you don't set them separately),
- **refuses to deploy** unless `DATABASE_URL`'s host is `postgres` and
  `REDIS_URL`'s host is `redis`. This is the hard guard that keeps TME pointed
  at its own dedicated containers and never at another app's datastore.

The resulting server `.env` looks like:

```dotenv
MAIN_BOT_TOKEN=<from BOT_TOKEN secret>
WEBHOOK_BASE_URL=<from WEBHOOK_BASE_URL variable>
WEBHOOK_SECRET=<from WEBHOOK_SECRET secret>
DATABASE_URL=postgresql+asyncpg://tme:<pw>@postgres:5432/tme
REDIS_URL=redis://redis:6379/0
CONFIG_CACHE_TTL=3600
LOG_LEVEL=INFO
POSTGRES_USER=tme          # parsed from DATABASE_URL
POSTGRES_PASSWORD=<pw>      # parsed from DATABASE_URL
POSTGRES_DB=tme            # parsed from DATABASE_URL
```

> Note: the DB password is parsed by splitting on `@`, so it must not contain a
> literal `@` (URL-encode it as `%40` if it does). Hex/base64url passwords from
> `openssl rand` are fine.

> Generate secrets with e.g. `openssl rand -hex 32`. Keep the test and prod
> `WEBHOOK_SECRET` / DB passwords distinct.

---

## How a deploy runs

1. **CI** (`_reusable-ci.yml`): `ruff check`, `ruff format --check`, `pytest`
   (against ephemeral Postgres+Redis services), and `alembic upgrade head`.
2. **Deploy** (composite action `.github/actions/deploy`):
   - configures SSH (strict host key checking when `KNOWN_HOSTS` is set),
   - `rsync`es the repo to `$DEPLOY_PATH` (excludes `.git`, `.venv`, `.env`),
   - writes `.env` on the server from the `DOTENV` secret (`umask 077`),
   - `docker compose -p <project> --env-file .env -f docker-compose.prod.yml up -d --build --remove-orphans`,
   - polls `http://127.0.0.1:<APP_PORT>/health` and **fails the deploy** (dumping
     `app` logs) if it never becomes healthy.

The app container runs `alembic upgrade head` on start, then `uvicorn` behind
`--proxy-headers`.

---

## Promoting to production

```bash
git checkout main && git pull
git tag v0.1.0          # choose the tested commit
git push origin v0.1.0  # triggers Deploy • production → waits for your approval
```

Approve the run in the Actions tab (or the environment's pending-deployment
prompt). To roll back: `git push origin v0.1.0-hotfix` from a known-good commit,
or re-run an older tag's deploy.

---

## Local development

See `README.md`. In short: `docker compose up -d` (the *dev* compose with host
ports), `cp .env.example .env`, `uv sync --extra dev`, `uv run alembic upgrade
head`, `uv run tme-api`.

---

## First-deploy checklist

- [ ] `test` and `production` environments created; `production` has a required reviewer.
- [ ] Per-environment secrets set: `SSH_HOST`, `SSH_USER`, `SSH_PRIVATE_KEY`, `BOT_TOKEN`, `WEBHOOK_SECRET`, `DATABASE_URL`, `REDIS_URL` (+ optional `KNOWN_HOSTS`).
- [ ] Per-environment variable `WEBHOOK_BASE_URL` set (required — not in your current secret list).
- [ ] `DATABASE_URL` host is `postgres` and `REDIS_URL` host is `redis` (the deploy hard-fails otherwise).
- [ ] `APP_PORT` chosen per env and **confirmed free** (`sudo ss -ltnp | grep :<port>`).
- [ ] Deploy user is in the `docker` group and its public key is authorized.
- [ ] Reverse proxy forwards each public hostname → `127.0.0.1:<APP_PORT>`.
- [ ] Push to `main` → watch **Deploy • test** go green.
- [ ] Tag `vX.Y.Z` → approve → **Deploy • production**.
