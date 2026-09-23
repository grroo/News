import sys
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from unittest.mock import patch

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ingestion
from ingestion import (
    FEED_MAX_BYTES,
    FEED_MAX_WORKERS,
    FEED_RETRY_STATUSES,
    FEED_RETRY_TOTAL,
    Fetcher,
    MemoryFeedCache,
    decode_feed_body,
    encode_feed_record,
    fetch_all,
)


NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)


def rss(title, published: datetime | None):
    pub = f"<pubDate>{format_datetime(published)}</pubDate>" if published else ""
    return (
        f"<?xml version='1.0'?><rss version='2.0'><channel><item><title>{title}</title>"
        f"<link>https://example.com/{title}</link><guid>https://example.com/{title}</guid>{pub}"
        f"<description>Summary</description></item></channel></rss>"
    ).encode()


def empty_rss():
    return b"<?xml version='1.0'?><rss version='2.0'><channel></channel></rss>"


class _Response:
    def __init__(self, status=200, body=b"", headers=None, chunks=None):
        self.status_code = status
        self.headers = headers or {}
        self.content = body
        self._chunks = chunks if chunks is not None else [body]
        self.closed = False
        self.read_started = False

    def iter_content(self, chunk_size=65536):
        self.read_started = True
        for chunk in self._chunks:
            yield chunk

    def close(self):
        self.closed = True


class FeedBehaviorTests(unittest.TestCase):
    def _fetcher(self, responses):
        fetcher = Fetcher(None, cache=MemoryFeedCache())
        calls = []

        def get(url, timeout=None, headers=None, stream=None):
            calls.append({"url": url, "timeout": timeout, "headers": headers or {}})
            result = responses[url]
            if isinstance(result, Exception):
                raise result
            return result

        fetcher.session.get = get
        fetcher.calls = calls
        return fetcher

    def test_failure_kinds_stay_distinct(self):
        good = rss("Live", NOW - timedelta(hours=1))
        responses = {
            "https://example.com/transport": requests.ConnectionError("reset"),
            "https://example.com/missing": _Response(404, b"<html>no such channel</html>", {"Content-Type": "text/html"}),
            "https://example.com/html": _Response(200, b"<html>Not RSS</html>", {"Content-Type": "text/html"}),
            "https://example.com/empty": _Response(200, empty_rss(), {"Content-Type": "application/rss+xml"}),
            "https://example.com/ok": _Response(200, good, {"Content-Type": "application/rss+xml", "ETag": "abc"}),
        }
        fetcher = self._fetcher(responses)
        sources = [
            {"name": "Transport", "url": "https://example.com/transport"},
            {"name": "Missing", "url": "https://example.com/missing"},
            {"name": "HTML", "url": "https://example.com/html"},
            {"name": "Empty", "url": "https://example.com/empty"},
            {"name": "Ok", "url": "https://example.com/ok"},
        ]
        items = fetch_all(fetcher, sources, "media", now=NOW)
        by_name = {row["name"]: row for row in fetcher.feed_health}
        self.assertEqual(by_name["Transport"]["failure"], "transport")
        self.assertEqual(by_name["Transport"]["status"], "unavailable")
        self.assertEqual(by_name["Missing"]["failure"], "http")
        self.assertEqual(by_name["Missing"]["reason"], "HTTP 404")
        self.assertIn("no such channel", by_name["Missing"]["diagnostic"]["body_prefix"])
        self.assertEqual(by_name["HTML"]["failure"], "parse")
        self.assertEqual(by_name["HTML"]["status"], "unavailable")
        self.assertEqual(by_name["Empty"]["status"], "empty")
        self.assertIsNone(by_name["Empty"]["failure"])
        self.assertEqual(by_name["Empty"]["parsed_count"], 0)
        self.assertEqual(by_name["Ok"]["status"], "ok")
        self.assertEqual(len(items), 1)
        self.assertEqual([row["name"] for row in fetcher.feed_health], [src["name"] for src in sources])

    def test_malformed_feed_does_not_abort_the_others(self):
        fetcher = Fetcher(None)
        with patch.object(fetcher, "get", side_effect=lambda url: rss("Kept", NOW) if url.endswith("good") else b"<html>nope</html>"):
            items = fetch_all(
                fetcher,
                [{"name": "Bad", "url": "https://example.com/bad"}, {"name": "Good", "url": "https://example.com/good"}],
                now=NOW,
            )
        self.assertEqual(len(items), 1)
        self.assertEqual(fetcher.feed_health[0]["status"], "unavailable")
        self.assertEqual(fetcher.feed_health[1]["status"], "ok")

    def test_unexpected_parser_error_is_isolated(self):
        fetcher = Fetcher(None)
        with patch.object(fetcher, "get", return_value=rss("X", NOW)), patch.object(
            ingestion, "parse_feed", side_effect=RuntimeError("boom")
        ):
            items = fetch_all(fetcher, [{"name": "Bad", "url": "https://example.com/a"}, {"name": "Also", "url": "https://example.com/b"}], now=NOW)
        self.assertEqual(items, [])
        self.assertEqual([row["failure"] for row in fetcher.feed_health], ["unexpected", "unexpected"])

    def test_future_dates_and_freshness_window(self):
        body = (
            b"<?xml version='1.0'?><rss><channel>"
            + rss("Soon", NOW + timedelta(hours=2)).split(b"<channel>", 1)[1].split(b"</channel>", 1)[0]
            + rss("Far", NOW + timedelta(days=3)).split(b"<channel>", 1)[1].split(b"</channel>", 1)[0]
            + rss("Old", NOW - timedelta(days=5)).split(b"<channel>", 1)[1].split(b"</channel>", 1)[0]
            + rss("Fresh", NOW - timedelta(hours=3)).split(b"<channel>", 1)[1].split(b"</channel>", 1)[0]
            + b"</channel></rss>"
        )
        fetcher = self._fetcher({"https://example.com/feed": _Response(200, body)})
        items = fetch_all(fetcher, [{"name": "Feed", "url": "https://example.com/feed"}], now=NOW, freshness_hours=36)
        self.assertEqual([item["title"] for item in items], ["Soon", "Fresh"])
        health = fetcher.feed_health[0]
        self.assertEqual(health["status"], "ok")
        self.assertEqual(health["dropped_future"], 1)
        self.assertEqual(health["dropped_freshness"], 1)
        self.assertEqual(health["parsed_count"], 4)

        only_future = rss("Far", NOW + timedelta(days=2))
        fetcher = self._fetcher({"https://example.com/feed": _Response(200, only_future)})
        items = fetch_all(fetcher, [{"name": "Feed", "url": "https://example.com/feed"}], now=NOW)
        self.assertEqual(items, [])
        self.assertEqual(fetcher.feed_health[0]["status"], "filtered")
        self.assertEqual(fetcher.feed_health[0]["reason"], "entries removed by date filtering")

    def test_304_reuses_cache_and_304_without_cache_is_explicit(self):
        body = rss("Cached", NOW - timedelta(hours=1))
        cache = MemoryFeedCache()
        cache.put("https://example.com/feed", encode_feed_record(body, etag='"v1"', last_modified="Tue, 22 Sep 2026 12:00:00 GMT", fetched_at="2026-09-22T12:00:00+00:00"))
        fetcher = Fetcher(None, cache=cache)
        seen = {}

        def get(url, timeout=None, headers=None, stream=None):
            seen["headers"] = headers
            seen["timeout"] = timeout
            return _Response(304, b"", {"ETag": '"v1"'})

        fetcher.session.get = get
        items = fetch_all(fetcher, [{"name": "Feed", "url": "https://example.com/feed"}], now=NOW)
        self.assertEqual(seen["headers"]["If-None-Match"], '"v1"')
        self.assertEqual(seen["headers"]["If-Modified-Since"], "Tue, 22 Sep 2026 12:00:00 GMT")
        self.assertEqual(seen["timeout"], ingestion.FEED_TIMEOUT)
        self.assertEqual(items[0]["title"], "Cached")
        self.assertEqual(fetcher.feed_health[0]["status"], "ok")
        self.assertEqual(fetcher.feed_health[0]["cache"], "not_modified")
        self.assertEqual(cache.get("https://example.com/feed")["fetched_at"], NOW.isoformat())

        bare = Fetcher(None)
        bare.session.get = lambda url, timeout=None, headers=None, stream=None: _Response(304, b"")
        fetch_all(bare, [{"name": "Feed", "url": "https://example.com/feed"}], now=NOW)
        self.assertEqual(bare.feed_health[0]["status"], "unavailable")
        self.assertEqual(bare.feed_health[0]["failure"], "no_cache")
        self.assertIn("304", bare.feed_health[0]["reason"])

    def test_stale_cache_is_age_limited_and_still_filtered(self):
        fresh_body = rss("Cached", NOW - timedelta(hours=2))
        old_articles = rss("Ancient", NOW - timedelta(days=10))
        cache = MemoryFeedCache()
        cache.put(
            "https://example.com/fresh",
            encode_feed_record(fresh_body, etag=None, last_modified=None, fetched_at=(NOW - timedelta(hours=2)).isoformat()),
        )
        cache.put(
            "https://example.com/expired",
            encode_feed_record(fresh_body, etag=None, last_modified=None, fetched_at=(NOW - timedelta(days=10)).isoformat()),
        )
        cache.put(
            "https://example.com/ancient",
            encode_feed_record(old_articles, etag=None, last_modified=None, fetched_at=(NOW - timedelta(hours=1)).isoformat()),
        )

        def get(url, timeout=None, headers=None, stream=None):
            raise requests.Timeout("slow")

        fetcher = Fetcher(None, cache=cache)
        fetcher.session.get = get
        items = fetch_all(
            fetcher,
            [
                {"name": "Fresh", "url": "https://example.com/fresh"},
                {"name": "Expired", "url": "https://example.com/expired"},
                {"name": "Ancient", "url": "https://example.com/ancient"},
            ],
            now=NOW,
            freshness_hours=36,
        )
        by_name = {row["name"]: row for row in fetcher.feed_health}
        self.assertEqual(by_name["Fresh"]["status"], "stale")
        self.assertEqual(by_name["Fresh"]["failure"], "transport")
        self.assertEqual(by_name["Expired"]["status"], "unavailable")
        self.assertEqual(by_name["Expired"]["cache"], "expired")
        self.assertEqual(by_name["Ancient"]["status"], "filtered")
        self.assertEqual(by_name["Ancient"]["cache"], "stale")
        self.assertEqual(by_name["Ancient"]["item_count"], 0)
        self.assertEqual([item["title"] for item in items], ["Cached"])
        self.assertIsNone(decode_feed_body({"body_b64": "%%%"}))

    def test_oversize_response_is_rejected_without_reading_past_the_cap(self):
        huge = _Response(200, b"", headers={"Content-Length": str(FEED_MAX_BYTES + 1)})
        fetcher = self._fetcher({"https://example.com/big": huge})
        items = fetch_all(fetcher, [{"name": "Big", "url": "https://example.com/big"}], now=NOW)
        self.assertEqual(items, [])
        self.assertEqual(fetcher.feed_health[0]["failure"], "oversize")
        self.assertFalse(huge.read_started)

        chunks = [b"x" * (FEED_MAX_BYTES // 2 + 10), b"y" * (FEED_MAX_BYTES // 2 + 10)]
        streamed = _Response(200, b"", chunks=chunks)
        fetcher = self._fetcher({"https://example.com/big": streamed})
        fetch_all(fetcher, [{"name": "Big", "url": "https://example.com/big"}], now=NOW)
        self.assertEqual(fetcher.feed_health[0]["failure"], "oversize")

    def test_retries_are_bounded_and_concurrency_is_capped(self):
        fetcher = Fetcher(None)
        retry = fetcher.session.get_adapter("https://example.com").max_retries
        self.assertEqual(retry.total, FEED_RETRY_TOTAL)
        self.assertNotIn(404, FEED_RETRY_STATUSES)
        self.assertTrue(retry.is_retry("GET", 503, has_retry_after=False))
        self.assertFalse(retry.is_retry("GET", 404, has_retry_after=False))

        current = {"n": 0, "max": 0}
        lock = threading.Lock()
        fetcher = Fetcher(None)

        def get(url):
            with lock:
                current["n"] += 1
                current["max"] = max(current["max"], current["n"])
            time.sleep(0.02)
            with lock:
                current["n"] -= 1
            return empty_rss()

        with patch.object(fetcher, "get", side_effect=get):
            fetch_all(fetcher, [{"name": f"S{i}", "url": f"https://example.com/{i}"} for i in range(20)], now=NOW)
        self.assertLessEqual(current["max"], FEED_MAX_WORKERS)
        self.assertGreater(current["max"], 1)

    def test_fallback_still_wins_over_a_usable_stale_primary(self):
        cache = MemoryFeedCache()
        cache.put(
            "https://example.com/publisher",
            encode_feed_record(
                rss("Stale primary", NOW - timedelta(hours=1)),
                etag=None,
                last_modified=None,
                fetched_at=(NOW - timedelta(hours=1)).isoformat(),
            ),
        )
        fetcher = Fetcher(None, cache=cache)

        def get(url, timeout=None, headers=None, stream=None):
            if "example.com/publisher" in url:
                return _Response(404, b"missing", {"Content-Type": "text/plain"})
            return _Response(200, rss("Fallback", NOW - timedelta(minutes=30)))

        fetcher.session.get = get
        items = fetch_all(
            fetcher,
            [{"name": "Les Echos", "url": "https://example.com/publisher", "fallback_query": "site:lesechos.fr", "fallback_lang": "fr"}],
            "finance",
            now=NOW,
        )
        self.assertEqual(fetcher.feed_health[0]["status"], "fallback")
        self.assertEqual(items[0]["title"], "Fallback")


if __name__ == "__main__":
    unittest.main()
