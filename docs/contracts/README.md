# Shared integration contract (T00)

Version 2 defines the interfaces between the Python builder, GitHub workflow, Cloudflare Worker/Durable Object and static frontend. T01–T09 implement them. T00 does not change production output or the deployed scheduler. Schemas live in `schemas/`; shared examples and decision cases live in `tests/fixtures/contracts/`. JSON Schema checks structure; the decision cases check timestamp order, health and request identity.

## Edition identity, age and completion

| Field | Meaning | Authority |
|---|---|---|
| `edition_id` | Stable identity of the content. Reusing it retains its ID; it need not equal a current request ID. | Builder output |
| `generated_at` | When content was generated. Does not advance for unchanged content. | Builder output |
| `source_checked_at` | Time of this run's source and price check. | Builder output |
| `last_success_at` | Successful provider time for one section; retained when reused. | Builder section |
| `refresh.outcome` | `generated` or `no_change`, separate from content health. | Builder output |
| `refresh.completed_at` | When this run finished a validated result; not proof that Pages published it. | Builder output |
| `scheduled_slot` | Target Rome slot encoded in UTC, required for scheduled v2 output. | Worker reservation / workflow |
| `request_ids` | Requests acknowledged by this edition, including coalesced requests. | Builder output |
| `checked_at` | Optional time a client or Worker read live Pages; never evidence of successful fetching. | Observer |

For unchanged content, retain `edition_id`, `generated_at`, `generated_local`, section text and each section's `last_success_at`. Set new source and completion times, `refresh.outcome: no_change`, and append acknowledged request IDs. `refresh.reused_edition_id` equals `edition_id`. Each reused AI section is `unchanged` with its previous success time and `reused_edition_id`. `quality.overall: healthy` is possible for a successful no-change check; `unchanged` is not a quality level.

For generated content, set `refresh.outcome: generated`. The new edition ID is stable for that generated content. It need not equal one of several coalesced request IDs. A degraded edition can be published for reading but cannot complete a healthy scheduled slot.

### Healthy scheduled slot decision

The scheduler decides **after reading live Pages JSON**. Workflow success or a client timestamp is insufficient. A v2 edition satisfies slot `S` only when all conditions hold:

1. It validates as v2, targets `scheduled_slot == S`, has `mode: llm`, `quality.overall: healthy`, and includes the expected request ID if the scheduler previously dispatched one.
2. `source_checked_at` and `refresh.completed_at` are each at or after `S`, no more than 60 seconds in the future, and completion is not before the source check. Failed or stale checks cannot complete a slot.
3. For `refresh.outcome: generated`, `generated_at >= S`. For `no_change`, the older generation time remains and `refresh.reused_edition_id == edition_id`.
4. The three AI sections (`news`, `sport`, `finance`) are present. Each is `healthy` or `unchanged`, has no error and records a valid `last_success_at`. An unchanged section names its reused edition. The media section is present; `skipped` is valid because it does not use AI. Degraded, failed and missing sections cannot complete a healthy slot, even when last-good content remains visible.

T04 defines which source failures degrade a check; old articles alone cannot turn a failed required source check healthy. Retries remain bounded. An authorized manual refresh is complete only when its exact request ID is acknowledged in live Pages or an equivalent status record verified against the live edition. An unrelated newer edition is insufficient. The HTTP status may be `no_change` while edition health remains `healthy`.

Legacy editions without `schema_version: 2` remain readable. They lack reliable section-success evidence and cannot satisfy new slots or manual requests **after the strict gate is enabled**. T04 must publish v2 before T01/T05 enable that gate. Until then the deployed scheduler retains existing behavior, avoiding a migration retry storm.

## Authentication and public surface

The first release uses an owner control page protected by Cloudflare Access on the Worker's origin. The public Pages app links to it; only an explicit authenticated `POST /refresh` starts paid work. Configure Access for the owner page, refresh POST and private refresh-status paths. Keep `/health` public and exclude `/internal/reservations/claim` from that browser Access policy; the latter is protected by its own workflow bearer secret. Cron is not an HTTP route. [Cloudflare documents Access for Worker hostnames](https://developers.cloudflare.com/workers/configuration/cloudflare-access/), including `workers.dev`. The actual account's path policy, owner identity, audience and login flow **are not yet verified**. T05 must prove the path protection and exclusion on the actual hostname before enabling refresh. If it cannot do so, return for architecture review rather than silently using a different authentication scheme. A Cloudflare service token is not the browser user's identity.

The Worker verifies Access token signature, issuer, audience, expiry and expected owner identity. The owner page sends same-origin JSON POST with a custom `X-Requested-With` header. For browser refresh POST, reject a missing or mismatched `Origin`, allow no cross-origin credentials/preflight, and require that header; these checks prevent an unrelated site from submitting the owner's Access cookie through a form. T05 tests this in a real browser. CORS and a job ID are not authorization. Public `/health` stays read-only; refresh status is private. No GitHub or model credential reaches the browser.

## Authoritative state

| Concern | Authority |
|---|---|
| Reservations, daily counters, cooldown, active job and dispatch attempts | Durable Object, persisted before external dispatch |
| Edition JSON, archives and publication history | Git repository `data/` |
| Live publication and exact request acknowledgment | Pages `data/briefing.json` |
| Browser reading state | `localStorage`, never generation authority |

### One workflow-to-Durable-Object claim protocol

T01 and T05 implement **online single-run claiming**, not an offline signed token. The Worker dispatches `build.yml` with opaque `reservation_id` and `request_id` inputs; `scheduled_slot` is present for scheduled work and absent for standalone manual work. The build job calls `POST /internal/reservations/claim` **before fetching feeds or calling a provider**. This endpoint is separate from owner refresh. It accepts the `NEWS_RESERVATION_GATE_TOKEN` bearer secret stored in GitHub Actions and the Worker. It is not a browser credential. The Worker compares it in constant time before consulting the Durable Object. PR and untrusted jobs never receive it.

The claim body has `reservation_id`, `request_id`, `run_id` (`GITHUB_RUN_ID`), `run_attempt` (`GITHUB_RUN_ATTEMPT`), `repository`, `workflow` and `ref`. The Durable Object atomically verifies the stored request/scope/expiry, then binds the first valid claim to that run and attempt. Repeating the identical claim returns the same `allowed` result after a lost HTTP response. A different run or GitHub rerun is denied. A rerun that regenerates content needs a new reservation and allowance. Deployment-only recovery uses a separate path with no provider calls. The workflow stops if the claim is denied or unreachable; missing credentials never turn into mock generation.

T05 validates the bearer secret and that the reservation targets the expected repository, workflow and `main` ref. The claim binds to values supplied by the trusted GitHub workflow environment. Keep the gate secret exclusive to that paid workflow; if this trust boundary changes, add independent GitHub run verification. T01 invokes generation at most once per claimed workflow attempt. An idempotent claim response does not authorize two builder invocations inside one attempt.

Reservations have unpredictable IDs, unique request IDs, UTC creation/expiry times, trigger and expected scope. The initial claim lease is 15 minutes; a claimed run stays bound until terminal status or bounded recovery. Server counters use the `Europe/Rome` calendar, including daylight saving transitions. Initial limits: one active generation job, 15-minute manual cooldown, five new manual reservations per Rome day and eight total workflow-generation reservations per day. For every manual request or retry, reserve at least one unused allowance for each remaining scheduled slot that has not received its first attempt; a due slot's first attempt consumes its reserved capacity. Reserve an allowance when accepting new work, even if fetching later finds no change. Idempotent repeats and coalesced requests reuse that reservation. New retry workflows consume another allowance. Worker restarts do not reset counters.

Refund an unclaimed reservation only after expiry **and** confirmation GitHub has no corresponding dispatch/run. An ambiguous dispatch timeout retains the reservation and cooldown. Reconcile using run identity or a deterministic run name containing the opaque request ID. A safe redispatch with the same reservation still permits only one run to claim it. Never refund a claimed reservation automatically. A failed deployment reuses committed output through the deployment-only route. `reservation-cases.json` covers these decisions.

After verifying the deployed Worker, remove legacy GitHub cron or make it acquire the same reservation during overlap. A privileged direct `workflow_dispatch` obtains a reservation through the same owner gate. Roll out strict workflow claiming only after the Worker can issue/validate reservations and T04 emits v2 editions.

## Schemas and compatibility

1. `schemas/edition.json` is the root schema for legacy and v2 editions.
2. `schemas/provider-result.json` is the root schema for normalized provider success/failure. Semantic candidate-ID validation remains separate.
3. `schemas/refresh-job.json` has root validation and response-specific `#/$defs/createResponse`, `#/$defs/statusResponse`, `#/$defs/limitResponse` entrypoints. Use the specific entrypoint for each HTTP response.
4. `schemas/reservation.json` validates Durable Object state at the root and has `#/$defs/claimRequest` and `#/$defs/claimResponse` entrypoints. Authentication and atomic transitions are semantic rules.

Legacy editions may omit `schema_version`; v2 requires identity, health, request mapping, refresh outcome and section states. Extra legacy fields remain allowed. The shared fixtures and decision cases are **contract examples**, not deployed Worker tests. T01/T05 must run their production implementations against the same cases.

The provider-result schema validates **normalized internal results**. T02 should derive an OpenAI-supported strict-output subset for the API request and enforce local-only constraints, such as ID uniqueness, after parsing. It must not send this entire provider-result schema to the Responses API. To run the contract suites locally, install `requirements-test.txt` for Python and run `npm ci` at the repository root for the Node validator. T01 wires both into CI without making paid model calls.
