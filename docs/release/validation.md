# T09 validation record

Local validation on 23 September 2026; base main `d164629c3b85222263ed49d86e1b7dedf49e3e18`.
Python **3.12.14**, Node **25.6.1**, Chrome **153.0.8010.53**. CI specifies Python 3.12 / Node 22; PR CI is a separate gate. No live provider, deployment or production mutation was performed.

| Check | Final result |
|---|---|
| `python -m unittest discover -s tests -p 'test_*.py'` | **128 passed**, 7.200 s |
| `NEWS_TEST_PYTHON=<python3.12> node --test tests/*.test.cjs` | **35 passed**, 2.210 s |
| Worker `npm run check` | Wrangler types, TypeScript and **24 tests passed** |
| Clean Python environment, install `requirements.lock`, `pip check`, import build/guard/provider | **Passed** |
| Root and Worker `npm audit --json` | **0 advisories** in each final lock |
| `pip-audit -r requirements.lock --format json` | **0 known vulnerabilities** in final lock |
| Real Chrome desktop + phone smoke | **Both passed**, no page errors, no horizontal overflow; [browser evidence](evidence/browser.json) |
| T08 `--validate-cases` | **20 section snapshots valid** |
| T08 `--check-fixtures` | **3 expected outcomes passed** (including expected failed fixture) |
| T08 `--estimate` | **UNVERIFIED heuristic**: 42 calls / $0.1942, not spend or quality evidence |
| `git diff --check` | Passed |

Audits are snapshots of advisory databases, not a guarantee of security. Initial findings were Ajv `GHSA-2g4f-4pwh-qvx6`, idna `CVE-2026-45409`, and urllib3 `CVE-2026-44431` / `CVE-2026-44432`. Python audit initially returned six entries because those three advisories were duplicated across records. Patched versions are in the candidate locks. Audit JSON is in [evidence](evidence/).

## End-to-end coverage and boundary

`tests/release.test.cjs` imports production HTTP, JWT, reservation and UI modules. It uses a real generated RSA key/signature and the production verification path, then passes dispatched inputs into `tests/release/build_driver.py`. That driver executes the production Python guard, feed fixtures, builder and provider HTTP-response normalization. Only external HTTP/provider responses, persistence and live publication are substituted. It does not invoke the deployed Durable Object or real Access edge.

| T09 scenario | Offline evidence | Live status |
|---|---|---|
| Authenticated manual produces one matching publication | Signed POST → reservation → one allowed run/attempt → real builder JSON → exact acknowledgment/UI | Pending real owner login, dispatch and Pages |
| Overlapping schedule/manual, both arrival orders | Coalesced IDs, one dispatch; all matching jobs finish on scheduled fast path; distinct IDs still require acknowledgment | Pending actual DO/network concurrency |
| Explicit manual after satisfied slot | New POST dispatches without `scheduled_slot`; Python guard allows; unchanged build acknowledges request with zero AI | Pending staging |
| Changed / unchanged feeds | Real fixture XML changes regenerate news only; repeat unchanged build makes zero HTTP provider calls | Pending live source/conditional requests |
| Partial provider failure | Real provider parser/retry path times out sport twice; other sections succeed; old sport success time retained, degraded | Pending staged failure injection |
| Missing credentials | Guard subprocess exits 1, build/publish false; builder's existing no-key test proves no mock write | Pending missing-secret staging |
| Deploy-only retry | Guard returns publish=true/build=false with zero AI calls; workflows share publication concurrency/current-main checkout | Pending real failed deploy/recovery |
| Stale completion | Older builder clock cannot overwrite newer bytes; existing pipeline test covers becoming newer during build | Pending overlapping real workflows/Pages |
| Unauthorized / budget exhausted | Missing/forged JWT, cross-origin, service token/expired token in Worker suite; exhausted counts yield no dispatch | Pending Access edge, midnight/restart |
| Rome schedule / DST | All 07/13/19 slots on both sides of spring/autumn transitions plus shared slot/day fixtures | Pending complete observed Rome day |
| Editorial cache integration | Mode, policy, version and editorial order invalidate; legacy same-set `new` bookkeeping reuses | Editorial flag stays false; quality pending |
| UI/health | Failed/degraded takes priority over no-change; Chrome phone/desktop, storage denied, keyboard owner submit, exact return | Browser external services simulated |

The initial order-sensitive cache change failed T04's unchanged legacy-feed regression. Final code keeps legacy same-set ordering normalized while including exact editorial order. The first coalesced-job change failed the existing distinct-request acknowledgment test; final code preserves that invariant. The first browser assertion incorrectly assumed an old fixture would display “latest” rather than “overdue”; the corrected smoke accepts both legitimate check-only outcomes. All final checks above passed; no failure is being counted as quality evidence.

## Reproduce

Use the README installation commands in a clean checkout. If Python 3.12 is not `python3` on PATH, set `NEWS_TEST_PYTHON` for the Node integration test. CI runs this integration test alongside the existing frontend/contract suite.

The optional browser test requires installed Playwright and Chrome:

```sh
NEWS_PLAYWRIGHT_MODULE=/absolute/path/to/playwright node tests/release/browser.cjs
```

It routes all resources to local fixtures, temporarily supplies a stage-3 owner URL in the served HTML, and mocks external Access/refresh/publication. It does not claim that a real owner logged in. The tracked page leaves the owner URL empty. The browser test is optional local evidence, not yet an installed CI browser dependency.

```sh
npm audit --json
npm audit --prefix cloudflare/news-scheduler --json
pip-audit -r requirements.lock --format json
python evals/run.py --validate-cases
python evals/run.py --check-fixtures
```

## Checks that could not run

- Real staging Access login/policies, deployed reservation persistence/concurrency, GitHub dispatch and exact Pages publication: no verified staging environment and no deployment authorization.
- Paid Haiku–Luna comparison, blinded quality scoring, measured Luna cost/latency: no local provider credentials or explicit paid allowance; T08's budget/report caveats need resolution first.
- Full-day three-slot cutover observation and seven-day Luna canary: intentionally not started. The [runbook](../operations/T09-rollout.md) supplies the sequence, evidence and rollback criteria.

No dry run, fixture output, failure, pre-run estimate or historical workflow duration is accepted as live Luna readiness evidence.
