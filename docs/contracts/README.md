# Shared integration contract (T00)

Frozen 23 September 2026. This document defines the smallest additive interfaces
between the Python builder, GitHub workflow, Cloudflare Worker/Durable Object and
static frontend. Downstream tasks (T01–T09) extend these contracts; they must not
invent competing schemas.

Machine-readable schemas live in `schemas/`. Shared examples live in
`tests/fixtures/contracts/` and are loaded by both Python and JavaScript tests.

## Timestamp semantics

| Field | Meaning | Authority |
|---|---|---|
| `generated_at` | When provider output for this edition was produced (UTC ISO). | Builder / edition JSON |
| `checked_at` | When a scheduler or client last inspected live Pages JSON. | Worker DO / client |
| `source_checked_at` | When feeds/prices were last fetched for candidate material. | Builder (T04) |
| `last_success_at` | When a section last completed a successful provider call. | Builder section state |
| `scheduled_slot` | Rome-time publication slot this run intends to satisfy (UTC ISO). | Worker dispatch / workflow input |

**Slot satisfaction (healthy scheduled refresh):** live Pages JSON satisfies slot `S`
only when **all** of the following hold:

1. `generated_at >= S` and `generated_at <= now + 60s` (publication window),
2. `mode === "llm"` (mock is never a healthy completion),
3. `quality.overall === "healthy"` (proposed; until T04 lands, treat missing as healthy only when every AI section succeeded without fallback error),
4. no section has `state === "failed"` with empty last-good content.

An edition merely **published** with stale or failed sections must **not** satisfy
a healthy scheduled refresh. Degraded editions remain readable; schedulers and the
UI must surface pending/overdue states honestly.

**No-change results:** when input fingerprints match a prior successful edition,
the builder may return `quality.overall === "unchanged"`. The response maps all
coalesced `request_id`s to the same edition without advancing `generated_at`.
Clients must not treat `checked_at` alone as fresh content.

## Authentication (first release)

Owner refresh uses a **Cloudflare Access-protected control page** on the Worker's
origin (not GitHub Pages). The public Pages app links out; the first visit may
require sign-in. Paid work starts only on an explicit authenticated `POST`.

Verification for this account/domain (September 2026):

- Repository `grroo/News` is public; Pages serves `https://grroo.github.io/News/`.
- Cloudflare Worker can be deployed on the same account with Access policies on
  its `*.workers.dev` or custom hostname origin.
- No cross-site cookie dependency on the public Pages origin is required.
- Account setup (Access application, team domain, service token) is operational
  work for T05; T00 only confirms the arrangement is supportable.

Unauthenticated refresh requests are denied. Limit responses include
`retry_after` (ISO UTC).

## Authoritative state

| Concern | Authoritative store | Notes |
|---|---|---|
| Cooldowns, daily counters, active job, dispatch attempts | Durable Object | Persist **before** external GitHub dispatch |
| Edition JSON, archives, seen history | Git repository (`data/`) | Survives runner ephemerality |
| Workflow reservation token | DO + workflow input | Workflow must validate before paid generation |
| Live publication truth | Pages `data/briefing.json` | Workflow success alone is insufficient |
| Browser read state | `localStorage` | Not authoritative for generation |

**Direct GitHub dispatch:** privileged `workflow_dispatch` must include or
resolve a reservation recorded in the DO (or a signed token derived from it).
The workflow gate (`check_slot.py`, extended in T01) rejects unpaid work without
a valid reservation when limits apply.

**Uncertain dispatch:** if GitHub returns timeout after dispatch, the DO keeps
cooldown and reconciles against existing runs — never blind immediate redispatch.
See `schemas/reservation.json`.

**Bypass prevention:** every paid entry point (scheduled cron, manual refresh,
privileged dispatch) passes the same budget/idempotency gate implemented in the
DO and enforced in the workflow before `build.py` runs.

## Contract surfaces

1. **Edition metadata** — `schemas/edition.json`
2. **Provider result** — `schemas/provider-result.json`
3. **Refresh API** — `schemas/refresh-job.json`
4. **Reservation / DO state** — `schemas/reservation.json`

## Backward compatibility

Existing `data/briefing.json` and archived editions remain valid. New fields are
additive. Readers fall back when optional fields are absent (see
`archived-edition.json` fixture). Schema version `"1"` denotes current production
shape; `"2"` adds health, request mapping and refresh metadata defined here.

## File ownership after merge

See `docs/implementation-plan/COMMON.md`. T00 owns this directory and shared
fixtures until the integrator (T09) applies contract changes.
