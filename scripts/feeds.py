"""
Minimal RSS 2.0 / Atom parser on the standard library.

Deliberately dependency-free: the only things the build needs from a feed are
title, link, published time, a short text summary and (for media) a duration.
"""
from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "media": "http://search.yahoo.com/mrss/",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd",
    "dc": "http://purl.org/dc/elements/1.1/",
    "content": "http://purl.org/rss/1.0/modules/content/",
}

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def strip_html(text: Optional[str], limit: int = 300) -> str:
    if not text:
        return ""
    text = html.unescape(_TAG_RE.sub(" ", text))
    text = _WS_RE.sub(" ", text).strip()
    if len(text) > limit:
        text = text[: limit - 1].rsplit(" ", 1)[0] + "…"
    return text


def parse_date(value: Optional[str]) -> Optional[datetime]:
    """Accepts RFC 822 (RSS) and ISO 8601 (Atom). Returns an aware UTC datetime."""
    if not value:
        return None
    value = value.strip()
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_duration(value: Optional[str]) -> Optional[int]:
    """itunes:duration → seconds. Accepts '3600', '1:00:00', '59:30'."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return int(value)
    parts = value.split(":")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return None
    secs = 0
    for n in nums:
        secs = secs * 60 + n
    return secs


def _text(el: Optional[ET.Element]) -> Optional[str]:
    return el.text if el is not None and el.text else None


def parse_feed(raw: bytes, source_name: str) -> list[dict]:
    """Return a list of normalised item dicts. Never raises on odd feeds; returns []."""
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        # Some feeds sneak in control characters or a BOM; try a lenient pass.
        cleaned = re.sub(rb"[\x00-\x08\x0b\x0c\x0e-\x1f]", b"", raw).lstrip(b"\xef\xbb\xbf")
        try:
            root = ET.fromstring(cleaned)
        except ET.ParseError:
            return []

    tag = root.tag.lower()
    if tag.endswith("feed"):  # Atom
        return [_atom_entry(e, source_name) for e in root.findall("atom:entry", NS)]
    # RSS 2.0 (channel/item); RSS 1.0 (rdf) puts <item> at root level
    items = root.findall("./channel/item") or root.findall("item")
    return [_rss_item(i, source_name) for i in items]


def _rss_item(item: ET.Element, source_name: str) -> dict:
    link = _text(item.find("link")) or ""
    if not link:
        enc = item.find("enclosure")
        if enc is not None:
            link = enc.get("url", "")
    guid = _text(item.find("guid")) or link
    published = parse_date(
        _text(item.find("pubDate"))
        or _text(item.find("dc:date", NS))
        or _text(item.find("published"))
    )
    summary = (
        _text(item.find("description"))
        or _text(item.find("itunes:summary", NS))
        or _text(item.find("content:encoded", NS))
    )
    enclosure = item.find("enclosure")
    return {
        "id": guid.strip(),
        "title": strip_html(_text(item.find("title")), 200),
        "url": link.strip(),
        "summary": strip_html(summary),
        "published": published.isoformat() if published else None,
        "source": source_name,
        "duration_s": parse_duration(_text(item.find("itunes:duration", NS))),
        "enclosure_url": enclosure.get("url") if enclosure is not None else None,
    }


def _atom_entry(entry: ET.Element, source_name: str) -> dict:
    link = ""
    for l in entry.findall("atom:link", NS):
        rel = l.get("rel", "alternate")
        if rel == "alternate" or not link:
            link = l.get("href", "")
    published = parse_date(
        _text(entry.find("atom:published", NS)) or _text(entry.find("atom:updated", NS))
    )
    summary = _text(entry.find("atom:summary", NS)) or _text(entry.find("atom:content", NS))
    media_desc = entry.find("media:group/media:description", NS)
    if summary is None and media_desc is not None:
        summary = media_desc.text
    video_id = _text(entry.find("yt:videoId", NS))
    return {
        "id": (_text(entry.find("atom:id", NS)) or link).strip(),
        "title": strip_html(_text(entry.find("atom:title", NS)), 200),
        "url": link.strip(),
        "summary": strip_html(summary),
        "published": published.isoformat() if published else None,
        "source": source_name,
        "duration_s": None,
        "video_id": video_id,
    }
