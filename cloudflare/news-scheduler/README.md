# Cloudflare briefing scheduler

This Worker checks every five minutes for the latest due slot in `../../schedule.json`
(07:00, 13:00 and 19:00 Europe/Rome). It handles daylight saving locally; the
Cloudflare cron itself is UTC. It checks the published Pages JSON before dispatching
GitHub Actions, so a missed tick catches up on the next check.

A SQLite Durable Object serializes checks and remembers dispatch attempts. The
Worker waits for active GitHub builds, retries at intervals of at least 15 minutes,
and stops after three dispatches per slot. Successful publication clears the slot.
`/health` exposes read-only status and Cloudflare logs record check failures.
There is no public endpoint that can start a paid briefing build. Owner refresh
stays off until the flags below are set on purpose.

## Deploy and activate

1. Install dependencies: `npm ci` in this directory, then `npm run check`.
2. Authenticate to Cloudflare with `npx wrangler login`, or an API token with the
   Workers editing permissions required by Wrangler on the intended account.
3. Create a GitHub **fine-grained personal access token** with resource owner
   `grroo`, repository access **Only select repositories → News**, and repository
   permission **Actions: Read and write**. Metadata read is automatic. Set an
   expiry and renew it before expiration. No Contents write permission is needed.
4. For an existing Worker, run `npx wrangler secret put GITHUB_TOKEN` and enter
   the token at its secure prompt. Alternatively, save it as a **Secret** named
   `GITHUB_TOKEN` under the Worker's Settings → Variables and Secrets, then deploy
   the new version from Deployments. Saving a version alone does not activate it.
   Never commit the token.
5. Run `npm run deploy`. For a brand-new Worker with required secrets, supply
   `wrangler deploy --secrets-file /path/to/private/secrets.json` with a private
   file containing `{"GITHUB_TOKEN":"your-token"}`; remove that local file after
   deployment. Wrangler provisions the SQLite Durable Object and cron.
6. Allow up to 15 minutes for cron propagation. Confirm `/health` reports
   `configured: true` and a recent `checkedAt`, then verify an actual scheduled
   dispatch reaches `phase: published` and Pages shows the new briefing.
7. **Only after that verification**, remove `on.schedule` from
   `.github/workflows/build.yml`. The GitHub cron remains enabled during setup.

This removes dependency on GitHub's scheduled-event delivery. GitHub runner
queues, feed providers, the LLM, and Pages deployment can still delay publication.

`scheduled_slot` is passed to the workflow. Its guard skips a slot already live,
rejects obsolete queued requests, and redeploys committed current data without
another LLM call if a previous deployment failed. Manual runs with no slot still
generate normally.

## Operation

- `phase: published`: the real briefing for the latest due slot is live.
- `needs_github_secret`: add or restore the GitHub secret.
- `dispatched`, `waiting`, `build_in_progress`: update/retry in progress.
- `attention_needed`: three attempts did not publish; inspect Actions and Pages.
- `check_failed`: inspect the sanitized error and Cloudflare logs. HTTP 401/403
  typically requires checking the token's expiry and permissions.

The health endpoint provides diagnostics; it does not send email or notifications.
To roll back after cutover, remove the Cloudflare cron and restore GitHub's Rome
schedule. Changing `schedule.json` requires redeploying the Worker too.

## Authenticated refresh (off by default)

`REFRESH_ENABLED`, `RESERVATION_GATE_ENABLED`, and `STRICT_SLOT_GATE` are plain
vars and default to `false`. With those defaults the cron keeps the previous
slot check, `GET /health` stays public, and `/`, `POST /refresh`, and
`GET /refresh/:id` answer 404. Nothing in this repository turns the flags on,
creates Access applications, or writes secrets.

Do not set `REFRESH_ENABLED=true` until Cloudflare Access on the **deployed**
hostname is proven. That proof is still outstanding: this repo cannot see the
account's Access path policy. Required before enabling refresh:

- Access covers `GET /`, `POST /refresh`, and `GET /refresh/:id`.
- `GET /health` stays public and does not start a build.
- `POST /internal/reservations/claim` is **excluded** from browser Access. It
  accepts only `Authorization: Bearer` equal to the Worker secret
  `NEWS_RESERVATION_GATE_TOKEN` (constant-time compare). Do not add that name
  to `secrets.required` in `wrangler.jsonc`; a missing value must not block
  deploys that are still on the legacy cron.
- The Worker checks the Access JWT (`Cf-Access-Jwt-Assertion`): RS256 signature
  against `https://<ACCESS_TEAM_DOMAIN>.cloudflareaccess.com/cdn-cgi/access/certs`,
  issuer, audience (`ACCESS_AUD`), expiry, and `email` equal to `OWNER_EMAIL`.
  A service token (`common_name` without `email`) is rejected.

Set the non-secret vars in the dashboard or with `npx wrangler secret`/`vars`,
not in git, when you are ready:

| Name | Value |
|---|---|
| `ACCESS_TEAM_DOMAIN` | Access team name, without the `.cloudflareaccess.com` suffix |
| `ACCESS_AUD` | Access application audience tag (AUD) |
| `OWNER_EMAIL` | The one email allowed to refresh |
| `PAGES_SITE_URL` | Public briefing origin. `?return=` is accepted only when its origin matches this URL |
| `REFRESH_ENABLED` | `true` only after the Access checks above |
| `RESERVATION_GATE_ENABLED` | `true` together with refresh, after claim is deployed |
| `STRICT_SLOT_GATE` | leave `false` until editions are schema v2 |

`NEWS_RESERVATION_GATE_TOKEN` is a second Worker secret, shared with the GitHub
Actions secret of the same name. Generate it outside the repo and enter it with
`npx wrangler secret put NEWS_RESERVATION_GATE_TOKEN`. `GITHUB_TOKEN` stays the
fine-grained Actions read/write token for `grroo/News` only. Neither value
belongs in the browser, in logs, or in git.

Enable `RESERVATION_GATE_ENABLED` and `REFRESH_ENABLED` together, and only after
Access is proven. The gate makes every paid `build.yml` run claim a reservation
before it fetches or calls a model. `deploy_only` recovery does not call the
model and does not spend another generation allowance. `STRICT_SLOT_GATE=true`
switches publication from `mode === llm` to per-section outcomes; turn it on
only after v2 editions are what Pages serves, or the cron will treat legacy
briefings as unpublished.

Limits, stored before GitHub is called: 15 minutes between manual refreshes,
five manual requests per Europe/Rome day, eight generation runs per Rome day
with room left for scheduled slots that have not had a first attempt. Retries
count. The same idempotency key, or a second request while a reservation is
still open, joins that reservation and keeps its original `request_id`. A new
click does not invent an ID the workflow will never publish. A job becomes
succeeded, no-change, or degraded only when live Pages lists that job's own
request ID.

The owner page polls `GET /refresh/:id` on the Worker origin. It does not send
CORS headers, and `OPTIONS` stays 403, so the public Pages app cannot read
private status. After the job is terminal, the page returns to the validated
Pages URL with `request` and `job` query parameters and without a status URL.
The app then watches the public edition for that exact request ID. A `return`
value whose origin is not `PAGES_SITE_URL` is ignored.

A dispatch that times out keeps the reservation and the allowance. The Worker
binds a GitHub run only when `claimed_run_id` matches or the run name or title
contains `request_id`. A nearby `workflow_dispatch`, including the only one in
the last 20 runs, is not that reservation. Until a run is identified, the Worker
does not dispatch again and does not refund. If publication is still missing
when the confirmation window ends, the job expires and that slot stops retrying.
`build.yml` does not put `request_id` in `run-name` yet. Until it does, the
claim call's run ID is the identity reconciliation can use. Ask the workflow
owner to add that run name before relying on refresh in production. Publication
is the Pages edition whose `request_ids` contain that request, including
`no_change` and `degraded`. An Actions success alone does not finish the job.
