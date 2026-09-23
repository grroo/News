# T09 — Integrate, review and stage the release

Integrator: Sol high. Final reviewer: Astra high, one focused gate. Depends on T01–T08; M01 has its own release/review. Own operations/root documentation and integration changes explicitly handed over by file owners.

Merge owned changes sequentially after T00. Resolve interface differences against the agreed contract; request owner fixes rather than silently inventing incompatible alternatives. Run the combined Python 3.12 suite, JS/browser tests, Worker types/tests and relevant dependency/security checks. Do not repeat all checks after documentation-only edits unless necessary.

Review key end-to-end cases: authenticated manual request produces exactly one matching publication; scheduler overlap coalesces; already-satisfied slot does not suppress an explicit authorized refresh; changed feeds produce fresh sections; unchanged feeds avoid AI; partial provider failure shows age/health honestly; missing key fails; deploy-only retry never calls AI; old completion cannot overwrite new data; unauthorized and budget-exhausted calls never dispatch. Test actual production-shaped authentication in staging, not only mocked headers.

Create a compact final evidence bundle: changed architecture/contracts, diffs for auth/limits/publication, test results, paired eval report, measured cost/latency, migrations/secrets checklist without values, and rollback procedure. Astra should examine these decision points and inspect relevant source directly, not repeat mechanical test generation or every investigation. Report blockers and material limitations. High effort is the default; use xhigh only for a specific unresolved complex question.

Prepare staged release: reliable schedule/health on Haiku; evaluated Luna configuration; protected manual refresh; editorial/offline flags later. Retain independent rollback controls. Verify scheduler cutover on a full day of three intended Rome slots and use DST fixtures. Review seven days of Luna outcomes before removing the fallback option. Avoid live paid double-running except the bounded evaluation.

Document actual usage versus projected costs, the separate Mac Actions allowance problem, what refresh does, source/quote freshness, public configuration exposure, age-based archives, no guaranteed full-article factual grounding, and model/secret maintenance. Keep required operational steps explicit. Do not change billing limits, deploy or publish unless the implementation session authorizes those actions. A reviewable release candidate with clearly enumerated pending live checks is preferable to claiming unperformed verification succeeded.

