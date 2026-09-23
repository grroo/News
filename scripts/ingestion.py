"""Feed fetching helpers. Owned by T03 after T00 extraction."""
from __future__ import annotations

import json
import sys
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from feeds import parse_feed

UA = "Mozilla/5.0 (compatible; personal-briefing/1.0; +https://github.com)"
_GN_LOCALES = {"en": ("en-US", "US"), "fr": ("fr", "FR"), "it": ("it", "IT"), "de": ("de", "DE"), "es": ("es", "ES")}

log = lambda *a: print(*a, file=sys.stderr, flush=True)  # noqa: E731


class Fetcher:
    def __init__(self, fixtures: Path | None):
        self.fixtures = fixtures
        self.fixture_map = {}
        if fixtures:
            self.fixture_map = json.loads((fixtures / "map.json").read_text())
        self.session = requests.Session()
        self.session.headers["User-Agent"] = UA
        retry = Retry(
            total=2,
            backoff_factor=0.5,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["GET"],
            respect_retry_after_header=False,
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retry))
        self.errors = {}
        self.feed_health = []

    def get(self, url: str) -> bytes | None:
        if self.fixtures:
            name = self.fixture_map.get(url)
            if not name:
                log(f"  [fixture missing] {url}")
                self.errors[url] = "Fixture unavailable"
                return None
            return (self.fixtures / name).read_bytes()
        try:
            r = self.session.get(url, timeout=20)
            r.raise_for_status()
            return r.content
        except requests.RequestException as e:
            status = getattr(e.response, "status_code", None)
            self.errors[url] = f"HTTP {status}" if status else e.__class__.__name__
            log(f"  [fetch failed] {url} → {self.errors[url]}")
            return None

    def get_json(self, url: str):
        raw = self.get(url)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None


def google_news_rss(query: str, lang: str = "en") -> str:
    hl, gl = _GN_LOCALES.get(lang.lower()[:2], ("en-US", "US"))
    q = urllib.parse.quote_plus(query)
    return f"https://news.google.com/rss/search?q={q}&hl={hl}&gl={gl}&ceid={gl}:{hl.split('-')[0]}"


def topic_sources(entries, prefix: str) -> list[dict]:
    out = []
    for e in entries or []:
        if isinstance(e, dict):
            out.append({"name": f"{prefix}: {e['query']}", "url": google_news_rss(e["query"], e.get("lang", "en"))})
        else:
            out.append({"name": f"{prefix}: {e}", "url": google_news_rss(str(e))})
    return out


def fetch_all(fetcher: Fetcher, sources: list[dict], section: str = "") -> list[dict]:
    items: list[dict] = []

    def one(src):
        raw = fetcher.get(src["url"])
        try:
            parsed = parse_feed(raw, src["name"], strict=True) if raw is not None else []
            status = ("ok" if parsed else "empty") if raw is not None else "unavailable"
        except ValueError:
            parsed, status = [], "unavailable"
        reason = fetcher.errors.get(src["url"], "No readable feed") if status == "unavailable" else None
        if status == "unavailable" and src.get("fallback_query"):
            fallback = google_news_rss(src["fallback_query"], src.get("fallback_lang", "en"))
            raw = fetcher.get(fallback)
            parsed = parse_feed(raw, src["name"]) if raw is not None else []
            if parsed:
                status = "fallback"
        health = {
            "name": src["name"],
            "section": section,
            "status": status,
            "item_count": len(parsed),
            "reason": reason,
        }
        log(f"  {len(parsed):3d} items  {src['name']}")
        return parsed, health

    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(one, s) for s in sources]
        for f in futs:
            parsed, health = f.result()
            items.extend(parsed)
            fetcher.feed_health.append(health)
    return items
