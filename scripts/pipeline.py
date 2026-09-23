"""Edition health, publication guard and config checks for the builder."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parent.parent
EDITION_SCHEMA = json.loads((ROOT / "docs/contracts/schemas/edition.json").read_text())
EDITION_VALIDATOR = Draft202012Validator(EDITION_SCHEMA)
AI_SECTIONS = ("news", "sport", "finance")
BLOCKING_SOURCE = {"unavailable", "stale"}


def validate_config(cfg: dict) -> list[str]:
    problems = []
    provider = cfg.get("provider", "anthropic")
    if provider not in {"anthropic", "openai"}:
        problems.append("provider must be anthropic or openai")
    if not isinstance(cfg.get("model"), str) or not cfg.get("model"):
        problems.append("model must be a non-empty string")
    attempts = cfg.get("provider_max_attempts", 2)
    if type(attempts) is not int or not 1 <= attempts <= 2:
        problems.append("provider_max_attempts must be 1 or 2")
    timeout = cfg.get("provider_timeout_seconds", 120)
    if type(timeout) is not int or not 10 <= timeout <= 300:
        problems.append("provider_timeout_seconds must be an integer from 10 to 300")
    prompt_version = cfg.get("prompt_version", 1)
    if type(prompt_version) is not int or prompt_version < 1:
        problems.append("prompt_version must be an integer >= 1")
    archive = cfg.get("archive")
    if archive is not None:
        if not isinstance(archive, dict):
            problems.append("archive must be a mapping")
        else:
            age = archive.get("max_age_days", 7)
            count = archive.get("max_count", cfg.get("keep_past_briefings", 6))
            size = archive.get("max_bytes", 8_000_000)
            if type(age) is not int or not 1 <= age <= 30:
                problems.append("archive.max_age_days must be an integer from 1 to 30")
            if type(count) is not int or not 1 <= count <= 200:
                problems.append("archive.max_count must be an integer from 1 to 200")
            if type(size) is not int or not 100_000 <= size <= 50_000_000:
                problems.append("archive.max_bytes must be an integer from 100000 to 50000000")
    feed_cache = cfg.get("feed_cache")
    if feed_cache is not None:
        if not isinstance(feed_cache, dict):
            problems.append("feed_cache must be a mapping")
        else:
            size = feed_cache.get("max_bytes", 1_000_000)
            records = feed_cache.get("max_records", 40)
            if type(size) is not int or not 100_000 <= size <= 5_000_000:
                problems.append("feed_cache.max_bytes must be an integer from 100000 to 5000000")
            if type(records) is not int or not 1 <= records <= 80:
                problems.append("feed_cache.max_records must be an integer from 1 to 80")
    return problems


def parse_timestamp(value: str | None, label: str) -> datetime:
    if not value:
        raise ValueError(f"Missing {label}")
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError(f"{label} needs a timezone")
    return stamp.astimezone(timezone.utc)


def run_clock(now_text: str | None) -> datetime:
    if now_text:
        return parse_timestamp(now_text, "--now")
    return datetime.now(timezone.utc)


def scheduled_slot(env_value: str | None) -> str | None:
    if not env_value:
        return None
    parse_timestamp(env_value, "SCHEDULED_SLOT")
    return env_value


def trigger_for(event_name: str | None, slot: str | None) -> str:
    if slot or event_name == "schedule":
        return "scheduled"
    if event_name == "push":
        return "push"
    if event_name == "workflow_dispatch":
        return "workflow_dispatch"
    return "workflow_dispatch"


def request_ids(existing: list[str] | None, current: str, extra: str | None, *, reuse: bool) -> list[str]:
    values = list(existing or []) if reuse else []
    for item in [current, *(part.strip() for part in (extra or "").split(","))]:
        if item and item not in values:
            values.append(item)
    return values


def source_error(feed_health: list[dict], section: str) -> str | None:
    failed = [row for row in feed_health if row.get("section") == section and row.get("status") in BLOCKING_SOURCE]
    if not failed:
        return None
    names = ", ".join(str(row.get("name") or "source") for row in failed[:5])
    return f"Source check failed: {names}"


def overall_quality(mode: str, states: list[str]) -> str:
    if mode == "mock":
        return "mock"
    if states and all(state == "failed" for state in states):
        return "failed"
    if any(state in {"failed", "degraded"} for state in states):
        return "degraded"
    return "healthy"


def publication_is_newer(existing: dict | None, run_at: datetime) -> bool:
    if not isinstance(existing, dict):
        return False
    refresh = existing.get("refresh") if isinstance(existing.get("refresh"), dict) else {}
    stamp = existing.get("source_checked_at") or refresh.get("completed_at") or existing.get("generated_at")
    try:
        previous = parse_timestamp(stamp, "existing edition")
    except ValueError:
        return False
    return previous > run_at


def edition_errors(edition: dict) -> list[str]:
    return [error.message for error in EDITION_VALIDATOR.iter_errors(edition)]


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text)
    os.replace(temporary, path)


def load_edition(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None
