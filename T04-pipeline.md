# T04 — Truthful health, durable reuse, cost ledger and archives

Builder: Grok high or Sol medium. Reviewer: Sol high. Depends on T00; integrate T02/T03. Own build.py, new pipeline/cache/usage/archive modules, config.yml and pipeline tests. Coordinate workflow inputs with T01 and status with T05.

Fix the reproduced case where every model call fails but an API key causes `mode=llm` and a new timestamp to satisfy the scheduler. Report per-section actual outcome and edition quality. Preserve last-good sections with their original age; explicitly distinguish fresh, reused and degraded content. Missing credentials must fail production generation visibly. Keep mock mode explicit.

Integrate provider and ingestion results under frozen interfaces. Retry only failed sections and honor the shared reservation/attempt budget. Reuse successful work during a retry. Record request identity in the result and perform atomic publication of a validated artifact. Do not let an older run replace a newer edition. Publishing a degraded result and satisfying a scheduled health requirement are separate decisions with bounded retries.

Persist bounded feed cache and section fingerprints across ephemeral runners. Fingerprints cover candidates, meaningful input text, interests/policy, provider/model, prompt version and finance context. A manual fetch still checks sources/prices but avoids AI calls for unchanged valid sections. Keep actual generation timestamps when reusing text. Define what is saved in Git versus artifacts or the existing service; do not use opportunistic Actions caches as the only durable truth.

Replace latest-cost-times-90 with an actual usage ledger plus separately labelled forecast. Include every known provider attempt, cached/reasoning normalization, unknown timed-out cost and explicit versioned pricing. Unknown model rate stays unknown. Keep operational details/secrets out of public data. Reconcile to provider billing when available; this ledger is an estimate, not a perfect invoice.

Change six-edition retention to a configurable age/count policy suitable for more manual builds, for example seven days with a hard size/count ceiling. Maintain archive index consistency. Deleting old files does not erase Git history; no history rewrite in this task. Validate configuration ranges and timestamps. Apply T02/T03/T07 requested config additions without unrelated edits.

Acceptance: all-provider failure cannot look healthy; one failed section keeps good others and shows correct age; unchanged inputs make zero section-generation calls; model/prompt/context changes invalidate the cache; failed deployment can reuse the same result; usage counts retries without double-counting; archives remain navigable after frequent manual updates; existing archive schema still loads. T04 is the sole pipeline integrator, not a reason to rewrite all modules.

