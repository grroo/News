# T06 — Clear refresh experience and resilient reading

Builder: Composer Standard. Reviewer: Sol medium. Depends on T00 fixtures; integrate T04/T05. Own site files and browser tests. Preserve the small plain-JavaScript app and current visual style.

Rename the existing published-JSON reload action to Check for updates. Add Fetch new briefing leading to the protected owner flow agreed in T00/T05. Do not embed API or GitHub credentials. If the first version uses a separate owner control page, clearly explain sign-in and return-to-app behavior rather than implying a public button fetches instantly.

Show queued, building, publishing, completed, no-new-content, degraded, failed and rate-limited states. Keep the current briefing readable. Show per-section age when reused/stale, and distinguish quote timestamp from briefing time. Poll status with backoff, a maximum duration and visibility handling. Stop on terminal states and do not start multiple pollers. Success requires exact request acknowledgment. Minute-by-minute normal polling stays read-only.

Guard all localStorage reads/writes and retain usable in-memory state when storage is denied. Improve focus handling, keyboard operation, status announcements and finance table labels. Avoid implementation jargon in user-facing text. Support old archive data gracefully.

Optional second PR after the core refresh UI: offline reopening with cached app shell and last-good briefing. Include cache/schema versioning, stale/offline labels and recovery on reconnect. Never cache owner authentication responses, refresh endpoints or secrets. Keep this separate if development quota is tight.

Acceptance: browser tests cover two-click attempts, timeouts, failure, rate limit, no-change, unrelated newer edition, stale partial sections, archive navigation and denied storage. Verify mobile/desktop layout and accessible status feedback. For offline PR, verify cold reopening after a prior successful visit; first-ever offline use can show an explicit unavailable state. Use fixtures first, then one integrated staging path.

