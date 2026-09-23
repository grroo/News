# Scheduler cutover: GitHub cron → Cloudflare Worker

Operational sequence for T01/T05. Do **not** remove GitHub cron until every verification step below passes on the **deployed** Worker.

## Current state after T01

| Workflow | Trigger | AI calls | Deploy |
|---|---|---|---|
| `ci.yml` | PR + code pushes | Never | Never |
| `deploy-pages.yml` | `site/**`, `data/**` pushes | Never | Always |
| `briefing.yml` | Rome cron + manual dispatch | When guard allows | When guard allows |

Automated `briefing-bot` data commits publish through `deploy-pages.yml` only. They no longer trigger generation.

## Prerequisites before cutover

1. **Worker deployed and healthy**
   - `GET /health` returns `configured: true` and a recent `checkedAt`.
   - Cloudflare cron has propagated (allow up to 15 minutes after deploy).

2. **Worker dispatch target updated (T05)**
   - `GITHUB_WORKFLOW=briefing.yml` (was `build.yml`).
   - Dispatch inputs include `scheduled_slot`, and after T05 lands also `reservation_id` and `request_id`.

3. **GitHub secrets/variables**
   - `GITHUB_TOKEN` on the Worker (fine-grained, Actions read/write on `grroo/News`).
   - `NEWS_RESERVATION_GATE_TOKEN` shared between Worker and GitHub (T05 implements claim endpoint).
   - Repository variable `RESERVATION_CLAIM_URL` → Worker `POST /internal/reservations/claim` URL.
   - Optional: `STRICT_SLOT_GATE=true` only after T04 publishes schema v2 editions.

4. **One full Rome day observed**
   - Three slots complete with `phase: published` on `/health`.
   - Pages JSON timestamps match expected slots.

## Cutover steps

1. Deploy Worker with reservation + claim support (T05).
2. Set `RESERVATION_CLAIM_URL` and `NEWS_RESERVATION_GATE_TOKEN` in GitHub.
3. Run one manual `workflow_dispatch` on `briefing.yml` with reservation inputs; confirm claim + publication.
4. Observe at least one Worker-initiated scheduled dispatch succeed end-to-end.
5. Remove the `schedule:` block from `.github/workflows/briefing.yml`.
6. Set `STRICT_SLOT_GATE=true` once T04 emits v2 editions (enables per-section health gate).

## Overlap period (optional)

If both schedulers must run temporarily:

- GitHub cron computes the same Rome slot as the Worker (`check_slot.py` derives it from `schedule.json`).
- Both paths use the same idempotency guard (`check_slot.py` / Worker `tick()` reading live Pages JSON).
- Worker dispatches include `scheduled_slot`; GitHub cron auto-derives the slot when `GITHUB_EVENT_NAME=schedule`.
- Do **not** enable `REQUIRE_RESERVATION` for cron until the Worker owns all scheduled dispatches.

## Rollback

1. Restore the `schedule:` block in `briefing.yml`.
2. Disable the Cloudflare cron in Wrangler (or pause the Worker).
3. Keep `ci.yml` / `deploy-pages.yml` split — they do not affect scheduling.

## Account verification still required (T05)

- Cloudflare Access policy on owner refresh paths.
- Worker hostname and Access application on the actual account.
- Confirm `/internal/reservations/claim` is **excluded** from browser Access and protected by the gate token only.

These items are documented in `docs/contracts/README.md` but must be proved on the live hostname before enabling owner refresh.
