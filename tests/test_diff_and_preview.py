"""
Antigravity (CAGY) Visual Diff Engine & Live Canvas Preview Tests
Covers structured side-by-side diff computation, diff endpoints, and preview routing.
"""

import os
import sys
import json
import shutil
import tempfile
import unittest
import urllib.request
import urllib.error
from pathlib import Path

# Ensure root repository is in python search path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
WEBUI_DIR = REPO_ROOT / "webui"
if str(WEBUI_DIR) not in sys.path:
    sys.path.insert(0, str(WEBUI_DIR))

from webui.api.diff_viewer import compute_structured_diff, get_file_diff_against_head


class TestDiffViewerUnit(unittest.TestCase):
    """Unit tests for the difflib-based structured diff computation."""

    def test_compute_structured_diff_identical(self):
        """Identical texts should report no changes and 100% equal rows."""
        text = "def hello():\n    return 'world'\n"
        res = compute_structured_diff(text, text, filename="hello.py")

        self.assertEqual(res["filename"], "hello.py")
        self.assertFalse(res["has_changes"])
        self.assertEqual(res["stats"]["additions"], 0)
        self.assertEqual(res["stats"]["deletions"], 0)
        self.assertEqual(res["stats"]["total_changes"], 0)

        for row in res["rows"]:
            self.assertEqual(row["left"]["type"], "equal")
            self.assertEqual(row["right"]["type"], "equal")
            self.assertEqual(row["left"]["text"], row["right"]["text"])

    def test_compute_structured_diff_replacements(self):
        """Modified lines should register as deletions on left and additions on right."""
        old_text = "def calculate(a, b):\n    return a - b\n"
        new_text = "def calculate(a, b):\n    return a + b\n"
        res = compute_structured_diff(old_text, new_text, filename="calc.py")

        self.assertTrue(res["has_changes"])
        self.assertEqual(res["stats"]["deletions"], 1)
        self.assertEqual(res["stats"]["additions"], 1)

        # First line is equal
        self.assertEqual(res["rows"][0]["left"]["type"], "equal")
        self.assertEqual(res["rows"][0]["right"]["type"], "equal")

        # Second line is replaced (delete on left, insert on right)
        self.assertEqual(res["rows"][1]["left"]["type"], "delete")
        self.assertEqual(res["rows"][1]["right"]["type"], "insert")
        self.assertIn("a - b", res["rows"][1]["left"]["text"])
        self.assertIn("a + b", res["rows"][1]["right"]["text"])

    def test_compute_structured_diff_insertions_and_deletions(self):
        """Inserting lines and deleting lines should maintain line numbering and empty fillers."""
        old_text = "line1\nline2\nline3\n"
        new_text = "line1\nline3\nline4\n"
        res = compute_structured_diff(old_text, new_text, filename="sample.txt")

        self.assertTrue(res["has_changes"])
        self.assertEqual(res["stats"]["deletions"], 1)  # line2 removed
        self.assertEqual(res["stats"]["additions"], 1)  # line4 added

    def test_get_file_diff_against_head_nonexistent(self):
        """Non-existent file should be handled gracefully without crashing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            res = get_file_diff_against_head(Path(tmpdir), "missing.py")
            self.assertIn("stats", res)
            self.assertIn("rows", res)


from server import QuietHTTPServer, Handler
import threading

class TestDiffAndPreviewApiEndpoints(unittest.TestCase):
    """HTTP integration tests for diff computing and file diff API endpoints."""

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.mkdtemp(prefix="cagy_diff_test_")
        cls.state_dir = Path(cls.temp_dir) / "state"
        cls.state_dir.mkdir(parents=True, exist_ok=True)

        os.environ["AGY_WEBUI_STATE_DIR"] = str(cls.state_dir)
        os.environ["HERMES_WEBUI_STATE_DIR"] = str(cls.state_dir)
        os.environ["AGY_WEBUI_SKIP_ONBOARDING"] = "1"
        os.environ["HERMES_WEBUI_SKIP_ONBOARDING"] = "1"

        cls.httpd = QuietHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.httpd.server_address[1]
        cls.server_url = f"http://127.0.0.1:{cls.port}"

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

    def _post(self, path: str, payload: dict):
        req = urllib.request.Request(
            f"{self.server_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))

    def _get(self, path: str):
        req = urllib.request.Request(f"{self.server_url}{path}")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))

    def test_post_diff_compute_endpoint(self):
        """Test POST /api/diff/compute returns structured side-by-side rows."""
        status, data = self._post("/api/diff/compute", {
            "original": "const x = 1;\nconsole.log(x);",
            "modified": "const x = 2;\nconsole.log(x);\nconsole.log('done');",
            "filename": "index.js"
        })
        self.assertEqual(status, 200)
        self.assertEqual(data["filename"], "index.js")
        self.assertTrue(data["has_changes"])
        self.assertEqual(data["stats"]["deletions"], 1)
        self.assertEqual(data["stats"]["additions"], 2)
        self.assertIsInstance(data["rows"], list)
        self.assertGreater(len(data["rows"]), 0)

    def test_get_diff_file_endpoint(self):
        """Test GET /api/diff/file responds with valid diff object."""
        status, data = self._get("/api/diff/file?path=README.md")
        self.assertEqual(status, 200)
        self.assertIn("stats", data)
        self.assertIn("rows", data)


if __name__ == "__main__":
    unittest.main()
