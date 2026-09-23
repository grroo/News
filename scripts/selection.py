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


def editorial_enabled(cfg: dict) -> bool:
    flag = cfg.get("editorial_selection", False)
    return flag is True or flag == "true"


def selection_fingerprint_fields(cfg: dict, section: str, candidates: list[dict]) -> dict:
    """Fields T09 must fold into section_cache.fingerprint when editorial ranking ships.

    T04 currently sorts candidate keys and ignores selection mode/order. Keep
    ``editorial_selection`` false until T09 merges this into the live fingerprint.
    """
    return {
        "selection_version": SELECTION_VERSION,
        "selection_mode": "editorial" if editorial_enabled(cfg) else "legacy",
        "candidate_order": [it.get("key") for it in candidates],
        "selection_policy": (cfg.get("selection") or {}),
    }


def select_candidates(items: list[dict], cfg: dict, section: str) -> list[dict]:
    """Pick the bounded candidate list for one section.

    The default path is the historical recency cutoff. ``editorial_selection: true``
    ranks with explainable topic, preference and diversity components. T04 owns
    ``config.yml``; leave the flag false until T08 has compared providers on a
    fixed candidate list.
    """
    if editorial_enabled(cfg):
        return _select_editorial(items, cfg, section)
    return _select_legacy(items, cfg, section)


def _select_legacy(items: list[dict], cfg: dict, section: str) -> list[dict]:
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


_PUBLISHER_ALIASES = {
    "ft": "financial times",
    "ft.com": "financial times",
    "the financial times": "financial times",
    "financial times": "financial times",
    "reuters.com": "reuters",
    "bloomberg.com": "bloomberg",
    "culturepsg.com": "culturepsg",
}

_STOP = {
    "the", "and", "for", "with", "from", "that", "this", "after", "before", "over", "into", "about",
    "les", "des", "une", "dans", "pour", "avec", "sur", "par", "qui", "que", "est",
    "del", "della", "delle", "dei", "degli", "con", "per", "che", "una", "sul",
}

_UPDATE_TOKENS = {
    "injury", "injured", "injures", "blessé", "blesse", "blessure",
    "transfer", "transfers", "signs", "signed", "signing", "signe",
    "suspended", "suspension", "merger", "acquisition", "acquires",
}

# Generic headline verbs/nouns that must not alone justify collapsing distinct stories.
_GENERIC_STORY = _STOP | {
    "raises", "raise", "raised", "outlook", "preview", "previews", "insurance",
    "reports", "report", "says", "said", "update", "updates", "beat", "beats",
    "win", "wins", "loss", "draw", "vs", "versus", "match", "game", "cup",
}

_OUTLET_SUFFIX = re.compile(r"\s+[-–|]\s+[^-–|]{2,48}$")

SELECTION_VERSION = "2026-09-23-v1"

_SECTION_TOPICS = {
    "news": (
        "geopolitics", "france", "italy", "italia", "eu", "european union", "macro",
        "central bank", "ecb", "fed", "insurance", "reinsurance", "zurich",
        "artificial intelligence", "ai",
    ),
    "sport": (
        "psg", "paris saint-germain", "ligue 1", "champions league", "france national",
        "rugby", "counter-strike", "vitality", "culturepsg",
    ),
    "finance": (
        "equities", "rates", "ecb", "fed", "credit", "fx", "insurance", "zurich",
        "s&p", "msci", "bitcoin", "ethereum", "merger", "acquisition",
    ),
}

_CRYPTO_TERMS = ("bitcoin", "ethereum", "crypto", "btc", "eth", "memecoin")


def normalize_publisher(item: dict) -> str:
    raw = (item.get("source") or item.get("feed_name") or "").strip().lower()
    raw = re.sub(r"\s+", " ", raw)
    raw = re.sub(r"^https?://", "", raw).split("/")[0]
    raw = re.sub(r"^www\.", "", raw)
    return _PUBLISHER_ALIASES.get(raw, raw)


def _terms(cfg: dict, section: str) -> tuple[str, ...]:
    configured = ((cfg.get("selection") or {}).get("topics") or {}).get(section)
    if configured:
        return tuple(str(term).lower() for term in configured if str(term).strip())
    interests = f" {(cfg.get('interests') or '').lower()} "
    return tuple(term for term in _SECTION_TOPICS.get(section, ()) if term in interests)


def _contains_term(text: str, term: str) -> bool:
    term = term.strip().lower()
    if not term:
        return False
    if " " in term:
        return term in text
    return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text) is not None


def _topic_hits(item: dict, terms: tuple[str, ...]) -> int:
    text = f"{(item.get('title') or '').lower()} {(item.get('summary') or '').lower()}"
    return sum(1 for term in terms if _contains_term(text, term))


def _story_tokens(title: str, item: dict | None = None) -> tuple[str, ...]:
    words = re.findall(r"[a-z0-9àâäéèêëïîôùûüç]+", _headline_core(title, item))
    kept = []
    for word in words:
        if any(char.isdigit() for char in word) or (word not in _STOP and len(word) > 2):
            kept.append(word)
    return tuple(kept)


def _headline_core(title: str, item: dict | None = None) -> str:
    """Normalize a headline without dropping internal 'Team - Opponent' segments."""
    text = title.strip()
    suffix = _OUTLET_SUFFIX.search(text)
    if suffix:
        candidate = suffix.group(0).strip(" -–|").strip().lower()
        strip = False
        if item:
            publisher = normalize_publisher(item)
            if candidate == publisher or candidate in publisher or publisher in candidate:
                strip = True
        if candidate in _PUBLISHER_ALIASES or candidate in _PUBLISHER_ALIASES.values():
            strip = True
        if strip:
            text = text[: suffix.start()]
    text = re.sub(r"[^a-z0-9 àâäéèêëïîôùûüç'-]", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def _distinctive_tokens(title: str, item: dict | None = None) -> frozenset[str]:
    words = re.findall(r"[a-z0-9àâäéèêëïîôùûüç]+", _headline_core(title, item))
    kept = set()
    for word in words:
        if any(char.isdigit() for char in word):
            kept.add(word)
        elif word not in _GENERIC_STORY and len(word) > 2:
            kept.add(word)
    return frozenset(kept)


def _same_story(left: dict, right: dict) -> bool:
    left_title = left.get("title") or ""
    right_title = right.get("title") or ""
    if _headline_core(left_title, left) == _headline_core(right_title, right):
        return True

    left_distinct = _distinctive_tokens(left_title, left)
    right_distinct = _distinctive_tokens(right_title, right)
    if not left_distinct or not right_distinct:
        return False
    distinct_overlap = len(left_distinct & right_distinct)
    distinct_min = min(len(left_distinct), len(right_distinct))
    if len(left_distinct) >= 2 and len(right_distinct) >= 2 and distinct_overlap < 2:
        return False
    if distinct_overlap / distinct_min < 0.75:
        return False

    left_tokens, right_tokens = set(_story_tokens(left_title, left)), set(_story_tokens(right_title, right))
    if len(left_tokens) < 3 or len(right_tokens) < 3:
        return distinct_overlap >= distinct_min

    token_overlap = len(left_tokens & right_tokens)
    jaccard = token_overlap / len(left_tokens | right_tokens)
    return jaccard >= 0.72 and distinct_overlap >= 2


def _is_followup(newer: dict, older: dict) -> bool:
    if (newer.get("published") or "") <= (older.get("published") or ""):
        return False
    novel = set(_story_tokens(newer.get("title") or "")) - set(_story_tokens(older.get("title") or ""))
    return any(token in _UPDATE_TOKENS or any(char.isdigit() for char in token) for token in novel)


def _representative_key(item: dict) -> tuple:
    return (
        int(bool(item.get("_preferred"))),
        int(not item.get("via")),
        len(item.get("summary") or ""),
        item.get("published") or "",
        item.get("key") or "",
    )


def _collapse_duplicates(items: list[dict]) -> list[dict]:
    ordered = sorted(items, key=lambda it: (it.get("published") or "", it.get("key") or ""))
    kept: list[dict] = []
    for item in ordered:
        replaced = False
        for index, previous in enumerate(kept):
            if not _same_story(item, previous):
                continue
            if _is_followup(item, previous):
                continue
            if _representative_key(item) > _representative_key(previous):
                kept[index] = item
            replaced = True
            break
        if not replaced:
            kept.append(item)
    return kept


def _topic_caps(cfg: dict, section: str) -> dict[str, tuple[tuple[str, ...], int]]:
    configured = ((cfg.get("selection") or {}).get("topic_caps") or {}).get(section) or {}
    caps: dict[str, tuple[tuple[str, ...], int]] = {}
    if section == "finance":
        caps["crypto"] = (_CRYPTO_TERMS, int(configured.get("crypto", 2)))
    for name, value in configured.items():
        if name == "crypto":
            continue
        if isinstance(value, dict):
            terms = tuple(str(term).lower() for term in value.get("terms", ()))
            caps[name] = (terms, int(value.get("cap", 0)))
    return caps


def _capped(item: dict, counts: dict[str, int], caps: dict[str, tuple[tuple[str, ...], int]]) -> bool:
    text = f"{(item.get('title') or '').lower()} {(item.get('summary') or '').lower()}"
    for name, (terms, cap) in caps.items():
        if any(_contains_term(text, term) for term in terms) and counts.get(name, 0) >= cap:
            return True
    return False


def _select_editorial(items: list[dict], cfg: dict, section: str) -> list[dict]:
    policy = cfg.get("selection") or {}
    limit = max(0, int(cfg.get("max_candidates", 40)))
    excerpt = max(40, int(policy.get("excerpt_chars", 140)))
    publisher_cap = max(1, int(policy.get("publisher_cap", 8)))
    topic_slots = max(0, int(policy.get("min_topic_slots", 8)))
    terms = _terms(cfg, section)
    caps = _topic_caps(cfg, section)
    preferences = cfg.get("source_preferences", {}).get(section, {})
    prepared = []
    for item in items:
        if not item.get("title") or "key" not in item:
            continue
        copy = dict(item)
        copy["summary"] = str(copy.get("summary") or "")[:excerpt]
        copy["_publisher"] = normalize_publisher(copy)
        copy["_hits"] = _topic_hits(copy, terms)
        copy["_preferred"] = copy.get("feed_name", copy.get("source")) in preferences
        prepared.append(copy)
    prepared = _collapse_duplicates(prepared)

    def rank_key(item: dict) -> tuple:
        return (
            item["_hits"],
            int(item["_preferred"]),
            int(not item.get("via")),
            item.get("published") or "",
            item.get("key") or "",
        )

    by_recency = sorted(prepared, key=lambda item: (item.get("published") or "", item.get("key") or ""), reverse=True)
    by_recency.sort(key=lambda item: not item.get("new"))
    reserved = []
    for name, pref in preferences.items():
        matched = [item for item in by_recency if item.get("feed_name", item.get("source")) == name]
        reserved.extend(matched[: pref.get("candidate_slots", 0)])
    chosen = []
    seen = set()
    publishers: dict[str, int] = {}
    topics: dict[str, int] = {}

    def note(item: dict) -> None:
        publishers[item["_publisher"]] = publishers.get(item["_publisher"], 0) + 1
        text = f"{(item.get('title') or '').lower()} {(item.get('summary') or '').lower()}"
        for name, (words, _cap) in caps.items():
            if any(_contains_term(text, word) for word in words):
                topics[name] = topics.get(name, 0) + 1

    def accept(item: dict, ignore_caps: bool = False) -> None:
        if item["key"] in seen:
            return
        if not ignore_caps and (_capped(item, topics, caps) or publishers.get(item["_publisher"], 0) >= publisher_cap):
            return
        item["selection_reason"] = (
            f"topic:{item['_hits']} publisher:{item['_publisher']} "
            f"preferred:{int(item['_preferred'])} direct:{int(not item.get('via'))}"
        )
        chosen.append(item)
        seen.add(item["key"])
        note(item)

    for item in reserved:
        accept(item, ignore_caps=True)
        if len(chosen) >= limit:
            return _finish(chosen)
    topical = [item for item in prepared if item["key"] not in seen and item["_hits"] > 0]
    topical.sort(key=rank_key, reverse=True)
    topical_added = 0
    for item in topical:
        before = len(chosen)
        accept(item)
        topical_added += len(chosen) - before
        if len(chosen) >= limit:
            return _finish(chosen)
        if topical_added >= topic_slots:
            break
    rest = [item for item in prepared if item["key"] not in seen]
    rest.sort(key=rank_key, reverse=True)
    for item in rest:
        accept(item)
        if len(chosen) >= limit:
            break
    return _finish(chosen)


def _finish(items: list[dict]) -> list[dict]:
    for item in items:
        item.pop("_publisher", None)
        item.pop("_hits", None)
        item.pop("_preferred", None)
    return items


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
