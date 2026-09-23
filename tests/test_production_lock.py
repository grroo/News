"""Verify the production lock includes runtime dependencies used by the builder."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class ProductionLockTests(unittest.TestCase):
    def test_lock_includes_jsonschema_runtime(self):
        lock = (ROOT / "requirements.lock").read_text()
        for package in (
            "jsonschema==",
            "attrs==",
            "referencing==",
            "rpds-py==",
            "rfc3339-validator==",
        ):
            self.assertIn(package, lock, package)


if __name__ == "__main__":
    unittest.main()
