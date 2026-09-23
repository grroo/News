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
import json
import os
import sys
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

sys.path.insert(0, str(Path(__file__).parent))
from feeds import parse_feed  # noqa: E402
from ingestion import Fetcher, fetch_all, google_news_rss, topic_sources  # noqa: E402
from llm_provider import (  # noqa: E402
    BRIEFING_TOOL,
    SYSTEM_PROMPT,
    USAGE,
    call_claude,
    candidates_for_prompt,
    usage_summary,
)
from prices import price_moves  # noqa: E402
from selection import (  # noqa: E402
    canonical_url,
    dedupe,
    enforce_preferences,
    item_key,
    mark_new,
    published_item,
    select_candidates,
    title_key,
    within,
)

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yml"
DATA_DIR = ROOT / "data"
PAST_DIR = DATA_DIR / "past"
SEEN_PATH = DATA_DIR / "seen.json"
OUT_PATH = DATA_DIR / "briefing.json"

log = lambda *a: print(*a, file=sys.stderr, flush=True)  # noqa: E731


def load_seen() -> dict:
    if SEEN_PATH.exists():
        try:
            stored = json.loads(SEEN_PATH.read_text())
            if stored.get("version") == 2:
                return stored.get("items", {})
            published = {}
            for path in [OUT_PATH, *PAST_DIR.glob("*.json")]:
                try:
                    briefing = json.loads(path.read_text())
                    for section in briefing["sections"].values():
                        for it in section.get("items", []):
                            published[it["key"]] = briefing["generated_at"]
                except (OSError, ValueError, KeyError, TypeError):
                    continue
            return published
        except json.JSONDecodeError:
            pass
    return {}


def save_seen(seen: dict, now: datetime, max_age_days: int = 7):
    cutoff = (now - timedelta(days=max_age_days)).isoformat()
    seen = {k: v for k, v in seen.items() if v >= cutoff}
    SEEN_PATH.write_text(json.dumps({"version": 2, "items": seen}, indent=0, sort_keys=True))


def llm_section(section: str, items: list[dict], cfg: dict, api_key: str | None, extra_context: str = "") -> dict:
    n = cfg["item_targets"].get(section, 8)
    language = cfg.get("language", "English")
    if not items:
        return {"briefing": "", "items": [], "reviewed_count": 0}
    items = select_candidates(items, cfg, section)
    policies = cfg.get("source_preferences", {}).get(section, {})
    if api_key is None:
        out = mock_section(section, items, n)
        out["items"] = enforce_preferences(out["items"], items, policies, n)
        out["reviewed_count"] = 0
        return out

    system = SYSTEM_PROMPT.replace("{n}", str(n)).replace("{language}", language)
    if policies:
        system += "\nPRIORITY SOURCES: " + "; ".join(
            f"Prefer {name}; select at least {policy.get('min_new_items', 0)} distinct new stories when available. "
            "Prefer direct original reporting over aggregated copies."
            for name, policy in policies.items()
        )
    user = (
        f"SECTION: {section}\n\nREADER INTERESTS:\n{cfg['interests'].strip()}\n\n"
        + (f"CONTEXT:\n{extra_context}\n\n" if extra_context else "")
        + f"CANDIDATES ({len(items)}; fields: id, t=title, src=publisher, at=published MM-DDTHH:MM, new=1 if not previously published here, s=summary):\n"
        + json.dumps(candidates_for_prompt(items), ensure_ascii=False, separators=(",", ":"))
    )
    try:
        data = call_claude(api_key, cfg.get("model", "claude-sonnet-4-6"), system, user)
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            raise ValueError("Invalid briefing response")
    except Exception as e:
        log(f"  [llm failed for {section}] {e} → falling back to automatic selection")
        out = mock_section(section, items, n)
        out["items"] = enforce_preferences(out["items"], items, policies, n)
        out["briefing"] = "Summary unavailable. Showing recent articles instead."
        out["error"] = str(e)[:300]
        out["reviewed_count"] = 0
        return out

    chosen, keys = [], set()
    for sel in data["items"]:
        idx = sel.get("id") if isinstance(sel, dict) else None
        if type(idx) is not int or not 0 <= idx < len(items):
            continue
        src = items[idx]
        if src["key"] in keys:
            continue
        chosen.append(published_item(src, sel))
        keys.add(src["key"])
    chosen = enforce_preferences(chosen, items, policies, n)
    bullets = []
    raw_bullets = data.get("briefing", [])
    for bullet in raw_bullets if isinstance(raw_bullets, list) else []:
        if not isinstance(bullet, dict) or not isinstance(bullet.get("text"), str):
            continue
        refs, used = [], set()
        ids = bullet.get("source_ids", [])
        for idx in ids if isinstance(ids, list) else []:
            if type(idx) is not int or not 0 <= idx < len(items) or idx in used:
                continue
            used.add(idx)
            refs.append({k: items[idx].get(k) for k in ("key", "title", "url", "source", "via")})
        if refs and bullet["text"].strip():
            bullets.append({"text": bullet["text"].strip(), "sources": refs[:3]})
    return {"briefing": bullets[:8], "items": chosen, "reviewed_count": len(items)}


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
        "items": [published_item(p) for p in picks],
    }


def build_news(fetcher, cfg, seen, now):
    log("NEWS")
    sources = list(cfg.get("news_sources", []))
    sources += topic_sources(cfg.get("watched_topics"), "Topic")
    items = mark_new(dedupe(within(fetch_all(fetcher, sources, "news"), cfg["lookback_hours"], now)), seen)
    return items


def build_sport(fetcher, cfg, seen, now):
    log("SPORT")
    sources = topic_sources(cfg.get("sport_teams"), "Team")
    sources += list(cfg.get("sport_sites", []))
    return mark_new(
        dedupe(
            within(fetch_all(fetcher, sources, "sport"), cfg["lookback_hours"], now),
            preferred=cfg.get("source_preferences", {}).get("sport", {}),
        ),
        seen,
    )


def build_finance(fetcher, cfg, seen, now):
    log("FINANCE")
    sources = []
    for t in cfg.get("tickers", []):
        sym = urllib.parse.quote(t["symbol"])
        sources.append(
            {
                "name": t.get("label", t["symbol"]),
                "url": f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={sym}&region=US&lang=en-US",
            }
        )
    sources += list(cfg.get("market_news_sources", []))
    items = mark_new(dedupe(within(fetch_all(fetcher, sources, "finance"), cfg["lookback_hours"], now)), seen)
    log("  prices…")
    prices = price_moves(fetcher, cfg.get("tickers", []))
    return items, prices


def build_media(fetcher, cfg, seen, now):
    log("MEDIA")
    sources = [
        {"name": c["name"], "url": f"https://www.youtube.com/feeds/videos.xml?channel_id={c['channel_id']}"}
        for c in cfg.get("youtube_channels", [])
    ]
    yt_names = {s["name"] for s in sources}
    sources += [{"name": p["name"], "url": p["rss_url"]} for p in cfg.get("podcasts", [])]
    items = mark_new(
        dedupe(within(fetch_all(fetcher, sources, "media"), cfg["media_days"] * 24, now), by_title=False),
        seen,
    )
    items.sort(key=lambda x: x["published"], reverse=True)
    out = []
    for it in items:
        out.append(
            {
                "title": it["title"],
                "url": it["url"],
                "source": it["source"],
                "kind": "video" if it["source"] in yt_names else "podcast",
                "published": it["published"],
                "duration_s": it.get("duration_s"),
                "new": it["new"],
                "key": it["key"],
            }
        )
    return out


def load_json_lenient(path: Path):
    return json.loads(path.read_text(), parse_constant=lambda _: None)


def rotate_past(cfg: dict):
    PAST_DIR.mkdir(parents=True, exist_ok=True)
    if OUT_PATH.exists():
        try:
            prev = load_json_lenient(OUT_PATH)
            stamp = prev["generated_at"].replace(":", "-")
            (PAST_DIR / f"{stamp}.json").write_text(json.dumps(prev, ensure_ascii=False, allow_nan=False))
        except (json.JSONDecodeError, KeyError, ValueError):
            pass
    files = sorted(PAST_DIR.glob("*.json"), reverse=True)
    files = [f for f in files if f.name != "index.json"]
    keep = cfg.get("keep_past_briefings", 6)
    for old in files[keep:]:
        old.unlink()
    index = []
    for f in files[:keep]:
        try:
            j = load_json_lenient(f)
            index.append(
                {
                    "file": f.name,
                    "generated_at": j["generated_at"],
                    "counts": {k: len(v.get("items", [])) for k, v in j["sections"].items()},
                }
            )
        except Exception:
            continue
    (PAST_DIR / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1))


def schedule_metadata() -> dict:
    return json.loads((ROOT / "schedule.json").read_text())


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
        f"{p['label']} {p['change_pct']:+.2f}%" for p in prices if p["change_pct"] is not None
    )

    sections = {
        "news": llm_section("news", news_items, cfg, api_key),
        "sport": llm_section("sport", sport_items, cfg, api_key),
        "finance": {**llm_section("finance", fin_items, cfg, api_key, extra_context=price_ctx), "tickers": prices},
        "media": {"briefing": "", "items": media_items},
    }
    for name, sec in sections.items():
        sec["candidate_count"] = {
            "news": len(news_items),
            "sport": len(sport_items),
            "finance": len(fin_items),
            "media": len(media_items),
        }[name]
        sec["new_count"] = sum(1 for it in sec["items"] if it.get("new"))

    for sec in sections.values():
        for it in sec["items"]:
            seen[it["key"]] = run_key
        for bullet in sec.get("briefing", []):
            if isinstance(bullet, dict):
                for ref in bullet.get("sources", []):
                    seen[ref["key"]] = run_key

    rotate_past(cfg)
    out = {
        "generated_at": now.isoformat(),
        "generated_local": now.astimezone(tz).strftime("%a %d %b %Y, %H:%M"),
        "timezone": cfg.get("timezone", "Europe/Rome"),
        "schedule": schedule_metadata(),
        "feed_health": fetcher.feed_health,
        "mode": "mock" if api_key is None else "llm",
        "model": cfg.get("model"),
        "usage": usage_summary(cfg.get("model", "")),
        "sections": sections,
    }
    OUT_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=1, allow_nan=False))
    save_seen(seen, now)
    log(f"wrote {OUT_PATH.relative_to(ROOT)}  mode={out['mode']}  usage={out['usage']}")


if __name__ == "__main__":
    main()
