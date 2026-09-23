import importlib.util
import json
import os
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "contracts"
SCHEMAS = ROOT / "docs" / "contracts" / "schemas"
spec = importlib.util.spec_from_file_location("check_slot", ROOT / "scripts" / "check_slot.py")
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)
SCHEDULE = json.loads((ROOT / "schedule.json").read_text())
NOW = datetime(2026, 9, 7, 5, 5, tzinfo=timezone.utc)
SLOT = "2026-09-07T05:00:00Z"
FRESH = {"generated_at": SLOT, "mode": "llm"}
CLAIM_REQUEST = Draft202012Validator(
    {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$defs": json.loads((SCHEMAS / "reservation.json").read_text())["$defs"],
        "$ref": "#/$defs/claimRequest",
    },
    format_checker=FormatChecker(),
)
CLAIM_RESPONSE = Draft202012Validator(
    {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$defs": json.loads((SCHEMAS / "reservation.json").read_text())["$defs"],
        "$ref": "#/$defs/claimResponse",
    },
    format_checker=FormatChecker(),
)


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

    def test_missing_api_key_allows_explicit_mock_without_publish(self):
        self.assertEqual(decide(has_api_key=False, mock_requested=True)[:2], (True, False))

    def test_mock_preview_never_publishes(self):
        self.assertEqual(decide(mock_requested=True)[:2], (True, False))

    def test_deploy_only_never_builds(self):
        self.assertEqual(decide(deploy_only=True)[:2], (False, True))

    def test_manual_generation_without_reservation_when_not_required(self):
        self.assertEqual(decide(slot_text="")[:2], (True, True))

    def test_manual_generation_requires_reservation_and_request_when_configured(self):
        self.assertEqual(decide(slot_text="", require_reservation=True)[:2], (False, False))
        self.assertEqual(
            decide(slot_text="", require_reservation=True, reservation_id="resv_123")[:2],
            (False, False),
        )

    def test_manual_generation_allowed_with_matching_reservation(self):
        self.assertEqual(
            decide(
                slot_text="",
                require_reservation=True,
                reservation_id="resv_123",
                request_id="req_123",
            )[:2],
            (True, True),
        )

    def test_scheduled_generation_requires_reservation_when_gate_enabled(self):
        self.assertEqual(
            decide(require_reservation=True, reservation_id="resv", request_id="req")[:2],
            (True, True),
        )
        self.assertEqual(decide(require_reservation=True)[:2], (False, False))

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

    def test_strict_gate_rejects_legacy_timestamp_only_edition(self):
        slot = datetime.fromisoformat(SLOT.replace("Z", "+00:00"))
        self.assertFalse(
            guard.slot_satisfied(FRESH, slot, NOW, strict_gate=True, slot_text=SLOT)
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


class ClaimTests(unittest.TestCase):
    github_env = {
        "GITHUB_REPOSITORY": "grroo/News",
        "GITHUB_WORKFLOW_REF": "build.yml",
        "GITHUB_REF": "refs/heads/main",
    }

    def test_claim_body_matches_frozen_contract_scope(self):
        with patch.dict(os.environ, self.github_env, clear=False):
            body = guard.claim_request_body("resv_abc", "req_xyz", 4242, 1)
        self.assertTrue(CLAIM_REQUEST.is_valid(body))
        self.assertEqual(body["workflow"], "build.yml")
        self.assertEqual(body["ref"], "refs/heads/main")

    def test_validate_allowed_response_requires_matching_ids(self):
        payload = {"decision": "allowed", "reservation_id": "resv_abc", "request_id": "req_xyz"}
        self.assertTrue(CLAIM_RESPONSE.is_valid(payload))
        ok, _ = guard.validate_claim_response(payload, "resv_abc", "req_xyz")
        self.assertTrue(ok)

    def test_validate_rejects_mismatched_response_ids(self):
        payload = {"decision": "allowed", "reservation_id": "other", "request_id": "req_xyz"}
        ok, message = guard.validate_claim_response(payload, "resv_abc", "req_xyz")
        self.assertFalse(ok)
        self.assertIn("mismatch", message)

    def test_validate_rejects_denied_without_required_reason(self):
        payload = {"decision": "denied", "reservation_id": "resv_abc", "request_id": "req_xyz"}
        ok, message = guard.validate_claim_response(payload, "resv_abc", "req_xyz")
        self.assertFalse(ok)

    def test_claim_fails_closed_on_malformed_response(self):
        def fake_post(*_args, **_kwargs):
            class Response:
                status_code = 200

                @staticmethod
                def json():
                    return {"decision": "allowed", "reservation_id": "wrong", "request_id": "req_xyz"}

            return Response()

        with patch.dict(
            os.environ,
            {
                **self.github_env,
                "NEWS_RESERVATION_GATE_TOKEN": "secret",
                "RESERVATION_CLAIM_URL": "https://worker.example/claim",
            },
            clear=False,
        ):
            ok, message = guard.claim_reservation("resv_abc", "req_xyz", 1, 1, post=fake_post)
        self.assertFalse(ok)
        self.assertIn("mismatch", message)


if __name__ == "__main__":
    unittest.main()
