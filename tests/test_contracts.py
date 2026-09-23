"""Test-only reference decisions shared with the JavaScript contract suite."""
from __future__ import annotations

import copy
import json
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "contracts"
SCHEMAS = ROOT / "docs" / "contracts" / "schemas"
FORMAT_CHECKER = FormatChecker()
SCHEMA_DOCS = {p.stem: json.loads(p.read_text()) for p in SCHEMAS.glob("*.json")}


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def validator(name: str, entrypoint: str | None = None) -> Draft202012Validator:
    doc = SCHEMA_DOCS[name]
    schema = doc if not entrypoint else {
        "$schema": doc["$schema"], "$defs": doc["$defs"], "$ref": f"#/$defs/{entrypoint}"
    }
    return Draft202012Validator(schema, format_checker=FORMAT_CHECKER)


def materialize(case: dict):
    obj = copy.deepcopy(load(case["fixture"]) if "fixture" in case else case["instance"])

    def parent(pointer: str):
        parts = pointer.strip("/").split("/")
        target = obj
        for part in parts[:-1]:
            target = target[part]
        return target, parts[-1]

    for pointer, value in case.get("set", {}).items():
        target, key = parent(pointer)
        target[key] = value
    for pointer in case.get("delete", []):
        target, key = parent(pointer)
        del target[key]
    return obj


def instant(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.utcoffset() is None:
        raise ValueError("Timezone required")
    return dt


def satisfies_healthy_slot(edition: dict, slot: str, now: datetime, request_id: str | None = None) -> bool:
    """Reference contract rule; production enforcement belongs to T01/T05."""
    if not validator("edition").is_valid(edition) or edition.get("schema_version") != 2:
        return False
    if edition["mode"] != "llm" or edition["quality"]["overall"] != "healthy":
        return False
    if request_id and request_id not in edition["request_ids"]:
        return False
    try:
        due = instant(slot)
        if instant(edition.get("scheduled_slot", "")) != due:
            return False
        source = instant(edition["source_checked_at"])
        done = instant(edition["refresh"]["completed_at"])
        generated = instant(edition["generated_at"])
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
            if section["state"] not in ("healthy", "unchanged") or "error" in section:
                return False
            if not isinstance(section.get("briefing"), list) or not isinstance(section.get("items"), list):
                return False
            succeeded = instant(section["last_success_at"])
            if succeeded > done + timedelta(seconds=60):
                return False
            if section["state"] == "healthy" and succeeded < due:
                return False
        media = edition["sections"]["media"]
        return media["state"] in ("healthy", "unchanged", "skipped") and "error" not in media
    except (KeyError, TypeError, ValueError):
        return False


def claim_decision(reservation: dict, claim: dict, now: str) -> tuple[str, str | None]:
    """Reference claim semantics; T05 implements the atomic Durable Object transition."""
    if not validator("reservation", "reservation").is_valid(reservation):
        return "denied", "unknown"
    if not validator("reservation", "claimRequest").is_valid(claim):
        return "denied", "scope_mismatch"
    if reservation["reservation_id"] != claim["reservation_id"]:
        return "denied", "unknown"
    if reservation["request_id"] != claim["request_id"]:
        return "denied", "request_mismatch"
    if reservation["scope"] != {k: claim[k] for k in ("repository", "workflow", "ref")}:
        return "denied", "scope_mismatch"
    if reservation["status"] == "cancelled":
        return "denied", "cancelled"
    if reservation["status"] == "claimed":
        if (reservation["claimed_run_id"], reservation["claimed_run_attempt"]) == (
            claim["run_id"], claim["run_attempt"]
        ):
            return "allowed", None
        return "denied", "claimed_by_other"
    if reservation["status"] != "reserved":
        return "denied", "expired"
    if instant(now) >= instant(reservation["expires_at"]):
        return "denied", "expired"
    return "allowed", None


class ContractTests(unittest.TestCase):
    def test_schemas_are_valid_and_fixtures_parse(self):
        for name, schema in SCHEMA_DOCS.items():
            with self.subTest(schema=name):
                Draft202012Validator.check_schema(schema)
        for path in FIXTURES.glob("*.json"):
            with self.subTest(fixture=path.name):
                json.loads(path.read_text())

    def test_shared_schema_cases(self):
        for case in load("schema-cases.json")["cases"]:
            with self.subTest(case=case["name"]):
                self.assertEqual(validator(case["schema"], case.get("entrypoint")).is_valid(materialize(case)), case["valid"])

    def test_shared_slot_decisions(self):
        for case in load("slot-cases.json")["cases"]:
            with self.subTest(case=case["name"]):
                self.assertEqual(
                    satisfies_healthy_slot(materialize(case), case["slot"], instant(case["now"]), case.get("request_id")),
                    case["expected"],
                )

    def test_shared_claim_decisions(self):
        for case in load("reservation-cases.json")["cases"]:
            with self.subTest(case=case["name"]):
                self.assertEqual(
                    claim_decision(case["reservation"], case["claim"], case["now"]),
                    (case["expected_decision"], case.get("expected_reason")),
                )

    def test_lifecycle_records_reservation_before_dispatch(self):
        life = load("refresh-job-lifecycle.json")
        queued = next(t for t in life["transitions"] if t["status"] == "queued")
        building = next(t for t in life["transitions"] if t["status"] == "building")
        self.assertLess(instant(queued["at"]), instant(building["at"]))
        self.assertTrue(validator("reservation", "reservation").is_valid(queued["reservation"]))
        self.assertEqual(life["duplicate_request"]["response"]["job_id"], life["transitions"][0]["response"]["job_id"])

    def test_no_change_retains_content_identity_and_generation_age(self):
        earlier = load("healthy-edition.json")
        later = load("no-change-result.json")
        self.assertEqual((later["edition_id"], later["generated_at"]), (earlier["edition_id"], earlier["generated_at"]))
        for section in ("news", "sport", "finance"):
            self.assertEqual(later["sections"][section]["items"], earlier["sections"][section]["items"])
            self.assertEqual(later["sections"][section]["last_success_at"], earlier["sections"][section]["last_success_at"])
        self.assertIn(earlier["request_ids"][0], later["request_ids"])

    def test_legacy_edition_is_readable_but_cannot_complete_a_new_slot(self):
        legacy = load("archived-edition.json")
        self.assertTrue(validator("edition").is_valid(legacy))
        self.assertFalse(satisfies_healthy_slot(legacy, "2026-09-05T05:00:00Z", instant("2026-09-05T05:30:00Z")))


class ExtractionCompatibilityTests(unittest.TestCase):
    def test_build_reexports_extracted_symbols(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        import build

        for name in ("Fetcher", "fetch_all", "call_claude", "select_candidates", "dedupe", "enforce_preferences", "published_item", "price_moves"):
            self.assertTrue(hasattr(build, name), name)


if __name__ == "__main__":
    unittest.main()
