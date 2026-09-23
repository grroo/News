import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build
from briefing_archive import policy_from, prune
from pipeline import publication_is_newer, source_error, validate_config
from section_cache import fingerprint
from slot_health import satisfies_healthy_slot
from usage_ledger import ledger, section_usage

ROOT = Path(__file__).resolve().parents[1]


def provider_ok():
    return {
        "status": "success",
        "provider": "anthropic",
        "model": "claude-haiku-4-5",
        "content": {
            "briefing": [{"text": "Supported claim.", "source_ids": [0]}],
            "items": [{"id": 0, "title": "Headline", "summary": "One sentence."}],
        },
        "usage": {
            "attempts": 1,
            "input_tokens": 100,
            "cached_input_tokens": 10,
            "output_tokens": 50,
            "reasoning_tokens": 20,
            "unknown": False,
            "est_cost_usd": None,
            "price_table_version": "unpriced",
            "pricing_inputs": {
                "input_tokens": 90,
                "cached_input_tokens": 10,
                "cache_write_tokens": 0,
                "output_tokens": 50,
                "reasoning_tokens": 20,
                "reasoning_included_in_output": True,
            },
        },
        "provider_request_ids": ["msg_test"],
    }


def provider_timeout():
    return {
        "status": "failed",
        "retryable": True,
        "provider": "anthropic",
        "model": "claude-haiku-4-5",
        "error": "anthropic timeout",
        "usage": {"attempts": 2, "est_cost_usd": None, "price_table_version": "unpriced", "unknown": True},
        "provider_request_ids": [],
    }


class LedgerTests(unittest.TestCase):
    def test_reasoning_is_not_added_to_output_and_timeout_is_not_zero(self):
        known = section_usage("news", provider_ok())
        timed = section_usage("sport", provider_timeout())
        summary = ledger([known, timed])
        self.assertEqual(summary["output_tokens"], 50)
        self.assertEqual(summary["reasoning_tokens"], 20)
        self.assertEqual(summary["input_tokens"], 90)
        self.assertEqual(summary["cached_input_tokens"], 10)
        self.assertEqual(summary["attempts"], 3)
        self.assertTrue(summary["unknown"])
        self.assertTrue(summary["unreported_cost"])
        self.assertGreater(summary["est_cost_usd"], 0)
        self.assertIsNone(ledger([timed])["est_cost_usd"])
        self.assertNotEqual(ledger([timed])["est_cost_usd"], 0)

    def test_unknown_model_is_not_priced_as_sonnet(self):
        row = section_usage("news", provider_ok())
        row["model"] = "gpt-unknown"
        summary = ledger([row])
        self.assertIsNone(summary["est_cost_usd"])
        self.assertIsNone(summary["forecast_month_usd"])
        self.assertTrue(summary["unreported_cost"])


class CacheTests(unittest.TestCase):
    def test_model_prompt_and_context_change_the_fingerprint(self):
        cfg = {"provider": "anthropic", "model": "claude-haiku-4-5", "prompt_version": 1, "interests": "rates", "language": "English", "item_targets": {"finance": 8}, "source_preferences": {}}
        items = [{"key": "a", "title": "Story", "source": "BBC", "published": "2026-09-23T10:00:00+00:00", "summary": "Text", "new": True}]
        base = fingerprint("finance", items, cfg, "prices 1%")
        self.assertEqual(base, fingerprint("finance", [{**items[0], "new": False}], cfg, "prices 1%"))
        self.assertNotEqual(base, fingerprint("finance", items, {**cfg, "model": "gpt-6-luna", "provider": "openai"}, "prices 1%"))
        self.assertNotEqual(base, fingerprint("finance", items, {**cfg, "prompt_version": 2}, "prices 1%"))
        self.assertNotEqual(base, fingerprint("finance", items, cfg, "prices 2%"))

    def test_unavailable_and_stale_sources_block_a_healthy_section(self):
        health = [
            {"name": "BBC", "section": "news", "status": "ok"},
            {"name": "Reuters", "section": "news", "status": "fallback"},
            {"name": "ANSA", "section": "news", "status": "unavailable"},
            {"name": "Old", "section": "sport", "status": "stale"},
            {"name": "Empty", "section": "finance", "status": "empty"},
        ]
        self.assertIn("ANSA", source_error(health, "news"))
        self.assertIn("Old", source_error(health, "sport"))
        self.assertIsNone(source_error(health, "finance"))


class ArchiveTests(unittest.TestCase):
    def test_age_and_count_keep_the_index_navigable(self):
        now = datetime(2026, 9, 23, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            past = Path(tmp)
            for days, name in ((1, "new"), (3, "mid"), (10, "old")):
                stamp = (now - timedelta(days=days)).isoformat()
                payload = {"generated_at": stamp, "sections": {"news": {"items": [{"key": name}]}}}
                (past / f"{name}.json").write_text(json.dumps(payload))
            index = prune(past, now, {"max_age_days": 7, "max_count": 1, "max_bytes": 8_000_000})
            self.assertEqual([row["file"] for row in index], ["new.json"])
            self.assertEqual(json.loads((past / "index.json").read_text()), index)
            self.assertFalse((past / "old.json").exists())
            self.assertFalse((past / "mid.json").exists())


class PipelineRunTests(unittest.TestCase):
    def run_build(self, root: Path, argv, generate, extra_env=None):
        fixtures = root / "fixtures"
        past = root / "past"
        paths = dict(DATA_DIR=root, PAST_DIR=past, SEEN_PATH=root / "seen.json", OUT_PATH=root / "briefing.json", CONFIG_PATH=root / "config.yml")
        if not (root / "config.yml").exists():
            (root / "config.yml").write_text((ROOT / "config.yml").read_text())
        env = {key: value for key, value in os.environ.items() if key not in {"ANTHROPIC_API_KEY", "OPENAI_API_KEY", "SCHEDULED_SLOT", "REQUEST_ID"}}
        env["ANTHROPIC_API_KEY"] = "sk-ant-test"
        env.update(extra_env or {})
        with patch.multiple(build, **paths), patch.object(sys, "argv", argv), patch.dict(os.environ, env, clear=True):
            with patch("build.generate_briefing", side_effect=generate) as calls:
                build.main()
                return calls

    def test_failure_is_not_healthy_and_unchanged_inputs_make_no_calls(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            root = Path(tmp)
            import subprocess
            subprocess.run([sys.executable, str(ROOT / "tests/make_fixtures.py"), "--output-dir", str(root / "fixtures")], check=True, capture_output=True)
            slot = "2026-09-04T17:00:00+00:00"
            calls = self.run_build(
                root, ["build.py", "--fixtures", str(root / "fixtures"), "--now", "2026-09-04T17:00:00+00:00"],
                lambda **_k: provider_ok(), {"SCHEDULED_SLOT": slot, "REQUEST_ID": "req-slot"},
            )
            self.assertEqual(calls.call_count, 3)
            first = json.loads((root / "briefing.json").read_text())
            self.assertEqual(first["quality"]["overall"], "healthy")
            self.assertEqual(first["mode"], "llm")
            self.assertEqual(first["usage"]["output_tokens"], 150)
            self.assertEqual(first["usage"]["reasoning_tokens"], 60)
            self.assertGreater(first["usage"]["est_cost_usd"], 0)
            self.assertTrue(satisfies_healthy_slot(first, slot, datetime(2026, 9, 4, 17, 5, tzinfo=timezone.utc), "req-slot"))

            again = self.run_build(root, ["build.py", "--fixtures", str(root / "fixtures"), "--now", "2026-09-04T18:00:00+00:00"], lambda **_k: provider_ok())
            self.assertEqual(again.call_count, 0)
            second = json.loads((root / "briefing.json").read_text())
            self.assertEqual(second["refresh"]["outcome"], "no_change")
            self.assertEqual(second["edition_id"], first["edition_id"])
            self.assertEqual(second["generated_at"], first["generated_at"])
            self.assertEqual(second["sections"]["news"]["state"], "unchanged")
            self.assertEqual(second["sections"]["news"]["last_success_at"], first["sections"]["news"]["last_success_at"])
            self.assertEqual(second["usage"]["calls"], 0)

            (root / "config.yml").write_text((root / "config.yml").read_text().replace("prompt_version: 1", "prompt_version: 2"))
            changed = self.run_build(root, ["build.py", "--fixtures", str(root / "fixtures"), "--now", "2026-09-04T19:00:00+00:00"], lambda **_k: provider_ok())
            self.assertEqual(changed.call_count, 3)

    def test_one_failed_section_keeps_its_previous_age(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            root = Path(tmp)
            import subprocess
            subprocess.run([sys.executable, str(ROOT / "tests/make_fixtures.py"), "--output-dir", str(root / "fixtures")], check=True, capture_output=True)
            (root / "section-cache.json").write_text(json.dumps({"version": 1, "sections": {"sport": {
                "fingerprint": "sha256:stale",
                "edition_id": "ed-old",
                "last_success_at": "2026-09-01T00:00:00+00:00",
                "briefing": [{"text": "Earlier sport", "sources": []}],
                "items": [{"key": "old-sport", "title": "Earlier", "url": "https://example.com/old", "source": "L'Equipe", "new": False}],
            }}}))
            def generate(**kwargs):
                if "SECTION: sport" in kwargs["user"]:
                    return provider_timeout()
                return provider_ok()
            self.run_build(root, ["build.py", "--fixtures", str(root / "fixtures"), "--now", "2026-09-04T17:00:00+00:00"], generate)
            edition = json.loads((root / "briefing.json").read_text())
            self.assertEqual(edition["quality"]["overall"], "degraded")
            self.assertEqual(edition["sections"]["news"]["state"], "healthy")
            self.assertEqual(edition["sections"]["sport"]["state"], "degraded")
            self.assertEqual(edition["sections"]["sport"]["last_success_at"], "2026-09-01T00:00:00+00:00")
            self.assertEqual(edition["sections"]["sport"]["briefing"][0]["text"], "Earlier sport")
            self.assertIn("timeout", edition["sections"]["sport"]["error"])
            self.assertFalse(satisfies_healthy_slot(edition, "2026-09-04T17:00:00+00:00", datetime(2026, 9, 4, 18, tzinfo=timezone.utc)))

    def test_total_provider_failure_cannot_satisfy_a_slot(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            root = Path(tmp)
            import subprocess
            subprocess.run([sys.executable, str(ROOT / "tests/make_fixtures.py"), "--output-dir", str(root / "fixtures")], check=True, capture_output=True)
            self.run_build(root, ["build.py", "--fixtures", str(root / "fixtures"), "--now", "2026-09-04T17:00:00+00:00"], lambda **_k: provider_timeout())
            edition = json.loads((root / "briefing.json").read_text())
            self.assertEqual(edition["mode"], "llm")
            self.assertEqual(edition["quality"]["overall"], "failed")
            self.assertTrue(all(edition["sections"][name]["state"] == "failed" for name in ("news", "sport", "finance")))
            self.assertIsNone(edition["usage"]["est_cost_usd"])
            self.assertTrue(edition["usage"]["unknown"])
            now = datetime(2026, 9, 4, 18, tzinfo=timezone.utc)
            self.assertFalse(satisfies_healthy_slot({**edition, "trigger": "scheduled", "scheduled_slot": "2026-09-04T17:00:00+00:00"}, "2026-09-04T17:00:00+00:00", now))

    def test_missing_credential_does_not_write_a_mock(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            root = Path(tmp)
            (root / "config.yml").write_text((ROOT / "config.yml").read_text())
            argv = ["build.py", "--now", "2026-09-04T17:00:00+00:00"]
            env = {key: value for key, value in os.environ.items() if key not in {"ANTHROPIC_API_KEY", "OPENAI_API_KEY"}}
            with patch.multiple(build, CONFIG_PATH=root / "config.yml", OUT_PATH=root / "briefing.json", DATA_DIR=root), patch.object(sys, "argv", argv), patch.dict(os.environ, env, clear=True):
                with self.assertRaises(SystemExit):
                    build.main()
            self.assertFalse((root / "briefing.json").exists())

    def test_older_run_does_not_replace_a_newer_edition(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            root = Path(tmp)
            import subprocess
            subprocess.run([sys.executable, str(ROOT / "tests/make_fixtures.py"), "--output-dir", str(root / "fixtures")], check=True, capture_output=True)
            current = {"source_checked_at": "2026-09-04T18:00:00+00:00", "generated_at": "2026-09-04T18:00:00+00:00", "marker": "newer"}
            (root / "briefing.json").write_text(json.dumps(current))
            calls = self.run_build(root, ["build.py", "--fixtures", str(root / "fixtures"), "--now", "2026-09-04T17:00:00+00:00"], lambda **_k: provider_ok())
            self.assertEqual(calls.call_count, 0)
            self.assertEqual(json.loads((root / "briefing.json").read_text())["marker"], "newer")
            self.assertTrue(publication_is_newer(current, datetime(2026, 9, 4, 17, tzinfo=timezone.utc)))

    def test_config_ranges_reject_an_extra_retry(self):
        cfg = yaml_config()
        cfg["provider_max_attempts"] = 3
        self.assertTrue(validate_config(cfg))
        cfg = yaml_config()
        self.assertEqual(validate_config(cfg), [])
        self.assertEqual(policy_from(cfg)["max_age_days"], 7)


def yaml_config():
    import yaml
    return yaml.safe_load((ROOT / "config.yml").read_text())


if __name__ == "__main__":
    unittest.main()
