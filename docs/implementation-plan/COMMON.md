# Common instructions for every implementation agent

Implement only your assigned task in the specified repository. Read its AGENTS.md and current code. These packets derive from a September 23 review; check for newer changes. Work in your own branch/worktree. Do not edit files under another agent's ownership, merge unrelated changes, alter billing, dispatch production builds, expose credentials or deploy unless separately authorized for that action. Prepare a reviewable diff and exact operational steps.

For News, read the frozen T00 contract and relevant shared fixtures. Until T00 is merged, propose your interface needs; do not independently invent competing schemas. Preserve the static app/Python/GitHub/Cloudflare architecture. Keep tests offline by default. Live provider tests belong to T08 and an explicit small evaluation budget.

Ownership after T00:

- T01: `.github/workflows/**`, `scripts/check_slot.py`, Python dependency lock and installation files, workflow tests.
- T02: `scripts/llm_provider.py`, provider schema and provider-specific tests.
- T03: `scripts/feeds.py`, extracted ingestion/price modules and their tests.
- T04: `scripts/build.py`, pipeline health/cache/usage/archive modules, `config.yml`, pipeline tests.
- T05: `cloudflare/news-scheduler/**`, including its package/lock files and service tests.
- T06: `site/**` and browser/frontend tests.
- T07: extracted candidate selection module and selection tests.
- T08: `evals/**`, input snapshots and evaluation reporting, with private fixtures excluded from public commits.
- T09: root README, operations/release documentation and final integration changes explicitly handed over by owners.
- T00: shared contract/docs/fixtures; initial extraction and import rewiring before the above work begins. Later contract changes go through the integrator.

New modules are proposed boundaries, not permission for a broad refactor. Preserve existing public/import behavior where tests rely on it. Each owner may add focused tests in its named area. Request shared-file changes from the owner rather than editing them in parallel.

Return: problem solved; changed files; decisions and compatibility notes; exact tests and results; unresolved risks; required configuration/secrets without values; and a compact handoff of at most roughly 500 words. Include evidence for claimed improvements. Do not claim mocked success proves production performance. After two failed attempts at the same problem, hand off a minimal reproduction to the designated stronger model. Do not repeatedly re-audit the entire repository.

