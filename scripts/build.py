#!/usr/bin/env python3
"""
Build the briefing.

    python scripts/build.py                # full run (needs ANTHROPIC_API_KEY)
    python scripts/build.py --mock         # skip the LLM, pick newest items
    python scripts/build.py --fixtures dir # read feeds from local files (tests)

Reads config.yml, fetches every feed, dedupes against data/seen.json, asks
Claude for one briefing per section, writes data/briefing.json and rotates the
previous briefing into data/past/.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import yaml

sys.path.insert(0, str(Path(__file__).parent))
from feeds import parse_feed  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yml"
DATA_DIR = ROOT / "data"
PAST_DIR = DATA_DIR / "past"
SEEN_PATH = DATA_DIR / "seen.json"
OUT_PATH = DATA_DIR / "briefing.json"

UA = "Mozilla/5.0 (compatible; personal-briefing/1.0; +https://github.com)"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
CANDIDATE_SUMMARY_CHARS = 160  # keep the prompt small → keeps cost small

log = lambda *a: print(*a, file=sys.stderr, flush=True)  # noqa: E731


# ── fetching ────────────────────────────────────────────────────────────────
class Fetcher:
    def __init__(self, fixtures: Path | None):
        self.fixtures = fixtures
        self.fixture_map = {}
        if fixtures:
            self.fixture_map = json.loads((fixtures / "map.json").read_text())
        self.session = requests.Session()
        self.session.headers["User-Agent"] = UA

    def get(self, url: str) -> bytes | None:
        if self.fixtures:
            name = self.fixture_map.get(url)
            if not name:
                log(f"  [fixture missing] {url}")
                return None
            return (self.fixtures / name).read_bytes()
        try:
            r = self.session.get(url, timeout=20)
            r.raise_for_status()
            return r.content
        except requests.RequestException as e:
            log(f"  [fetch failed] {url} → {e.__class__.__name__}")
            return None

    def get_json(self, url: str):
        raw = self.get(url)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None


def google_news_rss(query: str, lang: str = "en-US", country: str = "US") -> str:
    q = urllib.parse.quote_plus(query)
    return f"https://news.google.com/rss/search?q={q}&hl={lang}&gl={country}&ceid={country}:{lang.split('-')[0]}"


def fetch_all(fetcher: Fetcher, sources: list[dict]) -> list[dict]:
    """sources: [{name, url}] → flat item list, fetched concurrently."""
    items: list[dict] = []

    def one(src):
        raw = fetcher.get(src["url"])
        if raw is None:
            return []
        parsed = parse_feed(raw, src["name"])
        log(f"  {len(parsed):3d} items  {src['name']}")
        return parsed

    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(one, s) for s in sources]
        for f in as_completed(futs):
            items.extend(f.result())
    return items


# ── dedupe ──────────────────────────────────────────────────────────────────
_TRACKING = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "fbclid", "gclid", "ref", "cmpid"}


def canonical_url(url: str) -> str:
    p = urllib.parse.urlsplit(url.strip())
    q = [(k, v) for k, v in urllib.parse.parse_qsl(p.query) if k.lower() not in _TRACKING]
    return urllib.parse.urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"), urllib.parse.urlencode(q), ""))


def title_key(title: str) -> str:
    # Google News appends " - Outlet"; drop it so the same story from two feeds collapses
    if " - " in title:
        title = title.rsplit(" - ", 1)[0]
    t = re.sub(r"[^a-z0-9 ]", "", title.lower())
    return re.sub(r"\s+", " ", t).strip()


def item_key(item: dict) -> str:
    base = canonical_url(item["url"]) if item.get("url") else (item.get("id") or item["title"])
    return hashlib.sha1(base.encode()).hexdigest()[:16]


def dedupe(items: list[dict], by_title: bool = True) -> list[dict]:
    """Collapse duplicates by canonical URL and (optionally) by normalised title —
    the latter catches the same story arriving via two feeds."""
    seen_urls, seen_titles, out = set(), set(), []
    for it in items:
        if not it.get("title"):
            continue
        k, tk = item_key(it), (title_key(it["title"]) if by_title else None)
        if k in seen_urls or (tk and tk in seen_titles):
            continue
        seen_urls.add(k)
        seen_titles.add(tk)
        it["key"] = k
        out.append(it)
    return out


def within(items: list[dict], hours: float, now: datetime) -> list[dict]:
    cutoff = now - timedelta(hours=hours)
    keep = []
    for it in items:
        if not it.get("published"):
            continue  # undated items are unreliable for a "what's new" briefing
        if datetime.fromisoformat(it["published"]) >= cutoff:
            keep.append(it)
    return keep


def load_seen() -> dict:
    if SEEN_PATH.exists():
        try:
            return json.loads(SEEN_PATH.read_text())
        except json.JSONDecodeError:
            pass
    return {}


def save_seen(seen: dict, now: datetime, max_age_days: int = 7):
    cutoff = (now - timedelta(days=max_age_days)).isoformat()
    seen = {k: v for k, v in seen.items() if v >= cutoff}
    SEEN_PATH.write_text(json.dumps(seen, indent=0, sort_keys=True))


# ── finance prices ──────────────────────────────────────────────────────────
def price_moves(fetcher: Fetcher, tickers: list[dict]) -> list[dict]:
    rows = []
    try:
        import yfinance as yf  # optional; falls back to Yahoo chart endpoint
    except ImportError:
        yf = None

    for t in tickers:
        sym, label = t["symbol"], t.get("label", t["symbol"])
        row = {"symbol": sym, "label": label, "price": None, "change_pct": None, "currency": None}
        try:
            if yf is not None and not fetcher.fixtures:
                h = yf.Ticker(sym).history(period="5d", interval="1d")
                if len(h) >= 2:
                    last, prev = float(h["Close"].iloc[-1]), float(h["Close"].iloc[-2])
                    row.update(price=round(last, 2), change_pct=round((last / prev - 1) * 100, 2))
                    try:
                        row["currency"] = yf.Ticker(sym).fast_info.get("currency")
                    except Exception:
                        pass
            else:
                url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}?range=5d&interval=1d"
                j = fetcher.get_json(url)
                res = (j or {}).get("chart", {}).get("result") or []
                if res:
                    meta = res[0].get("meta", {})
                    closes = [c for c in res[0]["indicators"]["quote"][0].get("close", []) if c is not None]
                    if len(closes) >= 2:
                        last, prev = closes[-1], closes[-2]
                        row.update(price=round(last, 2), change_pct=round((last / prev - 1) * 100, 2),
                                   currency=meta.get("currency"))
        except Exception as e:  # never let one bad ticker kill the run
            log(f"  [price failed] {sym}: {e.__class__.__name__}")
        rows.append(row)
    return rows


# ── LLM ─────────────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You write a personal news briefing for one reader. You receive a list of
candidate items (title, source, published time, short summary) plus the
reader's interests profile. You select the most relevant items and write a
short briefing.

Rules:
- Reply by calling the submit_briefing tool (no prose).
- "briefing": 4-6 sentences on what matters right now for this reader. Concise,
  neutral, no filler, no repetition of the headlines verbatim. If there is
  little real news, say so in one or two sentences instead of padding.
- "items": pick up to {n} items, most important first. Use the exact "url"
  from the candidates. Each "title" may be cleaned up (remove outlet suffixes),
  each "summary" is ONE sentence, ≤ 25 words, factual.
- Prefer items marked new=true, but a still-major story from the last day may
  be kept if nothing newer covers it.
- Never invent facts, numbers or items not present in the candidates.
- Do not select two items about the same story; choose the best one.
- Write in {language}."""


BRIEFING_TOOL = {
    "name": "submit_briefing",
    "description": "Submit the finished briefing for this section.",
    "input_schema": {
        "type": "object",
        "properties": {
            "briefing": {"type": "string", "description": "4-6 sentence briefing paragraph."},
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "Exact url of a candidate item."},
                        "title": {"type": "string"},
                        "summary": {"type": "string", "description": "One sentence, <= 25 words."},
                    },
                    "required": ["url", "title", "summary"],
                },
            },
        },
        "required": ["briefing", "items"],
    },
}


def call_claude(api_key: str, model: str, system: str, user: str, max_tokens: int = 2500) -> dict:
    """One Messages API call. Forces a tool call so the reply is schema-valid JSON
    (no fence-stripping, no unescaped quotes) and returns the tool input dict."""
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": 0.2,
        "system": system,
        "messages": [{"role": "user", "content": user}],
        "tools": [BRIEFING_TOOL],
        "tool_choice": {"type": "tool", "name": "submit_briefing"},
    }
    last_err = None
    for attempt in range(3):
        try:
            r = requests.post(ANTHROPIC_URL, headers=headers, json=body, timeout=120)
            if r.status_code in (429, 500, 502, 503, 529):
                raise requests.HTTPError(f"{r.status_code}: {r.text[:200]}")
            if r.status_code >= 400:  # 400/401/403/404: not retryable, surface the API's message
                raise RuntimeError(f"Anthropic API {r.status_code}: {r.text[:300]}")
            data = r.json()
            for block in data.get("content", []):
                if block.get("type") == "tool_use" and block.get("name") == "submit_briefing":
                    return block["input"]
            # Fallback: model answered in plain text (shouldn't happen with tool_choice)
            text = "".join(b.get("text", "") for b in data.get("content", []))
            return parse_llm_json(text)
        except requests.RequestException as e:
            last_err = e
            log(f"  [claude retry {attempt + 1}] {e}")
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"Claude call failed: {last_err}")


def parse_llm_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            return json.loads(m.group(0))
        raise


def candidates_for_prompt(items: list[dict]) -> list[dict]:
    return [
        {
            "url": it["url"],
            "title": it["title"],
            "source": it["source"],
            "published": it["published"][:16] if it.get("published") else None,
            "new": it["new"],
            "summary": it["summary"][:CANDIDATE_SUMMARY_CHARS],
        }
        for it in items
    ]


def llm_section(section: str, items: list[dict], cfg: dict, api_key: str | None, extra_context: str = "") -> dict:
    """Return {"briefing": str, "items": [...]} for one section."""
    n = cfg["item_targets"].get(section, 8)
    language = cfg.get("language", "English")
    if not items:
        return {"briefing": f"No {section} items were fetched in this window.", "items": []}

    # newest first, new-before-old, then cap
    items = sorted(items, key=lambda x: x["published"] or "", reverse=True)   # newest first…
    items = sorted(items, key=lambda x: not x["new"])[:cfg.get("max_candidates", 40)]  # …unseen first (stable sort)
    by_url = {canonical_url(it["url"]): it for it in items}

    if api_key is None:
        return mock_section(section, items, n)

    system = SYSTEM_PROMPT.replace("{n}", str(n)).replace("{language}", language)
    user = (
        f"SECTION: {section}\n\nREADER INTERESTS:\n{cfg['interests'].strip()}\n\n"
        + (f"CONTEXT:\n{extra_context}\n\n" if extra_context else "")
        + f"CANDIDATES ({len(items)}):\n{json.dumps(candidates_for_prompt(items), ensure_ascii=False)}"
    )
    try:
        data = call_claude(api_key, cfg.get("model", "claude-sonnet-4-6"), system, user)
    except Exception as e:
        log(f"  [llm failed for {section}] {e} → falling back to mock selection")
        out = mock_section(section, items, n)
        out["briefing"] = "(Automatic selection — the LLM call failed this run.) " + out["briefing"]
        out["error"] = str(e)[:300]
        return out

    chosen = []
    for sel in data.get("items", [])[:n]:
        src = by_url.get(canonical_url(sel.get("url", "")))
        if not src:
            continue  # guard: never publish an item the LLM made up
        chosen.append({
            "title": (sel.get("title") or src["title"]).strip()[:200],
            "summary": (sel.get("summary") or src["summary"]).strip()[:300],
            "url": src["url"],
            "source": src["source"],
            "published": src["published"],
            "new": src["new"],
            "key": src["key"],
        })
    return {"briefing": str(data.get("briefing", "")).strip(), "items": chosen}


def mock_section(section: str, items: list[dict], n: int) -> dict:
    picks = items[:n]
    srcs = sorted({p["source"] for p in picks})
    briefing = (
        f"[Mock briefing — set ANTHROPIC_API_KEY to get a real one.] "
        f"Showing the {len(picks)} newest of {len(items)} {section} candidates from {', '.join(srcs[:4])}"
        + ("…" if len(srcs) > 4 else ".")
    )
    return {
        "briefing": briefing,
        "items": [{k: p[k] for k in ("title", "summary", "url", "source", "published", "new", "key")} for p in picks],
    }


# ── sections ────────────────────────────────────────────────────────────────
def mark_new(items: list[dict], seen: dict) -> list[dict]:
    for it in items:
        it["new"] = it["key"] not in seen
    return items


def build_news(fetcher, cfg, seen, now):
    log("NEWS")
    sources = list(cfg.get("news_sources", []))
    sources += [{"name": f"Topic: {t}", "url": google_news_rss(t)} for t in cfg.get("watched_topics", [])]
    items = mark_new(dedupe(within(fetch_all(fetcher, sources), cfg["lookback_hours"], now)), seen)
    return items


def build_sport(fetcher, cfg, seen, now):
    log("SPORT")
    sources = [{"name": f"Team: {t}", "url": google_news_rss(t)} for t in cfg.get("sport_teams", [])]
    sources += list(cfg.get("sport_sites", []))
    return mark_new(dedupe(within(fetch_all(fetcher, sources), cfg["lookback_hours"], now)), seen)


def build_finance(fetcher, cfg, seen, now):
    log("FINANCE")
    sources = []
    for t in cfg.get("tickers", []):
        sym = urllib.parse.quote(t["symbol"])
        sources.append({"name": t.get("label", t["symbol"]),
                        "url": f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={sym}&region=US&lang=en-US"})
    sources += list(cfg.get("market_news_sources", []))
    items = mark_new(dedupe(within(fetch_all(fetcher, sources), cfg["lookback_hours"], now)), seen)
    log("  prices…")
    prices = price_moves(fetcher, cfg.get("tickers", []))
    return items, prices


def build_media(fetcher, cfg, seen, now):
    log("MEDIA")
    sources = [{"name": c["name"], "url": f"https://www.youtube.com/feeds/videos.xml?channel_id={c['channel_id']}"}
               for c in cfg.get("youtube_channels", [])]
    yt_names = {s["name"] for s in sources}
    sources += [{"name": p["name"], "url": p["rss_url"]} for p in cfg.get("podcasts", [])]
    items = mark_new(dedupe(within(fetch_all(fetcher, sources), cfg["media_days"] * 24, now), by_title=False), seen)
    items.sort(key=lambda x: x["published"], reverse=True)
    out = []
    for it in items:
        out.append({
            "title": it["title"],
            "url": it["url"],
            "source": it["source"],
            "kind": "video" if it["source"] in yt_names else "podcast",
            "published": it["published"],
            "duration_s": it.get("duration_s"),
            "new": it["new"],
            "key": it["key"],
        })
    return out


# ── output ──────────────────────────────────────────────────────────────────
def rotate_past(cfg: dict):
    PAST_DIR.mkdir(parents=True, exist_ok=True)
    if OUT_PATH.exists():
        try:
            prev = json.loads(OUT_PATH.read_text())
            stamp = prev["generated_at"].replace(":", "-")
            (PAST_DIR / f"{stamp}.json").write_text(json.dumps(prev, ensure_ascii=False))
        except (json.JSONDecodeError, KeyError):
            pass
    files = sorted(PAST_DIR.glob("*.json"), reverse=True)
    files = [f for f in files if f.name != "index.json"]
    keep = cfg.get("keep_past_briefings", 6)
    for old in files[keep:]:
        old.unlink()
    index = []
    for f in files[:keep]:
        try:
            j = json.loads(f.read_text())
            index.append({"file": f.name, "generated_at": j["generated_at"],
                          "counts": {k: len(v.get("items", [])) for k, v in j["sections"].items()}})
        except Exception:
            continue
    (PAST_DIR / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mock", action="store_true", help="skip the LLM even if a key is set")
    ap.add_argument("--fixtures", type=Path, help="directory with map.json + feed files (offline test)")
    ap.add_argument("--now", help="override current time (ISO), for fixture runs")
    args = ap.parse_args()

    cfg = yaml.safe_load(CONFIG_PATH.read_text())
    tz = ZoneInfo(cfg.get("timezone", "Europe/Rome"))
    now = datetime.fromisoformat(args.now).astimezone(timezone.utc) if args.now else datetime.now(timezone.utc)

    api_key = None if args.mock else (os.environ.get("ANTHROPIC_API_KEY") or "").strip() or None
    if api_key is None:
        log("!! No ANTHROPIC_API_KEY (or --mock): producing mock briefings")
    elif not api_key.startswith("sk-ant-"):
        log("!! ANTHROPIC_API_KEY is set but does not look like an Anthropic key (expected sk-ant-…)")

    fetcher = Fetcher(args.fixtures)
    DATA_DIR.mkdir(exist_ok=True)
    seen = load_seen()
    run_key = now.isoformat()

    news_items = build_news(fetcher, cfg, seen, now)
    sport_items = build_sport(fetcher, cfg, seen, now)
    fin_items, prices = build_finance(fetcher, cfg, seen, now)
    media_items = build_media(fetcher, cfg, seen, now)

    log(f"candidates: news={len(news_items)} sport={len(sport_items)} finance={len(fin_items)} media={len(media_items)}")

    price_ctx = "Today's price moves: " + "; ".join(
        f"{p['label']} {p['change_pct']:+.2f}%" for p in prices if p["change_pct"] is not None)

    sections = {
        "news": llm_section("news", news_items, cfg, api_key),
        "sport": llm_section("sport", sport_items, cfg, api_key),
        "finance": {**llm_section("finance", fin_items, cfg, api_key, extra_context=price_ctx), "tickers": prices},
        "media": {"briefing": "", "items": media_items},
    }
    for name, sec in sections.items():
        sec["candidate_count"] = {"news": len(news_items), "sport": len(sport_items),
                                  "finance": len(fin_items), "media": len(media_items)}[name]
        sec["new_count"] = sum(1 for it in sec["items"] if it.get("new"))

    # everything we considered is now "seen" for the next run
    for it in news_items + sport_items + fin_items + media_items:
        seen.setdefault(it["key"], run_key)

    rotate_past(cfg)
    out = {
        "generated_at": now.isoformat(),
        "generated_local": now.astimezone(tz).strftime("%a %d %b %Y, %H:%M"),
        "timezone": cfg.get("timezone", "Europe/Rome"),
        "mode": "mock" if api_key is None else "llm",
        "model": cfg.get("model"),
        "sections": sections,
    }
    OUT_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    save_seen(seen, now)
    log(f"wrote {OUT_PATH.relative_to(ROOT)}  mode={out['mode']}")


if __name__ == "__main__":
    main()
