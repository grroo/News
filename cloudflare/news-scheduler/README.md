# Cloudflare briefing scheduler

This Worker checks every five minutes for the latest due slot in `../../schedule.json`
(07:00, 13:00 and 19:00 Europe/Rome). It handles daylight saving locally; the
Cloudflare cron itself is UTC. It checks the published Pages JSON before dispatching
GitHub Actions, so a missed tick catches up on the next check.

A SQLite Durable Object serializes checks and remembers dispatch attempts. The
Worker waits for active GitHub builds, retries at intervals of at least 15 minutes,
and stops after three dispatches per slot. Successful publication clears the slot.
`/health` exposes read-only status and Cloudflare logs record check failures.
There is no public endpoint that can start a paid briefing build.

## Deploy and activate

1. Install dependencies: `npm ci` in this directory, then `npm run check`.
2. Authenticate to Cloudflare with `npx wrangler login`, or an API token with the
   Workers editing permissions required by Wrangler on the intended account.
3. Create a GitHub **fine-grained personal access token** with resource owner
   `grroo`, repository access **Only select repositories → News**, and repository
   permission **Actions: Read and write**. Metadata read is automatic. Set an
   expiry and renew it before expiration. No Contents write permission is needed.
4. Run `npx wrangler secret put GITHUB_TOKEN` and enter the token at its secure
   prompt. Alternatively, save it as a **Secret** named `GITHUB_TOKEN` under the
   deployed Worker's Settings → Variables and Secrets. Never commit the token.
5. Run `npm run deploy`. Wrangler provisions the SQLite Durable Object and cron.
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
