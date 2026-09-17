"""Tests for webui/api/streaming.py SSE engine, stream management, and agent runner."""
import io
import json
import os
import queue
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock

REPO_ROOT = Path(__file__).resolve().parent.parent
WEBUI_DIR = REPO_ROOT / "webui"
MOCK_AGY = REPO_ROOT / "tests" / "mock_agy.py"

if str(WEBUI_DIR) not in sys.path:
    sys.path.insert(0, str(WEBUI_DIR))

from api import config
from api.config import (
    CANCEL_FLAGS,
    STREAMS,
    STREAMS_LOCK,
)
from api.models import Session
from api.streaming import (
    StreamTurnContext,
    StreamingUsageCollector,
    _compact_for_echo_compare,
    _run_agent_streaming,
    _sse,
    _sse_set_write_deadline,
    _strip_compact_echo_suffix,
    cancel_stream,
    get_stream_runtime_snapshot,
)


class DummySSEHandler:
    """Mock HTTP request handler exposing a writable wfile."""

    def __init__(self, connection=None):
        self.wfile = io.BytesIO()
        self.connection = connection


class TestSSEFormatting(unittest.TestCase):
    """Test Server-Sent Events serialization and socket timeout handling."""

    def test_sse_basic_message(self):
        """Verify _sse writes valid SSE event protocol framing."""
        handler = DummySSEHandler()
        _sse(handler, "token", {"delta": "hello"})
        output = handler.wfile.getvalue().decode("utf-8")
        self.assertTrue(output.startswith("event: token\n"))
        self.assertIn('data: {"delta": "hello"}', output)
        self.assertTrue(output.endswith("\n\n"))

    def test_sse_unicode_preservation(self):
        """Verify _sse preserves raw UTF-8 without ascii escapes."""
        handler = DummySSEHandler()
        _sse(handler, "message", {"text": "你好 🚀 world"})
        output = handler.wfile.getvalue().decode("utf-8")
        self.assertIn("你好 🚀 world", output)
        self.assertNotIn("\\u", output)

    def test_sse_set_write_deadline_with_mock_socket(self):
        """Verify _sse_set_write_deadline configures connection socket timeout."""
        mock_socket = MagicMock()
        handler = DummySSEHandler(connection=mock_socket)
        _sse_set_write_deadline(handler, seconds=7.5)
        mock_socket.settimeout.assert_called_once_with(7.5)

    def test_sse_set_write_deadline_graceful_on_missing_socket(self):
        """Verify _sse_set_write_deadline degrades gracefully when connection lacks settimeout."""
        handler = DummySSEHandler(connection=object())
        # Should not raise any exception
        _sse_set_write_deadline(handler, seconds=5.0)


class TestStreamingStateAndControl(unittest.TestCase):
    """Test stream registry, cancellation, and runtime snapshot telemetry."""

    def test_stream_runtime_snapshot(self):
        """Verify get_stream_runtime_snapshot returns non-blocking telemetry."""
        snapshot = get_stream_runtime_snapshot()
        self.assertIsInstance(snapshot, dict)
        self.assertIn("available", snapshot)
        self.assertIn("active", snapshot)
        self.assertIn("agent_instances", snapshot)

    def test_cancel_stream_inactive(self):
        """Verify cancel_stream returns False when stream_id does not exist."""
        self.assertFalse(cancel_stream("stream-does-not-exist-xyz"))

    def test_cancel_stream_active(self):
        """Verify cancel_stream flags cancellation and cleans up state."""
        stream_id = "test-stream-cancel-001"
        q = queue.Queue()
        cancel_evt = threading.Event()

        with STREAMS_LOCK:
            STREAMS[stream_id] = q
            CANCEL_FLAGS[stream_id] = cancel_evt

        try:
            result = cancel_stream(stream_id)
            self.assertTrue(result)
            self.assertTrue(cancel_evt.is_set())
        finally:
            with STREAMS_LOCK:
                STREAMS.pop(stream_id, None)
                CANCEL_FLAGS.pop(stream_id, None)


class TestStreamingHelpers(unittest.TestCase):
    """Test text compaction and echo suffix stripping helpers."""

    def test_compact_for_echo_compare(self):
        """Verify whitespace normalization and case handling."""
        raw = "  Hello,   World! \n\t"
        compacted = _compact_for_echo_compare(raw)
        self.assertEqual(compacted, "Hello,World!")

    def test_strip_compact_echo_suffix(self):
        """Verify trailing echo of prompt text is stripped from model response."""
        response = "The answer is 42. What is the answer?"
        prompt = "What is the answer?"
        stripped, did_strip = _strip_compact_echo_suffix(response, prompt)
        self.assertTrue(did_strip)
        self.assertEqual(stripped, "The answer is 42.")

    def test_strip_compact_echo_suffix_no_match(self):
        """Verify unmodified return when suffix is not present."""
        response = "Here is the response without echo."
        prompt = "Unrelated prompt"
        stripped, did_strip = _strip_compact_echo_suffix(response, prompt)
        self.assertFalse(did_strip)
        self.assertEqual(stripped, response)


class TestStreamingExecutionLifecycle(unittest.TestCase):
    """Test _run_agent_streaming thread entrypoint and mock execution."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.prev_session_dir = config.SESSION_DIR
        config.SESSION_DIR = Path(self.test_dir)
        self.prev_cli_path = os.environ.get("AGY_CLI_PATH")
        os.environ["AGY_CLI_PATH"] = str(MOCK_AGY)

    def tearDown(self):
        config.SESSION_DIR = self.prev_session_dir
        if self.prev_cli_path is not None:
            os.environ["AGY_CLI_PATH"] = self.prev_cli_path
        else:
            os.environ.pop("AGY_CLI_PATH", None)
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_run_agent_streaming_aborts_when_stream_not_registered(self):
        """Verify _run_agent_streaming exits immediately if stream_id not in STREAMS."""
        # Not in STREAMS: should exit cleanly without raising
        _run_agent_streaming(
            "nonexistent-sess",
            "hello",
            "gemini-3.8-flash",
            self.test_dir,
            "unregistered-stream-id",
        )

    def test_run_agent_streaming_emits_events_with_mock_cli(self):
        """Verify _run_agent_streaming processes turn and posts SSE tuples to queue."""
        session_id = "test-sess-stream-001"
        sess = Session(session_id=session_id, messages=[])
        sess.save()

        stream_id = "test-stream-events-001"
        q = queue.Queue()
        with STREAMS_LOCK:
            STREAMS[stream_id] = q

        try:
            _run_agent_streaming(
                session_id,
                "hello integration test",
                "gemini-3.8-flash",
                self.test_dir,
                stream_id,
                ephemeral=False,
            )

            events = []
            while not q.empty():
                events.append(q.get_nowait())

            self.assertGreaterEqual(len(events), 2)
            event_names = [e[0] for e in events if isinstance(e, tuple)]
            self.assertIn("token", event_names)
            # Find token event and verify mock response payload
            token_evt = next((e for e in events if isinstance(e, tuple) and e[0] == "token"), None)
            self.assertIsNotNone(token_evt)
            self.assertIn("Mock reply to:", token_evt[1].get("text", ""))
        finally:
            with STREAMS_LOCK:
                STREAMS.pop(stream_id, None)


class TestStreamTurnContextAndUsageCollector(unittest.TestCase):
    """Test StreamTurnContext data encapsulation and StreamingUsageCollector telemetry."""

    def test_stream_turn_context_defaults_and_cancellation(self):
        """Verify StreamTurnContext initialization, fields, and cancel check."""
        ctx = StreamTurnContext(
            session_id="sess-001",
            msg_text="test prompt",
            model="gemini-3.8-flash",
            workspace="/tmp/ws",
            stream_id="stream-001",
        )
        self.assertEqual(ctx.session_id, "sess-001")
        self.assertEqual(ctx.msg_text, "test prompt")
        self.assertEqual(ctx.model, "gemini-3.8-flash")
        self.assertEqual(ctx.workspace, "/tmp/ws")
        self.assertEqual(ctx.stream_id, "stream-001")
        self.assertFalse(ctx.ephemeral)
        self.assertFalse(ctx.is_cancelled())

        cancel_evt = threading.Event()
        ctx.cancel_event = cancel_evt
        self.assertFalse(ctx.is_cancelled())
        cancel_evt.set()
        self.assertTrue(ctx.is_cancelled())

    def test_streaming_usage_collector_estimates_and_tool_tracking(self):
        """Verify prompt seeding, tool delta bumps, and seen_tool_call_ids."""
        ctx = StreamTurnContext(
            session_id="sess-002",
            msg_text="hello",
            model="gemini-3.8-flash",
            workspace="/tmp/ws",
            stream_id="stream-002",
        )
        collector = StreamingUsageCollector(ctx)

        # Initially 0
        self.assertEqual(collector.seed_live_prompt_estimate(), 0)

        # Bump with tool messages (mocking _bounded_live_tool_prompt_delta for hermetic unit testing)
        with unittest.mock.patch("api.streaming._bounded_live_tool_prompt_delta", return_value=42):
            bumped = collector.bump_live_prompt_estimate([{"role": "tool", "content": "file contents"}])
            self.assertEqual(bumped, 42)
            self.assertEqual(collector.live_prompt_estimate_tokens[0], 42)

        # Seen tool IDs tracking
        self.assertNotIn("call-1", collector.seen_tool_call_ids)
        collector.seen_tool_call_ids.add("call-1")
        self.assertIn("call-1", collector.seen_tool_call_ids)

    def test_streaming_usage_collector_snapshot_with_agent_mock(self):
        """Verify snapshot extraction from agent and session attributes."""
        ctx = StreamTurnContext(
            session_id="sess-003",
            msg_text="telemetry test",
            model="gemini-3.8-flash",
            workspace="/tmp/ws",
            stream_id="stream-003",
        )
        collector = StreamingUsageCollector(ctx)

        # Mock agent attributes
        mock_agent = MagicMock()
        mock_agent.session_prompt_tokens = 1500
        mock_agent.session_completion_tokens = 320
        mock_agent.session_estimated_cost_usd = 0.0045
        mock_agent.session_cache_read_tokens = 500
        mock_agent.session_cache_write_tokens = 200
        mock_agent.context_compressor = None
        ctx.agent = mock_agent

        snapshot = collector.snapshot()
        self.assertEqual(snapshot["input_tokens"], 1500)
        self.assertEqual(snapshot["output_tokens"], 320)
        self.assertEqual(snapshot["estimated_cost"], 0.0045)
        self.assertEqual(snapshot["cache_read_tokens"], 500)
        self.assertEqual(snapshot["cache_write_tokens"], 200)
        self.assertIn("cache_hit_percent", snapshot)


if __name__ == "__main__":
    unittest.main()
