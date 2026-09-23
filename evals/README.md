# T08 — Provider evaluation harness

Reproducible comparison of **Claude Haiku 4.5** vs **GPT-6 Luna** on identical preselected candidate inputs. This harness saves complete prompts, model settings, normalized outputs, mechanical checks, latency and usage for replay.

Live provider calls are **opt-in**. Offline modes validate cases, run bundled fixture checks, and estimate cost without API keys.

## Quick start (offline)

```bash
# Validate the 20 public case snapshots
python evals/run.py --validate-cases

# Run mechanical checks on bundled fixture outputs
python evals/run.py --check-fixtures

# Estimate live provider spend for the default profile matrix
python evals/run.py --estimate

# Dry run: write replay bundles with explicit missing-key failures
python evals/run.py --output evals/runs/dry-run
```

## Live evaluation (authorized budget only)

Requires `ANTHROPIC_API_KEY` and/or `OPENAI_API_KEY`, plus explicit authorization:

```bash
export EVAL_LIVE=1
python evals/run.py --live --profile haiku --profile luna-none --case c001
```

Default budget cap: **$0.75** (`manifest.json`). The harness refuses to start when credentials are missing, when the pre-run estimate exceeds the cap, or when recorded spend reaches the cap mid-run. Live runs exit nonzero unless every planned call succeeds (`status: complete` in `summary.json`).

**Do not** run routine production dual-generation. Live quality comparison belongs here, not in `build.py`.

## Case matrix

Twenty **section-level** snapshots in `evals/cases/` compose into twenty grouped **editions** in `manifest.json` (`e01`–`e20`, each with news/sport/finance). Mechanical replay uses section cases; a model-switch decision still requires a paid paired live run plus blinded quality review (`quality_status: unverified`).

Replay bundles store the **provider-specific prompt actually sent** (`system_prompt_sent` and redacted `request_body`), not the Anthropic tool instruction alone.

| Profile | Provider | Model | Notes |
|---|---|---|---|
| `haiku` | anthropic | claude-haiku-4-5 | Rollback baseline |
| `luna-none` | openai | gpt-6-luna | reasoning effort `none` |
| `luna-low` | openai | gpt-6-luna | reasoning effort `low`; cases `c014`, `c019` only |

Regenerate cases after editing `evals/make_cases.py`:

```bash
python evals/make_cases.py
```

## Outputs

Each run writes to `evals/runs/<timestamp>/`:

- `<case>__<profile>.json` — replay bundle (inputs + result + checks)
- `summary.json` — aggregate status
- `report.md` — human-readable table

`evals/runs/` and `evals/private/` are gitignored. Never commit private preference snapshots or API keys.

## Mechanical checks

`evals/checks.py` validates:

- normalized `provider-result.json` shape
- content schema and candidate ID membership
- bullet/item length limits
- source preference minima (e.g. CulturePSG)
- injection resistance on tagged cases

These checks do **not** prove factual accuracy. Blinded human or Sol review is still required for unsupported claims, ranking usefulness, concision and diversity.

## Cost guidance

Twenty **paired full editions** (three sections × both providers) project to about **$0.58** in provider calls from the September 2026 sample workload, before retries, reasoning, or judge costs. The default 20-case section matrix is smaller; use `--estimate` before `--live`.

## T07 / ranking

Compare candidate **selection** changes separately with a dedicated flag once T07 lands. Do not change model, reasoning, prompt and ranking simultaneously and attribute the result to one factor.

## Handoff to T09

See `canary-checklist.md` for seven-day Luna canary and rollback criteria.
