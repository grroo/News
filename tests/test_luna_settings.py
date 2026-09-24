import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import yaml  # noqa: E402

import build  # noqa: E402
import llm_provider  # noqa: E402
from pipeline import validate_config  # noqa: E402
from section_cache import fingerprint  # noqa: E402

ITEMS = [
    {"key": f"k{i}", "title": f"Story {i}", "source": "Reuters", "feed_name": "Reuters",
     "published": "2026-09-24T08:00:00+00:00", "new": True, "summary": "Summary.", "url": f"https://example.com/{i}"}
    for i in range(5)
]


class LunaSettingsTests(unittest.TestCase):
    def test_production_config_is_luna_low_with_fill(self):
        cfg = yaml.safe_load((ROOT / "config.yml").read_text())
        self.assertEqual(validate_config(cfg), [])
        self.assertEqual((cfg["provider"], cfg["model"]), ("openai", "gpt-6-luna"))
        self.assertEqual(cfg["reasoning_effort"], "low")
        self.assertIs(cfg["fill_items"], True)

    def test_invalid_settings_are_rejected(self):
        cfg = yaml.safe_load((ROOT / "config.yml").read_text())
        self.assertIn("reasoning_effort must be none or low", validate_config({**cfg, "reasoning_effort": "high"}))
        self.assertIn("fill_items must be true or false", validate_config({**cfg, "fill_items": "yes"}))

    def test_openai_request_uses_effort_and_leaves_room_for_reasoning(self):
        _, low = llm_provider.openai_request("k", "gpt-6-luna", "sys", "user", reasoning_effort="low")
        _, none = llm_provider.openai_request("k", "gpt-6-luna", "sys", "user")
        self.assertEqual(low["reasoning"], {"effort": "low"})
        self.assertEqual(low["max_output_tokens"], llm_provider.OPENAI_REASONING_MAX_OUTPUT_TOKENS)
        self.assertEqual(none["reasoning"], {"effort": "none"})
        self.assertEqual(none["max_output_tokens"], llm_provider.OPENAI_MAX_OUTPUT_TOKENS)

    def _captured_call(self, cfg):
        with patch.object(build, "generate_briefing", return_value={"status": "failed", "error": "x"}) as call:
            build.llm_section("news", ITEMS, cfg, "key")
        return call.call_args.kwargs

    def test_build_passes_effort_and_fill_rule_for_luna(self):
        cfg = yaml.safe_load((ROOT / "config.yml").read_text())
        kwargs = self._captured_call(cfg)
        self.assertEqual(kwargs["reasoning_effort"], "low")
        self.assertIn("Fill the section", kwargs["system"])
        self.assertIn(f"close to {cfg['item_targets']['news']} items", kwargs["system"])

    def test_haiku_rollback_ignores_effort_and_fill_is_optional(self):
        cfg = yaml.safe_load((ROOT / "config.yml").read_text())
        kwargs = self._captured_call({**cfg, "provider": "anthropic", "model": "claude-haiku-4-5", "fill_items": False})
        self.assertIsNone(kwargs["reasoning_effort"])
        self.assertNotIn("Fill the section", kwargs["system"])

    def test_effort_and_fill_change_the_cache_fingerprint(self):
        cfg = yaml.safe_load((ROOT / "config.yml").read_text())
        base = fingerprint("news", ITEMS, cfg)
        self.assertNotEqual(base, fingerprint("news", ITEMS, {**cfg, "reasoning_effort": "none"}))
        self.assertNotEqual(base, fingerprint("news", ITEMS, {**cfg, "fill_items": False}))


if __name__ == "__main__":
    unittest.main()
