"""Unit and integration tests for Project-to-Workspace binding (Option A)."""
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

from server import QuietHTTPServer, Handler
from api.models import SESSIONS, load_projects, save_projects
from api.vault import infer_space_from_workspace, get_vault_dir


class TestProjectWorkspaceBinding(unittest.TestCase):
    """Test Project default_workspace binding and automatic session workspace adoption."""

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.mkdtemp(dir=Path.home())
        cls.state_dir = Path(cls.temp_dir) / "state"
        cls.state_dir.mkdir(parents=True, exist_ok=True)
        cls.sessions_dir = cls.state_dir / "sessions"
        cls.sessions_dir.mkdir(parents=True, exist_ok=True)
        cls.workspace_dir = Path(cls.temp_dir) / "workspace"
        cls.workspace_dir.mkdir(parents=True, exist_ok=True)
        cls.project_a_dir = cls.workspace_dir / "projects" / "cka-study"
        cls.project_a_dir.mkdir(parents=True, exist_ok=True)
        cls.project_b_dir = cls.workspace_dir / "projects" / "web-app"
        cls.project_b_dir.mkdir(parents=True, exist_ok=True)

        os.environ["AGY_WEBUI_STATE_DIR"] = str(cls.state_dir)
        os.environ["HERMES_WEBUI_STATE_DIR"] = str(cls.state_dir)
        os.environ["AGY_WEBUI_SKIP_ONBOARDING"] = "1"
        os.environ["HERMES_WEBUI_SKIP_ONBOARDING"] = "1"
        os.environ["AGY_CLI_PATH"] = str(MOCK_AGY)
        os.environ["HERMES_DEFAULT_WORKSPACE"] = str(cls.workspace_dir)

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
        cls._orig_config_projects_file = getattr(api.config, "PROJECTS_FILE", None)
        cls._orig_models_session_dir = api.models.SESSION_DIR
        cls._orig_models_index_file = api.models.SESSION_INDEX_FILE
        cls._orig_models_projects_file = getattr(api.models, "PROJECTS_FILE", None)
        cls._orig_routes_session_dir = getattr(api.routes, "SESSION_DIR", None)
        cls._orig_routes_index_file = getattr(api.routes, "SESSION_INDEX_FILE", None)

        api.config.STATE_DIR = cls.state_dir
        api.config.SESSION_DIR = cls.sessions_dir
        api.config.SESSION_INDEX_FILE = cls.sessions_dir / "_index.json"
        api.config.PROJECTS_FILE = cls.state_dir / "projects.json"
        api.models.SESSION_DIR = cls.sessions_dir
        api.models.SESSION_INDEX_FILE = cls.sessions_dir / "_index.json"
        api.models.PROJECTS_FILE = cls.state_dir / "projects.json"
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

        import api.config
        import api.models
        import api.routes
        api.config.STATE_DIR = cls._orig_config_state_dir
        api.config.SESSION_DIR = cls._orig_config_session_dir
        api.config.SESSION_INDEX_FILE = cls._orig_config_index_file
        if cls._orig_config_projects_file is not None:
            api.config.PROJECTS_FILE = cls._orig_config_projects_file
        api.models.SESSION_DIR = cls._orig_models_session_dir
        api.models.SESSION_INDEX_FILE = cls._orig_models_index_file
        if cls._orig_models_projects_file is not None:
            api.models.PROJECTS_FILE = cls._orig_models_projects_file
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

    def test_project_create_with_default_workspace(self):
        """Verify POST /api/projects/create saves default_workspace."""
        status, data = self._post(
            "/api/projects/create",
            {
                "name": "CKA Preparation",
                "color": "#4285f4",
                "default_workspace": str(self.project_a_dir),
            },
        )
        self.assertEqual(status, 200)
        self.assertTrue(data.get("ok"))
        proj = data.get("project", {})
        self.assertEqual(proj.get("name"), "CKA Preparation")
        self.assertEqual(proj.get("default_workspace"), str(self.project_a_dir.resolve()))

        # Verify GET /api/projects returns it
        status_get, data_get = self._get("/api/projects")
        self.assertEqual(status_get, 200)
        found = next((p for p in data_get.get("projects", []) if p["project_id"] == proj["project_id"]), None)
        self.assertIsNotNone(found)
        self.assertEqual(found.get("default_workspace"), str(self.project_a_dir.resolve()))

    def test_project_rename_and_workspace_rebind(self):
        """Verify POST /api/projects/rename updates and clears default_workspace."""
        # 1. Create initial project
        _, data = self._post(
            "/api/projects/create",
            {"name": "App Dev", "default_workspace": str(self.project_a_dir)},
        )
        pid = data["project"]["project_id"]

        # 2. Rebind to project_b_dir
        status_up, data_up = self._post(
            "/api/projects/rename",
            {
                "project_id": pid,
                "name": "Web Application",
                "default_workspace": str(self.project_b_dir),
            },
        )
        self.assertEqual(status_up, 200)
        self.assertEqual(data_up["project"]["default_workspace"], str(self.project_b_dir.resolve()))

        # 3. Unlink workspace
        status_un, data_un = self._post(
            "/api/projects/rename",
            {
                "project_id": pid,
                "default_workspace": None,
            },
        )
        self.assertEqual(status_un, 200)
        self.assertIsNone(data_un["project"].get("default_workspace"))

    def test_new_session_auto_adopts_project_default_workspace(self):
        """Verify POST /api/session/new with project_id automatically adopts project default_workspace."""
        # 1. Create a project bound to project_a_dir
        _, p_data = self._post(
            "/api/projects/create",
            {"name": "Kubernetes Cluster", "default_workspace": str(self.project_a_dir)},
        )
        pid = p_data["project"]["project_id"]

        # 2. Create new session under this project without passing an explicit workspace
        status_sess, data_sess = self._post(
            "/api/session/new",
            {
                "title": "K8s Debug Chat",
                "project_id": pid,
                # Simulate frontend where workspace is inherited from prev session
                "workspace_inherited_from_prev_session": True,
                "workspace": str(self.workspace_dir),
            },
        )
        self.assertEqual(status_sess, 200)
        sess = data_sess.get("session", {})
        self.created_session_ids.append(sess["session_id"])
        self.assertEqual(sess.get("project_id"), pid)
        # It must adopt the project bound workspace!
        self.assertEqual(sess.get("workspace"), str(self.project_a_dir.resolve()))

    def test_session_move_with_sync_workspace(self):
        """Verify moving a session to a project with sync_workspace updates session workspace."""
        # 1. Create project bound to project_b_dir
        _, p_data = self._post(
            "/api/projects/create",
            {"name": "Web Frontend", "default_workspace": str(self.project_b_dir)},
        )
        pid = p_data["project"]["project_id"]

        # 2. Create session in root workspace
        _, s_data = self._post(
            "/api/session/new",
            {"title": "Unassigned Session", "workspace": str(self.workspace_dir)},
        )
        sid = s_data["session"]["session_id"]
        self.created_session_ids.append(sid)
        self.assertEqual(s_data["session"]["workspace"], str(self.workspace_dir.resolve()))

        # 3. Move session into project with sync_workspace=True
        status_mv, data_mv = self._post(
            "/api/session/move",
            {"session_id": sid, "project_id": pid, "sync_workspace": True},
        )
        self.assertEqual(status_mv, 200)
        # Check retrieved session
        status_get, data_get = self._get(f"/api/session?session_id={sid}")
        self.assertEqual(status_get, 200)
        retrieved = data_get.get("session", {})
        self.assertEqual(retrieved.get("project_id"), pid)
        self.assertEqual(retrieved.get("workspace"), str(self.project_b_dir.resolve()))

    def test_infer_space_from_workspace_and_vault(self):
        """Verify infer_space_from_workspace infers correctly from bound workspace."""
        space_a = infer_space_from_workspace(self.project_a_dir)
        self.assertEqual(space_a, "cka-study")

        space_b = infer_space_from_workspace(self.project_b_dir)
        self.assertEqual(space_b, "web-app")

        space_root = infer_space_from_workspace(self.workspace_dir)
        self.assertEqual(space_root, "global")

    def test_session_update_workspace_switch(self):
        """Verify POST /api/session/update succeeds when switching workspaces (Spaces page)."""
        # 1. Create a session in root workspace
        _, s_data = self._post(
            "/api/session/new",
            {"title": "Switch Test Session", "workspace": str(self.workspace_dir)},
        )
        sid = s_data["session"]["session_id"]
        self.created_session_ids.append(sid)
        self.assertEqual(s_data["session"]["workspace"], str(self.workspace_dir.resolve()))

        # 2. Update session workspace to project_a_dir (Spaces page workspace activation)
        status_up, data_up = self._post(
            "/api/session/update",
            {
                "session_id": sid,
                "workspace": str(self.project_a_dir),
            },
        )
        self.assertEqual(status_up, 200)
        self.assertEqual(data_up["session"]["workspace"], str(self.project_a_dir.resolve()))


if __name__ == "__main__":
    unittest.main()

