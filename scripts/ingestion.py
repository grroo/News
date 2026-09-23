"""Feed fetching helpers. Owned by T03 after T00 extraction.

Last-good feed bytes are a T04 persistence concern. This module defines the
cache record and applies it. T04 stores the records across ephemeral runners;
Actions cache is not the store. Article dates are filtered again when a cached
body is reused. Full-article scraping is not a feed fallback.
"""
from __future__ import annotations

import base64
import json
import sys
import threading
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from feeds import parse_date, parse_feed

UA = "Mozilla/5.0 (compatible; personal-briefing/1.0; +https://github.com)"
_GN_LOCALES = {"en": ("en-US", "US"), "fr": ("fr", "FR"), "it": ("it", "IT"), "de": ("de", "DE"), "es": ("es", "ES")}

# Bounded on purpose: two retries for transient 5xx only. 404 is not retried.
FEED_MAX_WORKERS = 8
FEED_TIMEOUT = (5, 20)
FEED_MAX_BYTES = 2_000_000
FEED_RETRY_TOTAL = 2
FEED_RETRY_STATUSES = (500, 502, 503, 504)
# Entries further ahead than this are not "fresh"; clock skew is smaller.
FUTURE_SKEW = timedelta(hours=12)
# Outage reuse limit. A 304 revalidation is a live confirmation, not this path.
DEFAULT_MAX_STALE = timedelta(hours=36)

log = lambda *a: print(*a, file=sys.stderr, flush=True)  # noqa: E731


class PayloadTooLarge(Exception):
    pass


def google_news_rss(query: str, lang: str = "en") -> str:
    hl, gl = _GN_LOCALES.get(lang.lower()[:2], ("en-US", "US"))
    q = urllib.parse.quote_plus(query)
    return f"https://news.google.com/rss/search?q={q}&hl={hl}&gl={gl}&ceid={gl}:{hl.split('-')[0]}"


def topic_sources(entries, prefix: str) -> list[dict]:
    out = []
    for entry in entries or []:
        if isinstance(entry, dict):
            out.append({"name": f"{prefix}: {entry['query']}", "url": google_news_rss(entry["query"], entry.get("lang", "en"))})
        else:
            out.append({"name": f"{prefix}: {entry}", "url": google_news_rss(str(entry))})
    return out


def encode_feed_record(body: bytes, *, etag: str | None, last_modified: str | None, fetched_at: str) -> dict:
    """JSON record T04 can persist. Separate from public reading history."""
    return {
        "etag": etag,
        "last_modified": last_modified,
        "fetched_at": fetched_at,
        "body_b64": base64.b64encode(body).decode("ascii"),
    }


def decode_feed_body(record: dict | None) -> bytes | None:
    if not record or not isinstance(record.get("body_b64"), str):
        return None
    try:
        return base64.b64decode(record["body_b64"], validate=True)
    except (ValueError, TypeError):
        return None


def feed_record_is_fresh(record: dict | None, now: datetime, max_age: timedelta) -> bool:
    fetched = parse_date((record or {}).get("fetched_at"))
    if fetched is None:
        return False
    age = now - fetched
    return timedelta(0) <= age <= max_age


class MemoryFeedCache:
    """In-memory stand-in for tests. T04 supplies the durable implementation.

    `get` / `put` may run concurrently for different URLs.
    """

    def __init__(self):
        self.records: dict[str, dict] = {}
        self._lock = threading.Lock()

    def get(self, url: str) -> dict | None:
        with self._lock:
            record = self.records.get(url)
            return dict(record) if record else None

    def put(self, url: str, record: dict) -> None:
        with self._lock:
            self.records[url] = dict(record)


@dataclass
class FeedResponse:
    body: bytes | None = None
    failure: str | None = None
    reason: str | None = None
    status_code: int | None = None
    cache: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    fetched_at: str | None = None
    diagnostic: dict | None = None
    stale_record: dict | None = None
    store: bool = False


def _header(response, name: str) -> str | None:
    headers = getattr(response, "headers", {}) or {}
    value = headers.get(name) if hasattr(headers, "get") else None
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _diagnostic(response, limit: int = 160) -> dict:
    content_type = _header(response, "Content-Type")
    prefix = ""
    try:
        if hasattr(response, "iter_content"):
            for chunk in response.iter_content(limit):
                if chunk:
                    prefix = bytes(chunk)[:limit].decode("utf-8", "replace")
                break
        elif getattr(response, "content", None):
            prefix = bytes(response.content)[:limit].decode("utf-8", "replace")
    except Exception:
        prefix = ""
    return {
        "status_code": getattr(response, "status_code", None),
        "content_type": content_type,
        "body_prefix": prefix.replace("\n", " ")[:limit],
    }


def _read_limited(response, max_bytes: int) -> bytes:
    length = _header(response, "Content-Length")
    if length is not None and length.isdigit() and int(length) > max_bytes:
        raise PayloadTooLarge()
    chunks: list[bytes] = []
    total = 0
    if hasattr(response, "iter_content"):
        iterator = response.iter_content(65536)
    else:
        iterator = [getattr(response, "content", b"") or b""]
    for chunk in iterator:
        if not chunk:
            continue
        total += len(chunk)
        if total > max_bytes:
            raise PayloadTooLarge()
        chunks.append(bytes(chunk))
    return b"".join(chunks)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def apply_article_window(items: list[dict], now: datetime, freshness_hours: float | None) -> tuple[list[dict], int, int]:
    """Drop materially future entries, and stale ones when a window is given.

    Missing timestamps stay in the list so existing callers can keep applying
    their own lookback. A cached body uses the same rules as a live body.
    """
    now = _aware(now)
    future_cutoff = now + FUTURE_SKEW
    age_cutoff = now - timedelta(hours=freshness_hours) if freshness_hours is not None else None
    kept: list[dict] = []
    dropped_future = 0
    dropped_freshness = 0
    for item in items:
        published = item.get("published")
        if not published:
            kept.append(item)
            continue
        try:
            stamp = _aware(datetime.fromisoformat(published))
        except ValueError:
            kept.append(item)
            continue
        if stamp > future_cutoff:
            dropped_future += 1
            continue
        if age_cutoff is not None and stamp < age_cutoff:
            dropped_freshness += 1
            continue
        kept.append(item)
    return kept, dropped_future, dropped_freshness


class Fetcher:
    def __init__(self, fixtures: Path | None, cache: MemoryFeedCache | None = None, *, max_stale: timedelta = DEFAULT_MAX_STALE, max_bytes: int = FEED_MAX_BYTES):
        self.fixtures = fixtures
        self.cache = cache
        self.max_stale = max_stale
        self.max_bytes = max_bytes
        self.fixture_map = {}
        if fixtures:
            self.fixture_map = json.loads((fixtures / "map.json").read_text())
        self.session = requests.Session()
        self.session.headers["User-Agent"] = UA
        retry = Retry(
            total=FEED_RETRY_TOTAL,
            backoff_factor=0.5,
            status_forcelist=list(FEED_RETRY_STATUSES),
            allowed_methods=["GET"],
            respect_retry_after_header=False,
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retry))
        self.session.mount("http://", HTTPAdapter(max_retries=retry))
        self.errors: dict[str, str] = {}
        self.outcomes: dict[str, FeedResponse] = {}
        self.feed_health: list[dict] = []
        self.request_time: datetime | None = None

    def _lookup(self, url: str) -> dict | None:
        if self.cache is None:
            return None
        record = self.cache.get(url)
        return record if isinstance(record, dict) else None

    def _request(self, url: str, now: datetime) -> FeedResponse:
        record = self._lookup(url)
        headers = {}
        cached_body = decode_feed_body(record)
        if record and cached_body is not None:
            if record.get("etag"):
                headers["If-None-Match"] = record["etag"]
            if record.get("last_modified"):
                headers["If-Modified-Since"] = record["last_modified"]
        stale = record if cached_body is not None and feed_record_is_fresh(record, now, self.max_stale) else None
        expired = record if record and cached_body is not None and stale is None else None
        try:
            response = self.session.get(url, timeout=FEED_TIMEOUT, headers=headers, stream=True)
        except requests.RequestException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status:
                return FeedResponse(
                    failure="http",
                    reason=f"HTTP {status}",
                    status_code=status,
                    cache="expired" if expired else None,
                    stale_record=stale,
                    diagnostic=_diagnostic(exc.response) if getattr(exc, "response", None) is not None else None,
                )
            return FeedResponse(
                failure="transport",
                reason=exc.__class__.__name__,
                cache="expired" if expired else None,
                stale_record=stale,
            )

        try:
            status = getattr(response, "status_code", None)
            etag = _header(response, "ETag") or (record or {}).get("etag")
            last_modified = _header(response, "Last-Modified") or (record or {}).get("last_modified")
            if status == 304:
                if cached_body is None:
                    return FeedResponse(
                        failure="no_cache",
                        reason="HTTP 304 without cached content",
                        status_code=304,
                        cache="absent",
                    )
                return FeedResponse(
                    body=cached_body,
                    status_code=304,
                    cache="not_modified",
                    etag=etag,
                    last_modified=last_modified,
                    fetched_at=now.astimezone(timezone.utc).isoformat(),
                    store=True,
                )
            if status is not None and int(status) >= 400:
                diagnostic = _diagnostic(response)
                kind = "http"
                return FeedResponse(
                    failure=kind,
                    reason=f"HTTP {status}",
                    status_code=int(status),
                    cache="expired" if expired else None,
                    diagnostic=diagnostic,
                    stale_record=stale,
                )
            try:
                body = _read_limited(response, self.max_bytes)
            except PayloadTooLarge:
                return FeedResponse(
                    failure="oversize",
                    reason=f"response exceeds {self.max_bytes} bytes",
                    status_code=status,
                    cache="expired" if expired else None,
                    stale_record=stale,
                )
            return FeedResponse(
                body=body,
                status_code=status,
                cache="refreshed" if self.cache is not None else None,
                etag=_header(response, "ETag"),
                last_modified=_header(response, "Last-Modified"),
                fetched_at=now.astimezone(timezone.utc).isoformat(),
                store=self.cache is not None,
            )
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()

    def get(self, url: str) -> bytes | None:
        if self.fixtures:
            name = self.fixture_map.get(url)
            if not name:
                log(f"  [fixture missing] {url}")
                self.errors[url] = "Fixture unavailable"
                return None
            return (self.fixtures / name).read_bytes()
        outcome = self._request(url, self.request_time or datetime.now(timezone.utc))
        self.outcomes[url] = outcome
        if outcome.body is None:
            self.errors[url] = outcome.reason or outcome.failure or "Request failed"
            detail = ""
            if outcome.diagnostic and outcome.diagnostic.get("body_prefix"):
                detail = f" {outcome.diagnostic['content_type'] or ''} {outcome.diagnostic['body_prefix']}"
            log(f"  [fetch failed] {url} → {self.errors[url]}{detail}".rstrip())
            return None
        return outcome.body

    def get_json(self, url: str):
        raw = self.get(url)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None


def _health(src: dict, section: str, *, status: str, item_count: int, reason, failure, parsed_count: int, dropped_future: int, dropped_freshness: int, cache, diagnostic=None) -> dict:
    health = {
        "name": src["name"],
        "section": section,
        "status": status,
        "item_count": item_count,
        "reason": reason,
        "failure": failure,
        "parsed_count": parsed_count,
        "dropped_future": dropped_future,
        "dropped_freshness": dropped_freshness,
        "cache": cache,
    }
    if diagnostic:
        health["diagnostic"] = diagnostic
    return health


def _store(fetcher: Fetcher, url: str, outcome: FeedResponse | None) -> None:
    if fetcher.cache is None or outcome is None or not outcome.store or outcome.body is None or not outcome.fetched_at:
        return
    fetcher.cache.put(
        url,
        encode_feed_record(outcome.body, etag=outcome.etag, last_modified=outcome.last_modified, fetched_at=outcome.fetched_at),
    )


def _classify(parsed: list[dict], kept: list[dict], dropped_future: int, dropped_freshness: int, *, failure, used_stale: bool, fallback: bool) -> str:
    if failure == "parse":
        return "unavailable"
    if not kept and (dropped_future or dropped_freshness):
        return "filtered"
    if used_stale:
        return "stale"
    if fallback and kept:
        return "fallback"
    if not parsed:
        return "empty"
    return "ok"


def fetch_all(fetcher: Fetcher, sources: list[dict], section: str = "", *, now: datetime | None = None, freshness_hours: float | None = None) -> list[dict]:
    """Fetch each source independently. One failure does not cancel the others."""
    clock = _aware(now or datetime.now(timezone.utc))
    previous_clock = getattr(fetcher, "request_time", None)
    fetcher.request_time = clock
    items: list[dict] = []

    def one(src):
        url = src["url"]
        try:
            raw = fetcher.get(url)
            outcome = fetcher.outcomes.get(url)
            failure = None
            reason = None
            cache_state = outcome.cache if outcome else None
            diagnostic = outcome.diagnostic if outcome else None
            used_stale = False
            fallback = False
            parsed: list[dict] = []

            if raw is None and src.get("fallback_query"):
                fallback_url = google_news_rss(src["fallback_query"], src.get("fallback_lang", "en"))
                fallback_raw = fetcher.get(fallback_url)
                fallback_parsed: list[dict] = []
                if fallback_raw is not None:
                    try:
                        fallback_parsed = parse_feed(fallback_raw, src["name"])
                    except ValueError:
                        fallback_parsed = []
                if fallback_parsed:
                    raw = fallback_raw
                    parsed = fallback_parsed
                    fallback = True
                    url = fallback_url
                    outcome = fetcher.outcomes.get(fallback_url)
                    cache_state = outcome.cache if outcome else None
                    diagnostic = None
                    failure = None

            if raw is None and outcome is not None and outcome.stale_record is not None:
                cached = decode_feed_body(outcome.stale_record)
                if cached is not None:
                    raw = cached
                    used_stale = True
                    cache_state = "stale"
                    failure = outcome.failure
                    reason = f"cached feed after {outcome.reason}; fetched_at {outcome.stale_record.get('fetched_at')}"
                    diagnostic = outcome.diagnostic

            if raw is None:
                failure = (outcome.failure if outcome else None) or "transport"
                reason = fetcher.errors.get(src["url"]) or (outcome.reason if outcome else None) or "No readable feed"
                cache_state = outcome.cache if outcome else cache_state
                diagnostic = outcome.diagnostic if outcome else diagnostic
                health = _health(
                    src, section, status="unavailable", item_count=0, reason=reason, failure=failure,
                    parsed_count=0, dropped_future=0, dropped_freshness=0, cache=cache_state, diagnostic=diagnostic,
                )
                log(f"    0 items  {src['name']}  [unavailable/{failure}]")
                return [], health

            if not fallback:
                try:
                    parsed = parse_feed(raw, src["name"], strict=True)
                except ValueError:
                    failure = "parse"
                    parsed = []
            kept, dropped_future, dropped_freshness = apply_article_window(parsed, clock, freshness_hours)
            if failure != "parse" and not used_stale:
                _store(fetcher, url, outcome)
            status = _classify(
                parsed, kept, dropped_future, dropped_freshness,
                failure=failure, used_stale=used_stale, fallback=fallback,
            )
            if status == "filtered":
                reason = "entries removed by date filtering"
            elif status == "unavailable":
                reason = "Invalid feed XML" if failure == "parse" else (reason or "No readable feed")
            elif status == "stale":
                reason = reason or "cached feed after origin failure"
            health = _health(
                src, section, status=status, item_count=len(kept), reason=reason, failure=failure,
                parsed_count=len(parsed), dropped_future=dropped_future, dropped_freshness=dropped_freshness,
                cache=cache_state, diagnostic=diagnostic if status in ("unavailable", "stale") else None,
            )
            suffix = f"  [{status}]" if status != "ok" else ""
            log(f"  {len(kept):3d} items  {src['name']}{suffix}")
            return kept, health
        except Exception as exc:
            log(f"  [feed failed] {src.get('name')} → {exc.__class__.__name__}")
            health = _health(
                src, section, status="unavailable", item_count=0, reason=exc.__class__.__name__, failure="unexpected",
                parsed_count=0, dropped_future=0, dropped_freshness=0, cache=None,
            )
            return [], health

    results: list[tuple[list[dict], dict] | None] = [None] * len(sources)
    workers = max(1, min(FEED_MAX_WORKERS, len(sources) or 1))
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [(index, pool.submit(one, src)) for index, src in enumerate(sources)]
            for index, future in futures:
                try:
                    results[index] = future.result()
                except Exception as exc:
                    src = sources[index]
                    log(f"  [feed failed] {src.get('name')} → {exc.__class__.__name__}")
                    results[index] = (
                        [],
                        _health(
                            src, section, status="unavailable", item_count=0, reason=exc.__class__.__name__, failure="unexpected",
                            parsed_count=0, dropped_future=0, dropped_freshness=0, cache=None,
                        ),
                    )
    finally:
        fetcher.request_time = previous_clock
    for result in results:
        if result is None:
            continue
        parsed, health = result
        items.extend(parsed)
        fetcher.feed_health.append(health)
    return items
