# T09 staged rollout and rollback

**Prepared, not executed.** No deployment, merge, production model switch, billing change, Access setup or paid evaluation is authorized by this candidate. [Release decision and blockers](../release/T09-candidate.md).

## Configuration inventory (names only)

- GitHub: `ANTHROPIC_API_KEY` retained for Haiku/rollback; later `OPENAI_API_KEY` for evaluated Luna; shared `NEWS_RESERVATION_GATE_TOKEN` for the trusted workflow. Variables: `RESERVATION_CLAIM_URL`, `RESERVATION_GATE_ENABLED`, `STRICT_SLOT_GATE`.
- Worker: fine-grained `GITHUB_TOKEN` with Actions read/write for `grroo/News`; same `NEWS_RESERVATION_GATE_TOKEN`; Access settings `ACCESS_TEAM_DOMAIN`, `ACCESS_AUD`, `OWNER_EMAIL`; correct `BRIEFING_URL`, `PAGES_SITE_URL`, `GITHUB_REPO`, `GITHUB_WORKFLOW=build.yml`. Flags default false in `wrangler.jsonc`.
- Durable Object: retain existing `NewsScheduler` SQLite namespace and `v1` migration. New control table is created by code; never reset counters to migrate. Snapshot existing version ID, config and state using approved operator access before changes. Test upgrade and rollback on isolated staging state first.
- Model config: `provider: anthropic`, `model: claude-haiku-4-5`, `editorial_selection: false`. `prompt_version` is part of cache identity. No production dual-provider fallback calls.
- Public site: owner/API meta values are empty. Later set `news-owner-url` to the verified Worker **root `/`**, not `/owner`. Status polling happens on the owner origin; do not enable cross-origin credentialed status access.

Do not copy production bearer secrets into browser tools, fixtures, PRs or logs. Real owner Access authentication follows [Cloudflare's JWT validation contract](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/validating-json/). Validate the actual hostname policy, not only a local signed token.

## Stage 1 — reliable schedule and health on Haiku

1. After separate deployment authorization, publish v2 Haiku output and validate the live Pages schema/section health. Save main commit, Worker version, live edition JSON hash, publication time and current flags. Root CI and release tests must be green.
2. Deploy the candidate scheduler to isolated staging first. Its dispatch target must not reach production paid builds. Current reservation scope is deliberately fixed to `grroo/News`, `build.yml`, `refs/heads/main`; an isolated test repo requires a reviewed staging scope/configuration, not a silent bypass. Provision a test Pages target and independent Durable Object state. No such staging target was supplied to this task.
3. Prove reservation creation/claim and deployed state serialization under simultaneous cron, owner POST, status polling and claim requests, including restart while dispatch is ambiguous. A complete end-to-end paid staging run requires its own explicit budget. Local test doubles do not satisfy this gate.
4. In the authorized production cutover window, stop legacy cron from dispatching unreserved work: either remove it in the cutover change after deployed Worker verification, or retain it while `RESERVATION_GATE_ENABLED=true` makes unreserved cron fail closed. Do not run ungated cron concurrently with an enabled reservation scheduler. Configure the claim URL/token in GitHub before enabling the Worker reservation path.
5. Enable reservation scheduling independently of `REFRESH_ENABLED`, which remains false. Enable strict slot gates on both GitHub and Worker only after live Pages serves v2. T05's older README advice to enable refresh simultaneously is superseded: scheduling/health must be verified before public manual refresh.
6. Observe one complete Rome calendar day, preserving evidence for **07:00, 13:00, 19:00 Europe/Rome**. At each slot record reservation/request/run ID, one allowed claim, provider attempts, live request acknowledgment, section health and `phase: published`. Check at slot+5 minutes and again through the 45-minute display grace period. A failed/degraded edition is readable but does not complete a healthy slot; no-change can complete it with original generation age.
7. Remove residual GitHub cron only after the deployed verification and full-day observation pass. Keep one scheduler authority. If any slot lacks exact healthy acknowledgment or an extra generation appears, pause cutover and investigate before stage 2.

| Rome date | Local slots | Expected UTC slots |
|---|---|---|
| 2026-03-28, before DST | 07:00 / 13:00 / 19:00 | 06:00 / 12:00 / 18:00 |
| 2026-03-29, DST starts | 07:00 / 13:00 / 19:00 | 05:00 / 11:00 / 17:00 |
| 2026-10-24, before DST ends | 07:00 / 13:00 / 19:00 | 05:00 / 11:00 / 17:00 |
| 2026-10-25, DST ends | 07:00 / 13:00 / 19:00 | 06:00 / 12:00 / 18:00 |

The Worker polls every five minutes; that is not a guarantee of publication at the minute. Daily limits reset on the Rome date, not UTC. Fixture DST checks passed; future transitions and the full-day cutover remain operational checks.

## Stage 2 — evaluated Luna configuration, then seven-day canary

**Pending prerequisites:** provider keys; explicit maximum paid spend including retries/judging; resolution of the T08 budget/report caveats; accepted snapshot scope; a blinded scoring rubric fixed before seeing outputs. The manifest's `$0.75` is a default, not spending authorization or a hard upper bound. No live command below was run here.

Offline preparation (safe now):

```sh
python evals/run.py --validate-cases
python evals/run.py --check-fixtures
python evals/run.py --estimate --profile haiku --profile luna-none
```

After the prerequisites and authorization, securely supply `ANTHROPIC_API_KEY` and `OPENAI_API_KEY` without committing them. Freeze the source commit and manifest/case hashes. Run the same candidate lists and prompt policy through both providers:

```sh
EVAL_LIVE=1 python evals/run.py --live --profile haiku --profile luna-none --output evals/runs/t09-paired
```

Require every planned case/profile to succeed and every `passed_checks` to be true; process exit/status alone is insufficient. Stop for unknown spend, failure or budget uncertainty; do not blindly retry a full matrix. Preserve the replay bundle for each case, provider request IDs, token buckets, attempts, measured latency, and actual recorded cost. Price cache/read/write buckets correctly; keep judging cost separate. Report p50/p95 latency per provider and paired case outcomes. Dry-run missing-key results and synthetic fixture successes are **unverified quality**, not measurements.

Score blinded case pairs for unsupported factual/numerical claims, ranking usefulness, concision, diversity and explicit source preferences. Require all deterministic invariants, zero critical unsupported claims and no material quality regression under the fixed rubric. The 20 grouped edition-equivalents reuse section snapshots; disclose that sampling limitation. Run a small `luna-low` subset only where a measured issue justifies it, keeping its Haiku/none baselines; do not change ranking at the same time.

Only after Astra High's final model gate and separate production authorization: set both `provider: openai` and `model: gpt-6-luna`. Retain Haiku credentials and config rollback. Do not enable editorial selection. The fingerprint change will regenerate sections once.

Observe **seven complete Rome days / 21 intended slots**, not seven runs. Each day record exact slot publications, healthy/no-change/degraded/failed sections and their original ages, retries, cost coverage/unknowns, provider latency and one blinded quality sample. Do not routinely double-run providers. Before starting, write agreed cost and latency ceilings into the canary record; unset ceilings block launch. Keep Haiku available after day seven until review explicitly releases the fallback.

Rollback immediately on any critical unsupported factual/numerical claim, auth/limit bypass, wrong request acknowledgment, duplicate paid claim, or stale publication. Pause/rollback on two consecutive provider/schema failures excluding explained feed outages, any overdue healthy slot beyond the 45-minute grace without an understood recovery path, material quality regression, or measured cost/p95 latency above the agreed ceilings. Unknown spend is a stop condition, not zero.

## Stage 3 — protected manual refresh

On the deployed **staging** hostname, use a real browser owner login (not injected JWT headers). Record timestamps, response codes and dispatch/run IDs without storing tokens/cookies. Verify:

| Case | Required observation |
|---|---|
| Access path policy | `/`, `POST /refresh`, `GET /refresh/:id` require owner Access; `/health` public; `/internal/reservations/claim` excluded from browser Access but requires its bearer secret |
| No owner / wrong owner / forged or expired JWT / service token | No dispatch or counter change; private status unavailable |
| Cross-site form, wrong/missing Origin/custom header, OPTIONS, GET | No dispatch; no credentialed CORS |
| Authenticated explicit POST | One reservation/allowed claim/build and exact request ID in live Pages; GET/page load never starts work |
| Scheduler/manual overlap in both arrival orders | One paid run while active, all coalesced jobs terminate for the same acknowledged ID; deployed lease contention/restarts tested |
| Already-satisfied scheduled slot, new explicit manual POST | Accepted after cooldown; not skipped by slot freshness; a new acknowledgment is published even if content is unchanged |
| Changed / unchanged feed | Changed affected section regenerated; unchanged selection zero AI calls, old generation/section ages retained, new source check and acknowledgment |
| Partial provider failure / missing key | Honest degraded age or failed section; missing key fails workflow before feeds/AI, no mock publish |
| Deploy-only recovery / stale completion | No provider calls on recovery; publish current committed output; old run cannot replace newer edition |
| Five-manual/eight-total budget and cooldown | Rejected request does not dispatch; future schedule capacity retained; midnight and restart do not bypass limits |

Only then enable `REFRESH_ENABLED`, keeping reservation/strict controls in place, and configure the public link to the verified root URL. Limits count accepted generation reservations, including a later no-change result; coalesced/idempotent repeats reuse one. The public **Check for updates** button only reloads published JSON and never reserves paid work.

## Stage 4 — editorial selection later

Cache integration tests must remain green. Evaluate T07 ranking separately with a fixed model, prompt, data and reasoning setting; compare coverage, preferences and duplicate handling. Only a separate approval may set `editorial_selection: true`. Offline/PWA work is separately scoped; no offline guarantee is introduced here.

## Independent rollback controls

- **Model:** set **both** `provider: anthropic` and `model: claude-haiku-4-5`; ensure `ANTHROPIC_API_KEY` is available to guard/builder. Keep data and journal. Model rollback never requires enabling editorial selection.
- **Manual refresh:** disable Worker `REFRESH_ENABLED` and clear the public owner meta URL. Keep reservation scheduling/health enabled. Reconcile in-flight jobs instead of deleting state.
- **Editorial:** set `editorial_selection: false`; mode invalidates cache. Keep provider unchanged.
- **Publication:** deploy committed current main through the deploy-only workflow; no generation or new claim. Restore reviewed data via a normal forward commit if necessary, then deploy-only. Do not rerun the generation workflow as recovery.
- **Scheduler:** disable Worker cron before restoring legacy cron; preserve gate/strict migration ordering and one authority. Never disable the paid-work gate while leaving a public refresh route enabled. Record old/new Worker versions and observe the next slot.
- **Secrets/maintenance:** rotate provider, GitHub and claim secrets separately; update both sides of the shared claim token, validate no-key denial, then run only an authorized check. Review model availability, pricing table versions, dependency advisories and Access key rotation periodically. Do not change account billing limits as a recovery step.
