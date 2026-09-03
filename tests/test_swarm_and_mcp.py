"""Tests for Subagent Swarms DAG hierarchy and MCP Hub expansion."""
import os
import sys
import json
import shutil
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WEBUI_DIR = REPO_ROOT / "webui"
MOCK_AGY = REPO_ROOT / "tests" / "mock_agy.py"

if str(WEBUI_DIR) not in sys.path:
    sys.path.insert(0, str(WEBUI_DIR))

from api import subagents, mcp_hub


class TestSubagentsSwarm(unittest.TestCase):
    """Test subagent discovery, hierarchical tree construction, and transcript export."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.brain_dir = Path(self.temp_dir) / "brain"
        self.brain_dir.mkdir(parents=True, exist_ok=True)
        os.environ["AGY_APP_DATA_DIR"] = str(self.temp_dir)
        self.addCleanup(lambda: os.environ.pop("AGY_APP_DATA_DIR", None))
        self.addCleanup(lambda: shutil.rmtree(self.temp_dir, ignore_errors=True))

        # Create parent conversation directory with synthetic transcript
        self.parent_conv = "conv-parent-001"
        p_dir = self.brain_dir / self.parent_conv / ".system_generated" / "logs"
        p_dir.mkdir(parents=True, exist_ok=True)
        
        # Synthetic steps: parent invokes child_1, then child_1 response arrives
        transcript_lines = [
            json.dumps({
                "step_index": 0,
                "type": "PLANNER_RESPONSE",
                "tool_calls": [{
                    "name": "invoke_subagent",
                    "args": {
                        "Subagents": [{
                            "Role": "Research Analyst",
                            "TypeName": "research",
                            "Prompt": "Survey database tables",
                            "Model": "flash"
                        }]
                    }
                }]
            }),
            json.dumps({
                "step_index": 1,
                "type": "GENERIC",
                "content": 'Spawned subagent conversationId: "sub-child-001"'
            })
        ]
        (p_dir / "transcript.jsonl").write_text("\n".join(transcript_lines), encoding="utf-8")

        # Create child conversation directory
        self.child_conv = "sub-child-001"
        c_dir = self.brain_dir / self.child_conv / ".system_generated" / "logs"
        c_dir.mkdir(parents=True, exist_ok=True)
        child_transcript_lines = [
            json.dumps({
                "step_index": 0,
                "type": "USER_INPUT",
                "content": "Survey database tables"
            }),
            json.dumps({
                "step_index": 1,
                "type": "PLANNER_RESPONSE",
                "tool_calls": [{"name": "grep_search", "args": {"Query": "CREATE TABLE"}}]
            })
        ]
        (c_dir / "transcript.jsonl").write_text("\n".join(child_transcript_lines), encoding="utf-8")

    def test_subagent_discovery_and_tree(self):
        """Test hierarchical DAG tree construction from brain directories."""
        res = subagents.list_subagents(conv_id=self.parent_conv)

        self.assertIsNotNone(res)
        self.assertEqual(res.get("total"), 1)
        root = res.get("root", {})
        self.assertEqual(root.get("conversation_id"), self.parent_conv)
        self.assertEqual(len(root.get("children", [])), 1)

        # Check child details
        child = root["children"][0]
        self.assertEqual(child.get("role"), "Research Analyst")
        self.assertEqual(child.get("conversation_id"), self.child_conv)
        self.assertEqual(child.get("tool_count"), 1)
        self.assertIn("grep_search", child.get("tools_used", []))

    def test_transcript_loading_and_export(self):
        """Test transcript loading and JSON export for subagents."""
        t_data = subagents.get_subagent_transcript(self.child_conv)
        self.assertIsNotNone(t_data)
        self.assertEqual(t_data.get("subagent_id"), self.child_conv)
        self.assertEqual(len(t_data.get("steps", [])), 2)

        export_str = subagents.export_subagent_transcript(self.child_conv)
        self.assertIsInstance(export_str, str)
        parsed = json.loads(export_str)
        self.assertEqual(parsed.get("subagent_id"), self.child_conv)


class TestMcpHub(unittest.TestCase):
    """Test MCP Hub catalog, CRUD operations, and connection probe."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.mcp_json_path = Path(self.temp_dir) / "mcp.json"
        self.addCleanup(lambda: shutil.rmtree(self.temp_dir, ignore_errors=True))

        # Monkeypatch _get_mcp_json_paths
        self.orig_paths_fn = mcp_hub._get_mcp_json_paths
        mcp_hub._get_mcp_json_paths = lambda: [self.mcp_json_path]
        self.addCleanup(lambda: setattr(mcp_hub, "_get_mcp_json_paths", self.orig_paths_fn))

    def test_mcp_hub_crud_lifecycle(self):
        """Test listing, adding, toggling, and deleting MCP servers."""
        # 1. Initial list
        data = mcp_hub.list_mcp_hub_data()
        self.assertEqual(data.get("servers"), [])
        self.assertGreater(data.get("stats", {}).get("total_builtin_tools", 0), 0)

        # 2. Add server
        add_res = mcp_hub.add_or_update_mcp_server("github-srv", {
            "transport": "stdio",
            "command": "npx -y @modelcontextprotocol/server-github",
            "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "test_token"}
        })
        self.assertTrue(add_res.get("ok"))

        # 3. Retrieve server
        srv = mcp_hub.get_mcp_server("github-srv")
        self.assertIsNotNone(srv)
        self.assertEqual(srv.get("command"), "npx -y @modelcontextprotocol/server-github")

        # 4. Toggle server
        tog_res = mcp_hub.toggle_mcp_server("github-srv", False)
        self.assertTrue(tog_res.get("ok"))
        srv2 = mcp_hub.get_mcp_server("github-srv")
        self.assertFalse(srv2.get("enabled"))

        # 5. Delete server
        del_res = mcp_hub.delete_mcp_server("github-srv")
        self.assertTrue(del_res.get("ok"))
        self.assertIsNone(mcp_hub.get_mcp_server("github-srv"))

    def test_mcp_connection_probe(self):
        """Test test_mcp_server probe function."""
        # Probe valid python3 binary
        res_valid = mcp_hub.test_mcp_server({
            "transport": "stdio",
            "command": "python3 --version"
        })
        self.assertTrue(res_valid.get("ok"))
        self.assertGreater(res_valid.get("latency_ms", -1), 0)

        # Probe non-existent binary
        res_invalid = mcp_hub.test_mcp_server({
            "transport": "stdio",
            "command": "nonexistent_binary_xyz_12345"
        })
        self.assertFalse(res_invalid.get("ok"))
        self.assertIn("not found", res_invalid.get("error", "").lower())


import threading
import urllib.request
from server import QuietHTTPServer, Handler


class TestSwarmAndMcpApiEndpoints(unittest.TestCase):
    """Test HTTP API endpoints for Subagent Swarms and MCP Hub."""

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

    def test_subagents_endpoints(self):
        """Test GET /api/subagents and GET /api/subagents/export."""
        status, data = self._get("/api/subagents")
        self.assertEqual(status, 200)
        self.assertIn("root", data)
        self.assertIn("subagents", data)
        self.assertIn("tree", data)

        status, data = self._get("/api/subagents/export?id=mock-conv")
        self.assertEqual(status, 200)

    def test_mcp_hub_endpoints(self):
        """Test MCP Hub endpoints: list, add, test, toggle, delete."""
        # 1. Get MCP hub catalog
        status, data = self._get("/api/mcp/hub")
        self.assertEqual(status, 200)
        self.assertIn("built_in_tools", data)

        # 2. Test MCP connection probe endpoint
        status, test_res = self._post("/api/mcp/hub/test", {
            "transport": "stdio",
            "command": "python3 --version"
        })
        self.assertEqual(status, 200)
        self.assertTrue(test_res.get("ok"))
        self.assertGreater(test_res.get("latency_ms", -1), 0)

        # 3. Add MCP server
        status, add_res = self._post("/api/mcp/hub/add", {
            "name": "sqlite-test",
            "transport": "stdio",
            "command": "python3 -m unittest --help"
        })
        self.assertEqual(status, 200)
        self.assertTrue(add_res.get("ok"))

        # 4. Toggle MCP server
        status, tog_res = self._post("/api/mcp/hub/toggle", {
            "name": "sqlite-test",
            "enabled": False
        })
        self.assertEqual(status, 200)
        self.assertTrue(tog_res.get("ok"))

        # 5. Delete MCP server
        status, del_res = self._post("/api/mcp/hub/delete", {
            "name": "sqlite-test"
        })
        self.assertEqual(status, 200)
        self.assertTrue(del_res.get("ok"))


if __name__ == "__main__":
    unittest.main()

