"""Contract fixture and slot-satisfaction tests for T00."""
from __future__ import annotations

import importlib.util
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "contracts"
SCHEDULE = json.loads((ROOT / "schedule.json").read_text())

spec = importlib.util.spec_from_file_location("check_slot", ROOT / "scripts" / "check_slot.py")
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


def satisfies_healthy_slot(edition: dict, slot: str, now: datetime | None = None) -> bool:
    """Contract rule: healthy scheduled refresh completion."""
    now = now or datetime.now(timezone.utc)
    slot_dt = datetime.fromisoformat(slot.replace("Z", "+00:00"))
    if not guard.fresh(edition, slot_dt, now):
        return False
    quality = edition.get("quality", {}).get("overall")
    if quality is not None and quality not in ("healthy",):
        return False
    for section in edition.get("sections", {}).values():
        state = section.get("state")
        if state == "failed" and not section.get("items"):
            return False
    return True


class ContractFixtureTests(unittest.TestCase):
    def test_all_fixtures_are_valid_json(self):
        for path in FIXTURES.glob("*.json"):
            with self.subTest(path=path.name):
                json.loads(path.read_text())

    def test_healthy_edition_satisfies_slot(self):
        edition = json.loads((FIXTURES / "healthy-edition.json").read_text())
        self.assertTrue(
            satisfies_healthy_slot(edition, edition["scheduled_slot"], datetime(2026, 9, 23, 11, 30, tzinfo=timezone.utc))
        )

    def test_degraded_edition_does_not_satisfy_healthy_slot(self):
        edition = json.loads((FIXTURES / "degraded-edition.json").read_text())
        self.assertFalse(
            satisfies_healthy_slot(edition, edition["scheduled_slot"], datetime(2026, 9, 23, 17, 30, tzinfo=timezone.utc))
        )

    def test_provider_failure_does_not_satisfy_slot(self):
        edition = json.loads((FIXTURES / "provider-failure-edition.json").read_text())
        self.assertFalse(
            satisfies_healthy_slot(edition, edition["scheduled_slot"], datetime(2026, 9, 23, 17, 30, tzinfo=timezone.utc))
        )

    def test_no_change_keeps_original_generated_at(self):
        edition = json.loads((FIXTURES / "no-change-result.json").read_text())
        self.assertEqual(edition["quality"]["overall"], "unchanged")
        self.assertLess(datetime.fromisoformat(edition["generated_at"]), datetime.fromisoformat(edition["checked_at"]))
        self.assertGreater(len(edition["request_ids"]), 1)

    def test_archived_edition_has_legacy_shape(self):
        edition = json.loads((FIXTURES / "archived-edition.json").read_text())
        self.assertNotIn("schema_version", edition)
        self.assertIn("sections", edition)
        self.assertIn("generated_at", edition)

    def test_refresh_job_lifecycle_documents_statuses(self):
        lifecycle = json.loads((FIXTURES / "refresh-job-lifecycle.json").read_text())
        statuses = [step["status"] for step in lifecycle["transitions"]]
        self.assertEqual(statuses[0], "accepted")
        self.assertEqual(statuses[-1], "succeeded")


class ExtractionCompatibilityTests(unittest.TestCase):
    def test_build_reexports_extracted_symbols(self):
        import sys

        sys.path.insert(0, str(ROOT / "scripts"))
        import build

        for name in (
            "Fetcher",
            "fetch_all",
            "call_claude",
            "select_candidates",
            "dedupe",
            "enforce_preferences",
            "published_item",
            "price_moves",
        ):
            self.assertTrue(hasattr(build, name), name)


if __name__ == "__main__":
    unittest.main()
