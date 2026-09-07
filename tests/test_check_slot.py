import importlib.util
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("check_slot", ROOT / "scripts/check_slot.py")
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)
SCHEDULE = json.loads((ROOT / "schedule.json").read_text())
NOW = datetime(2026, 9, 7, 5, 5, tzinfo=timezone.utc)
SLOT = "2026-09-07T05:00:00Z"
FRESH = {"generated_at": SLOT, "mode": "llm"}


class SlotTests(unittest.TestCase):
    def decision(self, slot=SLOT, live=None, local=None):
        return guard.decide(slot, NOW, SCHEDULE, live or {}, local or {})[:2]

    def test_manual_builds(self):
        self.assertEqual(self.decision(slot="", live=FRESH), (True, True))

    def test_live_publication_skips(self):
        self.assertEqual(self.decision(live=FRESH), (False, False))

    def test_committed_data_redeploys_without_llm(self):
        self.assertEqual(self.decision(local=FRESH), (False, True))

    def test_stale_data_builds(self):
        self.assertEqual(self.decision(), (True, True))

    def test_obsolete_and_future_slots_skip(self):
        for slot in ["2026-09-06T17:00:00Z", "2026-09-07T11:00:00Z"]:
            self.assertEqual(self.decision(slot=slot), (False, False))

    def test_mock_never_completes_real_slot(self):
        self.assertEqual(self.decision(live={**FRESH, "mode": "mock"}), (True, True))

    def test_dst_previous_day(self):
        now = datetime(2026, 3, 29, 4, 59, tzinfo=timezone.utc)
        self.assertEqual(guard.latest_slot(now, SCHEDULE).astimezone(timezone.utc).isoformat(),
                         "2026-03-28T18:00:00+00:00")

    def test_invalid_timestamp_rejected(self):
        with self.assertRaises(ValueError):
            self.decision(slot="invalid")


if __name__ == "__main__":
    unittest.main()
