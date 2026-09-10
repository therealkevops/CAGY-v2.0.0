"""End-to-end HTTP integration tests for WebUI REST API endpoints."""
import os
import sys
import json
import shutil
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WEBUI_DIR = REPO_ROOT / "webui"
MOCK_AGY = REPO_ROOT / "tests" / "mock_agy.py"

if str(WEBUI_DIR) not in sys.path:
    sys.path.insert(0, str(WEBUI_DIR))

import server
from server import QuietHTTPServer, Handler


class TestApiEndpoints(unittest.TestCase):
    """Start an ephemeral WebUI server and test core REST API endpoints."""

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

        # Bind to ephemeral port
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

    def _get(self, path: str):
        req = urllib.request.Request(f"{self.base_url}{path}")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = resp.read().decode("utf-8")
                return resp.status, json.loads(data) if data else {}
        except urllib.error.HTTPError as err:
            data = err.read().decode("utf-8")
            return err.code, json.loads(data) if data else {}

    def _post(self, path: str, payload: dict):
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = resp.read().decode("utf-8")
                return resp.status, json.loads(data) if data else {}
        except urllib.error.HTTPError as err:
            data = err.read().decode("utf-8")
            return err.code, json.loads(data) if data else {}

    def test_health_endpoints(self):
        """Verify /health and /api/health/agent endpoints."""
        status, data = self._get("/health")
        self.assertEqual(status, 200)

        status, data = self._get("/api/health/agent")
        self.assertEqual(status, 200)
        self.assertTrue(data.get("alive"))
        self.assertEqual(data.get("details", {}).get("engine"), "Antigravity CLI (agy)")

    def test_models_endpoint(self):
        """Verify /api/models returns dynamic catalog."""
        status, data = self._get("/api/models?refresh=1")
        self.assertEqual(status, 200)
        self.assertIn(data.get("active_provider"), ("antigravity", "agy"))
        groups = data.get("groups", [])
        self.assertGreater(len(groups), 0)

    def test_updates_check_endpoint(self):
        """Verify /api/updates/check returns 200 with up_to_date flag."""
        status, data = self._get("/api/updates/check")
        self.assertEqual(status, 200)
        self.assertTrue(data.get("up_to_date"))

    def test_plugins_endpoint(self):
        """Verify /api/plugins returns clean empty list without warnings."""
        status, data = self._get("/api/plugins")
        self.assertEqual(status, 200)
        self.assertEqual(data.get("plugins"), [])
        self.assertTrue(data.get("empty"))

    def test_onboarding_status(self):
        """Verify onboarding status is completed automatically in CAGY."""
        status, data = self._get("/api/onboarding/status")
        self.assertEqual(status, 200)
        self.assertTrue(data.get("completed"))
        self.assertEqual(data.get("system", {}).get("provider"), "agy")

    def test_session_lifecycle(self):
        """Verify session creation, listing, and lookup."""
        # 1. List initial sessions
        status, data = self._get("/api/sessions")
        self.assertEqual(status, 200)
        initial_count = len(data.get("sessions", []))

        # 2. Create new session
        status, data = self._post("/api/session/new", {"title": "CI Test Session"})
        self.assertEqual(status, 200)
        session_obj = data.get("session", {})
        session_id = session_obj.get("session_id")
        self.assertIsNotNone(session_id)

        # 3. Retrieve created session
        status, data = self._get(f"/api/session?session_id={session_id}")
        self.assertEqual(status, 200)
        retrieved_session = data.get("session", {})
        self.assertEqual(retrieved_session.get("session_id"), session_id)

    def test_cors_preflight(self):
        """Verify OPTIONS preflight returns proper CORS headers for same-origin."""
        origin = f"http://127.0.0.1:{self.port}"
        req = urllib.request.Request(
            f"{self.base_url}/api/sessions",
            headers={"Origin": origin},
            method="OPTIONS",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            headers = {k.title(): v for k, v in dict(resp.headers).items()}
            self.assertEqual(headers.get("Access-Control-Allow-Origin"), origin)
            self.assertIn("Access-Control-Allow-Methods", headers)

    def test_nonexistent_endpoint_returns_404(self):
        """Verify requesting unknown endpoint yields 404 cleanly."""
        req = urllib.request.Request(f"{self.base_url}/api/not_a_valid_route_12345")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                status = resp.status
        except urllib.error.HTTPError as err:
            status = err.code
        self.assertEqual(status, 404)

    def test_session_recovery_endpoints(self):
        """Verify GET /api/session/recovery/audit and POST /api/session/recovery/repair-safe."""
        status, audit = self._get("/api/session/recovery/audit")
        self.assertEqual(status, 200)
        self.assertIn("status", audit)
        self.assertIn("summary", audit)

        status, repair = self._post("/api/session/recovery/repair-safe", {})
        self.assertIn(status, (200, 409))
        self.assertIn("clean", repair)


if __name__ == "__main__":
    unittest.main()
