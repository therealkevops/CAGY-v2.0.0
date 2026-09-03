"""Tests for run_agent.py AIAgent CLI streaming bridge."""
import os
import sys
import json
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WEBUI_DIR = REPO_ROOT / "webui"
MOCK_AGY = REPO_ROOT / "tests" / "mock_agy.py"

if str(WEBUI_DIR) not in sys.path:
    sys.path.insert(0, str(WEBUI_DIR))

import run_agent


class TestRunAgent(unittest.TestCase):
    """Test session mapping and AIAgent bridge execution."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.state_dir = Path(self.temp_dir.name) / "state"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        os.environ["AGY_WEBUI_STATE_DIR"] = str(self.state_dir)
        os.environ["HERMES_WEBUI_STATE_DIR"] = str(self.state_dir)

    def test_session_mapping(self):
        """Verify saving and loading agy conversation IDs to session map."""
        session_id = "test_session_xyz123"
        conv_id = "conv_google_gemini_456"

        self.assertIsNone(run_agent._load_agy_conv_id(session_id))

        run_agent._save_agy_conv_id(session_id, conv_id)
        loaded = run_agent._load_agy_conv_id(session_id)
        self.assertEqual(loaded, conv_id)

        # Map file should exist in state directory
        map_file = run_agent._get_map_file()
        self.assertTrue(map_file.exists())
        with open(map_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            self.assertEqual(data.get(session_id), conv_id)

    def test_ai_agent_with_mock_cli(self):
        """Execute AIAgent against mock_agy.py and test stream callback and completion."""
        os.environ["AGY_CLI_PATH"] = str(MOCK_AGY)
        self.addCleanup(lambda: os.environ.pop("AGY_CLI_PATH", None))

        deltas = []
        statuses = []

        def on_delta(delta):
            deltas.append(delta)

        def on_status(st):
            statuses.append(st)

        agent = run_agent.AIAgent(
            model="Gemini 3.8 Flash (Mock)",
            workspace=str(REPO_ROOT),
            stream_delta_callback=on_delta,
            status_callback=on_status,
        )

        session_id = "mock_session_001"
        result = agent.run_conversation("Hello from test suite", session_id=session_id)

        self.assertIsNotNone(result)
        # Should have captured the mock response
        messages = result.get("messages", [])
        self.assertGreater(len(messages), 0)
        assistant_msg = next((m for m in reversed(messages) if m.get("role") == "assistant"), None)
        self.assertIsNotNone(assistant_msg)
        response_text = assistant_msg.get("content", "")
        self.assertIn("Mock reply to: Hello from test suite", response_text)

        # Verify conv_id was mapped
        mapped_conv = run_agent._load_agy_conv_id(session_id)
        self.assertEqual(mapped_conv, "conv-mock-123456")

        # Verify deltas received
        self.assertGreater(len(deltas), 0)


if __name__ == "__main__":
    unittest.main()
