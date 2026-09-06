"""Unit and integration tests for workspace-targeted session exports and imports."""
import json
import os
import shutil
import sys
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

from api.models import Session, SESSIONS
from api.session_workspace_export import (
    sanitize_filename,
    generate_session_markdown,
    resolve_destination_dir,
    export_session_to_workspace,
    list_workspace_session_exports,
    validate_workspace_file_for_import,
)
from server import QuietHTTPServer, Handler


class TestSessionWorkspaceExportUnit(unittest.TestCase):
    """Unit tests for session_workspace_export functions."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(dir=Path.home())
        self.workspace_dir = Path(self.temp_dir) / "test_workspace"
        self.workspace_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_sanitize_filename(self):
        self.assertEqual(sanitize_filename("valid_name.md"), "valid_name.md")
        self.assertEqual(sanitize_filename("../../etc/passwd"), "passwd")
        self.assertEqual(sanitize_filename("hello:world?*<>.json"), "hello_world____.json")
        self.assertEqual(sanitize_filename("", fallback="def"), "def")

    def test_generate_session_markdown(self):
        session_data = {
            "session_id": "test-123",
            "title": "My Analysis",
            "workspace": str(self.workspace_dir),
            "model": "gemini-2.5-pro",
            "created_at": "2026-09-05T00:00:00Z",
            "messages": [
                {"role": "user", "content": "How do I optimize Docker?"},
                {"role": "assistant", "content": "Use multi-stage builds."},
                {"role": "tool", "content": "internal tool output"},
            ],
        }
        md = generate_session_markdown(session_data)
        self.assertIn("# My Analysis", md)
        self.assertIn("test-123", md)
        self.assertIn("gemini-2.5-pro", md)
        self.assertIn("## User", md)
        self.assertIn("How do I optimize Docker?", md)
        self.assertIn("## Assistant", md)
        self.assertIn("Use multi-stage builds.", md)
        self.assertNotIn("internal tool output", md)

    def test_resolve_destination_dir_and_traversal_prevention(self):
        base, target = resolve_destination_dir(str(self.workspace_dir), "transcripts")
        self.assertEqual(base, self.workspace_dir.resolve())
        self.assertEqual(target, (self.workspace_dir / "transcripts").resolve())

        # Test traversal rejection
        with self.assertRaises(ValueError):
            resolve_destination_dir(str(self.workspace_dir), "../../../etc")

    def test_export_session_formats_and_listing(self):
        session_data = {
            "session_id": "sess-abc",
            "title": "Test Session",
            "workspace": str(self.workspace_dir),
            "messages": [{"role": "user", "content": "Hello"}],
        }

        # Export MD
        res_md = export_session_to_workspace(
            session_data=session_data,
            workspace_path=str(self.workspace_dir),
            subfolder="exports",
            format="md",
        )
        self.assertTrue(res_md["ok"])
        self.assertTrue(os.path.exists(res_md["path"]))
        self.assertTrue(res_md["path"].endswith("agy-sess-abc.md"))

        # Export JSON
        res_json = export_session_to_workspace(
            session_data=session_data,
            workspace_path=str(self.workspace_dir),
            subfolder="exports",
            format="json",
        )
        self.assertTrue(res_json["ok"])
        self.assertTrue(os.path.exists(res_json["path"]))
        self.assertTrue(res_json["path"].endswith("agy-sess-abc.json"))

        # Export HTML
        res_html = export_session_to_workspace(
            session_data=session_data,
            workspace_path=str(self.workspace_dir),
            subfolder="exports",
            format="html",
        )
        self.assertTrue(res_html["ok"])
        self.assertTrue(os.path.exists(res_html["path"]))
        self.assertTrue(res_html["path"].endswith("agy-sess-abc.html"))

        # List exports
        listing = list_workspace_session_exports(
            workspace_path=str(self.workspace_dir),
            subfolder="exports",
        )
        self.assertTrue(listing["ok"])
        self.assertEqual(len(listing["exports"]), 3)
        formats = {e["format"] for e in listing["exports"]}
        self.assertEqual(formats, {"md", "json", "html"})

        json_entry = next(e for e in listing["exports"] if e["format"] == "json")
        self.assertTrue(json_entry["importable"])
        self.assertEqual(json_entry["title"], "Test Session")
        self.assertEqual(json_entry["message_count"], 1)

    def test_validate_workspace_file_for_import(self):
        json_file = self.workspace_dir / "valid.json"
        json_file.write_text(json.dumps({"title": "Valid", "messages": []}))

        validated = validate_workspace_file_for_import(str(json_file))
        self.assertEqual(validated, json_file.resolve())

        # Reject non-existent file
        with self.assertRaises(ValueError):
            validate_workspace_file_for_import(str(self.workspace_dir / "nonexistent.json"))

        # Reject non-json file
        txt_file = self.workspace_dir / "test.txt"
        txt_file.write_text("not json")
        with self.assertRaises(ValueError):
            validate_workspace_file_for_import(str(txt_file))


class TestSessionWorkspaceExportApi(unittest.TestCase):
    """End-to-end HTTP integration tests for workspace export/import routes."""

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.mkdtemp(dir=Path.home())
        cls.state_dir = Path(cls.temp_dir) / "state"
        cls.state_dir.mkdir(parents=True, exist_ok=True)
        cls.sessions_dir = cls.state_dir / "sessions"
        cls.sessions_dir.mkdir(parents=True, exist_ok=True)
        cls.workspace_dir = Path(cls.temp_dir) / "workspace"
        cls.workspace_dir.mkdir(parents=True, exist_ok=True)

        os.environ["AGY_WEBUI_STATE_DIR"] = str(cls.state_dir)
        os.environ["HERMES_WEBUI_STATE_DIR"] = str(cls.state_dir)
        os.environ["AGY_WEBUI_SKIP_ONBOARDING"] = "1"
        os.environ["HERMES_WEBUI_SKIP_ONBOARDING"] = "1"
        os.environ["AGY_CLI_PATH"] = str(MOCK_AGY)
        os.environ["HERMES_DEFAULT_WORKSPACE"] = str(cls.workspace_dir)

        # Hermetically isolate session storage to cls.sessions_dir across all webui modules
        import api.config
        import api.models
        import api.routes
        try:
            import api.streaming
        except ImportError:
            api.streaming = None
        try:
            import api.route_session_list_cache
        except ImportError:
            api.route_session_list_cache = None

        cls._orig_config_state_dir = api.config.STATE_DIR
        cls._orig_config_session_dir = api.config.SESSION_DIR
        cls._orig_config_index_file = api.config.SESSION_INDEX_FILE
        cls._orig_models_session_dir = api.models.SESSION_DIR
        cls._orig_models_index_file = api.models.SESSION_INDEX_FILE
        cls._orig_routes_session_dir = getattr(api.routes, "SESSION_DIR", None)
        cls._orig_routes_index_file = getattr(api.routes, "SESSION_INDEX_FILE", None)

        api.config.STATE_DIR = cls.state_dir
        api.config.SESSION_DIR = cls.sessions_dir
        api.config.SESSION_INDEX_FILE = cls.sessions_dir / "_index.json"
        api.models.SESSION_DIR = cls.sessions_dir
        api.models.SESSION_INDEX_FILE = cls.sessions_dir / "_index.json"
        if hasattr(api.routes, "SESSION_DIR"):
            api.routes.SESSION_DIR = cls.sessions_dir
        if hasattr(api.routes, "SESSION_INDEX_FILE"):
            api.routes.SESSION_INDEX_FILE = cls.sessions_dir / "_index.json"
        if api.streaming and hasattr(api.streaming, "SESSION_DIR"):
            cls._orig_streaming_session_dir = api.streaming.SESSION_DIR
            api.streaming.SESSION_DIR = cls.sessions_dir
        if api.route_session_list_cache and hasattr(api.route_session_list_cache, "SESSION_DIR"):
            cls._orig_cache_session_dir = api.route_session_list_cache.SESSION_DIR
            api.route_session_list_cache.SESSION_DIR = cls.sessions_dir

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

        # Restore original module paths
        import api.config
        import api.models
        import api.routes
        api.config.STATE_DIR = cls._orig_config_state_dir
        api.config.SESSION_DIR = cls._orig_config_session_dir
        api.config.SESSION_INDEX_FILE = cls._orig_config_index_file
        api.models.SESSION_DIR = cls._orig_models_session_dir
        api.models.SESSION_INDEX_FILE = cls._orig_models_index_file
        if cls._orig_routes_session_dir is not None:
            api.routes.SESSION_DIR = cls._orig_routes_session_dir
        if cls._orig_routes_index_file is not None:
            api.routes.SESSION_INDEX_FILE = cls._orig_routes_index_file
        if getattr(cls, "_orig_streaming_session_dir", None) is not None and getattr(sys.modules.get("api.streaming"), "SESSION_DIR", None):
            sys.modules["api.streaming"].SESSION_DIR = cls._orig_streaming_session_dir
        if getattr(cls, "_orig_cache_session_dir", None) is not None and getattr(sys.modules.get("api.route_session_list_cache"), "SESSION_DIR", None):
            sys.modules["api.route_session_list_cache"].SESSION_DIR = cls._orig_cache_session_dir

        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def setUp(self):
        self.created_session_ids = []

    def tearDown(self):
        for sid in list(self.created_session_ids):
            SESSIONS.pop(sid, None)
        self.created_session_ids.clear()

    def _get(self, path: str):
        req = urllib.request.Request(f"{self.base_url}{path}")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = resp.read().decode("utf-8")
            return resp.status, json.loads(data) if data else {}

    def _post(self, path: str, payload: dict):
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = resp.read().decode("utf-8")
            return resp.status, json.loads(data) if data else {}

    def test_export_list_and_import_flow(self):
        # Create a test session
        s = Session(
            title="E2E Workspace Export Test",
            workspace=str(self.workspace_dir),
            messages=[
                {"role": "user", "content": "Step 1: Test export"},
                {"role": "assistant", "content": "Step 2: Export succeeded"},
            ],
        )
        s.save()
        SESSIONS[s.session_id] = s
        self.created_session_ids.append(s.session_id)

        # 1. POST /api/session/export/workspace (JSON)
        status, data = self._post(
            "/api/session/export/workspace",
            {
                "session_id": s.session_id,
                "workspace": str(self.workspace_dir),
                "subfolder": "transcripts",
                "format": "json",
            },
        )
        self.assertEqual(status, 200)
        self.assertTrue(data.get("ok"))
        exported_path = data.get("path")
        self.assertTrue(os.path.exists(exported_path))

        # 2. POST /api/session/export/workspace (MD)
        status_md, data_md = self._post(
            "/api/session/export/workspace",
            {
                "session_id": s.session_id,
                "workspace": str(self.workspace_dir),
                "subfolder": "transcripts",
                "format": "md",
            },
        )
        self.assertEqual(status_md, 200)
        self.assertTrue(data_md.get("ok"))

        # 3. GET /api/session/workspace_exports
        status_list, data_list = self._get(
            f"/api/session/workspace_exports?workspace={urllib.parse.quote(str(self.workspace_dir))}&subfolder=transcripts"
        )
        self.assertEqual(status_list, 200)
        self.assertTrue(data_list.get("ok"))
        exports = data_list.get("exports", [])
        self.assertGreaterEqual(len(exports), 2)

        # 4. POST /api/session/import/workspace
        status_imp, data_imp = self._post(
            "/api/session/import/workspace",
            {"path": exported_path},
        )
        self.assertEqual(status_imp, 200)
        self.assertTrue(data_imp.get("ok"))
        imported_session = data_imp.get("session")
        self.assertIsNotNone(imported_session)
        self.assertEqual(imported_session.get("title"), "E2E Workspace Export Test")
        if imported_session and imported_session.get("session_id"):
            self.created_session_ids.append(imported_session["session_id"])

        # 5. Security: Path Traversal rejection
        try:
            self._post(
                "/api/session/export/workspace",
                {
                    "session_id": s.session_id,
                    "workspace": str(self.workspace_dir),
                    "subfolder": "../../etc",
                    "format": "json",
                },
            )
            self.fail("Expected HTTPError 400 for path traversal")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)
            e.close()


if __name__ == "__main__":
    unittest.main()
