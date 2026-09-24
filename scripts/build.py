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
from briefing_archive import archive_previous, policy_from, prune  # noqa: E402
from llm_provider import (  # noqa: E402
    OPENAI_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    candidates_for_prompt,
    call_claude,
    credential_env_var,
    generate_briefing,
    with_fill_rule,
)
from pipeline import (  # noqa: E402
    AI_SECTIONS,
    atomic_write,
    edition_errors,
    load_edition,
    overall_quality,
    publication_is_newer,
    request_ids,
    run_clock,
    scheduled_slot,
    source_error,
    trigger_for,
    validate_config,
)
from section_cache import GitFeedCache, fingerprint, load_sections, save_sections  # noqa: E402
from usage_ledger import ledger, record_run, section_usage  # noqa: E402
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


def apply_selection(data: dict, items: list[dict], policies: dict, limit: int) -> dict:
    """Publish only in-range integer IDs, then enforce source preferences."""
    chosen, keys = [], set()
    for sel in data.get("items") or []:
        idx = sel.get("id") if isinstance(sel, dict) else None
        if type(idx) is not int or not 0 <= idx < len(items):
            continue
        src = items[idx]
        if src["key"] in keys:
            continue
        chosen.append(published_item(src, sel))
        keys.add(src["key"])
    chosen = enforce_preferences(chosen, items, policies, limit)
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

    provider_name = cfg.get("provider", "anthropic")
    template = OPENAI_SYSTEM_PROMPT if provider_name == "openai" else SYSTEM_PROMPT
    system = template.replace("{n}", str(n)).replace("{language}", language)
    if policies:
        system += "\nPRIORITY SOURCES: " + "; ".join(
            f"Prefer {name}; select at least {policy.get('min_new_items', 0)} distinct new stories when available. "
            "Prefer direct original reporting over aggregated copies."
            for name, policy in policies.items()
        )
    if cfg.get("fill_items"):
        system = with_fill_rule(system, n)
    user = (
        f"SECTION: {section}\n\nREADER INTERESTS:\n{cfg['interests'].strip()}\n\n"
        + (f"CONTEXT:\n{extra_context}\n\n" if extra_context else "")
        + f"CANDIDATES ({len(items)}; fields: id, t=title, src=publisher, at=published MM-DDTHH:MM, new=1 if not previously published here, s=summary):\n"
        + json.dumps(candidates_for_prompt(items), ensure_ascii=False, separators=(",", ":"))
    )
    # generate_briefing already retries within provider_max_attempts. Do not loop here.
    result = generate_briefing(
        provider=provider_name,
        model=cfg.get("model", ""),
        api_key=api_key,
        system=system,
        user=user,
        candidate_count=len(items),
        max_attempts=cfg.get("provider_max_attempts", 2),
        timeout=cfg.get("provider_timeout_seconds", 120),
        reasoning_effort=cfg.get("reasoning_effort", "none") if provider_name == "openai" else None,
    )
    if result.get("status") != "success":
        log(f"  [llm failed for {section}] {result.get('error')}")
        failed = {
            "briefing": "Summary unavailable. Showing recent articles instead.",
            "items": [],
            "reviewed_count": 0,
            "error": str(result.get("error") or "provider failed")[:300],
            "provider_result": result,
        }
        return failed
    out = apply_selection(result["content"], items, policies, n)
    out["provider_result"] = result
    return out


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


def rotate_past(cfg: dict, now: datetime, *, archive_live: bool):
    if archive_live and OUT_PATH.exists():
        archive_previous(PAST_DIR, OUT_PATH)
    prune(PAST_DIR, now, policy_from(cfg))


def schedule_metadata() -> dict:
    return json.loads((ROOT / "schedule.json").read_text())


def _public_section(section: dict) -> dict:
    return {key: value for key, value in section.items() if key != "provider_result"}


def _last_good(cached: dict | None) -> bool:
    if not isinstance(cached, dict):
        return False
    briefing = cached.get("briefing")
    items = cached.get("items")
    has_text = (isinstance(briefing, list) and bool(briefing)) or (isinstance(briefing, str) and bool(briefing.strip()))
    has_items = isinstance(items, list) and bool(items)
    return has_text or has_items


def _reuse_section(cached: dict, *, state: str, error: str | None, fingerprint_value: str) -> dict:
    section = {
        "state": state,
        "briefing": cached.get("briefing", []),
        "items": cached.get("items", []),
        "reviewed_count": 0,
        "last_success_at": cached.get("last_success_at"),
        "reused_edition_id": cached.get("edition_id"),
        "input_fingerprint": fingerprint_value,
    }
    if error:
        section["error"] = error
    return section


def _finish_section(section: dict, *, state: str, checked_at: str, success_at: str | None, fingerprint_value: str, edition_id: str | None, error: str | None) -> dict:
    section = _public_section(section)
    section["state"] = state
    section["source_checked_at"] = checked_at
    if fingerprint_value:
        section["input_fingerprint"] = fingerprint_value
    if success_at:
        section["last_success_at"] = success_at
    if edition_id and state == "unchanged":
        section["reused_edition_id"] = edition_id
    if error:
        section["error"] = error
    elif state in {"healthy", "unchanged"}:
        section.pop("error", None)
    if state in {"healthy", "unchanged", "degraded"} and not isinstance(section.get("briefing"), list):
        section["briefing"] = []
    return section


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mock", action="store_true", help="skip the LLM even if a key is set")
    ap.add_argument("--fixtures", type=Path, help="directory with map.json + feed files (offline test)")
    ap.add_argument("--now", help="override current time (ISO), for fixture runs")
    args = ap.parse_args()

    cfg = yaml.safe_load(CONFIG_PATH.read_text())
    problems = validate_config(cfg)
    if problems:
        for problem in problems:
            log(f"!! config: {problem}")
        raise SystemExit(1)
    tz = ZoneInfo(cfg.get("timezone", "Europe/Rome"))
    now = run_clock(args.now)
    try:
        slot = scheduled_slot(os.environ.get("SCHEDULED_SLOT"))
    except ValueError as exc:
        log(f"!! {exc}")
        raise SystemExit(1)
    provider_name = cfg.get("provider", "anthropic")
    key_name = credential_env_var(provider_name)
    api_key = None if args.mock else (os.environ.get(key_name) or "").strip() or None
    if api_key is None and not args.mock:
        log(f"!! Missing {key_name}; refusing to generate")
        raise SystemExit(1)
    if args.mock:
        log("!! mock mode: no provider calls")

    existing = load_edition(OUT_PATH)
    if publication_is_newer(existing, now):
        log("!! existing edition is newer; leaving it in place")
        return

    feed_cfg = cfg.get("feed_cache") if isinstance(cfg.get("feed_cache"), dict) else {}
    feed_cache = GitFeedCache(
        DATA_DIR / "feed-cache.json",
        max_bytes=feed_cfg.get("max_bytes", 1_000_000),
        max_records=feed_cfg.get("max_records", 40),
    )
    fetcher = Fetcher(args.fixtures, feed_cache)
    DATA_DIR.mkdir(exist_ok=True)
    seen = load_seen()
    section_state = load_sections(DATA_DIR / "section-cache.json")

    news_items = build_news(fetcher, cfg, seen, now)
    sport_items = build_sport(fetcher, cfg, seen, now)
    fin_items, prices = build_finance(fetcher, cfg, seen, now)
    media_items = build_media(fetcher, cfg, seen, now)
    pools = {"news": news_items, "sport": sport_items, "finance": fin_items, "media": media_items}
    log(f"candidates: news={len(news_items)} sport={len(sport_items)} finance={len(fin_items)} media={len(media_items)}")
    price_ctx = "Today's price moves: " + "; ".join(
        f"{p['label']} {p['change_pct']:+.2f}%" for p in prices if p["change_pct"] is not None
    )
    checked_at = now.isoformat()
    new_edition_id = "ed-" + now.strftime("%Y%m%dT%H%M%S%fZ")
    usage_rows = []
    generated_any = False
    sections = {}

    for name in AI_SECTIONS:
        extra = price_ctx if name == "finance" else ""
        selected = select_candidates(pools[name], cfg, name) if pools[name] else []
        fingerprint_value = fingerprint(name, selected, cfg, extra)
        cached = section_state.get(name) if isinstance(section_state.get(name), dict) else None
        blocked = source_error(fetcher.feed_health, name)
        if not pools[name]:
            if blocked:
                generated_any = True
                if _last_good(cached):
                    sections[name] = _reuse_section(
                        cached, state="degraded", error=blocked,
                        fingerprint_value=cached.get("fingerprint") or fingerprint_value,
                    )
                    sections[name]["source_checked_at"] = checked_at
                else:
                    sections[name] = _finish_section(
                        {"briefing": [], "items": [], "reviewed_count": 0},
                        state="failed", checked_at=checked_at, success_at=None,
                        fingerprint_value=fingerprint_value, edition_id=None, error=blocked,
                    )
            elif cached and cached.get("fingerprint") == fingerprint_value and cached.get("briefing") is not None:
                sections[name] = _reuse_section(cached, state="unchanged", error=None, fingerprint_value=fingerprint_value)
                sections[name]["source_checked_at"] = checked_at
            else:
                generated_any = True
                sections[name] = _finish_section(
                    {"briefing": [], "items": [], "reviewed_count": 0},
                    state="healthy", checked_at=checked_at, success_at=checked_at,
                    fingerprint_value=fingerprint_value, edition_id=None, error=None,
                )
                section_state[name] = {
                    "fingerprint": fingerprint_value,
                    "edition_id": new_edition_id,
                    "last_success_at": checked_at,
                    "briefing": [],
                    "items": [],
                }
            continue
        if cached and cached.get("fingerprint") == fingerprint_value and cached.get("briefing") is not None:
            state = "degraded" if blocked else "unchanged"
            sections[name] = _reuse_section(cached, state=state, error=blocked, fingerprint_value=fingerprint_value)
            sections[name]["source_checked_at"] = checked_at
            continue
        if args.mock:
            produced = llm_section(name, pools[name], cfg, None, extra)
            sections[name] = _finish_section(
                produced, state="skipped", checked_at=checked_at, success_at=None,
                fingerprint_value=fingerprint_value, edition_id=None, error=None,
            )
            continue
        produced = llm_section(name, pools[name], cfg, api_key, extra)
        result = produced.get("provider_result")
        if result:
            usage_rows.append(section_usage(name, result))
        if produced.get("error"):
            generated_any = True
            if cached and cached.get("items"):
                sections[name] = _reuse_section(
                    cached, state="degraded", error=produced["error"], fingerprint_value=cached.get("fingerprint", fingerprint_value),
                )
                sections[name]["source_checked_at"] = checked_at
            else:
                sections[name] = _finish_section(
                    produced, state="failed", checked_at=checked_at, success_at=None,
                    fingerprint_value=fingerprint_value, edition_id=None, error=produced["error"],
                )
            continue
        generated_any = True
        state = "degraded" if blocked else "healthy"
        sections[name] = _finish_section(
            produced, state=state, checked_at=checked_at, success_at=checked_at,
            fingerprint_value=fingerprint_value, edition_id=None, error=blocked,
        )
        section_state[name] = {
            "fingerprint": fingerprint_value,
            "edition_id": new_edition_id,
            "last_success_at": checked_at,
            "briefing": sections[name]["briefing"],
            "items": sections[name]["items"],
        }

    sections["finance"]["tickers"] = prices
    sections["media"] = _finish_section(
        {"briefing": "", "items": media_items, "reviewed_count": 0},
        state="skipped", checked_at=checked_at, success_at=None,
        fingerprint_value="", edition_id=None, error=None,
    )
    for name, sec in sections.items():
        sec["candidate_count"] = len(pools[name])
        sec["new_count"] = sum(1 for it in sec.get("items", []) if it.get("new"))

    mode = "mock" if args.mock else "llm"
    states = [sections[name]["state"] for name in AI_SECTIONS]
    reuse = not generated_any and not args.mock
    previous_ids = existing.get("request_ids") if isinstance(existing, dict) else None
    current_request = (os.environ.get("REQUEST_ID") or "").strip() or f"run-{checked_at}"
    if reuse and existing:
        edition_id = existing.get("edition_id") or new_edition_id
        generated_at = existing.get("generated_at") or checked_at
        generated_local = existing.get("generated_local") or now.astimezone(tz).strftime("%a %d %b %Y, %H:%M")
        outcome = "no_change"
    else:
        edition_id = new_edition_id
        generated_at = checked_at
        generated_local = now.astimezone(tz).strftime("%a %d %b %Y, %H:%M")
        outcome = "generated"
        for name in AI_SECTIONS:
            if name in section_state and sections[name]["state"] == "healthy":
                section_state[name]["edition_id"] = edition_id
    for name in AI_SECTIONS:
        if sections[name]["state"] == "unchanged":
            sections[name]["reused_edition_id"] = (section_state.get(name) or {}).get("edition_id") or edition_id
    quality = overall_quality(mode, states)
    edition = {
        "schema_version": 2,
        "edition_id": edition_id,
        "generated_at": generated_at,
        "generated_local": generated_local,
        "source_checked_at": checked_at,
        "timezone": cfg.get("timezone", "Europe/Rome"),
        "schedule": schedule_metadata(),
        "trigger": trigger_for(os.environ.get("GITHUB_EVENT_NAME"), slot),
        "request_ids": request_ids(previous_ids if isinstance(previous_ids, list) else None, current_request, os.environ.get("EXTRA_REQUEST_IDS"), reuse=reuse),
        "quality": {"overall": quality},
        "refresh": {"outcome": outcome, "completed_at": checked_at},
        "mode": mode,
        "model": None if args.mock else cfg.get("model"),
        "feed_health": fetcher.feed_health,
        "usage": ledger(usage_rows),
        "sections": sections,
    }
    if slot:
        edition["scheduled_slot"] = slot
    if outcome == "no_change":
        edition["refresh"]["reused_edition_id"] = edition_id
    errors = edition_errors(edition)
    if errors:
        for error in errors[:8]:
            log(f"!! edition schema: {error}")
        raise SystemExit(1)
    if publication_is_newer(load_edition(OUT_PATH), now):
        log("!! existing edition became newer during the run; leaving it in place")
        feed_cache.save()
        return
    rotate_past(cfg, now, archive_live=outcome == "generated" and OUT_PATH.exists())
    if not args.mock:
        run_id = f"{os.environ.get('GITHUB_RUN_ID', current_request)}:{os.environ.get('GITHUB_RUN_ATTEMPT', '1')}"
        edition["usage"]["recorded_month_to_date"] = record_run(
            DATA_DIR / "usage-history.json", run_id, checked_at, edition["usage"],
        )
    text = json.dumps(edition, ensure_ascii=False, indent=1, allow_nan=False)
    atomic_write(OUT_PATH, text)
    if not args.mock:
        save_sections(DATA_DIR / "section-cache.json", section_state)
    feed_cache.save()
    run_key = generated_at
    for sec in sections.values():
        for it in sec.get("items", []):
            if it.get("key"):
                seen[it["key"]] = run_key
        for bullet in sec.get("briefing", []) if isinstance(sec.get("briefing"), list) else []:
            if isinstance(bullet, dict):
                for ref in bullet.get("sources", []):
                    if ref.get("key"):
                        seen[ref["key"]] = run_key
    save_seen(seen, now)
    log(f"wrote {OUT_PATH.relative_to(ROOT)}  mode={mode}  quality={quality}  outcome={outcome}")


if __name__ == "__main__":
    main()
