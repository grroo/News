# T03 — Repair feeds, price fallback and source freshness

Builder: Grok medium. Reviewer: Sol medium. Depends on T00. Own feeds.py and extracted ingestion/price modules/tests. T04 owns persistence/orchestration and config.

The installed-yfinance failure path currently does not try the direct Yahoo fallback. Repair the fallback for exceptions, empty or insufficient data and invalid values. Return each quote's source timestamp and comparison basis/session when available. Never imply that the briefing generation time makes an old quote live.

Feeds intermittently lose all six YouTube entries with HTTP 404, although intervening editions succeed. Instrument and reproduce retrieval behavior before changing channel IDs. Distinguish transport failure, parse failure, genuinely empty feeds and entries removed by date filtering. Isolate each feed's failure. Bound request timeout and response size, reject materially future-dated entries, and use bounded concurrency.

Implement conditional request metadata (ETag/Last-Modified) and a last-good-cache interface under the T00/T04 persistence contract. A 304 reuses valid cached content; an outage may use age-limited cached entries with explicit stale/source-health status. Old cache data must still respect article freshness policy. Do not introduce an unbounded retry storm or secretly fall back to scraping whole paywalled articles.

Acceptance: yfinance raises, returns empty and returns insufficient history each try the fallback; fresh valid results avoid unnecessary fallback; prices and timestamps stay paired; malformed one-feed XML cannot abort other sources; 304/no-cache behavior is defined; stale cache expires; future dates and oversized responses are bounded. Use recorded/synthetic fixtures offline. Report remaining uncertainty about YouTube rather than claiming a root cause without evidence.

