"""Avoid rebuilding a briefing when a scheduler retries the same publication slot."""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parent.parent
LIVE_URL = "https://grroo.github.io/News/data/briefing.json"


def latest_slot(now, schedule):
    local = now.astimezone(ZoneInfo(schedule["timezone"]))
    slots = [local.replace(hour=h, minute=schedule["minute"], second=0, microsecond=0)
             for h in schedule["hours"]]
    return max([s for s in slots if s <= local] or [slots[-1] - timedelta(days=1)])


def fresh(data, slot, now):
    try:
        generated = datetime.fromisoformat(data["generated_at"].replace("Z", "+00:00"))
        return data.get("mode") == "llm" and slot <= generated <= now + timedelta(minutes=1)
    except (KeyError, ValueError, TypeError):
        return False


def decide(slot_text, now, schedule, live, local):
    if not slot_text:
        return True, True, "Manual or code update"
    slot = datetime.fromisoformat(slot_text.replace("Z", "+00:00"))
    if slot.tzinfo is None:
        raise ValueError("scheduled_slot must include its timezone")
    if slot != latest_slot(now, schedule):
        return False, False, "Obsolete or invalid scheduled slot"
    if fresh(live, slot, now):
        return False, False, "Slot already published"
    if fresh(local, slot, now):
        return False, True, "Deploy existing briefing without generating again"
    return True, True, "New scheduled briefing"


def main():
    now = datetime.now(timezone.utc)
    slot_text = os.environ.get("SCHEDULED_SLOT", "")
    live = {}
    if slot_text:
        # Fail visibly on a network error. Cloudflare retries rather than paying
        # for a second briefing when the site's state cannot be established.
        response = requests.get(LIVE_URL, params={"slot_check": now.timestamp()},
                                headers={"Cache-Control": "no-cache"}, timeout=20)
        response.raise_for_status()
        live = response.json()
    local = json.loads((ROOT / "data/briefing.json").read_text())
    schedule = json.loads((ROOT / "schedule.json").read_text())
    build, publish, reason = decide(slot_text, now, schedule, live, local)
    print(reason)
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        output.write(f"build={str(build).lower()}\npublish={str(publish).lower()}\n")


if __name__ == "__main__":
    main()
