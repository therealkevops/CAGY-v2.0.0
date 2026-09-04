"""Unit and integration tests for Token Economics and Memory ROI Analytics."""
import os
import sys
import json
import shutil
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WEBUI_DIR = REPO_ROOT / "webui"
MOCK_AGY = REPO_ROOT / "tests" / "mock_agy.py"

if str(WEBUI_DIR) not in sys.path:
    sys.path.insert(0, str(WEBUI_DIR))

from api.analytics import estimate_tokens, compute_efficiency_metrics, MODEL_PRICING
from server import QuietHTTPServer, Handler


class TestAnalyticsEngine(unittest.TestCase):
    """Test analytics calculation engine directly."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.ws_path = Path(self.test_dir)
        self.vault_dir = self.ws_path / "knowledge"
        self.vault_dir.mkdir(parents=True, exist_ok=True)
        (self.vault_dir / "architecture").mkdir(parents=True, exist_ok=True)
        (self.vault_dir / "user").mkdir(parents=True, exist_ok=True)

        # Create sample notes
        (self.vault_dir / "architecture" / "system.md").write_text(
            """# System Topology
Linux containerized execution with agy-unified.
""",
            encoding="utf-8"
        )
        (self.vault_dir / "user" / "preferences.md").write_text(
            """# Preferences
Concise tone with standard markdown formatting.
""",
            encoding="utf-8"
        )

        # Create compiled rules
        rules_dir = self.ws_path / ".gemini" / "rules"
        rules_dir.mkdir(parents=True, exist_ok=True)
        (rules_dir / "knowledge_vault.md").write_text(
            """# Active Knowledge Vault
- system: Linux container
- preferences: Concise tone
""",
            encoding="utf-8"
        )

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_estimate_tokens(self):
        self.assertEqual(estimate_tokens(""), 0)
        self.assertEqual(estimate_tokens(None), 0)
        # 4 words -> ~5 tokens
        self.assertGreater(estimate_tokens("hello world from tests"), 3)

    def test_compute_efficiency_metrics_schema(self):
        metrics = compute_efficiency_metrics(session_id=None, workspace_path=self.ws_path)
        self.assertTrue(metrics.get("ok"))
        self.assertIn("session", metrics)
        self.assertIn("vault", metrics)
        self.assertIn("diet", metrics)
        self.assertIn("pricing_comparison", metrics)

        # Vault checks
        v = metrics["vault"]
        self.assertGreaterEqual(v["total_notes"], 2)
        self.assertGreater(v["total_words"], 0)
        self.assertGreater(v["compiled_rule_tokens"], 0)
        self.assertGreater(v["memory_leverage_ratio"], 0)
        self.assertGreater(v["avoided_turns_estimate"], 0)
        self.assertGreater(v["estimated_tokens_saved"], 0)

        # Diet checks
        diet = metrics["diet"]
        self.assertIn(diet["status"], ["optimal", "notice", "warning"])

        # Pricing comparison checks
        pricing = metrics["pricing_comparison"]
        self.assertIn("gemini_3_8_flash", pricing)
        self.assertIn("gemini_1_5_pro", pricing)
        self.assertIn("claude_3_5_sonnet", pricing)


class TestAnalyticsEndpoint(unittest.TestCase):
    """Test GET /api/analytics/efficiency HTTP endpoint."""

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.mkdtemp()
        cls.state_dir = Path(cls.temp_dir) / "state"
        cls.state_dir.mkdir(parents=True, exist_ok=True)

        os.environ["AGY_WEBUI_STATE_DIR"] = str(cls.state_dir)
        os.environ["HERMES_WEBUI_STATE_DIR"] = str(cls.state_dir)
        os.environ["AGY_WEBUI_SKIP_ONBOARDING"] = "1"
        os.environ["HERMES_WEBUI_SKIP_ONBOARDING"] = "1"
        os.environ["AGY_CLI_PATH"] = str(MOCK_AGY)

        cls.httpd = QuietHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.httpd.server_address[1]
        cls.base_url = f"http://127.0.0.1:{cls.port}"

        cls.server_thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.server_thread.start()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.httpd.shutdown()
            cls.httpd.server_close()
        except Exception:
            pass
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def test_get_efficiency_metrics_endpoint(self):
        req = urllib.request.Request(f"{self.base_url}/api/analytics/efficiency")
        with urllib.request.urlopen(req, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(data.get("ok"))
            self.assertIn("session", data)
            self.assertIn("vault", data)
            self.assertIn("pricing_comparison", data)


if __name__ == "__main__":
    unittest.main()
