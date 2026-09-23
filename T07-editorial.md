# T07 — Improve what reaches the model

Builder: Luna medium or Composer Standard. Reviewer: Sol medium. Depends on T00 extraction. Own selection module/tests; request config changes from T04. Keep new selection behind a flag until T08 finishes the fixed-input Luna comparison.

The latest reviewed edition had 505 news, 288 sport and 279 finance candidates; only 35/category reach the model, selected mainly by newness/recency. Improve deterministic relevance before that cutoff. Include topic relevance to configured interests, publisher normalization and configurable diversity constraints. Preserve CulturePSG reservations and current explicit preferences. Do not assume all FT finance selections are unwanted: make diversity policy explicit rather than overriding the user's preferences arbitrarily.

Improve duplicate-story handling across publisher/direct/Google News forms without unsafe arbitrary URL following. Preserve distinct follow-up reporting when genuinely new. Rank deterministically with tie-breaking and explainable components. Bound source excerpt length, using legitimately available feed text; do not add a browsing tool or full-article scraping system.

Acceptance: representative fixtures retain important older-within-window stories amid high-volume feeds; normalized publishers do not bypass caps; sports preferences hold; duplicate stories reduce without deleting meaningful updates; sparse sections degrade sensibly; deterministic inputs produce deterministic candidate order. Compare coverage/diversity against the current algorithm. Coordinate a separate evaluation toggle with T08 so provider and ranking changes can be attributed independently. Do not simply enlarge every prompt as the first optimization.

