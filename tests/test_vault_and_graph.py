"""Tests for Obsidian-style Knowledge Vault & Graph Memory System."""
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

from api import vault
from server import QuietHTTPServer, Handler


class TestVaultEngine(unittest.TestCase):
    """Test the core vault scanning, wikilink resolution, and rule synchronization engine."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.vault_dir = Path(self.temp_dir) / "knowledge"
        self.vault_dir.mkdir(parents=True, exist_ok=True)

        os.environ["AGY_WORKSPACE_ROOT"] = self.temp_dir
        os.environ["WORKSPACE_DIR"] = self.temp_dir
        self.addCleanup(lambda: os.environ.pop("AGY_WORKSPACE_ROOT", None))
        self.addCleanup(lambda: os.environ.pop("WORKSPACE_DIR", None))
        self.addCleanup(lambda: shutil.rmtree(self.temp_dir, ignore_errors=True))

        # Seed sample notes
        (self.vault_dir / "user").mkdir(parents=True, exist_ok=True)
        (self.vault_dir / "architecture").mkdir(parents=True, exist_ok=True)

        (self.vault_dir / "user" / "profile.md").write_text(
            "# User Profile\n\nRole: Lead Engineer\nReferences: [[cagy_unified|CAGY Platform]] and [[conventions]].\n",
            encoding="utf-8"
        )
        (self.vault_dir / "user" / "conventions.md").write_text(
            "# Coding Conventions\n\nRules: Use hermetic unittests.\nSee [[cagy_unified#Container]].\n",
            encoding="utf-8"
        )
        (self.vault_dir / "architecture" / "cagy_unified.md").write_text(
            "# Unified Architecture\n\nRuns in a single container. Adheres to [[conventions]].\n",
            encoding="utf-8"
        )

    def test_extract_wikilinks(self):
        sample = "Check [[Note A]], [[Note B|Alias B]], [[Note C#Section]], and [[Note D#Section|Alias D]]."
        links = vault.extract_wikilinks(sample)
        targets = [l["target"] for l in links]
        self.assertEqual(targets, ["Note A", "Note B", "Note C", "Note D"])
        self.assertEqual(links[1]["alias"], "Alias B")

    def test_scan_vault_and_build_graph(self):
        graph = vault.scan_vault(self.vault_dir)
        self.assertEqual(graph["stats"]["total_notes"], 3)
        self.assertGreaterEqual(graph["stats"]["total_edges"], 2)

        node_map = {n["path"]: n for n in graph["nodes"]}
        self.assertIn("user/profile.md", node_map)
        self.assertIn("architecture/cagy_unified.md", node_map)
        self.assertIn("user/conventions.md", node_map)

        profile_node = node_map["user/profile.md"]
        self.assertEqual(profile_node["title"], "User Profile")
        self.assertEqual(profile_node["folder"], "user")

        # CAGY unified should have incoming backlinks from profile and conventions
        cagy_node = node_map["architecture/cagy_unified.md"]
        self.assertIn("user/profile", cagy_node["backlinks"])
        self.assertIn("user/conventions", cagy_node["backlinks"])

    def test_note_crud_lifecycle(self):
        # Read note
        note = vault.get_note(self.vault_dir, "user/profile.md")
        self.assertIsNotNone(note)
        self.assertEqual(note["title"], "User Profile")
        self.assertIn("Lead Engineer", note["content"])

        # Save existing note
        save_res = vault.save_note(self.vault_dir, "user/profile.md", "# User Profile\n\nUpdated role: Principal Architect\n")
        self.assertTrue(save_res["ok"])
        updated = vault.get_note(self.vault_dir, "user/profile.md")
        self.assertIn("Principal Architect", updated["content"])

        # Create new note in subfolder
        create_res = vault.save_note(self.vault_dir, "decisions/adr_002.md", "# ADR 002\n\nLink to [[profile]].\n")
        self.assertTrue(create_res["ok"])
        new_note = vault.get_note(self.vault_dir, "decisions/adr_002.md")
        self.assertEqual(new_note["title"], "ADR 002")

        # Delete note
        del_res = vault.delete_note(self.vault_dir, "decisions/adr_002.md")
        self.assertTrue(del_res["ok"])
        self.assertFalse(vault.get_note(self.vault_dir, "decisions/adr_002.md")["exists"])

    def test_path_traversal_protection(self):
        # Attempt traversal
        res = vault.get_note(self.vault_dir, "../outside.md")
        self.assertFalse(res["ok"])
        save_res = vault.save_note(self.vault_dir, "../outside.md", "Malicious content")
        self.assertFalse(save_res["ok"])
        del_res = vault.delete_note(self.vault_dir, "../outside.md")
        self.assertFalse(del_res["ok"])

    def test_sync_vault_to_rules(self):
        res = vault.sync_vault_to_rules(self.vault_dir, Path(self.temp_dir))
        self.assertTrue(res["ok"])
        rule_file = Path(self.temp_dir) / ".gemini" / "rules" / "knowledge_vault.md"
        self.assertTrue(rule_file.exists())
        content = rule_file.read_text(encoding="utf-8")
        self.assertIn("# Antigravity Knowledge Vault & Long-Term Memory", content)
        self.assertIn("User Profile", content)
        self.assertIn("Unified Architecture", content)


class TestVaultApiEndpoints(unittest.TestCase):
    """Test HTTP API endpoints for Knowledge Vault and Graph."""

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.mkdtemp()
        cls.state_dir = Path(cls.temp_dir) / "state"
        cls.state_dir.mkdir(parents=True, exist_ok=True)
        cls.workspace_dir = Path(cls.temp_dir) / "workspace"
        cls.vault_dir = cls.workspace_dir / "knowledge"
        cls.vault_dir.mkdir(parents=True, exist_ok=True)

        os.environ["AGY_WEBUI_STATE_DIR"] = str(cls.state_dir)
        os.environ["HERMES_WEBUI_STATE_DIR"] = str(cls.state_dir)
        os.environ["AGY_WEBUI_SKIP_ONBOARDING"] = "1"
        os.environ["HERMES_WEBUI_SKIP_ONBOARDING"] = "1"
        os.environ["AGY_WORKSPACE_ROOT"] = str(cls.workspace_dir)
        os.environ["WORKSPACE_DIR"] = str(cls.workspace_dir)
        os.environ["AGY_CLI_PATH"] = str(MOCK_AGY)

        # Seed initial note
        (cls.vault_dir / "user").mkdir(parents=True, exist_ok=True)
        (cls.vault_dir / "user" / "profile.md").write_text(
            "# Dev Profile\n\nBio: AI Systems Engineer.\n", encoding="utf-8"
        )

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

    def _post(self, path: str, body: dict = None):
        payload = json.dumps(body or {}).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = resp.read().decode("utf-8")
            return resp.status, json.loads(data) if data else {}

    def test_get_vault_graph(self):
        status, data = self._get("/api/vault/graph")
        self.assertEqual(status, 200)
        self.assertIn("nodes", data)
        self.assertIn("edges", data)
        self.assertIn("stats", data)
        self.assertEqual(data["stats"]["total_notes"], 1)

    def test_get_vault_note(self):
        status, data = self._get("/api/vault/note?path=user/profile.md")
        self.assertEqual(status, 200)
        self.assertEqual(data["title"], "Dev Profile")
        self.assertIn("AI Systems Engineer", data["content"])

    def test_create_update_and_delete_vault_note(self):
        # Create
        status, res = self._post("/api/vault/note", {
            "path": "architecture/microservices.md",
            "content": "# Microservices\n\nOverview of services.\n"
        })
        self.assertEqual(status, 200)
        self.assertTrue(res["ok"])

        # Fetch newly created note
        status, data = self._get("/api/vault/note?path=architecture/microservices.md")
        self.assertEqual(status, 200)
        self.assertEqual(data["title"], "Microservices")

        # Sync rules
        status, sync_res = self._post("/api/vault/sync", {})
        self.assertEqual(status, 200)
        self.assertTrue(sync_res["ok"])
        self.assertGreaterEqual(sync_res["total_compiled_notes"], 2)

        # Delete note
        status, del_res = self._post("/api/vault/delete", {
            "path": "architecture/microservices.md"
        })
        self.assertEqual(status, 200)
        self.assertTrue(del_res["ok"])


if __name__ == "__main__":
    unittest.main()
