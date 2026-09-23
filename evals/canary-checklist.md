# Seven-day Luna canary checklist (T09 handoff)

Use after T08 live evaluation passes mechanical gates and human/Sol review finds no critical unsupported claims.

## Before enabling Luna in production

- [ ] T08 live run completed under authorized budget with replay bundles archived
- [ ] 100% schema/citation invariants on the acceptance sample
- [ ] Zero critical unsupported factual or numerical claims in blinded review
- [ ] Source preferences preserved (CulturePSG, insurance topics, multilingual coverage)
- [ ] Measured latency and cost recorded; forecast labelled separately from actuals
- [ ] Haiku rollback path verified (`config.yml` model + `ANTHROPIC_API_KEY` only)

## Daily checks (seven Rome calendar days)

| Day | Scheduled slots satisfied | Degraded sections | Manual fetch tested | Notes |
|---:|---|---|---|---|
| 1 | | | | |
| 2 | | | | |
| 3 | | | | |
| 4 | | | | |
| 5 | | | | |
| 6 | | | | |
| 7 | | | | |

- [ ] Compare provider usage ledger vs T08 forecast (not an invoice)
- [ ] Review at least one degraded edition for honest section age display
- [ ] Confirm no dual-provider production calls on failure retries

## Roll back to Haiku if

- Critical unsupported claim in a published briefing
- Material quality regression vs Haiku baseline under the pre-fixed rubric
- Repeated provider/schema failures not explained by feed outages
- Cost or latency exceeds agreed personal budget without corresponding quality gain

## Rollback steps

1. Set `model: claude-haiku-4-5` in `config.yml`
2. Ensure `ANTHROPIC_API_KEY` is present on the build workflow; remove Luna-only secrets if desired
3. Disable any experimental selection flag from T07
4. Keep T08 replay bundles for post-mortem; do not delete archived runs

## Limitations

This checklist does not prove universal correctness. It is a bounded acceptance sample for a personal briefing app.
