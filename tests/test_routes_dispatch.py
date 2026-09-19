"""Comprehensive HTTP route dispatch test suite for WebUI REST API endpoints.

Exercises GET and POST dispatchers across all major architectural domains:
- Static Shell & Manifest
- Authentication & CSRF
- System Telemetry & Health Probes
- Models, Providers, Plugins, & Configuration
- Session Management & Recovery
- Workspace Filesystem & Git
- MCP Hub, Subagents, & Knowledge Vault
- Fallback 404 & CORS Preflights
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WEBUI_DIR = REPO_ROOT / "webui"
MOCK_AGY = REPO_ROOT / "tests" / "mock_agy.py"

if str(WEBUI_DIR) not in sys.path:
    sys.path.insert(0, str(WEBUI_DIR))

from server import Handler, QuietHTTPServer


class TestRoutesDispatch(unittest.TestCase):
    """Start an ephemeral WebUI server and test route dispatching across all domains."""

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
        os.environ["AGY_VAULT_DIR"] = str(Path(cls.temp_dir) / "vault")

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

    def _get(self, path: str, raw_text: bool = False):
        req = urllib.request.Request(f"{self.base_url}{path}")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = resp.read().decode("utf-8", errors="replace")
                if raw_text:
                    return resp.status, data, dict(resp.headers)
                return resp.status, json.loads(data) if data else {}, dict(resp.headers)
        except urllib.error.HTTPError as err:
            try:
                data = err.read().decode("utf-8", errors="replace")
                if raw_text:
                    return err.code, data, dict(err.headers)
                try:
                    parsed = json.loads(data) if data else {}
                except Exception:
                    parsed = {"raw": data}
                return err.code, parsed, dict(err.headers)
            finally:
                err.close()

    def _post(self, path: str, payload: dict = None, raw_payload: bytes = None, headers: dict = None):
        h = {"Content-Type": "application/json"}
        if headers:
            h.update(headers)
        if raw_payload is not None:
            data_bytes = raw_payload
        else:
            data_bytes = json.dumps(payload or {}).encode("utf-8")

        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data_bytes,
            headers=h,
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = resp.read().decode("utf-8", errors="replace")
                try:
                    parsed = json.loads(data) if data else {}
                except Exception:
                    parsed = {"raw": data}
                return resp.status, parsed, dict(resp.headers)
        except urllib.error.HTTPError as err:
            try:
                data = err.read().decode("utf-8", errors="replace")
                try:
                    parsed = json.loads(data) if data else {}
                except Exception:
                    parsed = {"raw": data}
                return err.code, parsed, dict(err.headers)
            finally:
                err.close()

    # ─────────────────────────────────────────────────────────────
    # Domain 1: Static Shell & Manifest
    # ─────────────────────────────────────────────────────────────
    def test_get_root_shell(self):
        """GET / returns HTML index shell."""
        status, data, headers = self._get("/", raw_text=True)
        self.assertEqual(status, 200)
        self.assertIn("<html", data.lower())

    def test_get_login_shell(self):
        """GET /login returns HTML login shell."""
        status, data, headers = self._get("/login", raw_text=True)
        self.assertEqual(status, 200)
        self.assertIn("<html", data.lower())

    def test_get_manifest(self):
        """GET /manifest.json returns web manifest."""
        status, data, headers = self._get("/manifest.json")
        self.assertEqual(status, 200)
        self.assertIn("name", data)

    # ─────────────────────────────────────────────────────────────
    # Domain 2: Auth, CSRF, and Deprecations
    # ─────────────────────────────────────────────────────────────
    def test_get_auth_status(self):
        """GET /api/auth/status returns authentication state."""
        status, data, _ = self._get("/api/auth/status")
        self.assertEqual(status, 200)
        self.assertIn("auth_enabled", data)
        self.assertIn("logged_in", data)

    def test_post_auth_login_when_disabled(self):
        """POST /api/auth/login when auth disabled returns 200 ok."""
        status, data, _ = self._post("/api/auth/login", {})
        self.assertEqual(status, 200)
        self.assertTrue(data.get("ok"))

    def test_post_process_complete_ack_deprecated(self):
        """POST /api/process-complete-ack returns 410 Gone with X-Replaced-By header."""
        status, data, headers = self._post("/api/process-complete-ack", {})
        self.assertEqual(status, 410)
        self.assertEqual(headers.get("X-Replaced-By"), "/api/bg-task-complete-ack")

    def test_post_csp_report(self):
        """POST /api/csp-report accepts payload and returns 200 or 204."""
        status, data, _ = self._post("/api/csp-report", {"csp-report": {}})
        self.assertIn(status, (200, 204))

    # ─────────────────────────────────────────────────────────────
    # Domain 3: System Telemetry & Health
    # ─────────────────────────────────────────────────────────────
    def test_system_health_endpoints(self):
        """GET /health, /api/health/agent, and /api/system/health return 200."""
        status, _, _ = self._get("/health")
        self.assertEqual(status, 200)

        status, data, _ = self._get("/api/health/agent")
        self.assertEqual(status, 200)
        self.assertTrue(data.get("alive"))

        status, sys_health, _ = self._get("/api/system/health")
        self.assertEqual(status, 200)
        self.assertIn("status", sys_health)

    def test_system_updates_check(self):
        """GET /api/updates/check returns updates check status."""
        status, data, _ = self._get("/api/updates/check")
        self.assertEqual(status, 200)
        self.assertTrue(data.get("up_to_date"))

    def test_system_insights(self):
        """GET /api/insights returns system telemetry/insights."""
        status, data, _ = self._get("/api/insights")
        self.assertEqual(status, 200)

    # ─────────────────────────────────────────────────────────────
    # Domain 4: Config, Models, & Settings
    # ─────────────────────────────────────────────────────────────
    def test_models_and_providers_endpoints(self):
        """GET /api/models, /api/models/live, /api/model/auxiliary, /api/providers."""
        status, models, _ = self._get("/api/models?refresh=1")
        self.assertEqual(status, 200)
        self.assertIn("groups", models)

        status, live_models, _ = self._get("/api/models/live")
        self.assertEqual(status, 200)
        self.assertIn("models", live_models)

        status, aux_models, _ = self._get("/api/model/auxiliary")
        self.assertEqual(status, 200)

        status, providers, _ = self._get("/api/providers")
        self.assertEqual(status, 200)
        self.assertIsInstance(providers, list)

    def test_settings_and_plugins_endpoints(self):
        """GET /api/settings, /api/agy/settings, /api/plugins, /api/onboarding/status."""
        status, settings, _ = self._get("/api/settings")
        self.assertEqual(status, 200)

        status, agy_settings, _ = self._get("/api/agy/settings")
        self.assertEqual(status, 200)

        status, plugins, _ = self._get("/api/plugins")
        self.assertEqual(status, 200)
        self.assertEqual(plugins.get("plugins"), [])

        status, onboarding, _ = self._get("/api/onboarding/status")
        self.assertEqual(status, 200)
        self.assertTrue(onboarding.get("completed"))

    def test_post_config_and_settings(self):
        """POST /api/prompts and POST /api/agy/settings."""
        status, prompt_res, _ = self._post("/api/prompts", {"label": "Decomp Prompt", "text": "echo hello"})
        self.assertEqual(status, 200)
        self.assertTrue(prompt_res.get("ok"))
        self.assertEqual(prompt_res.get("prompt", {}).get("text"), "echo hello")

        status, agy_res, _ = self._post("/api/agy/settings", {"effort": "high", "mode": "plan"})
        self.assertEqual(status, 200)
        self.assertTrue(agy_res.get("ok"))
        self.assertEqual(agy_res.get("effort"), "high")
        self.assertEqual(agy_res.get("mode"), "plan")

    # ─────────────────────────────────────────────────────────────
    # Domain 5: Session Management & Recovery
    # ─────────────────────────────────────────────────────────────
    def test_session_lifecycle_and_recovery(self):
        """Create session, retrieve it, check status, rename, and run recovery audit."""
        # 1. Create session
        status, new_res, _ = self._post("/api/session/new", {"title": "Routes Test Session"})
        self.assertEqual(status, 200)
        session_id = new_res.get("session", {}).get("session_id")
        self.assertIsNotNone(session_id)

        # 2. Retrieve session
        status, sess_res, _ = self._get(f"/api/session?session_id={session_id}")
        self.assertEqual(status, 200)
        self.assertEqual(sess_res.get("session", {}).get("session_id"), session_id)

        # 3. Session status
        status, sess_status, _ = self._get(f"/api/session/status?session_id={session_id}")
        self.assertEqual(status, 200)

        # 4. Rename session
        status, rename_res, _ = self._post("/api/session/rename", {"session_id": session_id, "title": "Renamed Session"})
        self.assertEqual(status, 200)

        # 5. Recovery audit and repair-safe
        status, audit, _ = self._get("/api/session/recovery/audit")
        self.assertEqual(status, 200)
        self.assertIn("status", audit)

        status, repair, _ = self._post("/api/session/recovery/repair-safe", {})
        self.assertIn(status, (200, 409))

    # ─────────────────────────────────────────────────────────────
    # Domain 6: Workspaces, Git, & Projects
    # ─────────────────────────────────────────────────────────────
    def test_workspaces_and_git(self):
        """GET /api/workspaces, /api/git/status, /api/git-info, and POST /api/diff/compute."""
        status, workspaces, _ = self._get("/api/workspaces")
        self.assertEqual(status, 200)
        self.assertIn("workspaces", workspaces)

        # GET /api/git/status without session_id returns 400
        status, err_data, _ = self._get("/api/git/status")
        self.assertEqual(status, 400)

        # Create session and test /api/git/status with session_id
        _, new_res, _ = self._post("/api/session/new", {"title": "Git Test Session"})
        sess_id = new_res.get("session", {}).get("session_id")

        status, git_status, _ = self._get(f"/api/git/status?session_id={sess_id}")
        self.assertEqual(status, 200)
        self.assertIn("git", git_status)

        # GET /api/git-info without session_id returns 400
        status, err_info, _ = self._get("/api/git-info")
        self.assertEqual(status, 400)

        # GET /api/git-info with session_id returns 200
        status, git_info, _ = self._get(f"/api/git-info?session_id={sess_id}")
        self.assertEqual(status, 200)

        status, diff_res, _ = self._post(
            "/api/diff/compute",
            {"original": "line1\nline2", "modified": "line1\nline2 edited", "filename": "test.txt"}
        )
        self.assertEqual(status, 200)
        self.assertIn("rows", diff_res)
        self.assertIn("stats", diff_res)

    def test_post_projects_lifecycle(self):
        """POST /api/projects/create, rename, delete."""
        status, proj_res, _ = self._post("/api/projects/create", {"name": "Decomp Proj", "color": "#123456"})
        self.assertEqual(status, 200)
        self.assertTrue(proj_res.get("ok"))
        proj_id = proj_res.get("project", {}).get("project_id")
        self.assertIsNotNone(proj_id)

        status, ren_res, _ = self._post("/api/projects/rename", {"project_id": proj_id, "name": "Renamed Proj"})
        self.assertEqual(status, 200)
        self.assertTrue(ren_res.get("ok"))

        status, del_res, _ = self._post("/api/projects/delete", {"project_id": proj_id})
        self.assertEqual(status, 200)
        self.assertTrue(del_res.get("ok"))

    # ─────────────────────────────────────────────────────────────
    # Domain 7: Tools, MCP, Subagents, & Vault
    # ─────────────────────────────────────────────────────────────
    def test_mcp_and_subagents(self):
        """GET /api/mcp/hub, /api/mcp/tools, POST /api/mcp/hub/test, GET /api/subagents, /api/skills."""
        status, mcp_hub, _ = self._get("/api/mcp/hub")
        self.assertEqual(status, 200)
        self.assertIn("servers", mcp_hub)

        status, mcp_tools, _ = self._get("/api/mcp/tools")
        self.assertEqual(status, 200)

        status, test_res, _ = self._post("/api/mcp/hub/test", {"command": "echo", "args": ["hello"]})
        self.assertEqual(status, 200)

        status, subagents, _ = self._get("/api/subagents")
        self.assertEqual(status, 200)

        status, skills, _ = self._get("/api/skills")
        self.assertEqual(status, 200)

    def test_post_commands_and_updates(self):
        """POST /api/commands/bundles/resolve and POST /api/updates/check."""
        status, res, _ = self._post("/api/commands/bundles/resolve", {"command": ""})
        self.assertEqual(status, 400)

        status, up_res, _ = self._post("/api/updates/check", {"force": True})
        self.assertEqual(status, 200)

    def test_vault_endpoints(self):
        """GET /api/vault/health, /api/vault/spaces, /api/vault/graph, POST /api/vault/note, memorize."""
        status, health, _ = self._get("/api/vault/health")
        self.assertEqual(status, 200)

        status, spaces, _ = self._get("/api/vault/spaces")
        self.assertEqual(status, 200)

        status, graph, _ = self._get("/api/vault/graph")
        self.assertEqual(status, 200)

        status, note_res, _ = self._post("/api/vault/note", {"path": "test_note.md", "content": "# Test Note\nHello"})
        self.assertEqual(status, 200)

        status, mem_res, _ = self._post("/api/vault/memorize", {"insight": "Sprint R1 test insight", "category": "notes"})
        self.assertEqual(status, 200)

        status, del_res, _ = self._post("/api/vault/delete", {"path": "test_note.md"})
        self.assertEqual(status, 200)

    # ─────────────────────────────────────────────────────────────
    # Domain 8: Chat & Streaming Status
    # ─────────────────────────────────────────────────────────────
    def test_chat_and_stream_status(self):
        """GET /api/chat/stream/status, /api/approval/pending, /api/clarify/pending."""
        status, stream_stat, _ = self._get("/api/chat/stream/status")
        self.assertEqual(status, 200)

        status, app_pending, _ = self._get("/api/approval/pending")
        self.assertEqual(status, 200)

        status, cla_pending, _ = self._get("/api/clarify/pending")
        self.assertEqual(status, 200)

    # ─────────────────────────────────────────────────────────────
    # Domain 9: Negative Fallbacks & CORS Preflights
    # ─────────────────────────────────────────────────────────────
    def test_404_routing(self):
        """GET and POST to unknown routes cleanly return 404."""
        status, _, _ = self._get("/api/nonexistent_unknown_test_endpoint_123")
        self.assertEqual(status, 404)

        status, _, _ = self._post("/api/nonexistent_unknown_test_endpoint_123", {})
        self.assertEqual(status, 404)

    def test_cors_options_preflight(self):
        """OPTIONS preflight returns 200 with appropriate CORS headers."""
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

    def test_http_keepalive_consecutive_requests(self):
        """Verify that persistent HTTP/1.1 connections do not receive spurious 404s or desynchronize."""
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            endpoints = [
                "/api/projects",
                "/api/sessions",
                "/api/models",
                "/api/settings",
                "/api/vault/health",
            ]
            for endpoint in endpoints:
                conn.request("GET", endpoint)
                resp = conn.getresponse()
                body = resp.read()
                self.assertEqual(
                    resp.status,
                    200,
                    f"Expected 200 for {endpoint} on persistent connection, got {resp.status} with body: {body[:100]}"
                )
                self.assertNotEqual(resp.status, 404)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
