# T05 — Authenticated refresh, coalescing and publication status

Builder: Grok high or Sol high. Reviewer: independent Sol high. Depends on T00; integrate T01/T04. Own all `cloudflare/news-scheduler/**` including dependency updates. This is the highest-risk implementation task because it connects public HTTP requests to paid generation.

Implement the frozen refresh/status API and protected owner control page using the existing Worker and SQLite Durable Object. Verify identity server-side, including token signature, issuer, audience and expiry. Restrict to the intended owner. The proposed Access arrangement must be confirmed against actual deployment capabilities. CORS or possession of an opaque job ID alone is not authentication. Keep GitHub/provider secrets off the browser and redact logs.

Require an authenticated POST for refresh. Defend the chosen cookie/session flow against cross-site request forgery; validate allowed origins and use the appropriate CSRF mechanism. Do not trigger generation on GET, page load or a public health probe. Keep status private and limited to operational fields the owner needs. Use a repository-scoped GitHub credential with only necessary dispatch/status permissions.

Persist idempotency key, request/job identity, lock, cooldown and allowance reservation before dispatch. Coalesce concurrent calls and scheduled/manual overlap under the shared contract. Initial defaults: 15-minute manual cooldown; five manual requests/day maximum; eight generation runs/day overall, with space reserved for remaining scheduled slots. Retries count. Every paid workflow entry point must acquire/validate a reservation, including privileged manual dispatch.

Handle a dispatch timeout after GitHub may have accepted it: reconcile by correlation/run identity before retrying. Define lock expiry, recovery after Worker restart, stale reservations and safe release. Test Rome day boundaries and DST. Never mistake a completed Actions run for a published briefing: acknowledge exact request-to-edition mapping from Pages, including no-change and degraded outcomes. Time out boundedly if publication cannot be confirmed.

Update scheduler slot-health checks to use actual section outcomes rather than `mode`. Preserve bounded scheduled retry behavior without rerunning successful sections unnecessarily. Update Wrangler/miniflare's vulnerable development dependency chain to a verified patched version and run types/tests/audit; the prior audit identified one transitive sharp chain, not three separate deployed exploits.

Acceptance: unauthorized/forged/expired-token requests cannot dispatch; repeated clicks and concurrent tabs dispatch once; server limits survive restart; ambiguous dispatch reconciles; stale runs cannot mark another request complete; scheduler/manual collision is safe; exhausted allowance returns a clear retry/limit state; deployment-only recovery incurs no model calls. Exercise the DO state machine with deterministic mocked GitHub/Pages responses. Provide configuration instructions without applying access or secret changes unless separately authorized.

