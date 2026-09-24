"""Durable section fingerprints and last-good feed bodies.

Both live in the git checkout so a fresh runner can reuse them. Actions cache
is not the store. Feed bodies stay out of the public section text.
"""
from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime
from pathlib import Path

from selection import selection_fingerprint_fields


def fingerprint(section: str, items: list[dict], cfg: dict, extra_context: str = "") -> str:
    """Identity of the model input, ignoring publish bookkeeping such as new-flags."""
    rows = [
        {
            "key": it.get("key"),
            "title": it.get("title"),
            "source": it.get("source"),
            "published": it.get("published"),
            "summary": (it.get("summary") or "")[:140],
        }
        for it in items
    ]
    rows.sort(key=lambda row: (row["key"] or "", row["title"] or ""))
    selection = selection_fingerprint_fields(cfg, section, items)
    if selection["selection_mode"] == "legacy":
        # Legacy selection reorders the same pool after publishing sets `new`
        # flags. Preserve T04's no-change semantics for that bookkeeping only.
        # Editorial order is meaningful ranking and must remain order-sensitive.
        selection["candidate_order"] = [row["key"] for row in rows]
    payload = {
        "section": section,
        "candidates": rows,
        "interests": (cfg.get("interests") or "").strip(),
        "policy": cfg.get("source_preferences", {}).get(section, {}),
        "provider": cfg.get("provider", "anthropic"),
        "model": cfg.get("model"),
        "prompt_version": cfg.get("prompt_version", 1),
        "language": cfg.get("language", "English"),
        "item_target": cfg.get("item_targets", {}).get(section),
        "finance_context": extra_context or "",
        # Candidate IDs in the prompt are positional. Sorting rows alone loses
        # ranking changes; use T07's versioned handoff for both selection modes.
        **selection,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def load_sections(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        stored = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    sections = stored.get("sections") if isinstance(stored, dict) else None
    return sections if isinstance(sections, dict) else {}


def save_sections(path: Path, sections: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "sections": sections}, ensure_ascii=False, indent=1))


class GitFeedCache:
    """JSON feed records with a hard size and count ceiling."""

    def __init__(self, path: Path, *, max_bytes: int = 1_000_000, max_records: int = 40):
        self.path = path
        self.max_bytes = max_bytes
        self.max_records = max_records
        self._lock = threading.Lock()
        self.records = self._load()

    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            stored = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            return {}
        records = stored.get("records") if isinstance(stored, dict) else None
        return records if isinstance(records, dict) else {}

    def get(self, url: str) -> dict | None:
        with self._lock:
            record = self.records.get(url)
            return dict(record) if isinstance(record, dict) else None

    def put(self, url: str, record: dict) -> None:
        with self._lock:
            self.records[url] = dict(record)
            self._evict()

    def _evict(self) -> None:
        def fetched(item):
            value = item[1].get("fetched_at") or ""
            return value

        ordered = sorted(self.records.items(), key=fetched, reverse=True)
        kept = {}
        total = 0
        for url, record in ordered:
            if len(kept) >= self.max_records:
                break
            size = len(json.dumps(record))
            if kept and total + size > self.max_bytes:
                break
            kept[url] = record
            total += size
        self.records = kept

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"version": 1, "records": self.records}, indent=0))


def parse_time(value: str | None) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp
