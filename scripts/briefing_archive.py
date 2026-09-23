"""Age, count and size retention for data/past. Does not rewrite git history."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


def policy_from(cfg: dict) -> dict:
    archive = cfg.get("archive") if isinstance(cfg.get("archive"), dict) else {}
    count = archive.get("max_count", cfg.get("keep_past_briefings", 6))
    return {
        "max_age_days": int(archive.get("max_age_days", 7)),
        "max_count": int(count),
        "max_bytes": int(archive.get("max_bytes", 8_000_000)),
    }


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _load(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def archive_previous(past_dir: Path, current: Path) -> None:
    """Copy the live edition into past once. A repeated no-change run does not call this."""
    past_dir.mkdir(parents=True, exist_ok=True)
    data = _load(current)
    generated = (data or {}).get("generated_at")
    if not isinstance(generated, str):
        return
    dest = past_dir / f"{generated.replace(':', '-')}.json"
    if dest.exists():
        return
    dest.write_text(current.read_text())


def prune(past_dir: Path, now: datetime, policy: dict) -> list[dict]:
    past_dir.mkdir(parents=True, exist_ok=True)
    now = _aware(now)
    max_age = timedelta(days=policy["max_age_days"])
    rows = []
    for path in past_dir.glob("*.json"):
        if path.name == "index.json":
            continue
        data = _load(path)
        generated = _aware(datetime.fromisoformat(data["generated_at"])) if data and isinstance(data.get("generated_at"), str) else None
        if generated is None:
            path.unlink(missing_ok=True)
            continue
        rows.append((generated, path.stat().st_size, path, data))
    rows.sort(key=lambda row: row[0], reverse=True)
    kept = []
    total = 0
    for generated, size, path, data in rows:
        too_old = now - generated > max_age
        over_count = len(kept) >= policy["max_count"]
        over_size = bool(kept) and total + size > policy["max_bytes"]
        if too_old or over_count or over_size:
            path.unlink(missing_ok=True)
            continue
        kept.append((generated, size, path, data))
        total += size
    index = []
    for _generated, _size, path, data in kept:
        sections = data.get("sections") if isinstance(data.get("sections"), dict) else {}
        index.append({
            "file": path.name,
            "generated_at": data.get("generated_at"),
            "counts": {key: len((value or {}).get("items", [])) for key, value in sections.items() if isinstance(value, dict)},
        })
    (past_dir / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1))
    return index
