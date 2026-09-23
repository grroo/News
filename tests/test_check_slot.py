import importlib.util
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "contracts"
spec = importlib.util.spec_from_file_location("check_slot", ROOT / "scripts" / "check_slot.py")
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)
SCHEDULE = json.loads((ROOT / "schedule.json").read_text())
NOW = datetime(2026, 9, 7, 5, 5, tzinfo=timezone.utc)
SLOT = "2026-09-07T05:00:00Z"
FRESH = {"generated_at": SLOT, "mode": "llm"}


def decide(**kwargs):
    defaults = dict(
        slot_text=SLOT,
        now=NOW,
        schedule=SCHEDULE,
        live={},
        local={},
        strict_gate=False,
        has_api_key=True,
        generate_requested=True,
    )
    defaults.update(kwargs)
    return guard.decide(**defaults)


class SlotTests(unittest.TestCase):
    def test_live_publication_skips(self):
        self.assertEqual(decide(live=FRESH)[:2], (False, False))

    def test_committed_data_redeploys_without_llm(self):
        self.assertEqual(decide(local=FRESH)[:2], (False, True))

    def test_stale_data_builds(self):
        self.assertEqual(decide()[:2], (True, True))

    def test_obsolete_and_future_slots_skip(self):
        for slot in ["2026-09-06T17:00:00Z", "2026-09-07T11:00:00Z"]:
            self.assertEqual(decide(slot_text=slot)[:2], (False, False))

    def test_mock_never_completes_real_slot(self):
        self.assertEqual(decide(live={**FRESH, "mode": "mock"})[:2], (True, True))

    def test_missing_api_key_refuses_generation(self):
        self.assertEqual(decide(has_api_key=False)[:2], (False, False))

    def test_missing_api_key_allows_explicit_mock(self):
        self.assertEqual(decide(has_api_key=False, mock_requested=True)[:2], (True, True))

    def test_deploy_only_never_builds(self):
        self.assertEqual(decide(deploy_only=True)[:2], (False, True))

    def test_manual_generation_without_reservation_when_not_required(self):
        self.assertEqual(decide(slot_text="")[:2], (True, True))

    def test_manual_generation_requires_reservation_when_configured(self):
        self.assertEqual(decide(slot_text="", require_reservation=True)[:2], (False, False))

    def test_manual_generation_allowed_with_reservation(self):
        self.assertEqual(
            decide(slot_text="", require_reservation=True, reservation_id="resv_123")[:2],
            (True, True),
        )

    def test_manual_generation_after_slot_satisfied(self):
        healthy = json.loads((FIXTURES / "healthy-edition.json").read_text())
        self.assertEqual(decide(slot_text="", live=healthy, strict_gate=True)[:2], (True, True))

    def test_strict_gate_rejects_degraded_slot(self):
        degraded = json.loads((FIXTURES / "degraded-edition.json").read_text())
        self.assertEqual(
            decide(live=degraded, slot_text=degraded["scheduled_slot"], strict_gate=True)[:2],
            (False, False),
        )

    def test_strict_gate_accepts_healthy_slot(self):
        healthy = json.loads((FIXTURES / "healthy-edition.json").read_text())
        self.assertEqual(
            decide(
                live=healthy,
                slot_text=healthy["scheduled_slot"],
                strict_gate=True,
                request_id=healthy["request_ids"][0],
            )[:2],
            (False, False),
        )

    def test_dst_previous_day(self):
        now = datetime(2026, 3, 29, 4, 59, tzinfo=timezone.utc)
        self.assertEqual(
            guard.latest_slot(now, SCHEDULE).astimezone(timezone.utc).isoformat(),
            "2026-03-28T18:00:00+00:00",
        )

    def test_invalid_timestamp_rejected(self):
        with self.assertRaises(ValueError):
            decide(slot_text="invalid")


if __name__ == "__main__":
    unittest.main()
