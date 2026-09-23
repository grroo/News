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


def section(**extra):
    base = {
        "state": "healthy",
        "briefing": [],
        "items": [],
        "last_success_at": "2026-09-07T05:02:00+00:00",
        "source_checked_at": "2026-09-07T05:01:00+00:00",
    }
    base.update(extra)
    return base


def unchanged_sections():
    return {
        name: section(state="unchanged", reused_edition_id="ed-1", input_fingerprint="sha256:abc", last_success_at="2026-09-06T17:05:00+00:00")
        for name in ("news", "sport", "finance")
    } | {"media": {"state": "skipped", "briefing": "", "items": []}}


def v2_edition(**overrides):
    edition = {
        "schema_version": 2,
        "edition_id": "ed-1",
        "generated_at": "2026-09-07T05:03:00+00:00",
        "source_checked_at": "2026-09-07T05:01:00+00:00",
        "timezone": "Europe/Rome",
        "schedule": SCHEDULE,
        "trigger": "scheduled",
        "request_ids": ["req-1"],
        "scheduled_slot": SLOT,
        "quality": {"overall": "healthy"},
        "mode": "llm",
        "refresh": {"outcome": "generated", "completed_at": "2026-09-07T05:04:00+00:00"},
        "sections": {
            "news": section(),
            "sport": section(),
            "finance": section(),
            "media": {"state": "skipped", "briefing": "", "items": []},
        },
    }
    edition.update(overrides)
    return edition


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

    def test_v2_health_applies_while_strict_flag_is_off(self):
        slot = datetime.fromisoformat(SLOT.replace("Z", "+00:00"))
        healthy = v2_edition()
        no_change = v2_edition(
            generated_at="2026-09-06T17:00:00+00:00",
            refresh={"outcome": "no_change", "completed_at": "2026-09-07T05:04:00+00:00", "reused_edition_id": "ed-1"},
            sections=unchanged_sections(),
        )
        degraded = v2_edition(quality={"overall": "degraded"}, sections={**unchanged_sections(), "sport": {**section(), "state": "degraded", "error": "timeout"}})
        failed = v2_edition(
            generated_at="2026-09-07T05:03:00+00:00",
            quality={"overall": "failed"},
            sections={name: {**section(), "state": "failed", "error": "timeout", "briefing": "Summary unavailable."} for name in ("news", "sport", "finance")} | {"media": {"state": "skipped", "briefing": "", "items": []}},
        )
        legacy = FRESH
        for edition, expected in ((healthy, True), (no_change, True), (degraded, False), (failed, False), (legacy, True)):
            self.assertEqual(
                guard.slot_satisfied(edition, slot, NOW, strict_gate=False, request_id="req-1", slot_text=SLOT),
                expected,
            )
        self.assertFalse(guard.slot_satisfied(legacy, slot, NOW, strict_gate=True, slot_text=SLOT))
        self.assertEqual(decide(live=healthy, strict_gate=False, request_id="req-1")[:2], (False, False))
        self.assertEqual(decide(live=no_change, strict_gate=False, request_id="req-1")[:2], (False, False))
        self.assertEqual(decide(live=degraded, strict_gate=False, request_id="req-1")[:2], (True, True))
        self.assertEqual(decide(live=failed, strict_gate=False, request_id="req-1")[:2], (True, True))
        self.assertEqual(decide(live=legacy, strict_gate=False)[:2], (False, False))
        self.assertEqual(decide(live=legacy, strict_gate=True)[:2], (True, True))

    def test_guard_checks_the_configured_provider_secret(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "", "OPENAI_API_KEY": "sk-test"}, clear=False):
            present, name = guard.provider_has_credential()
        self.assertFalse(present)
        self.assertEqual(name, "ANTHROPIC_API_KEY")
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test"}, clear=False):
            present, name = guard.provider_has_credential()
        self.assertTrue(present)
        self.assertEqual(name, "ANTHROPIC_API_KEY")

    def test_build_step_receives_slot_request_and_provider_secrets(self):
        import yaml
        workflow = yaml.safe_load((ROOT / ".github/workflows/build.yml").read_text())
        steps = workflow["jobs"]["build"]["steps"]
        build_step = next(step for step in steps if step.get("name") == "Build briefing")
        env = build_step["env"]
        self.assertIn("steps.slot.outputs.scheduled_slot", env["SCHEDULED_SLOT"])
        self.assertIn("inputs.request_id", env["REQUEST_ID"])
        self.assertIn("github.event_name", env["GITHUB_EVENT_NAME"])
        self.assertIn("secrets.ANTHROPIC_API_KEY", env["ANTHROPIC_API_KEY"])
        self.assertIn("secrets.OPENAI_API_KEY", env["OPENAI_API_KEY"])
        self.assertNotIn("sk-", yaml.safe_dump(workflow))

    def test_pages_artifact_omits_private_caches(self):
        import yaml
        for name in (".github/workflows/build.yml", ".github/workflows/deploy-pages.yml"):
            workflow = yaml.safe_load((ROOT / name).read_text())
            steps = workflow["jobs"][next(iter(workflow["jobs"]))]["steps"]
            assemble = next(step for step in steps if "Assemble" in step.get("name", ""))
            script = assemble["run"]
            for private in ("seen.json", "feed-cache.json", "section-cache.json"):
                self.assertIn(f"_site/data/{private}", script)

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
        "NEWS_WORKFLOW_FILE": "build.yml",
        "GITHUB_REF": "refs/heads/main",
    }

    def test_claim_body_matches_frozen_contract_scope(self):
        with patch.dict(os.environ, self.github_env, clear=False):
            body = guard.claim_request_body("resv_abc", "req_xyz", 4242, 1)
        self.assertTrue(CLAIM_REQUEST.is_valid(body))
        self.assertEqual(body["workflow"], "build.yml")
        self.assertEqual(body["ref"], "refs/heads/main")

    def test_claim_body_parses_real_github_workflow_ref(self):
        env = {
            "GITHUB_REPOSITORY": "grroo/News",
            "GITHUB_WORKFLOW_REF": "grroo/News/.github/workflows/build.yml@refs/heads/main",
            "GITHUB_REF": "refs/heads/main",
        }
        with patch.dict(os.environ, env, clear=True):
            body = guard.claim_request_body("resv_abc", "req_xyz", 4242, 1)
        self.assertTrue(CLAIM_REQUEST.is_valid(body))
        self.assertEqual(body["workflow"], "build.yml")

    def test_news_workflow_file_overrides_github_workflow_ref(self):
        env = {
            "NEWS_WORKFLOW_FILE": "build.yml",
            "GITHUB_WORKFLOW_REF": "grroo/News/.github/workflows/other.yml@refs/heads/main",
        }
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(guard.claim_workflow_file(), "build.yml")

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
