# T09 release candidate — final decision is blocked

Prepared against `d164629c3b85222263ed49d86e1b7dedf49e3e18` (main, 23 September 2026). PRs #1–#11 are merged, including T07 review fixes (#11) and T08 replay/live-run corrections (#9). No open PR existed at intake. Main CI [35917577518](https://github.com/grroo/News/actions/runs/35917577518) passed. These facts were read again, not inferred from previous reviews. This candidate does not deploy, merge, change budgets, or select Luna.

**Production choice remains Anthropic / `claude-haiku-4-5`. Editorial selection and owner refresh remain disabled.** This document is a decision packet for one Astra High review, not a claim of approval.

## Integration changes and owner contracts

| Boundary | Contract / handoff | Candidate change |
|---|---|---|
| T07 → T04 cache | [PR #11](https://github.com/grroo/News/pull/11), `selection_fingerprint_fields()` | Fingerprint includes selection mode, implementation version, policy and editorial candidate order. A one-time cache invalidation is expected. Legacy same-set ordering stays normalized because `new` flags change on publication; editorial ordering is never normalized. |
| T01 → T02/T04 | [PR #7](https://github.com/grroo/News/pull/7), configured-provider credential guard | Supply `OPENAI_API_KEY` to the guard as well as the builder. Missing configured credentials or required reservation IDs now fail the guard process, not merely skip as a successful workflow. |
| T01 → T05 run reconciliation | [PR #8](https://github.com/grroo/News/pull/8), exact run identity | Add request ID to run name. Claim still binds one run/attempt; a rerun cannot regenerate on the old claim. |
| T04 → T05 publication | T00 v2 health rules; T04 migration handoff | Reserved scheduler uses the same `slotPublished` decision as legacy scheduler; v2 no-change/health rules apply even before strict migration. |
| T05 coalescing → T06 | Exact request acknowledgment in Pages | Complete every matching coalesced job on the scheduled fast path; expire/fail coalesced jobs together. Never attach an unrelated edition ID to a terminal failure. Distinct successful request IDs still require their own acknowledgment. |
| T04 → T06 health | Outcome and content quality are independent | Failed/degraded quality takes precedence over `no_change` in UI messages. |
| T01 publication | Deploy-only recovery must not regress to an old workflow checkout | Both publication workflows check out current main. Deploy-only skips generation. Shared publication concurrency remains unchanged. |
| T04 accounting handoff | Durable usage history explicitly left to T09 in PR #7 | Git-backed `usage-history.json`, one record per run/attempt, 12 UTC months; no-change runs remain observable independently of edition IDs and seven-day archive pruning. Public edition includes a bounded month-to-date summary. Raw journal is omitted from Pages. |
| T06 owner URL → T05 route | Worker serves `GET /`, not `/owner` | Remove the placeholder owner/API URLs. Operator sets the verified Worker root only after Access proof. |
| Dependency gates | Audit the combined locks | Patch test-only Ajv 8.17.1 → 8.20.0; idna 3.13 → 3.15; urllib3 2.6.3 → 2.7.0. No billing settings changed. |

The static app → Worker/Durable Object → GitHub Actions → Python → git data → GitHub Pages architecture remains. T00 schemas, limits and authority remain unchanged. Integration edits above reconcile merged owners' interfaces; no parallel owner branches were modified.

## Focused source review map

Review the candidate diff against the base above, then these decision points directly:

- **Authentication:** `cloudflare/news-scheduler/auth.mjs`, `http.mjs`, owner page routes; signature/issuer/audience/expiry/owner, JSON/Origin/custom-header CSRF checks. Service tokens are not owner identity. The claim bearer is independent of browser Access. No authentication bypass was introduced.
- **Limits and publication:** `reservations.mjs`, `worker.ts`, `core.mjs`; persistence before dispatch, exact request/run identity, ambiguous dispatch accounting, terminal coalesced jobs, strict-vs-v2 gate. Limits remain 15 minutes, five manual reservations and eight total generations per Rome day, reserving unattempted schedule capacity.
- **Workflow and output:** `.github/workflows/{build,deploy-pages,ci}.yml`, `scripts/check_slot.py`, `build.py`, `section_cache.py`, `usage_ledger.py`, `site/refresh.js`. Inspect checkout freshness, claim before paid work, deploy-only, stale completion and cache/journal removal from public artifacts.
- **Evaluation:** `evals/run.py`, `prompts.py`, `manifest.json`, `report.py`, `scripts/llm_provider.py`. Mechanical success is not quality acceptance or budget enforcement proof.

## Evidence and limitations

See [validation](validation.md) and machine-readable [evidence](evidence/). Tests use the actual cross-language interfaces, signed synthetic JWTs and real provider response parsing, with external services substituted. They do **not** exercise Cloudflare's actual Access edge, deployed SQLite object concurrency, real GitHub dispatch, or live Pages propagation. Browser tests explicitly simulate those external boundaries.

| Live decision evidence | Status |
|---|---|
| Paired Haiku–Luna outputs and blinded rubric | **UNVERIFIED**; no paid calls or blinded review in this task |
| Measured Luna tokens, cost, p50/p95 latency | **UNVERIFIED**; no successful live paired sample |
| Authenticated staging owner login and path policy | **PENDING**; no verified staging origin/Access application supplied |
| Three Rome slots on one complete day | **PENDING**; offline DST and slot checks are not observation |
| Seven-day Luna canary | **PENDING**, after evaluation and final approval |
| Account operating bill | **NOT measured here**; historical evidence below is dated, not a new invoice |

Local environment has neither `ANTHROPIC_API_KEY` nor `OPENAI_API_KEY`; no explicit paid-evaluation allowance was authorized. GitHub's secret-name listing contains only `ANTHROPIC_API_KEY`, and its Actions variable listing is empty. A GitHub stored key cannot be read back for local evaluation. No secret value was retrieved or printed.

## Blockers and unresolved risks

1. **Live proof unavailable:** stage-3 Access path coverage/exclusion, owner identity, no unauthorized dispatch, exact publication and concurrency must be tested on an authorized isolated staging deployment. No deployment was authorized here. [Runbook](../operations/T09-rollout.md) specifies the cases and evidence to retain.
2. **Luna is not release-ready:** T08 needs an authorized bounded paired run, complete replay outputs, measured usage/latency and blinded review. The manifest's 20 grouped edition-equivalents reuse 20 section snapshots; do not describe that as 20 independent observed full editions. Astra must accept this sample scope or request more snapshots before spending.
3. **T08 budget/report caveats need owner resolution before live evaluation:** `price_result()` does not price cached/write token buckets separately; unknown-cost calls do not increase recorded spend; the loop checks the cap only before the next call and does not reserve worst-case retry/output cost. `status: complete` counts provider success, even if mechanical checks fail. Do not treat the manifest's $0.75 or pre-run estimate as a guaranteed spend ceiling or acceptance signal. Fix/verify these T08 boundaries and pre-agree the rubric/cap before live execution.
4. **Deployment concurrency remains a staging gate:** local pure-state tests do not prove Durable Object lease behavior across awaited network calls, restarts, simultaneous cron/manual claim traffic, or GitHub's pending-run cancellation semantics. A fresh-main checkout reduces stale rerun risk but cannot establish live end-to-end ordering.
5. **Accounting coverage:** the new journal starts with this candidate's committed runs. A workflow killed before commit, an unknown provider charge, prior history, or other account usage is absent. The month-to-date amount is a token-priced lower bound, never a bill. Deployment-only retries add no model charge. No previous totals are invented or backfilled from repeated archive snapshots.
6. **Operational growth:** T05 retains jobs/idempotency/reservations in its control JSON without pruning. Observe payload growth during staging/canary and give T05 a retention policy before sustained use. Do not erase counters or reservations to work around a limit.

These blockers prevent a trustworthy production release or Luna-switch decision. They do not prevent reviewing this isolated candidate. Do not merge or enable flags from green offline tests alone.

## Cost evidence

[evidence/costs.json](evidence/costs.json) preserves the prior seven-edition token sample aggregates and labels every projection. Average observed Haiku workload was 13,795.29 input and 2,479 output tokens per full build. At the repository's dated rates, that implies $0.02619/build and $2.36/30-day month at three fully regenerated builds/day; same-volume Luna projection is $0.002619/build and $0.236/month. **Luna volume, cost and speed are unmeasured.** Cache reuse, retries, judging, manual requests, taxes, hosting and other account activity change totals.

T08's current default matrix estimate is $0.1942 for 42 section calls (20 Haiku, 22 Luna including two optional low-effort cases). This is a pre-run heuristic, excludes retries/judging, and is not an authorized budget. The paired none-only matrix must precede any evidence-driven low-effort subset.

Historical workflow durations combine setup, fetching, generation and deployment; they are not provider latency. Recent GitHub run metadata are linked in the evidence. No release-candidate production timings were collected.

The separate Mac Actions allowance incident is documented in [the existing account investigation](../implementation-plan/README.md#where-the-2000-actions-minutes-went): on September 23 it recorded private `meeting-transcription-mac` using 193.55 macOS minutes / $12 gross, public News using 224 Linux minutes / $1.34 gross, both showing $0 billed. This candidate did not re-open billing or alter its $0 stop-usage control. Gross usage is not an invoice; a model switch does not restore private-repository Actions allowance. Recheck account usage separately when diagnosing an Actions block.

## Astra High handoff

Inspect the auth/limit/publication and cache boundaries above, the security results, and the pending live checklist. Decide whether this is a safe **disabled-feature candidate**, separately from approving deployment, manual refresh, or Luna. Do not infer approval from this packet. Require actual paired outputs and reviewer scores before the model gate, a full Rome day before scheduler cutover, and seven observed days before retiring any fallback. Rollback controls and exact operational steps are in the runbook.
