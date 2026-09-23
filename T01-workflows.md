# T01 — Separate validation, generation and deployment

Builder: Composer Standard. Reviewer: Sol medium. Depends on T00's final interfaces. Own workflows, check_slot.py and Python dependency lock. Coordinate Worker changes with T05 and builder changes with T04.

The existing workflow runs both GitHub cron and Cloudflare dispatch; empty scheduled_slot always builds. Six editions were produced September 22 against a target of three. Styling/test changes also trigger paid generation. Fix these behaviors without losing the functioning schedule.

Implement:

1. A read-only PR/code-validation path covering Python, frontend behavior and `cloudflare/**`, including Worker types and tests. No provider credentials or live AI required. Validate on production Python 3.12.
2. A static deployment path that can publish committed briefing data with site changes, without rerunning ingestion/AI.
3. Explicit content generation triggers with the T00 reservation and request/slot contract. Missing credentials or invalid reservations fail visibly. Mock mode cannot overwrite normal production data accidentally.
4. A scheduler cutover: verify the deployed Worker's health/dispatch ability before removing GitHub cron. If temporarily retaining both, make both use the identical real slot and idempotency check. Plan the operational sequence explicitly.
5. Serialize publication; cancel obsolete code-validation jobs. Do not cancel a running publication merely because another refresh arrived. Ensure automated data commits do not trigger a generation loop.
6. A deploy-only recovery path, least necessary job permissions, reviewed Action revisions, and a reproducible Python dependency lock.

Acceptance: a site-only change yields zero AI calls; a PR yields zero AI calls and validates Worker changes; two triggers for one slot generate once; a valid manual request remains possible after the slot is satisfied; missing key does not produce a fresh mock edition; deploy-only retry makes zero AI calls. Slot health follows the shared per-section contract. Add meaningful workflow/guard tests and document any account verification needed for cutover.

