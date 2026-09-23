"""Production slot-satisfaction rules from docs/contracts/README.md."""
from __future__ import annotations

from datetime import datetime, timedelta


def _instant(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.utcoffset() is None:
        raise ValueError("Timezone required")
    return dt


def satisfies_healthy_slot(edition: dict, slot: str, now: datetime, request_id: str | None = None) -> bool:
    if edition.get("schema_version") != 2:
        return False
    if edition.get("mode") != "llm" or edition.get("quality", {}).get("overall") != "healthy":
        return False
    if request_id and request_id not in edition.get("request_ids", []):
        return False
    try:
        due = _instant(slot)
        if _instant(edition.get("scheduled_slot", "")) != due:
            return False
        source = _instant(edition["source_checked_at"])
        done = _instant(edition["refresh"]["completed_at"])
        generated = _instant(edition["generated_at"])
        if not (due <= source <= done <= now + timedelta(seconds=60)):
            return False
        if generated > now + timedelta(seconds=60):
            return False
        outcome = edition["refresh"]["outcome"]
        if outcome == "generated" and generated < due:
            return False
        if outcome == "no_change" and edition["refresh"]["reused_edition_id"] != edition["edition_id"]:
            return False
        for name in ("news", "sport", "finance"):
            section = edition["sections"][name]
            if section["state"] not in ("healthy", "unchanged") or section.get("error"):
                return False
            if not isinstance(section.get("briefing"), list) or not isinstance(section.get("items"), list):
                return False
            if section["state"] == "unchanged" and not section.get("reused_edition_id"):
                return False
            succeeded = _instant(section["last_success_at"])
            if succeeded > done + timedelta(seconds=60):
                return False
            if section["state"] == "healthy" and succeeded < due:
                return False
        media = edition["sections"]["media"]
        return media["state"] in ("healthy", "unchanged", "skipped") and not media.get("error")
    except (KeyError, TypeError, ValueError):
        return False
