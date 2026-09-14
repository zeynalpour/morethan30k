# Operations & Security Hardening — Deferred Checklist

Deferred deliberately: **feature work comes first** (owner's call, Sep 2026).
This file is the parking lot for the production-readiness work that must land
**before TME holds real user data**. Each item is written so it can be picked
up cold: why it matters, the exact command(s), and what "done" means.

Status key: `[ ]` open · `[~]` partially done · `[x]` done
Priority: **P0** = data-loss / public-exposure risk · **P1** = blast radius /
compliance · **P2** = hygiene

Snapshot of findings (verified on the live host, 2026-09-12):

- No backup tooling at all: no `pg_dump`/pgBackRest, no cron, no WAL
  archiving. Five data volumes (`tme-dev_pgdata`, `tme-test_pgdata`,
  `tme-prod_pgdata`, legacy `morethan30k_pgdata`), zero backups.
- One shared DB owner role (`tme`) is used by the app, migrations **and**
  human access. No read-only role, no blast-radius separation.
- A legacy stack (`tme-postgres` / `tme-redis`, pre-compose, project
  `morethan30k`) still runs and publishes Postgres on **`0.0.0.0:5432`**.
- Per-stack `.env` files on disk read by a shared deploy user
  (`tmedeploy`); dev DB password is 3 characters.
- No DB audit trail: `log_connections` / `log_statement` / `pgaudit` off.
- Postgres `ssl = off` (fine inside the compose network + behind SSH;
  mandatory for any cross-boundary connection).

---

## P0 — Data loss & exposure

- [ ] **Backups + proven restore.**
  Automated `pg_dump` (daily, compressed, per stack) shipped off-host to
  object storage, **plus** WAL archiving for PITR (pgBackRest or
  `archive_command` → S3/B2). Untested backups do not count: a restore drill
  into a scratch container is part of "done".
  *Done when:* a documented RPO/RTO exists, cron/systemd timer runs green for
  a week, and a restore of `tme-prod_pgdata` into a scratch stack succeeded.

- [ ] **Back up `VAULT_MASTER_KEY` offline, per stack — before vault
  activation.**
  Once `bots.token` is cleared (`scripts/vault_backfill.py
  --apply --clear-plaintext`) the vault is the *only* copy of a bot's token:
  lose the master key and every tenant bot must be re-created in BotFather.
  Store each stack's key (32 bytes, base64) outside the host and outside the
  deploy user's reach, and note which key belongs to which stack.
  *Done when:* each of dev/test/prod has its key stored offline and a
  documented restore path; rotation follows the re-wrap-DEKs procedure in
  `docs/architecture/04-security.md` (§ Secrets).

- [ ] **Close the public Postgres port.**
  The legacy `tme-postgres` publishes `0.0.0.0:5432`. Confirm what still uses
  it, migrate the data if it matters, then stop and remove the legacy stack.
  Check: `docker ps --format '{{.Names}} {{.Ports}}' | grep 0.0.0.0`
  *Done when:* no container publishes a DB port on a non-loopback interface.

- [ ] **Least-privilege DB roles.**
  Split the single `tme` owner role:
  `tme_app` (runtime: DML only), migration/owner role (DDL, used only by
  deploy), `tme_ro` (humans/analytics: `GRANT pg_read_all_data`).
  pgAdmin connects as `tme_ro`; prod access is read-only by default.
  *Done when:* the app runs as `tme_app`, `\du` shows three distinct roles,
  and pgAdmin no longer uses an owner credential.

## P1 — Blast radius & auditability

- [ ] **Secrets manager.** Move per-environment secrets out of on-disk
  `.env` files into a manager (Vault / Doppler / cloud SM), rotate the dev
  and prod DB passwords (dev's is 3 chars), and stop sharing one deploy
  account. No shared human credentials.
  *Done when:* secrets are injected at deploy time, `.env` holds no
  credentials, rotation is a documented one-command operation.

- [ ] **DB audit logging.** Enable `log_connections`, `log_disconnects`,
  `log_statement = 'ddl'` (and `pgaudit` if available) with retention, so
  "who changed what" is answerable.
  *Done when:* connection + DDL events land in retained logs.

- [ ] **Access path: bastion/VPN → broker or managed DB.**
  Today: SSH tunnel to a container IP (Option A in the owner's notes) — fine
  for a handful of operators, but the container IP changes on every rebuild.
  Target for prod: managed Postgres with private networking (RDS/Cloud SQL/
  Neon) **or** an identity-based broker (Teleport Database Access, AWS SSM
  port-forwarding, Cloudflare Access) giving per-person identity,
  short-lived certs and session recording.
  *Done when:* prod DB access is identity-based, not a shared static
  credential over a tunnel.

- [ ] **TLS for any cross-boundary connection.** `ssl = on` + `hostssl`
  entries (and `sslmode=require` in clients) once a client reaches Postgres
  over a network hop. The SSH tunnel covers the current setup; direct
  private-network access does not.
  *Done when:* every non-loopback connection is TLS-encrypted.

## P2 — Hygiene

- [ ] **Prod access gating.** No direct prod writes from laptops; test stack
  (`tme-test`) is the staging gate; break-glass path documented and logged.
- [ ] **Retire stale worktrees/volumes** (`morethan30k_pgdata`,
  `morethan30k_tme_pgdata`) once the legacy stack is confirmed unused.
- [ ] **Postgres version/patch cadence** for the `postgres:16-alpine` images
  (pinned pulls + a documented upgrade path).
- [ ] **pgAdmin connection runbook** — document the working recipe (SSH
  tunnel tab, `ssl mode = disable` inside the tunnel, read-only role,
  container-IP refresh command) so it is not re-derived each time.

---

## Notes for whoever picks this up

- The compose files intentionally publish **no** DB/Redis ports
  (`docker-compose.prod.yml`); keep it that way — expose via loopback only if
  a stable host address is genuinely needed (`127.0.0.1:5433/5434/5435` for
  dev/test/prod, since the legacy container holds 5432).
- Deploys run as `tmedeploy` for test/prod (`/home/tmedeploy/tme-{test,prod}`)
  and as `tme-ai` for dev; only the deploy user can read the test/prod `.env`.
- Related: `docs/architecture/04-security.md` (vault, auth matrix, SSRF) —
  this file covers the *operations* gap around it.
