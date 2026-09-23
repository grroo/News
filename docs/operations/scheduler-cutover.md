# Scheduler cutover: GitHub cron → Cloudflare Worker

Operational sequence for T01/T05. Do **not** remove GitHub cron until every verification step below passes on the **deployed** Worker.

## Current state after T01

| Workflow | Trigger | AI calls | Deploy |
|---|---|---|---|
| `ci.yml` | PR + code pushes | Never | Never |
| `deploy-pages.yml` | `site/**` pushes only | Never | Once |
| `build.yml` | Rome cron + manual dispatch | When guard allows | Once (generation path) |

Generation commits include `[skip ci]` so they do not trigger a second deploy. Site-only changes publish through `deploy-pages.yml` with the committed `data/` already on `main`.

## Staged modes (repository variables)

| Variable | Default | Meaning |
|---|---|---|
| `RESERVATION_GATE_ENABLED` | unset / false | Legacy cron and dispatch without Worker reservation (current production) |
| `RESERVATION_GATE_ENABLED=true` | after T05 verified | Every paid run requires matching `reservation_id` + `request_id` and a validated claim |
| `STRICT_SLOT_GATE=true` | after T04 v2 output | Strict per-section slot health; legacy editions never satisfy |

Do **not** enable strict slot or reservation modes until T04 publishes v2 editions and T05 deploys the claim service.

Provider credentials: the guard currently checks `ANTHROPIC_API_KEY` only. T04 must extend validation and secret wiring for `OPENAI_API_KEY` (Luna) while keeping Anthropic rollback.

## Prerequisites before cutover

1. **Worker deployed and healthy**
   - `GET /health` returns `configured: true` and a recent `checkedAt`.
   - Cloudflare cron has propagated (allow up to 15 minutes after deploy).

2. **Worker dispatch target (unchanged filename)**
   - `GITHUB_WORKFLOW=build.yml` in `wrangler.jsonc` (frozen contract scope).
   - Dispatch inputs include `scheduled_slot`, `reservation_id`, and `request_id`.

3. **GitHub secrets/variables**
   - `GITHUB_TOKEN` on the Worker (fine-grained, Actions read/write on `grroo/News`).
   - `NEWS_RESERVATION_GATE_TOKEN` shared between Worker and GitHub (T05 implements claim endpoint).
   - Repository variable `RESERVATION_CLAIM_URL` → Worker `POST /internal/reservations/claim` URL.
   - Claim body uses `workflow: build.yml` and `ref: refs/heads/main` (see `docs/contracts/schemas/reservation.json`). The workflow sets `NEWS_WORKFLOW_FILE=build.yml`; without it, `check_slot.py` parses GitHub's default `GITHUB_WORKFLOW_REF`.

4. **One full Rome day observed**
   - Three slots complete with `phase: published` on `/health`.
   - Pages JSON timestamps match expected slots.

## Cutover steps

1. Deploy Worker with reservation + claim support (T05).
2. Set `RESERVATION_CLAIM_URL`, `NEWS_RESERVATION_GATE_TOKEN`, and `RESERVATION_GATE_ENABLED=true`.
3. Run one manual `workflow_dispatch` on `build.yml` with reservation inputs; confirm claim + publication.
4. Observe at least one Worker-initiated scheduled dispatch succeed end-to-end.
5. Remove the `schedule:` block from `.github/workflows/build.yml`.
6. Set `STRICT_SLOT_GATE=true` once T04 emits v2 editions.

## Mock and deploy-only paths

- `mock=true` uploads `data/briefing.json` as a workflow artifact only; it never commits or publishes.
- `deploy_only=true` publishes committed data with zero provider calls through `build.yml` only.

## Rollback

1. Restore the `schedule:` block in `build.yml`.
2. Set `RESERVATION_GATE_ENABLED=false` and unset `STRICT_SLOT_GATE`.
3. Disable the Cloudflare cron in Wrangler (or pause the Worker).
4. Keep `ci.yml` / `deploy-pages.yml` split — they do not affect scheduling.

## Account verification still required (T05)

- Cloudflare Access policy on owner refresh paths.
- Worker hostname and Access application on the actual account.
- Confirm `/internal/reservations/claim` is **excluded** from browser Access and protected by the gate token only.

These items are documented in `docs/contracts/README.md` but must be proved on the live hostname before enabling owner refresh.
