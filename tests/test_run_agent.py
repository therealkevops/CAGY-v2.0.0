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

    def test_ai_agent_timeout_handling(self):
        """Verify AIAgent intercepts watchdog timeout from stderr and notifies user."""
        os.environ["AGY_CLI_PATH"] = str(MOCK_AGY)
        os.environ["AGY_PRINT_TIMEOUT"] = "45m"
        self.addCleanup(lambda: os.environ.pop("AGY_CLI_PATH", None))
        self.addCleanup(lambda: os.environ.pop("AGY_PRINT_TIMEOUT", None))

        agent = run_agent.AIAgent(
            workspace=str(REPO_ROOT),
        )

        result = agent.run_conversation("TRIGGER_TIMEOUT_ERROR", session_id="timeout_sess_1")
        self.assertIsNotNone(result)
        messages = result.get("messages", [])
        assistant_msg = next((m for m in reversed(messages) if m.get("role") == "assistant"), None)
        self.assertIsNotNone(assistant_msg)
        content = assistant_msg.get("content", "")
        self.assertIn("Execution Timeout", content)
        self.assertIn("45m", content)

    def test_resolve_model_and_effort(self):
        """Verify model and effort resolution avoids duplicate or illegal --effort flags."""
        # Models that encode effort in parens
        m, eff = run_agent.resolve_model_and_effort("Gemini 3.8 Flash (High)", "high")
        self.assertEqual(m, "Gemini 3.8 Flash (High)")
        self.assertIsNone(eff)

        # Lower tier promoted to High
        m, eff = run_agent.resolve_model_and_effort("Gemini 3.8 Flash (Medium)", "high")
        self.assertEqual(m, "Gemini 3.8 Flash (High)")
        self.assertIsNone(eff)

        m, eff = run_agent.resolve_model_and_effort("Gemini 3.8 Flash (Low)", "high")
        self.assertEqual(m, "Gemini 3.8 Flash (High)")
        self.assertIsNone(eff)

        # Lower tier dash-suffix promoted to High
        m, eff = run_agent.resolve_model_and_effort("gemini-3.8-flash-low", "high")
        self.assertEqual(m, "gemini-3.8-flash-high")
        self.assertIsNone(eff)

        # Claude models should never receive --effort
        m, eff = run_agent.resolve_model_and_effort("Claude Sonnet 4.6 (Thinking)", "high")
        self.assertEqual(m, "Claude Sonnet 4.6 (Thinking)")
        self.assertIsNone(eff)

        m, eff = run_agent.resolve_model_and_effort("claude-sonnet-4-6", "high")
        self.assertEqual(m, "claude-sonnet-4-6")
        self.assertIsNone(eff)

        # GPT-OSS models should never receive --effort
        m, eff = run_agent.resolve_model_and_effort("GPT-OSS 120B (Medium)", "high")
        self.assertEqual(m, "GPT-OSS 120B (Medium)")
        self.assertIsNone(eff)

        # Base models without tier accept --effort
        m, eff = run_agent.resolve_model_and_effort("gemini-3.8-flash", "high")
        self.assertEqual(m, "gemini-3.8-flash")
        self.assertEqual(eff, "high")

        # Unspecified/default models accept --effort
        m, eff = run_agent.resolve_model_and_effort("default", "high")
        self.assertIsNone(m)
        self.assertEqual(eff, "high")

        m, eff = run_agent.resolve_model_and_effort(None, "high")
        self.assertIsNone(m)
        self.assertEqual(eff, "high")

    def test_ai_agent_deepmode_model_and_effort_conflict_avoidance(self):
        """Verify DeepMode with Gemini 3.8 Flash (High) executes cleanly without --effort flag collision."""
        os.environ["AGY_CLI_PATH"] = str(MOCK_AGY)
        self.addCleanup(lambda: os.environ.pop("AGY_CLI_PATH", None))

        agent = run_agent.AIAgent(
            model="Gemini 3.8 Flash (High)",
            workspace=str(REPO_ROOT),
        )

        result = agent.run_conversation("/deepmode Summarize video content", session_id="deepmode_sess_1")
        self.assertIsNotNone(result)
        self.assertEqual(result.get("status"), "completed")
        self.assertIsNone(agent._last_error)
        self.assertNotIn("error", result)

        messages = result.get("messages", [])
        assistant_msg = next((m for m in reversed(messages) if m.get("role") == "assistant"), None)
        self.assertIsNotNone(assistant_msg)
        self.assertNotIn("⚠️ Antigravity execution error", assistant_msg.get("content", ""))

    def test_ai_agent_result_error_handling(self):
        """Verify AIAgent captures result errors from stdout without silent failure."""
        os.environ["AGY_CLI_PATH"] = str(MOCK_AGY)
        self.addCleanup(lambda: os.environ.pop("AGY_CLI_PATH", None))

        agent = run_agent.AIAgent(
            model="Gemini 3.8 Flash (High)",
            workspace=str(REPO_ROOT),
        )

        result = agent.run_conversation("TRIGGER_RESULT_ERROR", session_id="err_sess_1")
        self.assertIsNotNone(result)
        self.assertEqual(result.get("status"), "error")
        self.assertIn("simulated antigravity provider failure", result.get("error", ""))
        self.assertIn("simulated antigravity provider failure", agent._last_error)

        messages = result.get("messages", [])
        assistant_msg = next((m for m in reversed(messages) if m.get("role") == "assistant"), None)
        self.assertIsNotNone(assistant_msg)
        self.assertIn("⚠️ Antigravity execution error", assistant_msg.get("content", ""))


if __name__ == "__main__":
    unittest.main()
