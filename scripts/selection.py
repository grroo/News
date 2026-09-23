"""Candidate selection and deduplication. Owned by T07 after T00 extraction."""
from __future__ import annotations

import hashlib
import re
import urllib.parse
from datetime import datetime, timedelta


_TRACKING = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "fbclid", "gclid", "ref", "cmpid"}


def canonical_url(url: str) -> str:
    p = urllib.parse.urlsplit(url.strip())
    q = [(k, v) for k, v in urllib.parse.parse_qsl(p.query) if k.lower() not in _TRACKING]
    return urllib.parse.urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"), urllib.parse.urlencode(q), ""))


def title_key(title: str) -> str:
    if " - " in title:
        title = title.rsplit(" - ", 1)[0]
    t = re.sub(r"[^a-z0-9 ]", "", title.lower())
    return re.sub(r"\s+", " ", t).strip()


def item_key(item: dict) -> str:
    base = canonical_url(item["url"]) if item.get("url") else (item.get("id") or item["title"])
    return hashlib.sha1(base.encode()).hexdigest()[:16]


def dedupe(items: list[dict], by_title: bool = True, preferred=()) -> list[dict]:
    seen_urls, seen_titles, out = set(), set(), []
    items = sorted(
        items,
        key=lambda it: (it.get("feed_name", it.get("source")) not in preferred, bool(it.get("via")), it.get("url", "")),
    )
    for it in items:
        if not it.get("title") or not it.get("url"):
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
            continue
        if datetime.fromisoformat(it["published"]) >= cutoff:
            keep.append(it)
    return keep


def mark_new(items: list[dict], seen: dict) -> list[dict]:
    for it in items:
        it["new"] = it["key"] not in seen
    return items


def select_candidates(items: list[dict], cfg: dict, section: str) -> list[dict]:
    ranked = sorted(items, key=lambda x: (x["published"] or "", x["key"]), reverse=True)
    ranked.sort(key=lambda x: not x["new"])
    limit = max(0, cfg.get("max_candidates", 40))
    reserved = []
    for name, policy in cfg.get("source_preferences", {}).get(section, {}).items():
        reserved.extend(
            [it for it in ranked if it.get("feed_name", it["source"]) == name][: policy.get("candidate_slots", 0)]
        )
    keys = {it["key"] for it in reserved}
    return (reserved + [it for it in ranked if it["key"] not in keys])[:limit]


def published_item(src: dict, selection: dict | None = None) -> dict:
    sel = selection or {}
    return {
        **{k: src.get(k) for k in ("url", "source", "feed_name", "via", "published", "new", "key")},
        "title": str(sel.get("title") or src["title"]).strip()[:200],
        "summary": str(sel.get("summary") or src.get("summary", "")).strip()[:300],
    }


def enforce_preferences(chosen: list[dict], candidates: list[dict], policies: dict, limit: int) -> list[dict]:
    required = []
    for name, policy in policies.items():
        fresh = [it for it in candidates if it.get("feed_name", it["source"]) == name and it["new"]]
        minimum = min(policy.get("min_new_items", 0), len(fresh), max(0, limit - len(required)))
        selected = [it for it in chosen if it.get("feed_name", it["source"]) == name and it["new"]]
        picks = selected[:minimum]
        keys = {it["key"] for it in picks}
        picks += [published_item(it) for it in fresh if it["key"] not in keys][: minimum - len(picks)]
        required.extend(picks)
    keys = {it["key"] for it in required}
    return (required + [it for it in chosen if it["key"] not in keys])[:limit]
