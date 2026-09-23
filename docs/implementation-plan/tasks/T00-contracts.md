# T00 — Freeze interfaces and prepare independent ownership

Repository: `grroo/News`. Builder: Composer Standard; use Sol medium for unresolved design work. Reviewer: Astra high for one bounded architecture review. No dependencies.

Read COMMON.md and the README's integration contract. Agree the smallest additive JSON/API interfaces for provider output, section health, usage, refresh jobs, shared reservations and publication acknowledgment. Document authentication assumptions and prove the proposed owner-control-page login arrangement can be supported by the existing account/domain. Account setup is a later operational step, not permission to change access now.

Create shared fixtures for a healthy edition, partial failure with last-good content, complete provider failure, no-change result, old archived edition, and refresh job lifecycle. Define the meaning of generated/check/source timestamps and slot satisfaction. An edition merely published with stale sections must not silently satisfy a healthy scheduled refresh.

Mechanically extract only the existing provider call/parsing, ingestion/price helpers and candidate selection into small modules where that is necessary to give T02/T03/T07 independent ownership. Preserve behavior, compatibility and import paths. Leave build orchestration and config to T04 after merging this preparatory change. Avoid frameworks, dependency injection scaffolding or a general plugin system.

Define which state is authoritative in the Durable Object versus persisted pipeline data, how a direct GitHub dispatch gets a reservation, and how retries reconcile an uncertain external dispatch. Require a worker/workflow/builder contract that prevents bypass of limits. State that an unchanged result may acknowledge multiple coalesced requests without pretending it is newly generated content.

Acceptance: existing Python/JS tests pass; representative fixture output remains equivalent before behavior changes; shared examples are usable by both Python and Worker tests; every downstream task has file ownership and exact interfaces. Astra reviews only the contract, state machine, auth boundary and rollback compatibility, returning blocking issues or approval. Freeze/merge this prerequisite before downstream agents branch. Do not ask Astra to implement extraction or formatting.

## Deliverables (merged location)

- Contract docs: `docs/contracts/` (README + JSON schemas)
- Shared fixtures: `tests/fixtures/contracts/`
- Contract tests: `tests/test_contracts.py`, `tests/contracts.test.cjs`
- Extracted modules: `scripts/llm_provider.py`, `scripts/ingestion.py`, `scripts/prices.py`, `scripts/selection.py` (import paths preserved via `build.py` re-exports)
- Plan docs: `docs/implementation-plan/`

