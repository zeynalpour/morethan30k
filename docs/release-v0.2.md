# Release v0.2.0 — first version (target: 20 September 2026)

Deadline-driven: the first version must be live for the meeting with **David
Plakon (Product, warp.dev)** on **20 Sep 2026**. Everything below is scoped to
that date, not to the roadmap's natural pace. Roadmap phases past this release
are explicitly **out of scope**.

Priority: **P0** = blocks the release · **P1** = demo quality · **P2** = nice to have.

## What ships

Phases 0–2 in one release (prod is 12 days and three phases behind v0.1.0):

- Secret Vault **activated** (tokens encrypted at rest, hash routing live)
- Dashboard mini-app: settings, bot list, enable/disable, type switch
- Starter bots & templates: registry + picker + versioned re-clone + the shared
  `steps` engine (Hello World, Echo, Feedback collector, Quiz, Simple form)
- Multilanguage: per-bot translations, `/language` picker, main-language
  selector, single-language mode
- Bot lifecycle: liveness detection, archive sync, `/mybots` correctness
- The fixes from live testing: blank copy, template actions, editor remount,
  flow-step add/remove/reorder

**Out of scope (deferred):** Phase 3 GOD panel & `/describe` wizard, Phase 4 AI
gateway, marketplace, `collected_responses` (issue #21) unless the schedule
holds — see P2.

## P0 — blocks the tag

- [ ] **Vault activation** (issue #28): hash routing + backfill + `0006`.
      Dev first, verified end to end, then test/prod.
- [ ] **Prod backups + proven restore**: `pg_dump` on a schedule with retention
      **and one rehearsed restore**. Migrations run automatically at container
      start; without a backup a bad one is unrecoverable. No tag without this.
- [ ] **Per-stack vault keys**: dev (done) · test · prod — generated per stack,
      stored off-box by the owner. Losing one loses that stack's tokens.
- [ ] **Unbind the legacy Postgres from `0.0.0.0:5432`** (cheap exposure fix,
      same session as backups).
- [ ] **Manual verification pass on the test stack** at the exact commit to be
      released (owner).

## P1 — demo quality

- [ ] Tag `v0.2.0` → `deploy-prod.yml` → prod verified healthy.
- [ ] Prod smoke test: create a bot from a template, message it, see the
      dashboard, switch language, see a quiz run end to end.
- [ ] Demo content prepared **on prod**: one English bot + one Persian bot,
      each from a template, with a working menu and translations.
- [ ] `README` says what the product is and how it runs (first thing an
      evaluator reads).
- [ ] A short "what it is / what changed in this version" note for the meeting.
- [ ] No new feature work after **18 Sep** — freeze, fixes only.

## P2 — only if P0/P1 are green

- [ ] `collected_responses` (issue #21) — a template that collects answers
      should store them; currently the owner recap message is the only copy.
- [ ] GOD panel (Phase 3 S3.1) — defer past the release unless everything else
      is done.

## Rollback

Prod deploys from a tag, so rollback = check out the previous tag (`v0.1.0`) and
redeploy. Schema migrations are the part that does not roll back cleanly —
another reason the backup is P0: it is the only real undo for a migration.
