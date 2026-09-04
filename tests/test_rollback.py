"""Unit and integration tests for Git-backed workspace checkpoints and rollback."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WEBUI_DIR = REPO_ROOT / "webui"
MOCK_AGY = REPO_ROOT / "tests" / "mock_agy.py"

if str(WEBUI_DIR) not in sys.path:
    sys.path.insert(0, str(WEBUI_DIR))

from api.rollback import list_checkpoints, get_checkpoint_diff, restore_checkpoint
from server import QuietHTTPServer, Handler


class TestRollbackEngine(unittest.TestCase):
    """Unit tests for rollback Git execution logic."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.ws = Path(self.test_dir)
        self._git("init")
        self._git("config", "user.name", "Test Author")
        self._git("config", "user.email", "test@example.com")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _git(self, *args):
        r = subprocess.run(["git", *args], cwd=self.ws, capture_output=True, text=True)
        return r.stdout.strip()

    def test_empty_git_repo(self):
        """Fresh repo with zero commits returns ok: True and empty checkpoints."""
        res = list_checkpoints(str(self.ws))
        self.assertTrue(res.get("ok"))
        self.assertEqual(res.get("checkpoints"), [])

    def test_non_git_directory(self):
        """Non-git directory returns empty checkpoints without crashing."""
        with tempfile.TemporaryDirectory() as non_git:
            res = list_checkpoints(non_git)
            self.assertTrue(res.get("ok"))
            self.assertEqual(res.get("checkpoints"), [])

            diff = get_checkpoint_diff(non_git, "HEAD")
            self.assertFalse(diff.get("ok"))

            restore = restore_checkpoint(non_git, "HEAD")
            self.assertFalse(restore.get("ok"))

    def test_commit_lifecycle_and_diff(self):
        """Test commit listing and diff extraction across multiple commits."""
        # Commit 1
        (self.ws / "doc1.txt").write_text("Version 1 doc1\n", encoding="utf-8")
        (self.ws / "doc2.txt").write_text("Version 1 doc2\n", encoding="utf-8")
        self._git("add", ".")
        self._git("commit", "-m", "Initial commit")
        c1 = self._git("rev-parse", "HEAD")
        c1_short = c1[:7]

        # Commit 2
        (self.ws / "doc1.txt").write_text("Version 2 doc1 with edits\n", encoding="utf-8")
        (self.ws / "doc3.txt").write_text("New file in commit 2\n", encoding="utf-8")
        (self.ws / "doc2.txt").unlink()
        self._git("add", ".")
        self._git("commit", "-m", "Second commit: update doc1, add doc3, remove doc2")
        c2 = self._git("rev-parse", "HEAD")
        c2_short = c2[:7]

        # 1. list_checkpoints
        listed = list_checkpoints(str(self.ws))
        self.assertTrue(listed.get("ok"))
        checkpoints = listed.get("checkpoints", [])
        self.assertEqual(len(checkpoints), 2)
        self.assertEqual(checkpoints[0]["id"], c2_short)
        self.assertEqual(checkpoints[0]["author"], "Test Author")
        self.assertIn("Second commit", checkpoints[0]["message"])
        self.assertEqual(checkpoints[0]["files"], 3)
        self.assertEqual(checkpoints[1]["id"], c1_short)
        self.assertEqual(checkpoints[1]["files"], 2)

        # 2. get_checkpoint_diff for c2
        diff_res = get_checkpoint_diff(str(self.ws), c2_short)
        self.assertTrue(diff_res.get("ok"))
        self.assertEqual(diff_res.get("total_changes"), 3)
        status_map = {item["file"]: item["status"] for item in diff_res.get("files_changed", [])}
        self.assertEqual(status_map.get("doc1.txt"), "modified")
        self.assertEqual(status_map.get("doc3.txt"), "added")
        self.assertEqual(status_map.get("doc2.txt"), "deleted")
        self.assertIn("Version 2 doc1 with edits", diff_res.get("diff", ""))

    def test_restore_checkpoint_with_dirty_worktree(self):
        """Verify working directory restores to target checkpoint, pruning post-commit files and stashing dirty work."""
        # Commit 1
        (self.ws / "keep.txt").write_text("v1\n", encoding="utf-8")
        (self.ws / "deleted_later.txt").write_text("v1\n", encoding="utf-8")
        self._git("add", ".")
        self._git("commit", "-m", "c1")
        c1_short = self._git("rev-parse", "--short", "HEAD")

        # Commit 2
        (self.ws / "keep.txt").write_text("v2\n", encoding="utf-8")
        (self.ws / "added_later.txt").write_text("new\n", encoding="utf-8")
        (self.ws / "deleted_later.txt").unlink()
        self._git("add", ".")
        self._git("commit", "-m", "c2")

        # Create uncommitted dirty file
        (self.ws / "uncommitted.txt").write_text("dirty work\n", encoding="utf-8")

        # Restore to c1
        res = restore_checkpoint(str(self.ws), c1_short)
        self.assertTrue(res.get("ok"))
        self.assertTrue(res.get("stashed_prior_changes"))
        self.assertGreaterEqual(res.get("files_restored_count", 0), 2)

        # Verify filesystem state matches c1
        self.assertEqual((self.ws / "keep.txt").read_text(), "v1\n")
        self.assertTrue((self.ws / "deleted_later.txt").exists())
        self.assertFalse((self.ws / "added_later.txt").exists())
        self.assertFalse((self.ws / "uncommitted.txt").exists())

        # Verify stash contains the dirty work
        stash_list = self._git("stash", "list")
        self.assertIn("Auto-safety stash", stash_list)

    def test_invalid_checkpoint_ref(self):
        """Reject malicious or invalid checkpoint refs."""
        with self.assertRaises(ValueError):
            restore_checkpoint(str(self.ws), "--upload-pack=evil")
        with self.assertRaises(ValueError):
            get_checkpoint_diff(str(self.ws), "invalid ref with spaces")


class TestRollbackApiEndpoints(unittest.TestCase):
    """Integration tests for WebUI /api/rollback/* endpoints."""

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.mkdtemp()
        cls.state_dir = Path(cls.temp_dir) / "state"
        cls.state_dir.mkdir(parents=True, exist_ok=True)
        cls.workspace_dir = Path(cls.temp_dir) / "workspace"
        cls.workspace_dir.mkdir(parents=True, exist_ok=True)

        # Set up git in workspace
        subprocess.run(["git", "init"], cwd=cls.workspace_dir, capture_output=True)
        subprocess.run(["git", "config", "user.name", "CI Bot"], cwd=cls.workspace_dir, capture_output=True)
        subprocess.run(["git", "config", "user.email", "ci@bot.com"], cwd=cls.workspace_dir, capture_output=True)
        (cls.workspace_dir / "app.py").write_text("print('v1')\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=cls.workspace_dir, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=cls.workspace_dir, capture_output=True)
        cls.c1 = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=cls.workspace_dir, capture_output=True, text=True).stdout.strip()

        (cls.workspace_dir / "app.py").write_text("print('v2')\n", encoding="utf-8")
        (cls.workspace_dir / "extra.py").write_text("x = 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=cls.workspace_dir, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Feature update"], cwd=cls.workspace_dir, capture_output=True)
        cls.c2 = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=cls.workspace_dir, capture_output=True, text=True).stdout.strip()

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
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = resp.read().decode("utf-8")
            return resp.status, json.loads(data) if data else {}

    def test_api_list_checkpoints(self):
        """Test GET /api/rollback/list endpoint."""
        ws_enc = urllib.parse.quote(str(self.workspace_dir))
        status, data = self._get(f"/api/rollback/list?workspace={ws_enc}")
        self.assertEqual(status, 200)
        self.assertTrue(data.get("ok"))
        checkpoints = data.get("checkpoints", [])
        self.assertEqual(len(checkpoints), 2)
        self.assertEqual(checkpoints[0]["id"], self.c2)
        self.assertEqual(checkpoints[1]["id"], self.c1)

    def test_api_diff_checkpoint(self):
        """Test GET /api/rollback/diff endpoint."""
        ws_enc = urllib.parse.quote(str(self.workspace_dir))
        status, data = self._get(f"/api/rollback/diff?workspace={ws_enc}&checkpoint={self.c2}")
        self.assertEqual(status, 200)
        self.assertTrue(data.get("ok"))
        self.assertEqual(data.get("total_changes"), 2)
        self.assertIn("print('v2')", data.get("diff", ""))

    def test_api_restore_checkpoint(self):
        """Test POST /api/rollback/restore endpoint."""
        payload = {"workspace": str(self.workspace_dir), "checkpoint": self.c1}
        status, data = self._post("/api/rollback/restore", payload)
        self.assertEqual(status, 200)
        self.assertTrue(data.get("ok"))
        self.assertGreaterEqual(data.get("files_restored_count", 0), 1)

        # Verify disk
        self.assertEqual((self.workspace_dir / "app.py").read_text(), "print('v1')\n")
        self.assertFalse((self.workspace_dir / "extra.py").exists())


if __name__ == "__main__":
    unittest.main()
