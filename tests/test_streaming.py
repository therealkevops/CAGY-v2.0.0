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
    StreamingCallbacks,
    StreamingUsageCollector,
    _attempt_stream_credential_retry,
    _compact_for_echo_compare,
    _extract_max_iterations_cfg,
    _extract_max_tokens_cfg,
    _extract_reasoning_config,
    _handle_cancelled_turn,
    _make_agent_status_callback,
    _parse_fallback_entries,
    _phase_execute_agent,
    _phase_execute_ephemeral,
    _phase_finalize_writeback,
    _phase_prepare_context,
    _run_agent_streaming,
    _sse,
    _sse_set_write_deadline,
    _start_periodic_checkpoint,
    _stop_periodic_checkpoint,
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


class TestStreamingCallbacks(unittest.TestCase):
    """Test StreamingCallbacks event dispatch, echo stripping, and agent kwargs binding."""

    def test_streaming_callbacks_token_handling(self):
        """Verify on_token appends to partial buffer and emits token event."""
        events = []
        stream_id = "test-stream-cb-token"
        ctx = StreamTurnContext(
            session_id="sess-cb-1",
            msg_text="hello",
            model="gemini-3.8-flash",
            workspace="/tmp/ws",
            stream_id=stream_id,
            put=lambda ev, data: events.append((ev, data)),
        )
        with STREAMS_LOCK:
            config.STREAM_PARTIAL_TEXT[stream_id] = ""

        try:
            callbacks = StreamingCallbacks(ctx)
            callbacks.on_token("Hello ")
            callbacks.on_token("world!")

            self.assertTrue(callbacks.token_sent)
            self.assertTrue(ctx.token_sent)
            self.assertEqual(config.STREAM_PARTIAL_TEXT[stream_id], "Hello world!")
            token_events = [e for e in events if e[0] == "token"]
            self.assertEqual(len(token_events), 2)
            self.assertEqual(token_events[0][1]["text"], "Hello ")
            self.assertEqual(token_events[1][1]["text"], "world!")
        finally:
            with STREAMS_LOCK:
                config.STREAM_PARTIAL_TEXT.pop(stream_id, None)

    def test_streaming_callbacks_reasoning_and_echo_stripping(self):
        """Verify on_reasoning buffering, segment indexing, and echo suppression."""
        events = []
        stream_id = "test-stream-cb-reasoning"
        ctx = StreamTurnContext(
            session_id="sess-cb-2",
            msg_text="think",
            model="gemini-3.8-flash",
            workspace="/tmp/ws",
            stream_id=stream_id,
            put=lambda ev, data: events.append((ev, data)),
        )
        with STREAMS_LOCK:
            config.STREAM_REASONING_TEXT[stream_id] = ""

        try:
            callbacks = StreamingCallbacks(ctx)
            callbacks.on_reasoning("I am pondering ")
            callbacks.on_reasoning("the problem.")
            callbacks.flush_reasoning_buffer()

            self.assertIn("I am pondering the problem.", callbacks.reasoning_segments[0])
            self.assertEqual(config.STREAM_REASONING_TEXT[stream_id], "I am pondering the problem.")
            reasoning_events = [e for e in events if e[0] == "reasoning"]
            self.assertGreaterEqual(len(reasoning_events), 1)

            # Test echo stripping
            callbacks.strip_reasoning_output_echo("the problem.")
            self.assertNotIn("the problem.", callbacks.reasoning_segments.get(0, ""))
        finally:
            with STREAMS_LOCK:
                config.STREAM_REASONING_TEXT.pop(stream_id, None)

    def test_streaming_callbacks_tool_lifecycle(self):
        """Verify tool start and complete callbacks track calls and emit SSE events."""
        events = []
        stream_id = "test-stream-cb-tool"
        ctx = StreamTurnContext(
            session_id="sess-cb-3",
            msg_text="run tool",
            model="gemini-3.8-flash",
            workspace="/tmp/ws",
            stream_id=stream_id,
            put=lambda ev, data: events.append((ev, data)),
        )
        ctx.usage_collector = StreamingUsageCollector(ctx)
        with STREAMS_LOCK:
            config.STREAM_LIVE_TOOL_CALLS[stream_id] = []

        try:
            callbacks = StreamingCallbacks(ctx)
            callbacks.on_tool_start("tool-001", "bash", {"command": "echo 123"})
            self.assertEqual(len(callbacks.live_tool_calls), 1)
            self.assertEqual(callbacks.live_tool_calls[0]["name"], "bash")
            self.assertEqual(callbacks.live_tool_calls[0]["tid"], "tool-001")

            callbacks.on_tool_complete("tool-001", "bash", {"command": "echo 123"}, "123\n")
            self.assertTrue(callbacks.live_tool_calls[0]["done"])
            self.assertEqual(callbacks.live_tool_calls[0]["snippet"], "123\n")
            self.assertEqual(ctx.checkpoint_activity[0], 1)

            event_names = [e[0] for e in events]
            self.assertIn("tool", event_names)
            self.assertIn("tool_complete", event_names)
        finally:
            with STREAMS_LOCK:
                config.STREAM_LIVE_TOOL_CALLS.pop(stream_id, None)

    def test_streaming_callbacks_bind_to_agent_kwargs(self):
        """Verify bind_to_agent_kwargs wires all supported callback signatures."""
        ctx = StreamTurnContext(
            session_id="sess-cb-4",
            msg_text="bind",
            model="gemini-3.8-flash",
            workspace="/tmp/ws",
            stream_id="stream-cb-4",
        )
        callbacks = StreamingCallbacks(ctx)
        agent_kwargs = {}
        agent_params = {
            "stream_delta_callback",
            "reasoning_callback",
            "tool_progress_callback",
            "interim_assistant_callback",
            "tool_start_callback",
            "tool_complete_callback",
        }
        callbacks.bind_to_agent_kwargs(agent_kwargs, agent_params)

        self.assertEqual(agent_kwargs["stream_delta_callback"], callbacks.on_token)
        self.assertEqual(agent_kwargs["reasoning_callback"], callbacks.on_reasoning)
        self.assertEqual(agent_kwargs["tool_progress_callback"], callbacks.on_tool)
        self.assertEqual(agent_kwargs["interim_assistant_callback"], callbacks.on_interim_assistant)
        self.assertEqual(agent_kwargs["tool_start_callback"], callbacks.on_tool_start)
        self.assertEqual(agent_kwargs["tool_complete_callback"], callbacks.on_tool_complete)


class TestPhasePrepareContextAndCheckpoints(unittest.TestCase):
    """Hermetic unit tests for Sprint D3 extracted context preparation and checkpoint routines."""

    def test_extract_config_helpers(self):
        """Verify max_turns, max_tokens, and fallback parsing helpers."""
        # max_iterations
        self.assertEqual(_extract_max_iterations_cfg({"agent": {"max_turns": 42}}), 42)
        self.assertEqual(_extract_max_iterations_cfg({"max_turns": 15}), 15)
        self.assertIsNone(_extract_max_iterations_cfg({"agent": {"max_turns": -5}}))
        self.assertIsNone(_extract_max_iterations_cfg({}))

        # max_tokens
        self.assertEqual(_extract_max_tokens_cfg({"agent": {"max_tokens": 4096}}), 4096)
        self.assertEqual(_extract_max_tokens_cfg({"max_tokens": "2048"}), 2048)
        self.assertIsNone(_extract_max_tokens_cfg({"max_tokens": 0}))

        # fallback entries
        raw_list = [
            {"provider": "anthropic", "model": "claude-3-5-sonnet", "base_url": "https://api.anthropic.com"},
            {"provider": "", "model": "no-provider"},
            "invalid-entry",
        ]
        parsed = _parse_fallback_entries(raw_list)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["provider"], "anthropic")
        self.assertEqual(parsed[0]["model"], "claude-3-5-sonnet")

    def test_agent_status_callback_events(self):
        """Verify _make_agent_status_callback captures terminal errors and emits compressing/warning."""
        events = []
        ctx = StreamTurnContext(
            session_id="sess-status-1",
            msg_text="test",
            model="gemini-3.8-flash",
            workspace="/tmp/ws",
            stream_id="stream-status-1",
            put=lambda ev, data: events.append((ev, data)),
        )
        cb = _make_agent_status_callback(ctx)

        # Non-retryable HTTP error capture
        cb("error", "❌ Non-retryable error (HTTP 401): Invalid API key provided")
        self.assertIn("Non-retryable error (HTTP 401)", ctx.captured_terminal_error[0])

        # Compression event
        cb("lifecycle", "Compacting context (5000 tokens)")
        self.assertTrue(any(ev[0] == "compressing" for ev in events))

        # Fallback notice event
        cb("lifecycle", "Falling back to secondary model gemini-3.8-pro")
        self.assertTrue(any(ev[0] == "warning" and ev[1].get("type") == "fallback" for ev in events))

    def test_periodic_checkpoint_lifecycle(self):
        """Verify _start_periodic_checkpoint starts a thread and _stop_periodic_checkpoint joins it."""
        mock_session = MagicMock()
        ctx = StreamTurnContext(
            session_id="sess-ckpt-1",
            msg_text="test",
            model="gemini-3.8-flash",
            workspace="/tmp/ws",
            stream_id="stream-ckpt-1",
            s=mock_session,
            agent_lock=threading.Lock(),
        )
        _start_periodic_checkpoint(ctx)
        self.assertIsNotNone(ctx.ckpt_thread)
        self.assertTrue(ctx.ckpt_thread.is_alive())
        self.assertTrue(ctx.ckpt_thread.name.startswith("ckpt-sess-ckp"))

        # Stop and join
        _stop_periodic_checkpoint(ctx, timeout=2.0)
        self.assertTrue(ctx.checkpoint_stop.is_set())
        self.assertFalse(ctx.ckpt_thread.is_alive())

    def test_phase_prepare_context_preflight_cancel(self):
        """Verify _phase_prepare_context catches pre-flight cancel and exits cleanly."""
        cancel_evt = threading.Event()
        cancel_evt.set()  # Pre-cancelled
        events = []
        mock_session = MagicMock()
        mock_session.workspace = "/tmp/ws"

        ctx = StreamTurnContext(
            session_id="sess-cancel-1",
            msg_text="cancelled",
            model="gemini-3.8-flash",
            workspace="/tmp/ws",
            stream_id="stream-cancel-1",
            cancel_event=cancel_evt,
            put=lambda ev, data: events.append((ev, data)),
        )

        with unittest.mock.patch("api.streaming.get_session", return_value=mock_session), \
             unittest.mock.patch("api.streaming._get_session_agent_lock", return_value=threading.Lock()), \
             unittest.mock.patch("api.streaming._finalize_cancelled_turn") as mock_finalize:
            res = _phase_prepare_context(ctx)
            self.assertFalse(res)
            mock_finalize.assert_called_once()
            self.assertTrue(any(ev[0] == "cancel" for ev in events))


class TestPhaseExecuteAgentAndEphemeral(unittest.TestCase):
    """Test Sprint D4 core agent execution loop and ephemeral (/btw) branches."""

    def test_phase_execute_agent_normal(self):
        """Verify _phase_execute_agent executes agent, flushes buffer, and sets ctx.result."""
        mock_agent = MagicMock()
        mock_agent.run_conversation.return_value = {
            "messages": [{"role": "assistant", "content": "Hello from agent!"}]
        }
        mock_callbacks = MagicMock()
        ctx = StreamTurnContext(
            session_id="sess-exec-1",
            msg_text="hello",
            model="gemini-3.8-flash",
            workspace="/tmp/ws",
            stream_id="stream-exec-1",
            agent=mock_agent,
            callbacks=mock_callbacks,
            cancel_event=threading.Event(),
        )

        res = _phase_execute_agent(ctx)
        self.assertTrue(res)
        self.assertIsNotNone(ctx.result)
        self.assertEqual(ctx.result["messages"][0]["content"], "Hello from agent!")
        mock_callbacks.flush_reasoning_buffer.assert_called_once()

    def test_phase_execute_agent_cancelled(self):
        """Verify _phase_execute_agent handles cancellation, emits SSE cancel, and returns False."""
        mock_agent = MagicMock()
        mock_agent.run_conversation.return_value = {"messages": []}
        cancel_evt = threading.Event()
        cancel_evt.set()
        events = []
        mock_session = MagicMock()
        mock_session.session_id = "sess-cancel-exec-1"

        ctx = StreamTurnContext(
            session_id="sess-cancel-exec-1",
            msg_text="hello",
            model="gemini-3.8-flash",
            workspace="/tmp/ws",
            stream_id="stream-cancel-exec-1",
            s=mock_session,
            agent=mock_agent,
            agent_lock=threading.Lock(),
            cancel_event=cancel_evt,
            put=lambda ev, d: events.append((ev, d)),
        )

        with unittest.mock.patch("api.streaming._finalize_cancelled_turn") as mock_finalize, \
             unittest.mock.patch("api.streaming.append_turn_journal_event_for_stream") as mock_journal:
            res = _phase_execute_agent(ctx)
            self.assertFalse(res)
            mock_finalize.assert_called_once()
            mock_journal.assert_called_once()
            self.assertTrue(any(ev[0] == "cancel" for ev in events))

    def test_phase_execute_ephemeral_normal(self):
        """Verify _phase_execute_ephemeral extracts answer, emits done SSE, and unlinks session file."""
        mock_agent = MagicMock()
        mock_agent.run_conversation.return_value = {
            "messages": [
                {"role": "user", "content": "/btw what is pi?"},
                {"role": "assistant", "content": "pi is approximately 3.14159"},
            ]
        }
        events = []
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp_f:
            tmp_path = tmp_f.name

        mock_session = MagicMock()
        mock_session.path = tmp_path

        ctx = StreamTurnContext(
            session_id="sess-eph-1",
            msg_text="/btw what is pi?",
            model="gemini-3.8-flash",
            workspace="/tmp/ws",
            stream_id="stream-eph-1",
            s=mock_session,
            agent=mock_agent,
            ephemeral=True,
            cancel_event=threading.Event(),
            put=lambda ev, d: events.append((ev, d)),
        )

        try:
            _phase_execute_ephemeral(ctx)
            done_events = [ev for ev in events if ev[0] == "done"]
            self.assertEqual(len(done_events), 1)
            self.assertTrue(done_events[0][1]["ephemeral"])
            self.assertEqual(done_events[0][1]["answer"], "pi is approximately 3.14159")
            self.assertFalse(os.path.exists(tmp_path))
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def test_phase_execute_ephemeral_cancelled(self):
        """Verify _phase_execute_ephemeral aborts cleanly if cancelled."""
        mock_agent = MagicMock()
        mock_agent.run_conversation.return_value = {"messages": []}
        cancel_evt = threading.Event()
        cancel_evt.set()
        events = []

        ctx = StreamTurnContext(
            session_id="sess-eph-cancel-1",
            msg_text="/btw cancelled",
            model="gemini-3.8-flash",
            workspace="/tmp/ws",
            stream_id="stream-eph-cancel-1",
            agent=mock_agent,
            ephemeral=True,
            cancel_event=cancel_evt,
            put=lambda ev, d: events.append((ev, d)),
        )

        with unittest.mock.patch("api.streaming._finalize_cancelled_turn") as mock_finalize:
            _phase_execute_ephemeral(ctx)
            mock_finalize.assert_called_once()
            self.assertTrue(any(ev[0] == "cancel" for ev in events))
            self.assertFalse(any(ev[0] == "done" for ev in events))

    def test_attempt_stream_credential_retry_no_heal(self):
        """Verify _attempt_stream_credential_retry returns failure when heal_rt is None."""
        ctx = StreamTurnContext(
            session_id="sess-retry-1",
            msg_text="hello",
            model="gemini-3.8-flash",
            workspace="/tmp/ws",
            stream_id="stream-retry-1",
        )
        with unittest.mock.patch("api.streaming._attempt_credential_self_heal", return_value=None):
            ok, res, stale = _attempt_stream_credential_retry(ctx)
            self.assertFalse(ok)
            self.assertIsNone(res)
            self.assertIsNone(stale)


class TestPhaseFinalizeWriteback(unittest.TestCase):
    """Unit tests for _phase_finalize_writeback post-run persistence and event dispatch."""

    def test_finalize_writeback_stale_stream_skips(self):
        """Verify stale stream writeback returns early without emitting events or mutating state."""
        events = []
        mock_session = MagicMock()
        mock_session.active_stream_id = "stream-newer-999"
        mock_session.session_id = "sess-stale-1"

        ctx = StreamTurnContext(
            session_id="sess-stale-1",
            msg_text="hello",
            model="gemini-3.8-flash",
            workspace="/tmp/ws",
            stream_id="stream-old-111",
            s=mock_session,
            put=lambda ev, d: events.append((ev, d)),
        )

        _phase_finalize_writeback(ctx)
        self.assertFalse(ctx.success_writeback_committed)
        self.assertEqual(len(events), 0)

    def test_finalize_writeback_cancelled_returns_cancel_event(self):
        """Verify cancelled event causes clean early exit with cancel event emission."""
        events = []
        cancel_evt = threading.Event()
        cancel_evt.set()

        mock_session = MagicMock()
        mock_session.active_stream_id = "stream-cancel-1"
        mock_session.session_id = "sess-cancel-1"
        mock_session.messages = [{"role": "user", "content": "cancel me"}]

        ctx = StreamTurnContext(
            session_id="sess-cancel-1",
            msg_text="cancel me",
            model="gemini-3.8-flash",
            workspace="/tmp/ws",
            stream_id="stream-cancel-1",
            s=mock_session,
            cancel_event=cancel_evt,
            put=lambda ev, d: events.append((ev, d)),
        )

        with unittest.mock.patch("api.streaming._finalize_cancelled_turn") as mock_finalize:
            _phase_finalize_writeback(ctx)
            self.assertFalse(ctx.success_writeback_committed)
            mock_finalize.assert_called_once()
            self.assertTrue(any(ev[0] == "cancel" for ev in events))
            self.assertFalse(any(ev[0] == "done" for ev in events))

    def test_finalize_writeback_success_flow(self):
        """Verify normal writeback flow commits session state and delivers done/metering/stream_end."""
        events = []
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp_f:
            tmp_path = tmp_f.name

        session_id = "sess-success-1"
        stream_id = "stream-success-1"
        s = Session(session_id=session_id, path=tmp_path)
        s.active_stream_id = stream_id
        s.title = "Established Title"
        s.llm_title_generated = True
        s.messages = [{"role": "user", "content": "hello"}]

        mock_agent = MagicMock()
        mock_agent.session_id = session_id
        mock_agent.context_compressor = None
        mock_agent.session_prompt_tokens = 100
        mock_agent.session_completion_tokens = 50
        mock_agent.session_estimated_cost_usd = 0.001
        mock_agent.session_cache_read_tokens = 0
        mock_agent.session_cache_write_tokens = 0
        mock_agent.model = "gemini-3.8-flash"
        mock_agent._last_error = None

        ctx = StreamTurnContext(
            session_id=session_id,
            msg_text="hello",
            model="gemini-3.8-flash",
            workspace="/tmp/ws",
            stream_id=stream_id,
            s=s,
            agent=mock_agent,
            result={
                "messages": [
                    {"role": "user", "content": "hello"},
                    {"role": "assistant", "content": "world response"},
                ],
                "iterations": 1,
            },
            put=lambda ev, d: events.append((ev, d)),
        )
        ctx.usage_collector = StreamingUsageCollector(ctx)

        try:
            with unittest.mock.patch("api.streaming.append_turn_journal_event_for_stream"):
                _phase_finalize_writeback(ctx)

            self.assertTrue(ctx.success_writeback_committed)
            event_types = [ev[0] for ev in events]
            self.assertIn("done", event_types)
            self.assertIn("metering", event_types)
            self.assertIn("stream_end", event_types)

            done_payload = next(ev[1] for ev in events if ev[0] == "done")
            self.assertIn("session", done_payload)
            self.assertIn("usage", done_payload)
            # Assistant reply was incorporated into session messages
            self.assertTrue(any(m.get("content") == "world response" for m in s.messages if isinstance(m, dict)))
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def test_finalize_writeback_context_compression_migration(self):
        """Verify writeback handles context compression session migration and anchor calculations."""
        events = []
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp_f:
            tmp_path = tmp_f.name

        old_sid = "sess-orig-1"
        new_sid = "sess-cont-2"
        stream_id = "stream-comp-1"
        s = Session(session_id=old_sid, path=tmp_path)
        s.active_stream_id = stream_id
        s.messages = [
            {"role": "user", "content": "msg 1"},
            {"role": "assistant", "content": "resp 1"},
            {"role": "system", "content": "Summary of previous context", "_type": "context_compression_marker"},
            {"role": "user", "content": "msg 2"},
            {"role": "assistant", "content": "resp 2"},
        ]

        mock_agent = MagicMock()
        mock_agent.session_id = new_sid  # Agent migrated session ID during compression
        mock_agent.context_compressor = MagicMock()
        mock_agent.context_compressor.compression_count = 1
        mock_agent.context_compressor.context_length = 128000
        mock_agent.context_compressor.threshold_tokens = 100000
        mock_agent.context_compressor.last_prompt_tokens = 0
        mock_agent.model = "gemini-3.8-flash"
        mock_agent.session_prompt_tokens = 100
        mock_agent.session_completion_tokens = 50
        mock_agent.session_estimated_cost_usd = 0.001
        mock_agent.session_cache_read_tokens = 0
        mock_agent.session_cache_write_tokens = 0
        mock_agent._last_error = None

        ctx = StreamTurnContext(
            session_id=old_sid,
            msg_text="msg 2",
            model="gemini-3.8-flash",
            workspace="/tmp/ws",
            stream_id=stream_id,
            s=s,
            agent=mock_agent,
            result={"messages": s.messages, "iterations": 1},
            pre_compression_count=0,
            put=lambda ev, d: events.append((ev, d)),
        )
        ctx.usage_collector = StreamingUsageCollector(ctx)

        try:
            with unittest.mock.patch("api.streaming.append_turn_journal_event_for_stream"):
                _phase_finalize_writeback(ctx)

            self.assertTrue(ctx.success_writeback_committed)
            self.assertEqual(s.session_id, new_sid)
            self.assertEqual(s.parent_session_id, old_sid)
            self.assertIsNotNone(s.compression_anchor_visible_idx)
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)


if __name__ == "__main__":
    unittest.main()

