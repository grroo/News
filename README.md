# Personal briefing

A static, phone-first News · Sport · Finance · Media briefing, scheduled for **07:00, 13:00 and 19:00 Europe/Rome**. Python collects public feeds and quotes, selects candidates and asks a provider for structured, cited summaries. GitHub Actions commits data and GitHub Pages serves it. A Cloudflare Worker checks publication and provides a gated owner-refresh service.

Production runs **GPT-6 Luna** with low reasoning and the fill rule (`config.yml`: `reasoning_effort: low`, `fill_items: true`), chosen after a paired live comparison with Claude Haiku 4.5. Roll back by setting `provider: anthropic` and `model: claude-haiku-4-5`. Protected manual refresh and editorial ranking remain separate release gates. The [T09 candidate evidence](docs/release/T09-candidate.md) records completed checks and outstanding blockers; the [staged rollout runbook](docs/operations/T09-rollout.md) is prepared but not executed.

## Reading and refresh

- **Check for updates** reloads the latest published JSON. It makes no AI call. Visible pages also check periodically and on return from the background.
- **Fetch new briefing**, once enabled, opens the verified Access-protected owner page. Only its explicit authenticated POST requests paid work. The link is disabled until the real hostname and Access policy are proven.
- Accepted refreshes can return new content, no change, degraded content or failure. Completion requires the exact request ID in live Pages; a successful workflow or unrelated newer edition is insufficient. Coalesced clicks share one publication identity.
- Limits are one active generation, 15-minute manual cooldown, five manual reservations and eight total generations per Rome day, with capacity retained for remaining scheduled slots. Retries count; a no-change result still used its reservation, but can avoid model calls.

## What the timestamps mean

`generated_at` is content age; `source_checked_at` is a new source check. Reused sections retain `last_success_at`. A failed section can carry older text with an explicit degraded status; it does not become fresh because a file was written. Finance quotes use the source's `as_of` and prior daily-close comparison, independently of the briefing clock. Sources show fallback, stale, empty and unavailable outcomes.

Source links validate candidate membership, **not factual grounding in complete articles**. The model sees titles and short feed excerpts; paywalls, missing context, inaccurate feed text and unsupported inferences remain possible. Check the original reporting before relying on a claim. Media is not AI summarized.

Archives retain up to seven days, 40 editions and 8 MB; unchanged content does not create another content archive. Read state is browser-local, with an in-memory fallback when storage is denied. This release does not promise offline reading.

## Architecture and contracts

| Area | Files |
|---|---|
| Config and three-slot schedule | `config.yml`, `schedule.json` |
| Feed/quote ingestion | `scripts/feeds.py`, `ingestion.py`, `prices.py` |
| Selection, provider and builder | `scripts/selection.py`, `llm_provider.py`, `build.py` |
| Health, reuse and usage | `scripts/pipeline.py`, `section_cache.py`, `usage_ledger.py` |
| Workflow claim and publication | `scripts/check_slot.py`, `.github/workflows/` |
| Owner auth, reservations and scheduler | `cloudflare/news-scheduler/` |
| Static reading UI | `site/` |
| Paired provider evaluation | `evals/` |

[T00 contracts](docs/contracts/README.md) define identities, authentication, counters, claims and publication. CI is read-only and makes no paid calls. Site changes deploy static content only. Scheduled/authorized generation uses `build.yml`; deployment-only recovery republishes committed current main without calling AI. Missing provider credentials fail, and never silently select mock mode.

## Development

Use Python 3.12 and Node 22 or newer:

```sh
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.lock
npm ci --ignore-scripts
npm ci --ignore-scripts --prefix cloudflare/news-scheduler
python -m unittest discover -s tests -p 'test_*.py'
node --test tests/*.test.cjs
npm run check --prefix cloudflare/news-scheduler
```

See [release validation](docs/release/validation.md) for the optional real-Chrome smoke and audit commands. Tests are offline; synthetic JWTs and provider responses are not live authentication or quality evidence.

For a scratch preview, use a disposable checkout so mock data cannot replace your publication:

```sh
python tests/make_fixtures.py --output-dir /tmp/news-fixtures
python scripts/build.py --mock --fixtures /tmp/news-fixtures --now 2026-09-04T17:00:00Z
```

Build the local static directory as the workflows do: copy `site/` and `data/`, remove `seen.json`, `feed-cache.json`, `section-cache.json` and `usage-history.json` from the public copy, then serve it locally. `--mock` is explicit and not a deploy instruction.

Edit sources, interests, preferences and limits in `config.yml`. Config changes take effect on the next authorized generation, not on every code push. Keep editorial selection false until its separate evaluation. Model rollback requires both `provider: anthropic` and `model: claude-haiku-4-5`; retain the Anthropic secret.

## Costs and privacy

Provider usage is recorded per run with token buckets, attempts and latency when reported. `data/usage-history.json` keeps 12 UTC months of committed run records independently of article archives. The public month-to-date summary is a **token-priced lower bound**, not an invoice; unknown charges, failed-before-commit workflows and older usage can be absent. Monthly forecasts are labelled projections, never observed spend. See [actual versus projected evidence](docs/release/T09-candidate.md#cost-evidence). No measured Luna saving or latency claim is made.

The separate private Mac repository's GitHub Actions allowance issue is documented in the [account investigation](docs/implementation-plan/README.md#where-the-2000-actions-minutes-went). Changing the news model does not restore that allowance. Preserve existing billing controls.

This repository is public. Interests, source configuration and committed data are visible in git, including caches/journal even though those files are excluded from Pages. Never put secrets or private preference snapshots in configuration, commits or evaluation fixtures. Interests and selected excerpts are sent to the configured model provider. Browser read/theme/pending-refresh state stays local; requesting refresh contacts the protected Worker. Maintain provider/GitHub/Access credentials and dated model/pricing settings through the operational runbook.
