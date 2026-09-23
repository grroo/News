"""Workflow guard: slot idempotency, reservation claim, and deploy-only recovery."""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parent.parent
LIVE_URL = os.environ.get("LIVE_BRIEFING_URL", "https://grroo.github.io/News/data/briefing.json")
CLAIM_URL = os.environ.get("RESERVATION_CLAIM_URL", "").rstrip("/")
REPOSITORY = os.environ.get("GITHUB_REPOSITORY", "grroo/News")
WORKFLOW_FILE = os.environ.get("GITHUB_WORKFLOW", "briefing.yml")
GIT_REF = os.environ.get("GITHUB_REF_NAME", "main")

sys.path.insert(0, str(Path(__file__).parent))
from slot_health import satisfies_healthy_slot  # noqa: E402


def latest_slot(now, schedule):
    local = now.astimezone(ZoneInfo(schedule["timezone"]))
    slots = [
        local.replace(hour=h, minute=schedule["minute"], second=0, microsecond=0)
        for h in schedule["hours"]
    ]
    return max([s for s in slots if s <= local] or [slots[-1] - timedelta(days=1)])


def fresh_legacy(data, slot, now):
    try:
        generated = datetime.fromisoformat(data["generated_at"].replace("Z", "+00:00"))
        return data.get("mode") == "llm" and slot <= generated <= now + timedelta(minutes=1)
    except (KeyError, ValueError, TypeError):
        return False


def slot_satisfied(data, slot, now, *, strict_gate=False, request_id=None, slot_text=None):
    if not data:
        return False
    slot_iso = slot_text or slot.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if strict_gate and data.get("schema_version") == 2:
        return satisfies_healthy_slot(data, slot_iso, now, request_id)
    return fresh_legacy(data, slot, now)


def decide(
    slot_text,
    now,
    schedule,
    live,
    local,
    *,
    strict_gate=False,
    request_id=None,
    reservation_id=None,
    require_reservation=False,
    has_api_key=True,
    mock_requested=False,
    deploy_only=False,
    generate_requested=True,
):
    if deploy_only:
        return False, True, "Deploy committed briefing without generation"

    if generate_requested and not has_api_key and not mock_requested:
        return False, False, "Missing ANTHROPIC_API_KEY; refusing generation"

    if require_reservation and not reservation_id:
        return False, False, "Reservation required for generation"

    if not slot_text:
        if not generate_requested:
            return False, False, "No generation requested"
        return True, True, "Manual generation"

    slot = datetime.fromisoformat(slot_text.replace("Z", "+00:00"))
    if slot.tzinfo is None:
        raise ValueError("scheduled_slot must include its timezone")
    if slot != latest_slot(now, schedule):
        return False, False, "Obsolete or invalid scheduled slot"
    if slot_satisfied(live, slot, now, strict_gate=strict_gate, request_id=request_id, slot_text=slot_text):
        return False, False, "Slot already published"
    if slot_satisfied(local, slot, now, strict_gate=strict_gate, request_id=request_id, slot_text=slot_text):
        return False, True, "Deploy existing briefing without generating again"
    return True, True, "New scheduled briefing"


def claim_reservation(reservation_id, request_id, run_id, run_attempt):
    if not reservation_id:
        return True, "No reservation to claim (legacy scheduled path)"
    token = os.environ.get("NEWS_RESERVATION_GATE_TOKEN", "").strip()
    if not token:
        return False, "NEWS_RESERVATION_GATE_TOKEN is not configured"
    if not CLAIM_URL:
        return False, "RESERVATION_CLAIM_URL is not configured"
    body = {
        "reservation_id": reservation_id,
        "request_id": request_id,
        "run_id": int(run_id),
        "run_attempt": int(run_attempt),
        "repository": REPOSITORY,
        "workflow": WORKFLOW_FILE,
        "ref": GIT_REF,
    }
    response = requests.post(
        CLAIM_URL,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=body,
        timeout=20,
    )
    if response.status_code >= 400:
        return False, f"Reservation claim HTTP {response.status_code}: {response.text[:200]}"
    payload = response.json()
    if payload.get("decision") != "allowed":
        reason = payload.get("reason") or payload.get("decision") or "denied"
        return False, f"Reservation claim denied: {reason}"
    return True, "Reservation claimed"


def write_outputs(build, publish, reason, error=None):
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    with open(output_path, "a") as output:
        output.write(f"build={'true' if build else 'false'}\n")
        output.write(f"publish={'true' if publish else 'false'}\n")
        output.write(f"reason={reason}\n")
        if error:
            output.write(f"error={error}\n")


def guard_main():
    now = datetime.now(timezone.utc)
    slot_text = os.environ.get("SCHEDULED_SLOT", "").strip()
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    schedule = json.loads((ROOT / "schedule.json").read_text())
    if not slot_text and event_name == "schedule":
        slot = latest_slot(now, schedule)
        slot_text = slot.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    request_id = os.environ.get("REQUEST_ID", "").strip() or None
    reservation_id = os.environ.get("RESERVATION_ID", "").strip() or None
    strict_gate = os.environ.get("STRICT_SLOT_GATE", "").lower() in {"1", "true", "yes"}
    require_reservation = os.environ.get("REQUIRE_RESERVATION", "").lower() in {"1", "true", "yes"}
    deploy_only = os.environ.get("DEPLOY_ONLY", "").lower() in {"1", "true", "yes"}
    generate_requested = os.environ.get("GENERATE_REQUESTED", "true").lower() not in {"0", "false", "no"}
    mock_requested = os.environ.get("MOCK_REQUESTED", "").lower() in {"1", "true", "yes"}
    api_key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    has_api_key = bool(api_key)

    live = {}
    if slot_text and not deploy_only:
        response = requests.get(
            LIVE_URL,
            params={"slot_check": now.timestamp()},
            headers={"Cache-Control": "no-cache"},
            timeout=20,
        )
        response.raise_for_status()
        live = response.json()

    local = json.loads((ROOT / "data/briefing.json").read_text())
    build, publish, reason = decide(
        slot_text,
        now,
        schedule,
        live,
        local,
        strict_gate=strict_gate,
        request_id=request_id,
        reservation_id=reservation_id,
        require_reservation=require_reservation,
        has_api_key=has_api_key,
        mock_requested=mock_requested,
        deploy_only=deploy_only,
        generate_requested=generate_requested,
    )
    print(reason)
    write_outputs(build, publish, reason)


def claim_main():
    reservation_id = os.environ.get("RESERVATION_ID", "").strip()
    request_id = os.environ.get("REQUEST_ID", "").strip()
    run_id = os.environ.get("GITHUB_RUN_ID", "0")
    run_attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "1")
    ok, message = claim_reservation(reservation_id, request_id, run_id, run_attempt)
    print(message)
    if not ok:
        write_outputs(False, False, message, error=message)
        sys.exit(1)
    write_outputs(True, True, message)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--claim", action="store_true", help="Claim a Worker reservation before generation")
    args = parser.parse_args()
    if args.claim:
        claim_main()
    else:
        guard_main()


if __name__ == "__main__":
    main()
