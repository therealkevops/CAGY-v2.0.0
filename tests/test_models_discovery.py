"""Tests for dynamic model catalog discovery and vendor grouping."""
import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WEBUI_DIR = REPO_ROOT / "webui"
MOCK_AGY = REPO_ROOT / "tests" / "mock_agy.py"

if str(WEBUI_DIR) not in sys.path:
    sys.path.insert(0, str(WEBUI_DIR))

from api import routes


class TestModelsDiscovery(unittest.TestCase):
    """Test dynamic model discovery, vendor categorization, and fallback."""

    def test_dynamic_models_with_mock_cli(self):
        """Test model discovery using mock agy binary."""
        os.environ["AGY_CLI_PATH"] = str(MOCK_AGY)
        self.addCleanup(lambda: os.environ.pop("AGY_CLI_PATH", None))

        payload = routes._get_agy_models_payload(force=True)

        self.assertIsNotNone(payload)
        self.assertIn(payload.get("active_provider"), ("antigravity", "agy"))
        self.assertIn("Gemini 3.8 Flash", payload.get("default_model", ""))

        groups = payload.get("groups", [])
        all_models = [m for g in groups for m in g.get("models", [])]
        self.assertGreater(len(all_models), 0)

        # Check models parsed from mock_agy
        cli_ids = [m.get("cli_id") for m in all_models]
        self.assertIn("gemini-3.8-flash-high", cli_ids)
        self.assertIn("claude-3-7-sonnet", cli_ids)

        # Check vendor grouping
        group_names = [g.get("provider") for g in groups]
        self.assertTrue(any("Google" in n or "Gemini" in n for n in group_names))
        self.assertTrue(any("Anthropic" in n or "Claude" in n for n in group_names))

    def test_models_fallback_on_failure(self):
        """When agy CLI is unreachable, should fall back to Gemini 3.8 Flash without crash."""
        os.environ["AGY_CLI_PATH"] = "/nonexistent/path/to/agy"
        self.addCleanup(lambda: os.environ.pop("AGY_CLI_PATH", None))

        payload = routes._get_agy_models_payload(force=True)

        self.assertIsNotNone(payload)
        self.assertIn(payload.get("active_provider"), ("antigravity", "agy"))
        self.assertIn("Gemini 3.8 Flash", payload.get("default_model", ""))
        self.assertGreater(len(payload.get("groups", [])), 0)

    def test_cache_behavior(self):
        """Ensure cache bypass works when force=True."""
        os.environ["AGY_CLI_PATH"] = str(MOCK_AGY)
        self.addCleanup(lambda: os.environ.pop("AGY_CLI_PATH", None))

        # Initial fetch
        p1 = routes._get_agy_models_payload(force=True)
        # Cached fetch
        p2 = routes._get_agy_models_payload(force=False)
        self.assertEqual(p1, p2)

        # Force bypass fetch
        p3 = routes._get_agy_models_payload(force=True)
        self.assertEqual(p3.get("default_model"), p1.get("default_model"))


if __name__ == "__main__":
    unittest.main()
