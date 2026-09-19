"""Chat execution, streaming, terminal output, approvals, and clarifiers routes.

Decomposed from routes.py (Sprint R5, Sprint M2.1, Sprint M2.2).
"""
import copy
import logging
import os
import queue
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from urllib.parse import parse_qs

from api.agent_runtime import AgentRuntimeChangedError, ensure_agent_runtime_current, require_ai_agent_class
from api.background_process import get_session_channel
from api.compression_recovery import (
    COMPRESSION_RECOVERY_ACTION_START_FOCUSED,
    clear_compression_recovery,
    compression_recovery_payload_for_session,
    is_generic_continuation_intent,
)
from api.config import (
    CHAT_LOCK,
    LOCK,
    PENDING_BG_TASK_COMPLETIONS,
    PENDING_GOAL_CONTINUATION,
    STREAM_LAST_EVENT_ID,
    STREAMS,
    STREAMS_LOCK,
    STREAM_GOAL_RELATED,
    _get_session_agent_lock,
    _resolve_cli_toolsets,
    canonical_model_provider_lane,
    clear_session_writeback_owner_if_owned,
    create_stream_channel,
    get_config,
    get_config_snapshot,
    get_webui_session_save_mode,
    load_settings,
    model_with_provider_context,
    register_session_writeback_owner,
    register_stream_owner,
    stream_owner_session_id,
    unregister_stream_owner,
)
from api.gateway_chat import webui_gateway_chat_enabled, _run_gateway_chat_streaming
from api.helpers import (
    _CLIENT_DISCONNECT_ERRORS,
    _sanitize_error,
    bad,
    j,
    public_session_projection,
    redact_session_data,
    require,
)
from api.models import (
    PROCESS_WAKEUP_PAUSE_ERROR,
    SESSIONS,
    SESSION_DIR,
    Session,
    WorkspaceBindingPersistenceError,
    _REPAIR_STALE_PENDING_GRACE_SECONDS,
    _evict_sessions_over_cap,
    clear_process_wakeup_pause,
    clear_process_wakeup_pause_if_model_changed,
    find_compression_recovery_session,
    get_effective_default_model,
    get_session,
    persist_recovered_workspace_binding,
    process_wakeup_credential_state_fingerprint,
    process_wakeup_pause_credential_state_changed,
    process_wakeup_pause_matches,
    suppress_process_wakeup_for_provider_pause,
    title_from,
)
from api.oauth import poll_onboarding_oauth_flow
from api.profiles import (
    _PROFILE_ID_RE,
    get_active_profile_name as _get_active_profile_name,
    _is_root_profile,
    _profiles_match,
    profile_scope_for_detached_worker,
)
from api.providers import provider_has_process_wakeup_recovery_credential
from api.route_approvals import retire_gateway_pending_mirror
from api.run_journal import (
    _parse_run_journal_event_id as _shared_parse_run_journal_event_id,
    find_run_summary,
    read_run_events,
    read_session_run_events,
    session_journal_fingerprint,
    stale_interrupted_event,
    SSE_RELAY_CLOSE_EVENTS,
)
from api.runner_client import HttpRunnerClient
from api.runtime_adapter import (
    LegacyJournalRuntimeAdapter,
    StartRunRequest,
    build_runtime_adapter,
    runtime_adapter_enabled,
    runtime_adapter_runner_enabled,
)
from api.session_events import publish_session_list_changed
from api.sse_chunked import end_sse_headers
from api.streaming import (
    _ENV_LOCK,
    _run_agent_streaming,
    _sse,
    _sse_set_write_deadline,
    cancel_stream,
)
from api.workspace import (
    _is_remote_terminal_backend,
    get_last_workspace,
    resolve_implicit_workspace_with_recovery,
    resolve_trusted_workspace,
    set_last_workspace,
)

logger = logging.getLogger(__name__)

_SSE_HEARTBEAT_INTERVAL_SECONDS = 5
_SESSION_SSE_SENT_EVENT_ID_LIMIT = 4096

_REMOTE_TERMINAL_BACKEND_UNSUPPORTED_ERROR = "remote_terminal_backend_unsupported"
_REMOTE_TERMINAL_BACKEND_UNSUPPORTED_MESSAGE = (
    "Embedded terminal is only supported for local terminal backends."
)
_EMBEDDED_TERMINAL_GATE_DENIED_MESSAGE = (
    "Embedded terminal is only available from local networks when authentication "
    "is not configured. Configure a password/passkey, or set "
    "HERMES_WEBUI_ONBOARDING_OPEN=1 to allow it on a deliberately-exposed server."
)

_COMPRESSION_RECOVERY_START_LOCK = threading.Lock()


def _embedded_terminal_gate_allows(handler) -> bool:
    from api import routes as _routes
    return _routes._embedded_terminal_gate_allows(handler)


def _stream_id_visible_to_request_profile(
    handler,
    stream_id: str | None,
    *,
    emit_error: bool = True,
) -> bool:
    from api import routes as _routes
    return _routes._stream_id_visible_to_request_profile(handler, stream_id, emit_error=emit_error)


def _session_id_visible_to_request_profile(
    handler,
    session_id: str | None,
    *,
    emit_error: bool = True,
) -> bool:
    from api import routes as _routes
    return _routes._session_id_visible_to_request_profile(handler, session_id, emit_error=emit_error)


def _claim_or_synthesize_cli_session(*args, **kwargs):
    from api import routes as _routes
    return _routes._claim_or_synthesize_cli_session(*args, **kwargs)


def _clean_session_model_provider(*args, **kwargs):
    from api import routes as _routes
    return _routes._clean_session_model_provider(*args, **kwargs)


def _clear_stale_stream_state(*args, **kwargs):
    from api import routes as _routes
    return _routes._clear_stale_stream_state(*args, **kwargs)


def _get_or_materialize_session(*args, **kwargs):
    from api import routes as _routes
    return _routes._get_or_materialize_session(*args, **kwargs)


def _moa_fast_path_model_state(*args, **kwargs):
    from api import routes as _routes
    return _routes._moa_fast_path_model_state(*args, **kwargs)


def _read_profile_model_config(*args, **kwargs):
    from api import routes as _routes
    return _routes._read_profile_model_config(*args, **kwargs)


def _repair_foreign_session_model_provider(*args, **kwargs):
    from api import routes as _routes
    return _routes._repair_foreign_session_model_provider(*args, **kwargs)


def _resolve_compatible_session_model_state(*args, **kwargs):
    from api import routes as _routes
    return _routes._resolve_compatible_session_model_state(*args, **kwargs)


def _session_is_subagent_view_only(*args, **kwargs):
    from api import routes as _routes
    return _routes._session_is_subagent_view_only(*args, **kwargs)


def _session_visible_to_active_profile(*args, **kwargs):
    from api import routes as _routes
    return _routes._session_visible_to_active_profile(*args, **kwargs)

def _handle_get_chat_and_stream(handler, parsed):
    """Handle chat streaming, terminal output, approvals, and clarifiers routes.
    Returns True if handled, None if unhandled.
    """
    from api import routes as _routes

    if parsed.path == "/api/chat/stream/status":
        stream_id = parse_qs(parsed.query).get("stream_id", [""])[0]
        if not _stream_id_visible_to_request_profile(handler, stream_id):
            return True
        active = stream_id in STREAMS
        payload = {"active": active, "stream_id": stream_id, "replay_available": False}
        try:
            journal = find_run_summary(stream_id) if stream_id else None
        except Exception:
            logger.warning("Silent exception in handle_get", exc_info=True)
            journal = None
        if journal:
            payload["replay_available"] = True
            payload["journal"] = _routes._run_journal_status_payload(journal, active=active)
        return j(handler, payload)

    if parsed.path == "/api/chat/cancel":
        stream_id = parse_qs(parsed.query).get("stream_id", [""])[0]
        if not stream_id:
            return bad(handler, "stream_id required")
        if not _stream_id_visible_to_request_profile(handler, stream_id):
            return True
        gateway_stop_blocked = False
        try:
            from api.gateway_chat import (
                GATEWAY_RUN_ID_WAIT_TIMEOUT,
                stop_gateway_run,
                wait_for_gateway_run_id,
            )

            structured_gateway, run_id = wait_for_gateway_run_id(stream_id, GATEWAY_RUN_ID_WAIT_TIMEOUT)
            if not run_id and structured_gateway:
                gateway_stop_blocked = True
            if run_id:
                if stop_gateway_run(run_id):
                    owner_sid = stream_owner_session_id(stream_id)
                    if owner_sid:
                        retire_gateway_pending_mirror(owner_sid, run_id=run_id)
                else:
                    gateway_stop_blocked = True
        except Exception:
            logger.debug("Failed to stop gateway run during chat cancellation", exc_info=True)
            gateway_stop_blocked = True
        if gateway_stop_blocked:
            return j(
                handler,
                {
                    "ok": False,
                    "cancelled": False,
                    "stream_id": stream_id,
                    "error": "Gateway stop failed",
                },
                status=502,
            )

        from api.runtime_adapter import LegacyJournalRuntimeAdapter, runtime_adapter_enabled

        if runtime_adapter_enabled():
            adapter = LegacyJournalRuntimeAdapter(cancel_delegate=cancel_stream)
            cancelled = adapter.cancel_run(stream_id).accepted
        else:
            cancelled = cancel_stream(stream_id)
        return j(handler, {"ok": True, "cancelled": cancelled, "stream_id": stream_id})

    if parsed.path == "/api/chat/stream":
        return _handle_sse_stream(handler, parsed)

    if parsed.path == "/api/terminal/output":
        return _handle_terminal_output(handler, parsed)

    if parsed.path == "/api/approval/pending":
        return _routes._handle_approval_pending(handler, parsed)

    if parsed.path == "/api/approval/stream":
        return _routes._handle_approval_sse_stream(handler, parsed)

    if parsed.path == "/api/approval/inject_test":
        # Loopback-only: used by automated tests; blocked from any remote client
        if handler.client_address[0] != "127.0.0.1":
            return j(handler, {"error": "not found"}, status=404)
        return _routes._handle_approval_inject(handler, parsed)

    if parsed.path == "/api/clarify/pending":
        return _routes._handle_clarify_pending(handler, parsed)

    if parsed.path == "/api/clarify/stream":
        return _routes._handle_clarify_sse_stream(handler, parsed)

    if parsed.path == "/api/session/stream":
        return _routes._handle_session_sse_stream(handler, parsed)

    if parsed.path == "/api/clarify/inject_test":
        # Loopback-only: used by automated tests; blocked from any remote client
        if handler.client_address[0] != "127.0.0.1":
            return j(handler, {"error": "not found"}, status=404)
        return _routes._handle_clarify_inject(handler, parsed)

    if parsed.path == "/api/onboarding/oauth/poll":
        qs = parse_qs(parsed.query)
        flow_id = qs.get("flow_id", [""])[0]
        try:
            return j(
                handler,
                poll_onboarding_oauth_flow(flow_id),
                extra_headers={"Cache-Control": "no-store"},
            )
        except ValueError as e:
            return bad(handler, str(e))
        except KeyError as e:
            return bad(handler, str(e), 404)

    return None


def _handle_post_chat_and_stream(handler, parsed, body, diag=None):
    """Handle chat execution, streaming, steer commands, approvals, and terminal routes.
    Returns True if handled, None if unhandled.
    """
    from api import routes as _routes

    if parsed.path == "/api/btw":
        return _handle_btw(handler, body)

    if parsed.path == "/api/background":
        return _handle_background(handler, body)

    if parsed.path == "/api/goal":
        return _handle_goal_command(handler, body)

    if parsed.path == "/api/bg-task-complete-ack":
        return _handle_bg_task_complete_ack(handler, body)

    if parsed.path == "/api/chat/start":
        return _handle_chat_start(handler, body, diag=diag)

    if parsed.path == "/api/chat":
        return _handle_chat_sync(handler, body)

    if parsed.path == "/api/chat/steer":
        from api.streaming import _handle_chat_steer
        return _handle_chat_steer(handler, body)

    if parsed.path == "/api/terminal/start":
        return _handle_terminal_start(handler, body)

    if parsed.path == "/api/terminal/input":
        return _handle_terminal_input(handler, body)

    if parsed.path == "/api/terminal/resize":
        return _handle_terminal_resize(handler, body)

    if parsed.path == "/api/terminal/close":
        return _handle_terminal_close(handler, body)

    # ── Cron API (POST) ──
    # See GET-side comment above: wrap in cron_profile_context so writes go
    # to the TLS-active profile's jobs.json instead of the process default.
    if parsed.path == "/api/approval/respond":
        return _routes._handle_approval_respond(handler, body)

    # ── Clarify (POST) ──
    if parsed.path == "/api/clarify/respond":
        return _routes._handle_clarify_respond(handler, body)

    # ── Commands (POST) ──
    return None

def _sse_with_id(handler, event, data, event_id=None):
    if event_id:
        handler.wfile.write(f"id: {event_id}\n".encode("utf-8"))
    _sse(handler, event, data)


def _session_events_path_session_id(path: str | None) -> str | None:
    path = str(path or "")
    parts = path.strip("/").split("/")
    if len(parts) != 4:
        return None
    if parts[0] != "api" or parts[1] != "sessions" or parts[3] != "events":
        return None
    sid = str(parts[2] or "").strip()
    return sid or None


def _session_events_resume_event_id(handler, parsed) -> str | None:
    headers = getattr(handler, "headers", None)
    raw = None
    if headers is not None:
        try:
            raw = headers.get("Last-Event-ID")
        except Exception:
            logger.debug("Silent exception in _session_events_resume_event_id", exc_info=True)
            raw = None
    raw = str(raw or "").strip()
    if raw:
        return raw
    qs = parse_qs(getattr(parsed, "query", "") or "")
    raw = str(qs.get("after_event_id", [None])[0] or "").strip()
    return raw or None


def _session_snapshot_payload(session, *, active_stream_id: str | None = None) -> dict:
    try:
        payload = session.compact(
            include_runtime=bool(active_stream_id),
            active_stream_ids={active_stream_id} if active_stream_id else None,
        )
    except Exception:
        logger.debug("Silent exception in _session_snapshot_payload", exc_info=True)
        payload = {"session_id": str(getattr(session, "session_id", "") or "")}
    return {"session": payload}


def _parse_run_journal_event_id(raw: str | None) -> tuple[str | None, int | None]:
    return _shared_parse_run_journal_event_id(raw)


def _chat_stream_resume_cursor(handler, qs: dict, stream_id: str | None = None) -> tuple[int | None, bool, str | None, str | None]:
    """Resolve the client's resume cursor for ``/api/chat/stream``.

    Returns ``(after_seq, resume_requested, raw_cursor, runner_cursor)``:

    - ``after_seq``: the parsed same-run cursor seq, or ``None`` when there is
      no usable same-run (journal-shaped) cursor.
    - ``resume_requested``: True when the client SUPPLIED any cursor — via the
      ``after_event_id`` / ``after_seq`` query params, ``replay=1``, or the
      ``Last-Event-ID`` header — regardless of whether it parsed.
    - ``raw_cursor``: the opaque cursor string exactly as the client supplied it
      (the ``Last-Event-ID`` value, or the explicit ``after_event_id``).
    - ``runner_cursor``: the cursor to hand to the runner observe path, resolved
      with PROVENANCE so the runner adapter (whose cursors are opaque, not
      journal-shaped) gets a cursor it can actually use:

        * a valid ``after_seq`` pairs with whatever ``after_event_id`` was
          supplied — even an opaque runner id like ``event:2`` that the
          journal parser reads as a foreign run — so the paired runner cursor
          resumes at the seq (never ``None`` / full replay, which would
          duplicate events);
        * a header-only opaque runner id (``Last-Event-ID: event:2``) is
          preserved as-is so the runner resumes from it;
        * a malformed or foreign explicit cursor WITHOUT a valid paired
          ``after_seq`` yields ``None`` — it must block the header and replay
          from start (this preserves the r2 malformed-blocks-header rule and
          never forwards an unusable cursor to the runner).

    The presence flag must stay separate from validity: a malformed, foreign-run,
    or ahead-of-stream cursor resolves to ``after_seq=None`` but still means the
    client *asked* to resume. That request must be honored with a
    replay-from-start so no journal events are silently skipped — whereas a
    genuinely cursor-less request is a fresh subscribe (no replay).

    Precedence is decided by query-parameter PRESENCE, not successful parsing:
    when an explicit ``after_seq`` / ``after_event_id`` is supplied (even an
    unparseable one), the ``Last-Event-ID`` header is never consulted, so a
    header can never override an explicit cursor and silently skip events.

    ``Last-Event-ID`` is the cursor every spec-compliant SSE client (browser
    ``EventSource`` auto-reconnect, Android/CLI clients) sends automatically on
    reconnect, carrying the ``id:`` of the last event it received. Every
    journaled event on this stream already emits ``id: stream_id:seq`` via
    ``_sse_with_id()``. Same resolution-chain precedent as
    ``api/kanban_bridge.py`` (``?since=`` → ``Last-Event-ID``).
    """
    after_seq_raw = qs.get("after_seq", [None])[0]
    has_explicit_query = (
        after_seq_raw not in (None, "")
        or bool(qs.get("after_event_id", [None])[0])
        or bool(qs.get("replay", [""])[0])
    )
    if has_explicit_query:
        explicit_raw = str(qs.get("after_event_id", [None])[0] or "").strip() or None
        after_seq = _parse_run_journal_after_seq(qs, stream_id)
        # Runner cursor provenance. ``after_seq`` is authoritative when it
        # parses — it pairs with whatever ``after_event_id`` shape was
        # supplied, including opaque runner ids (``event:2``) that the journal
        # parser reads as a foreign run. Read it directly here (not via
        # ``_parse_run_journal_after_seq``, which checks ``after_event_id``
        # FIRST and would swallow a paired opaque runner id as "foreign").
        paired_seq = _parse_run_journal_after_seq_value(after_seq_raw)
        if paired_seq is not None:
            runner_cursor = str(paired_seq)
        else:
            event_run_id, event_seq = _parse_run_journal_event_id(explicit_raw)
            runner_cursor = (
                explicit_raw
                if explicit_raw and event_seq is not None and (not stream_id or event_run_id == stream_id)
                else None
            )
        return after_seq, True, explicit_raw, runner_cursor
    headers = getattr(handler, "headers", None)
    if headers is None:
        return None, False, None, None
    try:
        raw = headers.get("Last-Event-ID")
    except Exception:
        logger.debug("Silent exception in _chat_stream_resume_cursor", exc_info=True)
        return None, False, None, None
    raw = str(raw or "").strip()
    if not raw:
        return None, False, None, None
    event_run_id, event_seq = _parse_run_journal_event_id(raw)
    if event_run_id and event_seq is not None:
        if stream_id and event_run_id != stream_id:
            # Foreign-run journal cursor: asked to resume THIS run but the cursor
            # names a different journal run — can't honor it for the journal
            # path (after_seq stays None → replay-from-start). The runner path
            # keys cursors by run_id independently (the cursor is forwarded as
            # an opaque per-run query param), so a ``run:seq`` header still
            # reaches it as-is; this mirrors how a foreign after_event_id on
            # the journal path is rejected while the same client's explicit
            # opaque cursor= would still reach the runner.
            return None, True, raw, raw
        return event_seq, True, raw, raw
    # Malformed as a JOURNAL cursor. A colon-less opaque value is a plausible
    # runner cursor (runner ids need not be journal-shaped), so preserve it for
    # the runner; a value that merely fails int() parsing is unusable anywhere.
    runner_cursor = raw if ":" not in raw else None
    return None, True, raw, runner_cursor


def _parse_run_journal_after_seq_value(raw) -> int | None:
    """Parse a bare ``after_seq`` value, independent of any ``after_event_id``.

    Used by the runner-cursor provenance path, where ``after_seq`` is
    authoritative on its own and must NOT be gated behind the
    ``after_event_id``-first ordering of ``_parse_run_journal_after_seq`` (a
    paired opaque runner id like ``event:2`` would otherwise be read as a
    foreign run and swallow the seq). Mirrors the ``after_seq`` tail of that
    parser: absent/blank → None, non-numeric → 0.
    """
    if raw in (None, ""):
        return None
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0


def _parse_run_journal_after_seq(qs: dict, stream_id: str | None = None) -> int | None:
    event_run_id, event_seq = _parse_run_journal_event_id(qs.get("after_event_id", [None])[0])
    if event_run_id:
        if stream_id and event_run_id != stream_id:
            return None
        return event_seq
    raw = qs.get("after_seq", [None])[0]
    if raw in (None, ""):
        return None
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0


def _replay_run_journal(
    handler,
    stream_id: str,
    after_seq: int | None,
    *,
    max_seq: int | None = None,
    include_stale: bool = True,
) -> bool:
    summary = find_run_summary(stream_id)
    if not summary:
        return False
    journal = read_run_events(
        str(summary.get("session_id") or ""),
        stream_id,
        after_seq=after_seq,
        max_seq=max_seq,
    )
    for entry in journal.get("events") or []:
        _sse_with_id(
            handler,
            entry.get("event") or entry.get("type") or "message",
            entry.get("payload"),
            entry.get("event_id"),
        )
    if include_stale and not summary.get("terminal"):
        stale = stale_interrupted_event(
            str(summary.get("session_id") or ""),
            stream_id,
            after_seq=after_seq,
        )
        if stale:
            _sse_with_id(handler, stale["event"], stale["payload"], stale["event_id"])
    return True


def _run_journal_same_run_seq(event_id: str | None, stream_id: str) -> int | None:
    event_run_id, event_seq = _parse_run_journal_event_id(event_id)
    if event_run_id != stream_id:
        return None
    return event_seq


def _run_journal_covers_offline_gap(
    stream_id: str, after_seq: int | None, cutoff_seq: int | None
) -> bool:
    """Return True when the run journal PROVABLY backfills a dropped-frame gap.

    When StreamChannel evicted frames from its offline buffer
    (``offline_dropped_events > 0``), draining the retained tail to a
    reconnecting client is only safe if the journal replay actually covers
    everything from the client's cursor (*after_seq*, ``None`` = start of run)
    through the snapshot cutoff — journal seqs are assigned contiguously from 1,
    so coverage means every seq in ``(after_seq, cutoff_seq]`` is present. A
    missing journal, a journal that stops short of the cutoff, or a window with
    malformed/dropped lines all mean the evicted frames are unrecoverable here
    and the caller must signal recovery instead of streaming tail-only.

    ``cutoff_seq is None`` (the channel never saw a same-run journaled event id)
    counts as not covered: nothing can be proven against an unknown cutoff.
    """
    if cutoff_seq is None:
        return False
    floor = max(0, int(after_seq)) if after_seq is not None else 0
    if floor >= cutoff_seq:
        # Client cursor is already at/past everything the buffer ever held.
        return True
    try:
        summary = find_run_summary(stream_id)
        if not summary:
            return False
        journal = read_run_events(
            str(summary.get("session_id") or ""),
            stream_id,
            after_seq=floor,
            max_seq=cutoff_seq,
        )
    except Exception:
        logger.debug(
            "Run journal coverage check failed for stream %s", stream_id, exc_info=True
        )
        return False
    cutoff = int(cutoff_seq)
    seqs = set()
    for entry in journal.get("events") or []:
        try:
            seq = int(entry.get("seq") or 0)
        except (TypeError, ValueError):
            continue
        if floor < seq <= cutoff:
            seqs.add(seq)
    # Seqs are unique and bounded to the window, so full coverage means one
    # distinct seq per slot — no need to materialize the whole range.
    return len(seqs) == cutoff - floor


def _sse_replay_run_journal_gap_checked(
    handler, qs: dict, stream_id: str, stream_snapshot: dict,
    *, resume_cursor: tuple[int | None, bool] | None = None,
) -> tuple[bool, int | None]:
    """Journal-replay for a reconnecting client, enforcing offline-gap coverage.

    Returns ``(gap_recovered, replay_cutoff_seq)``. When the channel evicted
    frames from its offline buffer (``offline_dropped_events > 0`` in the
    subscribe snapshot) and the run journal cannot PROVE it backfills the gap
    (see ``_run_journal_covers_offline_gap``), a recovery_control apperror has
    been emitted and the caller must return instead of draining the retained
    tail (``gap_recovered=True``).

    ``resume_cursor`` is the caller-resolved ``(after_seq, resume_requested)``
    pair from ``_chat_stream_resume_cursor``. Presence and validity are kept
    separate: an invalid / foreign-run / unparseable cursor means the client
    *asked* to resume but we couldn't honor it, so it is normalized to
    replay-from-start (``after_seq=None`` inside the replay) rather than
    treated as "no cursor" — which would skip replay and silently drain a
    truncated buffer. A genuinely cursor-less request (``resume_requested``
    False) is a fresh subscribe and returns ``(False, None)`` with no replay.

    Direct callers that only supply ``qs`` keep the historical behavior: the
    cursor is derived from the query params (presence of ``replay`` /
    ``after_seq`` / ``after_event_id`` counts as resume-requested).
    """
    if resume_cursor is None:
        after_seq = _parse_run_journal_after_seq(qs, stream_id)
        resume_requested = (
            bool(qs.get("replay", [""])[0])
            or qs.get("after_seq", [None])[0] not in (None, "")
            or bool(qs.get("after_event_id", [None])[0])
        )
    else:
        after_seq, resume_requested = resume_cursor
    if not resume_requested:
        return False, None
    try:
        offline_dropped = int(stream_snapshot.get("offline_dropped_events") or 0)
    except (TypeError, ValueError):
        offline_dropped = 0
    snapshot_cutoff_seq = _run_journal_same_run_seq(
        str(stream_snapshot.get("last_event_id") or ""),
        stream_id,
    )
    # Normalize an unparseable / foreign cursor (asked to resume, but no usable
    # same-run seq) to replay-from-start so the gap check and dedup operate on
    # a real cursor instead of silently skipping the whole journal.
    if after_seq is None:
        after_seq = 0
    # Normalize a numeric cursor strictly AHEAD of the snapshot's last known
    # frame to replay-from-start too: the client believes it already holds
    # everything, so on a truncated buffer the coverage check's
    # ``floor >= replay_max_seq`` would falsely declare the gap covered, replay
    # nothing, and drain only the retained tail — silently losing every event
    # before it (Codex r2 #2). ``>`` (not ``>=``) — a cursor EQUAL to the
    # cutoff is a valid in-range cursor (see the dedup bound below).
    #
    # An UNKNOWN snapshot cutoff (no parseable ``last_event_id`` — e.g. the
    # channel has not seen an id-bearing frame yet) is treated as fence 0:
    # with no cutoff to bound it, any positive client cursor would otherwise
    # be installed verbatim as the live dedup bound and filter EVERY queued
    # frame — including the terminal ``stream_end`` fence — leaving the
    # reconnect stalled on heartbeats with an empty body (Codex r4). Failing
    # closed to replay-from-start delivers the buffered events (at worst
    # duplicating what the client already holds) instead of silently losing
    # them. Frames dropped while the cutoff is unknown still cannot prove
    # coverage below (``cutoff_seq is None`` → not covered), so the
    # recovery_control fail-closed path is preserved.
    effective_cutoff = snapshot_cutoff_seq if snapshot_cutoff_seq is not None else 0
    if after_seq > effective_cutoff:
        after_seq = 0
    # The subscribe snapshot already queued the retained offline tail, which
    # covers [first buffered frame → snapshot cutoff] by itself. The journal
    # only has to bridge (client cursor → first buffered frame) — and the
    # replay/dedup cutoff must stop there too, or the drain loop's
    # `seq <= replay_cutoff_seq` filter would eat queued frames the journal
    # never emitted. Without a parseable first-frame id (empty buffer, foreign
    # run, unjournaled head frame) fall back to the full (cursor → cutoff]
    # window as before.
    replay_max_seq = snapshot_cutoff_seq
    first_buffered_seq = _run_journal_same_run_seq(
        str(stream_snapshot.get("offline_first_event_id") or ""),
        stream_id,
    )
    if first_buffered_seq is not None:
        replay_max_seq = first_buffered_seq - 1
        if snapshot_cutoff_seq is not None:
            replay_max_seq = min(replay_max_seq, snapshot_cutoff_seq)
    covered = offline_dropped <= 0 or _run_journal_covers_offline_gap(
        stream_id, after_seq, replay_max_seq
    )
    replay_cutoff_seq = None
    replay_failed = False
    if covered:
        try:
            if _replay_run_journal(
                handler,
                stream_id,
                after_seq,
                max_seq=replay_max_seq,
                include_stale=False,
            ):
                replay_cutoff_seq = replay_max_seq
        except _CLIENT_DISCONNECT_ERRORS:
            raise
        except Exception:
            replay_failed = True
            logger.debug("Failed to replay active run journal for stream %s", stream_id, exc_info=True)
    if offline_dropped > 0 and (not covered or replay_failed):
        _sse_offline_gap_recovery(handler, stream_id, offline_dropped)
        return True, None
    # Two distinct dedup bounds feed the drain loop's `seq <=` filter: frames
    # the journal replay just emitted (replay_cutoff_seq, capped at the buffer
    # head so queued frames the journal never sent survive) AND frames the
    # client already holds per its own cursor. A cursor at/inside the retained
    # tail (after_seq >= first buffered frame) would otherwise get the queued
    # copy of frames it already rendered — a double-render, since this filter
    # is the only dedup for replayed streams.
    #
    # Dedup bound semantics: the drain filter skips ``seq <= replay_cutoff_seq``.
    # The event AT the cursor (seq == after_seq) was already delivered to this
    # client, so the cursor must itself enter the bound — equality included —
    # otherwise the buffered copy of that event double-sends. A cursor strictly
    # ahead of the snapshot was already normalized to 0 above; here only
    # in-range cursors (after_seq <= snapshot_cutoff_seq) contribute a bound,
    # and the terminal frame must always survive.
    if after_seq is not None and after_seq > 0:
        if snapshot_cutoff_seq is None or after_seq <= snapshot_cutoff_seq:
            replay_cutoff_seq = (
                after_seq
                if replay_cutoff_seq is None
                else max(replay_cutoff_seq, after_seq)
            )
    return False, replay_cutoff_seq


def _sse_offline_gap_recovery(handler, stream_id: str, offline_dropped: int) -> None:
    """Signal an unrecoverable replay gap instead of streaming tail-only.

    Frames were evicted from the channel's capped offline buffer and the run
    journal cannot prove it backfills (client cursor → snapshot cutoff]:
    draining the retained tail would render a silent transcript hole that ends
    in a normal ``stream_end``. Emit the established ``recovery_control``
    apperror (same client contract as ``run_journal.stale_interrupted_event``)
    so the tab restores the transcript from persisted session state instead.
    """
    # The client only acts on the recovery signal when the payload names its
    # session (eventMatchesCurrent), so fall back to the journal summary when
    # the pre-worker owner registration is already gone.
    try:
        session_id = stream_owner_session_id(stream_id) or ""
        if not session_id:
            session_id = str((find_run_summary(stream_id) or {}).get("session_id") or "")
    except Exception:
        logger.debug("Silent exception in _sse_offline_gap_recovery", exc_info=True)
        session_id = ""
    _sse(
        handler,
        "apperror",
        {
            "type": "interrupted",
            "recovery_control": True,
            "message": (
                "The live stream's replay buffer overflowed while no tab was "
                "attached and the run journal cannot backfill the dropped frames."
            ),
            "hint": "The transcript was restored to the last saved state.",
            "session_id": session_id,
            "stream_id": stream_id,
            "offline_dropped_events": offline_dropped,
        },
    )


def _runner_stream_cursor_from_query(qs: dict) -> str | None:
    cursor = str(qs.get("cursor", [""])[0] or "").strip()
    if cursor:
        return cursor
    after_seq = _parse_run_journal_after_seq(qs)
    return str(after_seq) if after_seq is not None else None


def _runner_event_name(entry: dict) -> str:
    return str(entry.get("event") or entry.get("type") or "message")


def _runner_event_payload(entry: dict):
    if "payload" in entry:
        return entry.get("payload")
    if "data" in entry:
        return entry.get("data")
    return entry


def _project_runner_event_payload(payload):
    """Strip internal replay fields (api_content, row-id aliases) from a runner
    SSE payload before it is relayed to the browser.

    Runner-backed SSE relays adapter payloads verbatim; a terminal event can
    carry a full ``session`` object (with per-message ``api_content`` sidecars)
    or be session/message-shaped itself. Neither must reach a client, so route
    the transcript-bearing shapes through the same public projection every other
    session emitter uses. Non-session payloads pass through unchanged.
    """
    if not isinstance(payload, dict):
        return payload
    # Terminal events wrap the session under a "session" key.
    if isinstance(payload.get("session"), dict):
        projected = dict(payload)
        projected["session"] = public_session_projection(payload["session"])
        return projected
    # Payload is itself session/message-shaped (has a messages transcript).
    if "messages" in payload or "context_messages" in payload:
        return public_session_projection(payload)
    return payload


def _runner_event_id(run_id: str, entry: dict) -> str | None:
    event_id = entry.get("event_id") or entry.get("id")
    if event_id:
        return str(event_id)
    seq = entry.get("seq")
    if seq not in (None, ""):
        return f"{run_id}:{seq}"
    return None


def _stream_runner_run_events(handler, run_id: str, cursor: str | None = None) -> bool:
    """Stream events from a configured runner without WebUI-owned runtime maps."""
    run_id = str(run_id or "").strip()
    if not run_id:
        return False
    try:
        from api.runtime_adapter import build_runtime_adapter, runtime_adapter_runner_enabled

        if not runtime_adapter_runner_enabled():
            return False
        adapter = build_runtime_adapter(runner_client_factory=_runtime_runner_client_factory)
    except NotImplementedError:
        return False
    if adapter is None:
        return False

    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("X-Accel-Buffering", "no")
    handler.send_header("Connection", "close")
    end_sse_headers(handler)
    cursor_value = cursor
    try:
        while True:
            try:
                event_stream = adapter.observe_run(run_id, cursor=cursor_value)
            except Exception as exc:
                logger.debug("Silent exception in _stream_runner_run_events", exc_info=True)
                _sse(handler, "error", {"error": _sanitize_error(exc)})
                break
            emitted = False
            terminal = False
            for entry in list(getattr(event_stream, "events", []) or []):
                if not isinstance(entry, dict):
                    continue
                event = _runner_event_name(entry)
                _sse_with_id(handler, event, _project_runner_event_payload(_runner_event_payload(entry)), _runner_event_id(run_id, entry))
                emitted = True
                if event in SSE_RELAY_CLOSE_EVENTS:
                    terminal = True
            next_cursor = getattr(event_stream, "cursor", None)
            if next_cursor not in (None, ""):
                cursor_value = str(next_cursor)
            if terminal:
                break
            if not emitted:
                status = None
                try:
                    status = adapter.get_run(run_id)
                except Exception:
                    logger.debug("Silent exception in _stream_runner_run_events", exc_info=True)
                    status = None
                state = str(getattr(status, "terminal_state", None) or getattr(status, "status", "") or "").lower()
                if state in ("completed", "complete", "failed", "error", "cancelled", "canceled"):
                    _sse(handler, "stream_end", {"run_id": run_id, "status": state})
                    break
                handler.wfile.write(b": heartbeat\n\n")
                handler.wfile.flush()
                time.sleep(_SSE_HEARTBEAT_INTERVAL_SECONDS)
    except _CLIENT_DISCONNECT_ERRORS:
        pass
    return True


def _handle_sse_stream(handler, parsed):
    qs = parse_qs(parsed.query)
    stream_id = qs.get("stream_id", [""])[0]
    if not _stream_id_visible_to_request_profile(handler, stream_id):
        return True
    # Resume cursor: explicit query params (after_event_id/after_seq/replay)
    # win; the Last-Event-ID header that spec-compliant SSE clients auto-send
    # on reconnect is the fallback. Presence is tracked separately from the
    # parsed seq — a client that supplied ANY cursor asked to resume, and an
    # unusable (invalid/foreign/ahead-of-stream) cursor must replay from start
    # rather than silently skip journal events.
    resume_cursor = _chat_stream_resume_cursor(handler, qs, stream_id)
    resume_after_seq, resume_requested, resume_raw_cursor, runner_resume_cursor = resume_cursor
    stream = STREAMS.get(stream_id)
    if stream is None:
        # Runner-observe path: consume the ALREADY-RESOLVED cursor — do not
        # re-parse query params or re-read the header (Codex r2 #3 / r3). The
        # explicit opaque ``cursor`` query param still wins for runner clients
        # that speak that contract. Otherwise use the resolver's
        # provenance-resolved runner cursor: a valid ``after_seq`` pairs with
        # opaque runner ids (event:2), a header-only opaque runner id resumes
        # as-is, and a malformed/foreign cursor without a valid paired seq
        # yields None (replay from start, never forwarding an unusable cursor).
        runner_cursor = str(qs.get("cursor", [""])[0] or "").strip() or None
        if runner_cursor is None and resume_requested:
            runner_cursor = runner_resume_cursor
        if _stream_runner_run_events(handler, stream_id, runner_cursor):
            return True
        try:
            journal_summary = find_run_summary(stream_id) if stream_id else None
        except Exception:
            logger.warning("Silent exception in _handle_sse_stream", exc_info=True)
            journal_summary = None
        if not journal_summary:
            return j(handler, {"error": "stream not found"}, status=404)
        # Normalize a cursor strictly AHEAD of the dead stream's authoritative
        # last_seq to replay-from-start: passing it straight through would make
        # the journal reader emit an empty SSE body for a journal that actually
        # holds events (Codex r2 #2). Equality is in-range — the event at the
        # cursor was already delivered, so the replay correctly resumes after it.
        dead_after_seq = resume_after_seq
        try:
            last_seq = int(journal_summary.get("last_seq") or 0)
        except (TypeError, ValueError):
            last_seq = 0
        if dead_after_seq is not None and dead_after_seq > last_seq:
            dead_after_seq = 0
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
        handler.send_header("Cache-Control", "no-cache")
        handler.send_header("X-Accel-Buffering", "no")
        handler.send_header("Connection", "close")
        end_sse_headers(handler)
        try:
            _replay_run_journal(handler, stream_id, dead_after_seq)
        except _CLIENT_DISCONNECT_ERRORS:
            pass
        return True
    if hasattr(stream, "subscribe_with_snapshot"):
        subscriber, stream_snapshot = stream.subscribe_with_snapshot()
    else:
        subscriber = stream.subscribe() if hasattr(stream, "subscribe") else stream
        stream_snapshot = {}
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("X-Accel-Buffering", "no")
    handler.send_header("Connection", "close")
    end_sse_headers(handler)
    _sse_set_write_deadline(handler)  # Defect A: slow tab can't pin this thread
    # Replay shares the drain loop's try/finally so every exit path unsubscribes.
    try:
        gap_recovered, replay_cutoff_seq = _sse_replay_run_journal_gap_checked(
            handler, qs, stream_id, stream_snapshot,
            resume_cursor=(resume_after_seq, resume_requested),
        )
        if gap_recovered:
            return True
        while True:
            try:
                item = subscriber.get(timeout=_SSE_HEARTBEAT_INTERVAL_SECONDS)
            except queue.Empty:
                handler.wfile.write(b": heartbeat\n\n")
                handler.wfile.flush()
                continue
            if len(item) >= 3:
                event, data, queued_event_id = item[0], item[1], item[2]
            else:
                event, data = item
                queued_event_id = STREAM_LAST_EVENT_ID.get(stream_id)
            # Stage-364: emit `id:` from STREAM_LAST_EVENT_ID side-channel so
            # the frontend's `_lastRunJournalSeq` cursor advances during live
            # streaming. Without this, mid-stream error→replay would arrive
            # with after_seq=0 and double-render every journaled event.
            event_id = queued_event_id or STREAM_LAST_EVENT_ID.get(stream_id)
            event_seq = _run_journal_same_run_seq(event_id, stream_id)
            if replay_cutoff_seq is not None and event_seq is not None and event_seq <= replay_cutoff_seq:
                continue
            if event_id:
                _sse_with_id(handler, event, data, event_id)
            else:
                _sse(handler, event, data)
            if event in SSE_RELAY_CLOSE_EVENTS:
                break
    except _CLIENT_DISCONNECT_ERRORS:
        pass
    finally:
        if subscriber is not stream and hasattr(stream, "unsubscribe"):
            try:
                stream.unsubscribe(subscriber)
            except Exception:
                logger.warning("Silent exception in _handle_sse_stream", exc_info=True)
                pass
    return True


def _handle_session_run_journal_stream_for_session(handler, parsed, session_id):
    if not _session_id_visible_to_request_profile(handler, session_id):
        return True
    try:
        session = get_session(session_id, metadata_only=True)
    except KeyError:
        return j(handler, {"error": "Session not found"}, status=404)

    # Parse the resume cursor and baseline the journal BEFORE committing SSE headers
    # (and thus before any run could complete mid-handler). Capturing after
    # end_sse_headers() leaves a window where a run finishing between header commit
    # and baseline is absorbed into the baseline and silently lost. Both operations
    # are side-effect-free (header read + stat-only fingerprint), safe pre-response.
    resume_event_id = _session_events_resume_event_id(handler, parsed)
    _idle_journal_fp = session_journal_fingerprint(session_id)

    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("X-Accel-Buffering", "no")
    # #3103: see _handle_gateway_sse_stream — `Connection: close` causes
    # EventSource reconnect storms in browsers on long-lived SSE.
    end_sse_headers(handler)
    _sse_set_write_deadline(handler)

    active_stream_id = _active_run_stream_for_session(session_id)
    subscriber = None
    subscriber_stream = None
    replay_cutoff_seq = None
    sent_event_ids: set[str] = set()
    sent_event_order = deque()

    def note_sent_event_id(event_id):
        if not event_id:
            return
        sent_event_ids.add(event_id)
        sent_event_order.append(event_id)
        while len(sent_event_order) > _SESSION_SSE_SENT_EVENT_ID_LIMIT:
            sent_event_ids.discard(sent_event_order.popleft())

    def attach_active_stream():
        stream_id = _active_run_stream_for_session(session_id)
        stream = STREAMS.get(stream_id) if stream_id else None
        if stream is None:
            return None, None, None, stream_id
        if hasattr(stream, "subscribe_with_snapshot"):
            queue_, snapshot = stream.subscribe_with_snapshot()
        else:
            queue_ = stream.subscribe() if hasattr(stream, "subscribe") else stream
            snapshot = {}
        return queue_, stream, snapshot, stream_id

    def emit_replay(events, stream_id, cutoff_seq):
        for entry in events:
            event_id = str(entry.get("event_id") or "")
            event_seq = _run_journal_same_run_seq(event_id, stream_id)
            if cutoff_seq is not None and event_seq is not None and event_seq > cutoff_seq:
                continue
            if event_id and event_id in sent_event_ids:
                continue
            _sse_with_id(handler, entry.get("event") or entry.get("type") or "message", entry.get("payload"), event_id)
            if event_id:
                note_sent_event_id(event_id)

    def emit_session_snapshot(active_stream_id):
        try:
            fresh_session = get_session(session_id, metadata_only=True)
        except KeyError:
            fresh_session = session
        _sse(handler, "session_snapshot", _session_snapshot_payload(fresh_session, active_stream_id=active_stream_id))

    try:
        replay_events = []
        replay_ok = False
        if resume_event_id:
            replay = read_session_run_events(session_id, after_event_id=resume_event_id)
            if replay.get("status") != "ok":
                emit_session_snapshot(active_stream_id)
            else:
                replay_ok = True
                replay_events = replay.get("events") or []
        subscriber, subscriber_stream, stream_snapshot, active_stream_id = attach_active_stream()
        if subscriber is None:
            if replay_ok:
                emit_replay(replay_events, active_stream_id, None)
            while True:
                subscriber, subscriber_stream, stream_snapshot, active_stream_id = attach_active_stream()
                if subscriber is not None:
                    break
                # Journal advanced with no live stream to attach → a run completed
                # entirely within the wait (or the first attach). Re-sync via a
                # snapshot boundary (the same honest-recovery contract used for a
                # failed reconciliation), then re-baseline so we only re-sync on
                # genuinely new advances.
                _current_journal_fp = session_journal_fingerprint(session_id)
                if _current_journal_fp != _idle_journal_fp:
                    _idle_journal_fp = _current_journal_fp
                    emit_session_snapshot(active_stream_id)
                handler.wfile.write(b": keepalive\n\n")
                handler.wfile.flush()
                time.sleep(_SSE_HEARTBEAT_INTERVAL_SECONDS)
        if subscriber is None:
            return True
        if replay_ok:
            replay_cutoff_seq = _run_journal_same_run_seq(str(stream_snapshot.get("last_event_id") or ""), active_stream_id)
            reconciled = read_session_run_events(session_id, after_event_id=resume_event_id)
            if reconciled.get("status") == "ok":
                emit_replay(reconciled.get("events") or [], active_stream_id, replay_cutoff_seq)
            else:
                emit_session_snapshot(active_stream_id)
        try:
            while True:
                try:
                    item = subscriber.get(timeout=_SSE_HEARTBEAT_INTERVAL_SECONDS)
                except queue.Empty:
                    handler.wfile.write(b": keepalive\n\n")
                    handler.wfile.flush()
                    continue
                if len(item) >= 3:
                    event, data, queued_event_id = item[0], item[1], item[2]
                else:
                    event, data = item
                    queued_event_id = STREAM_LAST_EVENT_ID.get(active_stream_id)
                event_id = queued_event_id or STREAM_LAST_EVENT_ID.get(active_stream_id)
                event_seq = _run_journal_same_run_seq(event_id, active_stream_id)
                _is_terminal = event in SSE_RELAY_CLOSE_EVENTS
                _already_sent = (
                    (replay_cutoff_seq is not None and event_seq is not None and event_seq <= replay_cutoff_seq)
                    or (event_id and event_id in sent_event_ids)
                )
                if _already_sent:
                    # Already delivered via replay/reconciliation (cutoff or dedup).
                    # A terminal event still has to end this loop — otherwise, when
                    # reconciliation replayed the active run's terminal at the cutoff,
                    # the live copy would be skipped here and the handler would stay
                    # blocked on a dead run's queue and miss subsequent session runs.
                    if _is_terminal:
                        break
                    continue
                if event_id:
                    _sse_with_id(handler, event, data, event_id)
                    note_sent_event_id(event_id)
                else:
                    _sse(handler, event, data)
                if _is_terminal:
                    break
        except _CLIENT_DISCONNECT_ERRORS:
            pass
    except _CLIENT_DISCONNECT_ERRORS:
        pass
    finally:
        if subscriber is not None and subscriber is not subscriber_stream and hasattr(subscriber_stream, "unsubscribe"):
            try:
                subscriber_stream.unsubscribe(subscriber)
            except Exception:
                logger.warning("Silent exception in _handle_session_run_journal_stream_for_session", exc_info=True)
                pass
    return True


_handle_session_sse_stream_for_session = _handle_session_run_journal_stream_for_session


def _terminal_session_lookup(body_or_query):
    sid = str(body_or_query.get("session_id", "")).strip()
    if not sid:
        raise ValueError("session_id required")
    try:
        s = get_session(sid)
    except KeyError:
        raise KeyError("Session not found")
    return sid, s


_REMOTE_TERMINAL_BACKEND_UNSUPPORTED_ERROR = "remote_terminal_backend_unsupported"
_REMOTE_TERMINAL_BACKEND_UNSUPPORTED_MESSAGE = (
    "Embedded terminal is only supported for local terminal backends."
)


def _terminal_remote_backend_enabled() -> bool:
    terminal_cfg = get_config().get("terminal", {})
    return _is_remote_terminal_backend(terminal_cfg)


def _handle_terminal_start(handler, body):
    try:
        if not _embedded_terminal_gate_allows(handler):
            return bad(handler, _EMBEDDED_TERMINAL_GATE_DENIED_MESSAGE, 403)
        sid, session = _terminal_session_lookup(body)
        if _terminal_remote_backend_enabled():
            return j(
                handler,
                {
                    "error": _REMOTE_TERMINAL_BACKEND_UNSUPPORTED_ERROR,
                    "message": _REMOTE_TERMINAL_BACKEND_UNSUPPORTED_MESSAGE,
                },
                status=400,
            )
        workspace = resolve_trusted_workspace(getattr(session, "workspace", "") or "")
        from api.terminal import start_terminal
        term = start_terminal(
            sid,
            workspace,
            rows=int(body.get("rows") or 24),
            cols=int(body.get("cols") or 80),
            restart=bool(body.get("restart")),
        )
        return j(
            handler,
            {
                "ok": True,
                "session_id": sid,
                "workspace": term.workspace,
                "running": term.is_alive(),
            },
        )
    except KeyError as e:
        return bad(handler, str(e), 404)
    except ValueError as e:
        return bad(handler, str(e), 400)
    except Exception as e:
        logger.warning("Silent exception in _handle_terminal_start", exc_info=True)
        return bad(handler, _sanitize_error(e), 500)


def _handle_terminal_input(handler, body):
    try:
        if not _embedded_terminal_gate_allows(handler):
            return bad(handler, _EMBEDDED_TERMINAL_GATE_DENIED_MESSAGE, 403)
        require(body, "session_id")
        data = str(body.get("data", ""))
        if len(data) > 8192:
            return bad(handler, "input too large", 413)
        from api.terminal import write_terminal
        write_terminal(body["session_id"], data)
        return j(handler, {"ok": True})
    except KeyError as e:
        return bad(handler, str(e), 404)
    except ValueError as e:
        return bad(handler, str(e), 400)
    except Exception as e:
        logger.warning("Silent exception in _handle_terminal_input", exc_info=True)
        return bad(handler, _sanitize_error(e), 500)


def _handle_terminal_resize(handler, body):
    try:
        if not _embedded_terminal_gate_allows(handler):
            return bad(handler, _EMBEDDED_TERMINAL_GATE_DENIED_MESSAGE, 403)
        require(body, "session_id")
        from api.terminal import resize_terminal
        resize_terminal(
            body["session_id"],
            rows=int(body.get("rows") or 24),
            cols=int(body.get("cols") or 80),
        )
        return j(handler, {"ok": True})
    except KeyError as e:
        return bad(handler, str(e), 404)
    except ValueError as e:
        return bad(handler, str(e), 400)
    except Exception as e:
        logger.warning("Silent exception in _handle_terminal_resize", exc_info=True)
        return bad(handler, _sanitize_error(e), 500)


def _handle_terminal_close(handler, body):
    try:
        if not _embedded_terminal_gate_allows(handler):
            return bad(handler, _EMBEDDED_TERMINAL_GATE_DENIED_MESSAGE, 403)
        require(body, "session_id")
        from api.terminal import close_terminal
        closed = close_terminal(body["session_id"])
        return j(handler, {"ok": True, "closed": closed})
    except ValueError as e:
        return bad(handler, str(e), 400)


def _handle_terminal_output(handler, parsed):
    if not _embedded_terminal_gate_allows(handler):
        return bad(handler, _EMBEDDED_TERMINAL_GATE_DENIED_MESSAGE, 403)
    qs = parse_qs(parsed.query)
    sid = qs.get("session_id", [""])[0]
    if not sid:
        return bad(handler, "session_id required")
    from api.terminal import attach_terminal
    # EventSource automatically returns the last received SSE id on transport
    # reconnect. Seed only newer backlog entries in that case so already-rendered
    # terminal bytes (including ANSI cursor controls) are not written twice. A
    # genuinely new viewer has no cursor and receives the full bounded backlog.
    after_seq = None
    last_event_id = str(handler.headers.get("Last-Event-ID", "") or "").strip()
    if last_event_id:
        try:
            after_seq = max(0, int(last_event_id))
        except ValueError:
            pass
    # Look up and subscribe in one atomic step. A separate `get_terminal()` then
    # `term.subscribe()` leaves a window in which the idle reaper can claim and
    # tear the terminal down, leaving this stream attached to a corpse after we
    # already committed a 200. Attaching atomically means we either hold a live
    # viewer (which makes the terminal un-reapable) or learn it is gone in time
    # to answer 404.
    attached = attach_terminal(sid, after_seq=after_seq)
    if attached is None:
        return j(handler, {"error": "terminal not running"}, status=404)
    term, output = attached

    # The subscription is live from here on, so EVERY exit path — including a
    # failure while writing the response headers — must unsubscribe. Writing
    # headers to a client that already dropped raises BrokenPipeError, and if
    # that escaped before the try block the queue would stay in
    # `_subscribers` forever, pinning `unwatched_since` at None and making the
    # terminal permanently unreapable: the exact fd/thread leak this reaper
    # exists to prevent. Hence the try starts immediately after the attach.
    try:
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
        handler.send_header("Cache-Control", "no-cache")
        handler.send_header("X-Accel-Buffering", "no")
        handler.send_header("Connection", "close")
        end_sse_headers(handler)
        _sse_set_write_deadline(handler)  # Defect A: slow tab can't pin this thread
        while True:
            try:
                event_seq, event, data = output.get(timeout=_SSE_HEARTBEAT_INTERVAL_SECONDS)
            except queue.Empty:
                handler.wfile.write(b": terminal heartbeat\n\n")
                handler.wfile.flush()
                if term.closed.is_set() and output.empty():
                    _sse(handler, "terminal_closed", {"exit_code": term.proc.poll()})
                    break
                continue
            _sse_with_id(handler, event, data, event_id=event_seq)
            if event in ("terminal_closed", "terminal_error"):
                break
    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
        pass
    finally:
        term.unsubscribe(output)
    return True



def _handle_btw(handler, body):
    """POST /api/btw — ephemeral side question using session context.

    Creates a temporary hidden session, streams the answer via SSE, then
    discards the session. The parent session is not modified.
    """
    try:
        require(body, "session_id")
        require(body, "question")
    except ValueError as e:
        return bad(handler, str(e))
    stale_response = _agent_runtime_barrier_response(runner_local_owned=False)
    if stale_response is not None:
        return j(handler, stale_response, status=409)
    if _session_is_subagent_view_only(str(body.get("session_id") or "")):
        return bad(handler, "Subagent sessions are view-only and cannot be used for /btw from WebUI", 400)
    try:
        s = get_session(body["session_id"])
    except KeyError:
        return bad(handler, "Session not found", 404)
    question = str(body["question"]).strip()
    if not question:
        return bad(handler, "question is required")
    # Duplicate-stream guard (same pattern as chat/start)
    current_stream_id = getattr(s, "active_stream_id", None)
    if current_stream_id:
        with STREAMS_LOCK:
            if current_stream_id in STREAMS:
                return j(handler, {"error": "session already has an active stream"}, status=409)
        s.active_stream_id = None
    # Create ephemeral hidden session inheriting context
    from api.models import new_session as _new_session
    model_provider = getattr(s, 'model_provider', None)
    ephemeral = _new_session(
        workspace=s.workspace,
        model=s.model,
        model_provider=model_provider,
        profile=getattr(s, 'profile', None),
    )
    # Copy conversation history for context (agent reads from messages)
    ephemeral.messages = list(s.messages or [])
    ephemeral.title = f"btw: {question[:60]}"
    ephemeral.save()
    stream_id = uuid.uuid4().hex
    ephemeral.active_stream_id = stream_id
    register_session_writeback_owner(ephemeral.session_id, stream_id)
    ephemeral.save()
    stream = create_stream_channel()
    register_stream_owner(stream_id, ephemeral.session_id)
    with STREAMS_LOCK:
        STREAMS[stream_id] = stream
    from api.background import track_btw
    track_btw(body["session_id"], ephemeral.session_id, stream_id, question)
    thr = threading.Thread(
        target=_run_agent_streaming,
        args=(ephemeral.session_id, question, s.model, s.workspace, stream_id, None),
        kwargs={"ephemeral": True, "model_provider": model_provider},
        daemon=True,
    )
    thr.start()
    return j(handler, {"stream_id": stream_id, "session_id": ephemeral.session_id, "parent_session_id": body["session_id"]})


def _handle_background(handler, body):
    """POST /api/background — run prompt in parallel background agent.

    Creates a hidden session, starts streaming in a daemon thread.
    Frontend polls /api/background/status for completed results.
    """
    try:
        require(body, "session_id")
        require(body, "prompt")
    except ValueError as e:
        return bad(handler, str(e))
    stale_response = _agent_runtime_barrier_response(runner_local_owned=False)
    if stale_response is not None:
        return j(handler, stale_response, status=409)
    try:
        s = get_session(body["session_id"])
    except KeyError:
        return bad(handler, "Session not found", 404)
    prompt = str(body["prompt"]).strip()
    if not prompt:
        return bad(handler, "prompt is required")
    from api.models import new_session as _new_session
    model_provider = getattr(s, 'model_provider', None)
    bg = _new_session(
        workspace=s.workspace,
        model=s.model,
        model_provider=model_provider,
        profile=getattr(s, 'profile', None),
    )
    bg.title = f"bg: {prompt[:60]}"
    bg.save()
    stream_id = uuid.uuid4().hex
    bg.active_stream_id = stream_id
    register_session_writeback_owner(bg.session_id, stream_id)
    bg.save()
    stream = create_stream_channel()
    register_stream_owner(stream_id, bg.session_id)
    with STREAMS_LOCK:
        STREAMS[stream_id] = stream
    task_id = uuid.uuid4().hex[:8]
    from api.background import track_background, complete_background
    parent_sid = body["session_id"]
    bg_sid = bg.session_id
    track_background(parent_sid, bg_sid, stream_id, task_id, prompt)

    def _run_bg_and_notify():
        """Run the background agent, then mark the tracked task `done` with the
        last assistant reply so `/api/background/status` can surface it.  Without
        this, `complete_background()` is never called and the result is lost —
        `get_results()` would see a forever-`running` task and return nothing.
        """
        try:
            _run_agent_streaming(
                bg_sid,
                prompt,
                s.model,
                s.workspace,
                stream_id,
                None,
                model_provider=model_provider,
            )
            # Reload the bg session from disk and extract the final assistant reply.
            try:
                from api.models import Session as _Session
                reloaded = _Session.load(bg_sid)
                _answer = ""
                for _m in reversed((reloaded.messages if reloaded else None) or []):
                    if not isinstance(_m, dict) or _m.get("role") != "assistant":
                        continue
                    if _m.get("_error"):
                        continue
                    _content = str(_m.get("content") or "").strip()
                    if _content:
                        _answer = _content
                        break
                complete_background(parent_sid, task_id, _answer or "(no answer produced)")
            except Exception:
                logger.warning("Silent exception in _run_bg_and_notify", exc_info=True)
                complete_background(parent_sid, task_id, "(background task failed)")
            # Best-effort cleanup of the hidden bg session file so it doesn't
            # clutter the sidebar or SESSION_DIR. The index is pruned on the
            # next rebuild via _index_entry_exists().
            try:
                (SESSION_DIR / f"{bg_sid}.json").unlink(missing_ok=True)
            except Exception:
                logger.warning("Silent exception in _run_bg_and_notify", exc_info=True)
                pass
        except Exception:
            logger.warning("Silent exception in _run_bg_and_notify", exc_info=True)
            try:
                complete_background(parent_sid, task_id, "(background task failed)")
            except Exception:
                logger.warning("Silent exception in _run_bg_and_notify", exc_info=True)
                pass

    thr = threading.Thread(target=_run_bg_and_notify, daemon=True)
    thr.start()
    return j(handler, {"task_id": task_id, "stream_id": stream_id, "session_id": bg.session_id})


def _checkpoint_user_message_for_eager_session_save(s, msg: str, attachments, started_at: float | None, source: str = "webui") -> None:
    """Materialize the current user turn for eager first-turn persistence.

    The streaming thread still receives ``pending_user_message`` so existing
    cancel/recovery/final-merge paths keep their current contract. Eager mode
    only adds a durable display-message checkpoint before the agent launches.
    """
    if not msg:
        return
    existing = list(getattr(s, "messages", None) or [])
    if existing:
        latest = existing[-1]
        if isinstance(latest, dict) and latest.get("role") == "user":
            latest_text = " ".join(str(latest.get("content") or "").split())
            msg_text = " ".join(str(msg or "").split())
            if latest_text == msg_text:
                if str(source or "").strip().lower() == "fork":
                    latest["_fork_child_turn"] = s.session_id
                return
    user_msg = {"role": "user", "content": msg}
    from api.process_event_utils import build_active_turn_token, stamp_message_source

    stamp_message_source(
        user_msg,
        source,
        active_turn_token=build_active_turn_token(getattr(s, "active_stream_id", None), started_at),
    )
    if str(source or "").strip().lower() == "fork":
        user_msg["_fork_child_turn"] = s.session_id
    if isinstance(started_at, (int, float)) and started_at > 0:
        user_msg["timestamp"] = float(started_at)
    if attachments:
        user_msg["attachments"] = list(attachments)
    s.messages.append(user_msg)
    # The new user turn is now committed to messages (#3831): advance the
    # truncation watermark to the new message's timestamp so that
    # merge_session_messages_append_only() still filters out replaced
    # pre-edit rows from state.db whose timestamps fall below the boundary.
    # The merge's sidecar_advanced_past_watermark guard (models.py:5172)
    # allows state.db rows newer than the watermark, so post-edit turns
    # are not dropped. Never 0.0 (the truncate-to-empty sentinel, #2914).
    if getattr(s, "truncation_watermark", None):
        s.truncation_watermark = user_msg.get("timestamp") or time.time()


def _is_default_or_empty_session_title(title) -> bool:
    return str(title or "").strip() in ("", "Untitled", "New Chat")


def _provisional_title_from_prompt(prompt: str, fallback: str = "Untitled") -> str:
    text = str(prompt or "").strip()
    if not text:
        return fallback
    return title_from([{"role": "user", "content": text}], fallback) or fallback


_RETAINED_CONTEXT_USER_UNSET = object()


def _prepare_chat_start_session_for_stream(
    s,
    *,
    msg: str,
    attachments,
    workspace: str,
    model: str,
    model_provider,
    stream_id: str,
    started_at: float | None = None,
    source: str = "webui",
    retained_user=None,
    retained_context_user=_RETAINED_CONTEXT_USER_UNSET,
    defer_save: bool = False,
):
    """Persist chat-start state according to webui.session_save_mode.

    ``deferred`` keeps the existing sidecar/WAL-backed behaviour: save pending
    fields but leave the display transcript empty until the agent merges the
    result. ``eager`` additionally writes the current user turn into messages so
    a process restart immediately after /api/chat/start preserves the prompt as
    a normal session message. Empty sessions are never saved here because this
    helper only runs after a non-empty message is validated.
    """
    effective_source = (
        "fork"
        if str(getattr(s, "session_source", None) or "").strip().lower() == "fork"
        else source
    )
    s.workspace = workspace
    s.model = model
    s.model_provider = model_provider
    s.active_stream_id = stream_id
    register_session_writeback_owner(s.session_id, stream_id)
    s.post_compression_context_tokens_estimate = None
    s.pending_user_message = msg
    s.pending_attachments = attachments
    s.pending_started_at = started_at if started_at is not None else time.time()
    s.pending_user_source = effective_source
    if retained_user is not None:
        from api.process_event_utils import build_active_turn_token

        retained_user["timestamp"] = s.pending_started_at
        active_turn_token = build_active_turn_token(stream_id, s.pending_started_at)
        retained_user["_active_turn_token"] = active_turn_token
        if str(effective_source or "").strip().lower() == "fork":
            retained_user["_fork_child_turn"] = s.session_id
        if retained_context_user is not _RETAINED_CONTEXT_USER_UNSET:
            if retained_context_user is not None and not any(
                row is retained_context_user
                for row in list(getattr(s, "context_messages", None) or [])
            ):
                raise RuntimeError("regeneration retained context row is not installed")
            if isinstance(retained_context_user, dict):
                retained_context_user["timestamp"] = s.pending_started_at
                retained_context_user["_active_turn_token"] = active_turn_token
                if str(effective_source or "").strip().lower() == "fork":
                    retained_context_user["_fork_child_turn"] = s.session_id
        else:
            retained_id = retained_user.get("id") or retained_user.get("message_id")
            retained_old_timestamp = retained_user.get("timestamp")
            retained_old_content = retained_user.get("content")
            for context_row in reversed(list(getattr(s, "context_messages", None) or [])):
                if not isinstance(context_row, dict) or context_row.get("role") != "user":
                    continue
                context_id = context_row.get("id") or context_row.get("message_id")
                id_match = retained_id is not None and context_id == retained_id
                old_shape_match = (
                    retained_old_timestamp is not None
                    and context_row.get("timestamp") == retained_old_timestamp
                    and context_row.get("content") == retained_old_content
                )
                if not (id_match or old_shape_match):
                    continue
                context_row["timestamp"] = s.pending_started_at
                context_row["_active_turn_token"] = active_turn_token
                if str(effective_source or "").strip().lower() == "fork":
                    context_row["_fork_child_turn"] = s.session_id
                break
    current_title = getattr(s, "title", None)
    if retained_user is None and _is_default_or_empty_session_title(current_title):
        provisional_title = _provisional_title_from_prompt(msg, current_title or "Untitled")
        if provisional_title and not _is_default_or_empty_session_title(provisional_title):
            s.title = provisional_title
    if retained_user is None and get_webui_session_save_mode() == "eager":
        _checkpoint_user_message_for_eager_session_save(
            s,
            msg,
            attachments,
            s.pending_started_at,
            source=effective_source,
        )
    if not defer_save:
        s.save()


def _is_hidden_empty_session(s) -> bool:
    return (
        getattr(s, "title", "Untitled") == "Untitled"
        and not getattr(s, "messages", None)
        and not getattr(s, "active_stream_id", None)
        and not getattr(s, "pending_user_message", None)
        and not getattr(s, "worktree_path", None)
    )


def _active_stream_blocks_chat_start(session, stream_id: str | None) -> bool:
    """Return whether an active_stream_id still owns this session's next turn.

    ``active_stream_id`` is written before the SSE channel is registered, so a
    very fresh pending turn must also block duplicate chat_start requests. If we
    only check STREAMS here, a second request can race through the registration
    gap and overwrite the sidecar owner.
    """
    if not stream_id:
        return False
    with STREAMS_LOCK:
        if stream_id in STREAMS:
            return True
    try:
        from api import config as _live_config
        with _live_config.ACTIVE_RUNS_LOCK:
            if stream_id in (_live_config.ACTIVE_RUNS or {}):
                return True
    except Exception:
        logger.debug("Silent exception in _active_stream_blocks_chat_start", exc_info=True)
        pass
    if getattr(session, "pending_user_message", None):
        try:
            from api.models import _REPAIR_STALE_PENDING_GRACE_SECONDS
            grace_seconds = float(_REPAIR_STALE_PENDING_GRACE_SECONDS)
        except Exception:
            logger.debug("Silent exception in _active_stream_blocks_chat_start", exc_info=True)
            grace_seconds = 30.0
        try:
            pending_started_at = float(getattr(session, "pending_started_at", None) or 0)
        except Exception:
            logger.debug("Silent exception in _active_stream_blocks_chat_start", exc_info=True)
            pending_started_at = 0.0
        if pending_started_at and time.time() - pending_started_at < grace_seconds:
            return True
    return False


def _start_regeneration_stream_locked(
    s,
    *,
    turn,
    workspace: str,
    model: str,
    model_provider,
    normalized_model: bool,
    diag,
    goal_related: bool,
    source: str,
    moa_config,
    backend_is_gateway: bool,
):
    """Commit a retained-row regeneration before releasing its real worker."""
    from api.session_ops import (
        RegenerationUnavailable,
        apply_regeneration_plan,
        plan_regeneration,
        restore_regeneration_state,
        snapshot_regeneration_state,
    )

    try:
        plan = plan_regeneration(
            s, expected_revision=turn.revision, lock_held=True
        )
        turn = plan.turn
    except RegenerationUnavailable as exc:
        return {
            "error": str(exc),
            "code": exc.code,
            "_status": exc.status,
        }
    # Snapshot only after lock-held authority validation, before mutation.
    snapshot = snapshot_regeneration_state(s)
    if compression_recovery_payload_for_session(s):
        clear_compression_recovery(s)
    stream_id = uuid.uuid4().hex
    gateway_starting = False
    thread_started = False
    save_attempted = False
    accepted = False
    journal_event = {}
    release_worker = threading.Event()
    abort_worker = threading.Event()
    worker_thread = None

    worker_target = (
        _run_gateway_chat_streaming if backend_is_gateway else _run_agent_streaming
    )
    worker_kwargs = {
        "model_provider": model_provider,
        "goal_related": goal_related,
    }
    if backend_is_gateway:
        worker_kwargs["regeneration"] = True
    if moa_config and not backend_is_gateway:
        worker_kwargs["moa_config"] = moa_config

    def _gated_worker():
        release_worker.wait()
        if abort_worker.is_set():
            return
        worker_target(
            s.session_id,
            turn.message_text,
            model,
            workspace,
            stream_id,
            copy.deepcopy(turn.attachments),
            **worker_kwargs,
        )

    def _cleanup_owned_start():
        if goal_related:
            STREAM_GOAL_RELATED.pop(stream_id, None)
        with STREAMS_LOCK:
            STREAMS.pop(stream_id, None)
        unregister_stream_owner(stream_id)
        clear_session_writeback_owner_if_owned(s.session_id, stream_id)
        if gateway_starting:
            try:
                from api.gateway_chat import (
                    _clear_gateway_run_starting,
                    _finish_gateway_run_starting,
                )

                _finish_gateway_run_starting(stream_id)
                _clear_gateway_run_starting(stream_id)
            except Exception:
                logger.debug(
                    "Failed to clear compensated gateway start %s",
                    stream_id,
                    exc_info=True,
                )

    try:
        applied, retained_context_user = apply_regeneration_plan(
            s,
            plan,
            return_context_user=True,
        )
        if not applied:
            restore_regeneration_state(s, snapshot)
            return {
                "error": "Session changed while regeneration was being prepared.",
                "code": "stale_regeneration_revision",
                "_status": 409,
            }
        retained_user = s.messages[-1]
        msg = turn.message_text
        attachments = copy.deepcopy(turn.attachments)
        was_hidden_empty_session = _is_hidden_empty_session(s)
        _prepare_chat_start_session_for_stream(
            s,
            msg=msg,
            attachments=attachments,
            workspace=workspace,
            model=model,
            model_provider=model_provider,
            stream_id=stream_id,
            source=turn.source,
            retained_user=retained_user,
            retained_context_user=retained_context_user,
            defer_save=True,
        )

        diag.stage("turn_journal_submitted") if diag else None
        from api.turn_journal import append_turn_journal_event

        journal_event = append_turn_journal_event(
            s.session_id,
            {
                "event": "submitted",
                "stream_id": stream_id,
                "role": "user",
                "content": msg,
                "attachments": attachments,
                "workspace": workspace,
                "model": model,
                "model_provider": model_provider,
                "created_at": s.pending_started_at,
            },
        )
        diag.stage("stream_registration") if diag else None
        stream = create_stream_channel()
        register_stream_owner(stream_id, s.session_id)
        with STREAMS_LOCK:
            STREAMS[stream_id] = stream
        if goal_related:
            STREAM_GOAL_RELATED[stream_id] = True
        if backend_is_gateway:
            from api.gateway_chat import _mark_gateway_run_starting

            gateway_starting = True
            _mark_gateway_run_starting(stream_id)

        diag.stage("worker_thread_start") if diag else None
        worker_thread = threading.Thread(target=_gated_worker, daemon=True)
        worker_thread.start()
        thread_started = True
        save_attempted = True
        s.save()
        accepted = True
        set_last_workspace(workspace)
        release_worker.set()
    except Exception as exc:
        abort_worker.set()
        release_worker.set()
        if (
            thread_started
            and worker_thread is not None
            and callable(getattr(worker_thread, "join", None))
        ):
            worker_thread.join(timeout=1)
        _cleanup_owned_start()
        if accepted:
            if journal_event:
                try:
                    append_turn_journal_event(
                        s.session_id,
                        {
                            "event": "interrupted",
                            "stream_id": stream_id,
                            "turn_id": journal_event.get("turn_id"),
                            "reason": "post_acceptance_workspace_failure",
                        },
                    )
                except Exception:
                    logger.warning("Failed to close accepted regeneration journal", exc_info=True)
            exc._regeneration_accepted = True
            raise
        restore_regeneration_state(s, snapshot)
        if save_attempted:
            try:
                s.save(touch_updated_at=False)
            except Exception:
                logger.exception(
                    "Failed to persist compensated regeneration for %s",
                    s.session_id,
                )
        if journal_event:
            try:
                append_turn_journal_event(
                    s.session_id,
                    {
                        "event": "interrupted",
                        "stream_id": stream_id,
                        "turn_id": journal_event.get("turn_id"),
                        "reason": "start_compensated",
                    },
                )
            except Exception:
                logger.warning(
                    "Failed to close compensated turn journal event",
                    exc_info=True,
                )
        raise

    release_worker.set()
    if was_hidden_empty_session:
        publish_session_list_changed(
            "session_new",
            profile=getattr(s, "profile", None),
            session_id=getattr(s, "session_id", None),
        )
    response = {
        "stream_id": stream_id,
        "session_id": s.session_id,
        "pending_started_at": s.pending_started_at,
        "turn_id": journal_event.get("turn_id"),
        "title": s.title,
    }
    if normalized_model:
        response["effective_model"] = model
    if model_provider:
        response["effective_model_provider"] = model_provider
    return response


def _active_run_stream_for_session(session_id: str | None) -> str | None:
    """Return a live worker stream for this session even if sidecar stream id is clear.

    cancel_stream() intentionally clears ``session.active_stream_id`` before the
    worker thread fully exits so Stop remains responsive. During that unwind
    window ACTIVE_RUNS is the worker-lifecycle truth; a successor chat/start for
    the same session must wait or it can reuse the cached agent while the old
    interrupt is still landing (#3808).

    Bounded: the post-cancel unwind is short (dominated by the worker finally's
    ``_ckpt_thread.join(timeout=15)``), and ``unregister_active_run`` runs in that
    finally, so a healthy worker leaves ACTIVE_RUNS within seconds. A detached /
    wedged worker that never reaches its finally (e.g. stuck in a provider call,
    or leaked by SIGKILL without restart) must NOT 409 the session forever — so an
    entry older than the unwind ceiling (180s) is treated as stale and ignored
    here. For a phase="cancelling" row the ceiling is anchored on the cancel time
    (``cancelled_at``), never on the original run start: cancel_stream() removes
    STREAMS itself, so absence from STREAMS is not worker-death proof, and a
    long-running turn that was just cancelled must not be reaped (and a successor
    admitted) while the old worker is still alive (#6623). A legitimately
    long-running turn keeps ``active_stream_id`` SET and is handled by
    ``_active_stream_blocks_chat_start`` above; this guard only covers the
    cleared-stream-id unwind window. (Codex brick-gate hardening, #3822.)
    """
    sid = str(session_id or "").strip()
    if not sid:
        return None
    ceiling = 180.0  # generous vs the 15s checkpoint-join unwind; finite to avoid permanent-409
    now = time.time()
    try:
        from api import config as _live_config
        # Snapshot the live-worker set BEFORE taking ACTIVE_RUNS_LOCK (sequential,
        # not nested, so no lock-ordering/deadlock risk). A long turn whose
        # active_stream_id was already cleared during final writeback can still be
        # mid-teardown — its STREAMS entry present — past the age ceiling, so age
        # alone must NOT pop its lifecycle row (health / background-wakeup /
        # active-agent-cache consumers read ACTIVE_RUNS as worker-lifecycle truth).
        with _live_config.STREAMS_LOCK:
            live_stream_ids = set(_live_config.STREAMS.keys())
        stale_stream_ids = []
        with _live_config.ACTIVE_RUNS_LOCK:
            for run_stream_id, raw in list((_live_config.ACTIVE_RUNS or {}).items()):
                stream_id = str((raw or {}).get("stream_id") or run_stream_id or "").strip()
                run_sid = str((raw or {}).get("session_id") or "").strip()
                if run_sid != sid or not stream_id:
                    continue
                try:
                    started_at = float((raw or {}).get("started_at") or 0)
                except (TypeError, ValueError):
                    started_at = 0.0
                # #6623 re-gate: cancel_stream() removes STREAMS itself, so a
                # missing SSE channel is NOT proof that a cancelled worker is
                # dead. For a phase="cancelling" row the unwind ceiling must be
                # anchored on the CANCEL time (cancelled_at, started_at as a
                # legacy fallback), never on the original run start: a turn that
                # ran for minutes and was just cancelled would otherwise be
                # reaped the instant its started_at crosses the ceiling and a
                # successor admitted while the old worker is still alive. A
                # recently cancelled run therefore keeps blocking a successor
                # (this function returns its stream id) until the worker either
                # unwinds (its finally unregisters the row within seconds) or
                # the cancel itself has been outstanding past the ceiling.
                _run_phase = str((raw or {}).get("phase") or "").strip()
                if _run_phase == "cancelling":
                    try:
                        _age_anchor = float(
                            (raw or {}).get("cancelled_at") or started_at or 0
                        )
                    except (TypeError, ValueError):
                        _age_anchor = started_at
                else:
                    _age_anchor = started_at
                # Past the unwind ceiling: never block a successor on it (the
                # anti-permanent-409 guarantee, #3822). Additionally reconcile the
                # zombie out of ACTIVE_RUNS so health/recovery polling stops seeing a
                # half-alive run — but ONLY when the worker is truly gone from
                # STREAMS, so a still-live / still-tearing-down worker keeps its
                # lifecycle row. Pop by the real dict key. (Codex gate, #4492)
                if _age_anchor and (now - _age_anchor) > ceiling:
                    if run_stream_id not in live_stream_ids and stream_id not in live_stream_ids:
                        stale_stream_ids.append(run_stream_id)
                    continue
                return stream_id
            for stale_stream_id in stale_stream_ids:
                (_live_config.ACTIVE_RUNS or {}).pop(stale_stream_id, None)
                # The zombie run is pruned directly here (not via the normal teardown
                # finally / unregister_active_run), so release its stream-owner entry too
                # or STREAM_SESSION_OWNERS leaks for every reconciled zombie. (#5198 gate)
                unregister_stream_owner(stale_stream_id)
    except Exception:
        logger.debug("Silent exception in _active_run_stream_for_session", exc_info=True)
        return None
    return None


def _agent_runtime_barrier_response(
    *,
    runner_local_owned: bool = False,
    external_runtime_owned: bool | None = None,
) -> dict | None:
    """Return the typed stale-runtime response for local in-process turns."""
    if external_runtime_owned is True:
        return None
    if runner_local_owned and webui_gateway_chat_enabled(get_config()):
        return None
    from api.runtime_adapter import runtime_adapter_runner_enabled

    if runner_local_owned and runtime_adapter_runner_enabled():
        return None
    try:
        ensure_agent_runtime_current()
    except AgentRuntimeChangedError as exc:
        return {
            "error": str(exc),
            "type": "agent_runtime_stale",
            "retryable": True,
        }
    return None


def _start_chat_stream_for_session(
    s,
    *,
    msg: str,
    attachments=None,
    workspace: str,
    model: str,
    model_provider=None,
    normalized_model: bool = False,
    diag=None,
    goal_related: bool = False,
    source: str = "webui",
    moa_config=None,
    external_runtime_owned: bool | None = None,
    regeneration=None,
):
    """Persist pending state, register an SSE channel, and start an agent turn."""
    if external_runtime_owned is None:
        external_runtime_owned = webui_gateway_chat_enabled(get_config())
    backend_is_gateway = bool(external_runtime_owned)
    stale_response = _agent_runtime_barrier_response(
        external_runtime_owned=backend_is_gateway,
    )
    if stale_response is not None:
        stale_response["_status"] = 409
        return stale_response
    attachments = attachments or []
    # Prevent duplicate runs in the same session while a stream is still active.
    # This commonly happens after page refresh/reconnect races and can produce
    # duplicated clarify cards for what appears to be a single user request.
    diag.stage("active_stream_check") if diag else None
    current_stream_id = getattr(s, "active_stream_id", None)
    if current_stream_id:
        if _active_stream_blocks_chat_start(s, current_stream_id):
            diag.stage("response_write") if diag else None
            return {
                "error": "session already has an active stream",
                "active_stream_id": current_stream_id,
                "_status": 409,
            }
        # Stale stream id from a previous run; clear and continue.
        diag.stage("stale_stream_cleanup") if diag else None
        _clear_stale_stream_state(s)

    # #1932: check if this session has a pending goal continuation flag.
    # The streaming hook sets PENDING_GOAL_CONTINUATION when goal_continue fires,
    # so the next chat/start for this session is automatically treated as goal-related.
    if not goal_related and s.session_id in PENDING_GOAL_CONTINUATION:
        goal_related = True
        PENDING_GOAL_CONTINUATION.discard(s.session_id)

    # process_complete wakeup (ours-original, Option B): if this session has a
    # pending process_complete marker (set by api/background_process.py drain),
    # discard it atomically here. Mirrors the goal_continue pattern (#1932).
    # The marker is server-internal telemetry; the actual wakeup is delivered
    # either server-side (Option Z) or via the PR #2279 next-turn drain.
    if s.session_id in PENDING_BG_TASK_COMPLETIONS:
        PENDING_BG_TASK_COMPLETIONS.discard(s.session_id)

    session_lock = _get_session_agent_lock(s.session_id)
    diag.stage("session_lock_wait") if diag else None
    while True:
        with session_lock:
            locked_stream_id = getattr(s, "active_stream_id", None)
            if locked_stream_id:
                if _active_stream_blocks_chat_start(s, locked_stream_id):
                    diag.stage("response_write") if diag else None
                    return {
                        "error": "session already has an active stream",
                        "active_stream_id": locked_stream_id,
                        "_status": 409,
                    }
                needs_stale_cleanup = True
            else:
                blocking_run_stream_id = _active_run_stream_for_session(s.session_id)
                if blocking_run_stream_id:
                    diag.stage("response_write") if diag else None
                    return {
                        "error": "session already has an active stream",
                        "active_stream_id": blocking_run_stream_id,
                        "_status": 409,
                    }
                needs_stale_cleanup = False
                if regeneration is not None:
                    return _start_regeneration_stream_locked(
                        s,
                        turn=regeneration,
                        workspace=workspace,
                        model=model,
                        model_provider=model_provider,
                        normalized_model=normalized_model,
                        diag=diag,
                        goal_related=goal_related,
                        source=source,
                        moa_config=moa_config,
                        backend_is_gateway=backend_is_gateway,
                    )
                stream_id = uuid.uuid4().hex
                diag.stage("save_pending_state") if diag else None
                was_hidden_empty_session = _is_hidden_empty_session(s)
                _prepare_chat_start_session_for_stream(
                    s,
                    msg=msg,
                    attachments=attachments,
                    workspace=workspace,
                    model=model,
                    model_provider=model_provider,
                    stream_id=stream_id,
                    source=source,
                )
                break
        if needs_stale_cleanup:
            diag.stage("stale_stream_cleanup") if diag else None
            cleared = _clear_stale_stream_state(s)
            if not cleared and getattr(s, "active_stream_id", None):
                diag.stage("response_write") if diag else None
                return {
                    "error": "session already has an active stream",
                    "active_stream_id": getattr(s, "active_stream_id", None),
                    "_status": 409,
                }
    if was_hidden_empty_session:
        publish_session_list_changed(
            "session_new",
            profile=getattr(s, "profile", None),
            session_id=getattr(s, "session_id", None),
        )
    diag.stage("turn_journal_submitted") if diag else None
    journal_event = {}
    try:
        from api.turn_journal import append_turn_journal_event
        journal_event = append_turn_journal_event(
            s.session_id,
            {
                "event": "submitted",
                "stream_id": stream_id,
                "role": "user",
                "content": msg,
                "attachments": attachments,
                "workspace": workspace,
                "model": model,
                "model_provider": model_provider,
                "created_at": s.pending_started_at,
            },
        )
    except Exception:
        logger.warning("Failed to append submitted turn journal event", exc_info=True)
    diag.stage("set_last_workspace") if diag else None
    set_last_workspace(workspace)
    diag.stage("stream_registration") if diag else None
    stream = create_stream_channel()
    register_stream_owner(stream_id, s.session_id)
    with STREAMS_LOCK:
        STREAMS[stream_id] = stream
    # #1932: mark stream as goal-related so the streaming hook evaluates the goal.
    if goal_related:
        STREAM_GOAL_RELATED[stream_id] = True
    diag.stage("worker_thread_start") if diag else None
    worker_target = _run_gateway_chat_streaming if backend_is_gateway else _run_agent_streaming
    worker_kwargs = {"model_provider": model_provider, "goal_related": goal_related}
    if moa_config and not backend_is_gateway:
        worker_kwargs["moa_config"] = moa_config
    if backend_is_gateway:
        from api.gateway_chat import _mark_gateway_run_starting
        _mark_gateway_run_starting(stream_id)
    thr = threading.Thread(
        target=worker_target,
        args=(s.session_id, msg, model, workspace, stream_id, attachments),
        kwargs=worker_kwargs,
        daemon=True,
    )
    try:
        thr.start()
    except Exception:
        if backend_is_gateway:
            try:
                from api.gateway_chat import _finish_gateway_run_starting
                _finish_gateway_run_starting(stream_id)
                from api.gateway_chat import _clear_gateway_run_starting
                _clear_gateway_run_starting(stream_id)
            except Exception:
                logger.debug("Failed to record gateway run-start failure for stream %s", stream_id, exc_info=True)
        raise
    response = {
        "stream_id": stream_id,
        "session_id": s.session_id,
        "pending_started_at": s.pending_started_at,
        "turn_id": journal_event.get("turn_id"),
        "title": s.title,
    }
    if normalized_model:
        response["effective_model"] = model
    if model_provider:
        response["effective_model_provider"] = model_provider
    return response


def _runtime_runner_client_factory():
    """Return the configured runner-local client.

    `runner-local` remains default-off and bounded: without an explicit runner
    endpoint this factory preserves the existing "runner-local chat backend is
    not configured" 501 path. When
    `HERMES_WEBUI_RUNNER_BASE_URL` is set, the WebUI process only acts as a
    transport client; the runner endpoint owns execution, run ids, replay, and
    controls.
    """
    # Keep this literal here for route-level contract tests and readable 501 provenance:
    # "runner-local chat backend is not configured"
    from api.runner_client import HttpRunnerClient

    return HttpRunnerClient.from_env()


def _chat_start_response_from_run_start(result):
    """Expose only the legacy browser-facing chat-start response fields."""
    payload = dict(getattr(result, "payload", {}) or {})
    response = {}
    for key in (
        "stream_id",
        "session_id",
        "pending_started_at",
        "turn_id",
        "title",
        "effective_model",
        "effective_model_provider",
        "error",
        "code",
        "active_stream_id",
        "_status",
    ):
        if key in payload:
            response[key] = payload[key]
    response.setdefault("stream_id", result.stream_id)
    response.setdefault("session_id", result.session_id)
    return response


def _runtime_adapter_goal_action(goal_args: str) -> str:
    """Return the bounded RuntimeAdapter goal action for WebUI /goal args."""
    action = str(goal_args or "").strip().lower()
    if not action or action == "status":
        return "status"
    if action in ("pause", "resume"):
        return action
    if action in ("clear", "stop", "done"):
        return "clear"
    return "set"


def _start_run(
    s,
    *,
    msg: str,
    attachments,
    workspace: str,
    model,
    model_provider,
    normalized_model,
    source: str,
    route: str,
    diag=None,
    moa_config=None,
    gateway_chat_enabled: bool | None = None,
    regeneration=None,
):
    """Shared start-run helper for /api/chat/start and start_session_turn.

    Centralizes the runtime-adapter selection block (Q-2979-A2 / Copilot
    discussion_r3305864087/r3305864173) so both entrypoints honor
    ``runtime_adapter_enabled()`` / ``runtime_adapter_runner_enabled()`` the
    same way. Prior to this helper ``start_session_turn`` bypassed the
    adapter path entirely, so a process-wakeup turn skipped the adapter that
    a human-typed turn would have hit — a behavioral divergence.

    ``source`` is the StartRunRequest.source (``"webui"`` for browser POSTs,
    ``"process_wakeup"`` for the drain-thread wakeup). ``route`` is the
    metadata.route label that lands on the run record for observability.

    Returns a dict with ``_status`` plus the legacy chat-start response
    fields (``stream_id``, ``session_id``, etc.). Adapter selection that
    returns no adapter is surfaced as ``{"error": str(exc), "_status": 501}``
    so both call sites can map it onto their own HTTP shape.
    """
    from api.runtime_adapter import (
        LegacyJournalRuntimeAdapter,
        StartRunRequest,
        build_runtime_adapter,
        runtime_adapter_enabled,
        runtime_adapter_runner_enabled,
    )

    if runtime_adapter_enabled() or runtime_adapter_runner_enabled():
        if regeneration is not None and runtime_adapter_runner_enabled():
            return {"error": "Regeneration is not supported by the runner backend.", "code": "unsupported_regeneration_backend", "_status": 409}
        def _legacy_start_run(request: StartRunRequest) -> dict:
            return _start_chat_stream_for_session(
                s,
                msg=request.message,
                attachments=request.attachments,
                workspace=request.workspace or workspace,
                model=request.model or model,
                model_provider=request.provider or model_provider,
                normalized_model=normalized_model,
                diag=diag,
                source=request.source or source,
                moa_config=moa_config,
                external_runtime_owned=gateway_chat_enabled,
                regeneration=regeneration,
            )

        def _legacy_adapter_factory():
            return LegacyJournalRuntimeAdapter(start_run_delegate=_legacy_start_run)

        try:
            adapter = build_runtime_adapter(
                legacy_adapter_factory=_legacy_adapter_factory,
                runner_client_factory=_runtime_runner_client_factory,
            )
            if adapter is None:
                raise NotImplementedError("runtime adapter selection returned no adapter")
            result = adapter.start_run(
                StartRunRequest(
                    session_id=s.session_id,
                    message=msg,
                    attachments=attachments,
                    workspace=workspace,
                    profile=getattr(s, "profile", None),
                    provider=model_provider,
                    model=model,
                    source=source,
                    metadata={"route": route},
                )
            )
        except NotImplementedError as exc:
            return {"error": str(exc), "_status": 501}
        return _chat_start_response_from_run_start(result)

    return _start_chat_stream_for_session(
        s,
        msg=msg,
        attachments=attachments,
        workspace=workspace,
        model=model,
        model_provider=model_provider,
        normalized_model=normalized_model,
        diag=diag,
        source=source,
        moa_config=moa_config,
        external_runtime_owned=gateway_chat_enabled,
        regeneration=regeneration,
    )


def _process_wakeup_revalidation_provider(model, provider) -> str:
    """Return the canonical provider id used for wakeup credential revalidation."""
    try:
        _resolved_model, resolved_provider = canonical_model_provider_lane(model, provider)
    except Exception:
        logger.debug(
            "failed to canonicalize process_wakeup revalidation lane for model=%r provider=%r",
            model,
            provider,
            exc_info=True,
        )
        resolved_provider = None
    candidate = resolved_provider if resolved_provider else provider
    return str(candidate or "").strip()


def _process_wakeup_provider_has_recovery_credential(
    session,
    *,
    model,
    provider,
    provider_id: str | None = None,
) -> bool:
    """Check paused credential-pool recovery in the owning session profile."""
    provider_id = str(
        provider_id or _process_wakeup_revalidation_provider(model, provider) or ""
    ).strip()
    if not provider_id:
        return False
    profile_name = str(getattr(session, "profile", "") or "").strip()
    if profile_name and not _is_root_profile(profile_name):
        with profile_scope_for_detached_worker(
            profile_name,
            "process_wakeup credential revalidation",
            logger_override=logger,
        ):
            return provider_has_process_wakeup_recovery_credential(provider_id, refresh=True)
    return provider_has_process_wakeup_recovery_credential(provider_id, refresh=True)


def _refresh_process_wakeup_pause_credential_fingerprint(session) -> bool:
    """Refresh the stored credential fingerprint without clearing the pause."""
    pause = getattr(session, "process_wakeup_pause", None)
    if not isinstance(pause, dict) or not pause.get("paused"):
        return False
    updated = dict(pause)
    updated["credential_state_fingerprint"] = process_wakeup_credential_state_fingerprint(session)
    session.process_wakeup_pause = updated
    return True


def start_session_turn(
    session_id: str,
    message: str,
    *,
    source: str = "process_wakeup",
):
    """Start a server-side agent turn for ``session_id`` with ``message``.

    Option Z primary wakeup entrypoint. This is the minimal, HTTP-handler-free
    core that ``/api/chat/start`` already reaches via ``_handle_chat_start`` →
    ``_start_chat_stream_for_session``. The drain thread
    (``api/background_process._process_one``) calls this directly with a
    synthetic ``[IMPORTANT: …]`` wakeup_prompt so a background process can wake
    the agent server-side with NO browser round-trip — exactly how CLI /
    gateway self-wake from a ``notify_on_complete`` completion.

    Contract:
      - Resolves the session record (profile/workspace/model/model_provider are
        already persisted on it; no user auth needed — same trust level as
        gateway/cron starting a turn).
      - Resolves workspace + model/provider through the SAME helpers
        ``_handle_chat_start`` uses, so a process-wakeup turn is constructed
        identically to a human-typed turn. If the session record has no model
        persisted, ``_resolve_compatible_session_model_state`` falls back to the
        configured default model/provider (documented in the impl report §1).
      - Delegates to ``_start_chat_stream_for_session`` which spawns the agent
        on a daemon worker thread (the drain thread NEVER blocks) and serializes
        on the per-session agent lock + active-stream guard, so a concurrent
        human ``/api/chat/start`` cannot double-start (one wins, the other gets
        the existing 409 "session already has an active stream").

    Returns the same dict ``_start_chat_stream_for_session`` returns, including
    ``_status`` (200 on start, 409 when a turn is already active). On 409 the
    caller must leave the ``PENDING_BG_TASK_COMPLETIONS`` marker in place so the
    PR #2279 next-turn drain delivers the wakeup when the active turn ends.
    """
    msg = str(message or "").strip()
    if _is_silent_control_message(msg):
        return {
            "status": "suppressed",
            "reason": "silent_control_message",
            "_status": 200,
        }
    if not msg:
        return {"error": "message is required", "_status": 400}
    stale_response = _agent_runtime_barrier_response(runner_local_owned=True)
    if stale_response is not None:
        stale_response["_status"] = 409
        return stale_response
    turn_source = str(source or "process_wakeup").strip() or "process_wakeup"
    try:
        s = get_session(session_id)
    except KeyError:
        return {"error": "Session not found", "_status": 404}

    try:
        workspace = _resolve_chat_workspace_with_recovery(s, None)
    except WorkspaceBindingPersistenceError as e:
        return {"error": str(e), "_status": 500}
    except ValueError as e:
        return {"error": str(e), "_status": 400}

    requested_model = s.model
    requested_provider = getattr(s, "model_provider", None)
    # Server-initiated wakeup (Option Z): resolve persisted model via the
    # standard helper in cache-only mode so wakeups never trigger a cold
    # catalog rebuild. Thread the session's PROFILE model defaults through too
    # (mirrors _handle_chat_start) — a brand-new session that spawned a
    # background task before its first human turn has an empty s.model, and
    # without the profile defaults the resolver would fall back to the global
    # DEFAULT_MODEL instead of the profile's configured default (greptile flag).
    _pp_provider, _pp_default, _pp_cfg = _read_profile_model_config(s, requested_provider)
    model, model_provider, normalized_model = _resolve_compatible_session_model_state(
        requested_model,
        requested_provider,
        profile_provider=_pp_provider,
        profile_default_model=_pp_default,
        profile_config=_pp_cfg,
        prefer_cached_catalog=True,
    )
    _paused_wakeup_response = None
    with _get_session_agent_lock(s.session_id):
        try:
            s = get_session(session_id)
        except KeyError:
            return {"error": "Session not found", "_status": 404}
        if clear_process_wakeup_pause_if_model_changed(
            s,
            model=model,
            provider=model_provider,
        ):
            try:
                s.save(touch_updated_at=False)
            except Exception:
                logger.debug(
                    "failed to persist process_wakeup pause reset for session %s",
                    session_id,
                    exc_info=True,
                )
        if turn_source == "process_wakeup":
            _credential_state_changed = False
            try:
                _credential_state_changed = process_wakeup_pause_credential_state_changed(s)
            except Exception:
                logger.debug(
                    "failed to compare process_wakeup credential state for session %s",
                    session_id,
                    exc_info=True,
                )
            if process_wakeup_pause_matches(
                s,
                model=model,
                provider=model_provider,
                classification='credential_pool_empty',
            ):
                _credential_recovered = False
                _credential_revalidation_provider = _process_wakeup_revalidation_provider(
                    model,
                    model_provider,
                )
                try:
                    _credential_recovered = _process_wakeup_provider_has_recovery_credential(
                        s,
                        model=model,
                        provider=model_provider,
                        provider_id=_credential_revalidation_provider,
                    )
                except Exception:
                    logger.debug(
                        "failed to revalidate process_wakeup credential availability for session %s",
                        session_id,
                        exc_info=True,
                    )
                if _credential_recovered:
                    _recovery_reason = (
                        'credential_state_changed'
                        if _credential_state_changed
                        else 'credential_recovered'
                    )
                    if clear_process_wakeup_pause(s, reason=_recovery_reason):
                        try:
                            s.save(touch_updated_at=False)
                        except Exception:
                            logger.debug(
                                "failed to persist process_wakeup credential recovery reset for session %s",
                                session_id,
                                exc_info=True,
                            )
                elif _credential_state_changed:
                    if _refresh_process_wakeup_pause_credential_fingerprint(s):
                        try:
                            s.save(touch_updated_at=False)
                        except Exception:
                            logger.debug(
                                "failed to persist process_wakeup credential-state fingerprint refresh for session %s",
                                session_id,
                                exc_info=True,
                            )
            _paused_wakeup = suppress_process_wakeup_for_provider_pause(
                s,
                model=model,
                provider=model_provider,
                classification='credential_pool_empty',
            )
            if _paused_wakeup is not None:
                try:
                    PENDING_BG_TASK_COMPLETIONS.discard(s.session_id)
                except Exception:
                    logger.debug(
                        "failed to discard pending bg-task marker for paused wakeup %s",
                        session_id,
                        exc_info=True,
                    )
                try:
                    s.save(touch_updated_at=False)
                except Exception:
                    logger.debug(
                        "failed to persist process_wakeup suppression for session %s",
                        session_id,
                        exc_info=True,
                    )
                _paused_wakeup_response = {
                    "error": PROCESS_WAKEUP_PAUSE_ERROR,
                    "message": (
                        "Automatic process wakeups are paused for this session because "
                        "the provider credential pool is unavailable."
                    ),
                    "process_wakeup_pause": _paused_wakeup,
                    "_status": 409,
                }
    if _paused_wakeup_response is not None:
        return _paused_wakeup_response
    resp = _start_run(
        s,
        msg=msg,
        attachments=[],
        workspace=workspace,
        model=model,
        model_provider=model_provider,
        normalized_model=normalized_model,
        source=turn_source,
        route="start_session_turn",
    )

    # ── Defect B: live-view of server-initiated turns ──────────────────────
    # Option Z starts this turn server-side, so NO browser EventSource is
    # attached to the new STREAMS[stream_id] (the browser only opens
    # /api/chat/stream when IT POSTs /api/chat/start). An already-open tab
    # would therefore see nothing until a manual refresh re-reads persisted
    # state. Fix: fan a lightweight `server_turn_started` {stream_id} frame
    # onto the persistent per-session live-view channel. messages.js handles
    # it by attaching its EXISTING chat-stream renderer (attachLiveStream) to
    # that stream_id — no second renderer, no chat/start POST.
    #
    # Idempotent with the closed-tab path: get_session_channel() is the
    # NON-creating accessor, so when no tab is open this is a pure no-op and
    # the server-side wakeup (the Option Z headline) is completely unaffected.
    # If the user also has the per-turn chat-stream open, the frontend dedupes
    # by stream_id so there is no double-render.
    try:
        status = int((resp or {}).get("_status", 200) or 200)
        stream_id = (resp or {}).get("stream_id")
        if status < 400 and stream_id:
            from api.background_process import get_session_channel

            ch = get_session_channel(session_id)
            if ch is not None:
                ch.emit(
                    "server_turn_started",
                    {
                        "session_id": str(session_id),
                        "stream_id": str(stream_id),
                        "pending_started_at": (resp or {}).get("pending_started_at"),
                        "source": source,
                    },
                )
    except Exception:
        logger.debug(
            "server_turn_started fan-out failed for session %s", session_id, exc_info=True
        )
    return resp


def _handle_bg_task_complete_ack(handler, body):
    """Acknowledge a bg_task_complete SSE event (diagnostic only).

    Option Z PIVOT: the agent wakeup is now started SERVER-SIDE by the drain
    thread (``api/background_process._process_one`` → ``start_session_turn``)
    with NO browser round-trip — the closed-tab case works (parity with
    CLI/Telegram). The frontend no longer re-POSTs ``wakeup_prompt`` to
    /api/chat/start; the per-session SSE channel is demoted to pure live-view.

    This endpoint is therefore a pure no-op for state — it exists so an open
    tab can confirm receipt of the live-view event and so a future follow-up
    (analytics, telemetry) has a stable hook. ``PENDING_BG_TASK_COMPLETIONS``
    is consumed by ``_start_chat_stream_for_session`` when the server-side
    wakeup turn (or the next human turn / PR #2279 next-turn drain) runs.
    """
    from api.helpers import j

    try:
        require(body, "session_id")
    except ValueError as e:
        return bad(handler, str(e))
    sid = str(body.get("session_id") or "").strip()
    try:
        s = get_session(sid)
    except KeyError:
        return bad(handler, "Session not found", 404)
    # process_id accepted as transitional alias; see Deprecation response header
    # + maintainer decision on removal milestone / future Sunset header. Only
    # flag Deprecation when the alias was ACTUALLY used (i.e. process_id present
    # and not empty), even if task_id is also present.
    _task_id_present = bool(str(body.get("task_id") or "").strip())
    _process_id_present = bool(str(body.get("process_id") or "").strip())
    legacy_process_id_used = _process_id_present
    pid = str(body.get("task_id") or body.get("process_id") or "").strip()
    # Post Option-Z pivot this endpoint owns no state: the server-side drain
    # thread starts the wakeup turn, the browser never re-POSTs /api/chat/start.
    # `noop` is returned so the diagnostic shape stays explicit about that and
    # matches the docstring ("pure no-op for state").
    return j(
        handler,
        {
            "ok": True,
            "session_id": s.session_id,
            "task_id": pid,
            "noop": True,
        },
        extra_headers={"Deprecation": "true"} if legacy_process_id_used else {},
    )


def _handle_session_compression_recovery_start(handler, body):
    try:
        require(body, "session_id")
    except ValueError as e:
        return bad(handler, str(e))
    sid = str(body.get("session_id") or "").strip()
    if not sid:
        return bad(handler, "session_id is required")
    if _session_is_subagent_view_only(sid):
        return bad(handler, "Subagent sessions are view-only and cannot start compression recovery from WebUI", 400)
    try:
        source = get_session(sid)
    except KeyError:
        return bad(handler, "Session not found", 404)
    if not _session_visible_to_active_profile(getattr(source, "profile", None), handler):
        return bad(handler, "Session not found", 404)
    recovery = compression_recovery_payload_for_session(source)
    if not recovery:
        return bad(handler, "Session does not have a compression recovery action.", 409)
    action = str(recovery.get("recommended_action") or "")
    if action != COMPRESSION_RECOVERY_ACTION_START_FOCUSED:
        return bad(handler, "Unsupported compression recovery action.", 409)

    created = False
    with _COMPRESSION_RECOVERY_START_LOCK:
        source_profile = getattr(source, "profile", None)
        copied_session = find_compression_recovery_session(sid, action, source_profile=source_profile)
        if copied_session is None:
            title = str(getattr(source, "title", None) or "Untitled").strip() or "Untitled"
            if not title.endswith(" (focused continuation)"):
                title = f"{title} (focused continuation)"
            copied_session = Session(
                session_id=uuid.uuid4().hex[:12],
                title=title,
                workspace=getattr(source, "workspace", get_last_workspace()),
                model=getattr(source, "model", None),
                model_provider=getattr(source, "model_provider", None),
                messages=[],
                tool_calls=[],
                pinned=False,
                archived=False,
                project_id=getattr(source, "project_id", None),
                profile=getattr(source, "profile", None),
                session_source="fork",
                personality=getattr(source, "personality", None),
                enabled_toolsets=copy.deepcopy(getattr(source, "enabled_toolsets", None)),
                context_length=getattr(source, "context_length", None),
                threshold_tokens=getattr(source, "threshold_tokens", None),
                gateway_routing=copy.deepcopy(getattr(source, "gateway_routing", None)),
                gateway_routing_history=copy.deepcopy(getattr(source, "gateway_routing_history", None) or []),
                parent_session_id=getattr(source, "session_id", sid),
                worktree_path=getattr(source, "worktree_path", None),
                worktree_branch=getattr(source, "worktree_branch", None),
                worktree_repo_root=getattr(source, "worktree_repo_root", None),
                worktree_created_at=getattr(source, "worktree_created_at", None),
                compression_recovery_source_session_id=sid,
                compression_recovery_action=action,
            )
            # Preserve the workspace/model/profile lane, but intentionally start with an
            # empty model-facing transcript so a focused follow-up does not replay the
            # exhausted state.db/context tail.
            copied_session.context_messages = []
            copied_session.composer_draft = {"text": "", "files": []}
            try:
                copied_session.save()
            except Exception as e:
                logger.exception("failed to persist compression recovery session for %s", sid)
                return bad(handler, f"Failed to start compression recovery: {_sanitize_error(e)}", 500)

            with LOCK:
                SESSIONS[copied_session.session_id] = copied_session
                SESSIONS.move_to_end(copied_session.session_id)
                _evict_sessions_over_cap()
            created = True
    if created:
        publish_session_list_changed(
            "session_compression_recovery",
            profile=getattr(copied_session, "profile", None),
            session_id=getattr(copied_session, "session_id", None),
        )
    session_payload = redact_session_data(copied_session.compact() | {"messages": copied_session.messages})
    return j(
        handler,
        {
            "ok": True,
            "session": session_payload,
            "source_session_id": sid,
            "recommended_recovery_action": action,
            "message": (
                "Started a focused continuation. Describe the next narrow task to continue."
                if created
                else "Opened the existing focused continuation for this exhausted session."
            ),
        },
    )


def _handle_goal_command(handler, body):
    """Handle WebUI /goal command controls and optional kickoff stream."""
    try:
        require(body, "session_id")
    except ValueError as e:
        return bad(handler, str(e))
    if _is_silent_control_message(body.get("args") or body.get("text")):
        return j(
            handler,
            {"status": "suppressed", "reason": "silent_control_message"},
            status=200,
        )
    if _session_is_subagent_view_only(str(body.get("session_id") or "")):
        return bad(handler, "Subagent sessions are view-only and cannot run /goal from WebUI", 400)
    try:
        s = get_session(body["session_id"])
    except KeyError:
        return bad(handler, "Session not found", 404)

    requested_profile = str(body.get("profile") or "").strip()
    if requested_profile:
        try:
            from api.profiles import _PROFILE_ID_RE

            if requested_profile != "default" and not _PROFILE_ID_RE.fullmatch(requested_profile):
                return bad(handler, "invalid profile", 400)
        except ImportError:
            requested_profile = ""
    if requested_profile and not _profiles_match(getattr(s, "profile", None), requested_profile):
        has_persisted_turns = bool(
            getattr(s, "messages", None)
            or getattr(s, "context_messages", None)
            or getattr(s, "pending_user_message", None)
        )
        if not has_persisted_turns:
            s.profile = requested_profile

    current_stream_id = getattr(s, "active_stream_id", None)
    stream_running = False
    if current_stream_id:
        with STREAMS_LOCK:
            stream_running = current_stream_id in STREAMS
        if not stream_running:
            _clear_stale_stream_state(s)

    try:
        from api.profiles import get_agy_home_for_profile

        profile_home = get_agy_home_for_profile(getattr(s, "profile", None))
    except Exception:
        logger.warning("Silent exception in _handle_goal_command", exc_info=True)
        profile_home = None

    from api.goals import goal_command_payload, goal_state_snapshot, restore_goal_state

    goal_args = str(body.get("args", "") or body.get("text", "") or "")
    goal_action = goal_args.strip().lower()
    will_kickoff = bool(
        goal_args.strip()
        and goal_action not in ("status", "pause", "resume", "clear", "stop", "done")
        and not stream_running
    )
    workspace = model = model_provider = normalized_model = None
    explicit_model_pick = bool(body.get("explicit_model_pick"))
    previous_goal_state = None
    if will_kickoff:
        try:
            workspace = str(resolve_trusted_workspace(body.get("workspace") or s.workspace))
        except ValueError as e:
            return bad(handler, str(e))
        requested_model = body.get("model") or s.model
        requested_provider = (
            body.get("model_provider")
            if "model_provider" in body
            else getattr(s, "model_provider", None)
        )
        # #6703: carry the explicit-pick marker through goal kickoffs. The
        # frontend marks a session-level provider/model choice as explicit (same
        # signal /api/chat/start receives); without it the model resolver treats
        # a persisted cross-provider pick as stale and "repairs" it back to the
        # profile default, silently switching providers mid-session.
        _pp_provider, _pp_default, _pp_cfg = _read_profile_model_config(s, requested_provider)
        model, model_provider, normalized_model = _resolve_compatible_session_model_state(
            requested_model,
            requested_provider,
            profile_provider=_pp_provider,
            profile_default_model=_pp_default,
            profile_config=_pp_cfg,
            explicit_model_pick=explicit_model_pick,
        )
        # #5979/#6703 parity with chat-start: record a SIGNATURE of the
        # deliberately-picked model+provider so the streaming resolver can
        # preserve a custom-proxy vendor namespace on a cold catalog. A first
        # /goal launch after a deliberate custom-provider pick must survive a
        # cold streaming catalog exactly like /api/chat/start does; otherwise the
        # provider reverts to the profile default mid-session.
        try:
            if explicit_model_pick:
                from api.models import model_explicit_pick_signature as _mk_sig
                s.model_explicit_pick_signature = _mk_sig(model, model_provider)
        except Exception:
            logger.warning("Silent exception in _handle_goal_command", exc_info=True)
            pass
        previous_goal_state = goal_state_snapshot(s.session_id, profile_home=profile_home)

    from api.runtime_adapter import LegacyJournalRuntimeAdapter, runtime_adapter_enabled

    def _legacy_goal_update(session_id: str, _action: str, text: str) -> dict:
        return goal_command_payload(
            session_id,
            text,
            stream_running=stream_running,
            profile_home=profile_home,
        )

    goal_adapter_action = _runtime_adapter_goal_action(goal_args)
    if runtime_adapter_enabled():
        adapter = LegacyJournalRuntimeAdapter(goal_delegate=_legacy_goal_update)
        control_result = adapter.update_goal(
            s.session_id,
            goal_adapter_action,
            goal_args,
        )
        # Slice 3c keeps the adapter as a structural seam only.  Preserve the
        # public /api/goal response by passing through the legacy payload rather
        # than deriving HTTP behavior from ControlResult.accepted/status.
        payload = dict(control_result.payload)
    else:
        payload = _legacy_goal_update(s.session_id, goal_adapter_action, goal_args)
    if not payload.get("ok", True):
        status = 409 if payload.get("error") == "agent_running" else 400
        return j(handler, payload, status=status)

    kickoff_prompt = str(payload.get("kickoff_prompt") or "").strip()
    if kickoff_prompt:
        if workspace is None:
            try:
                workspace = str(resolve_trusted_workspace(body.get("workspace") or s.workspace))
            except ValueError as e:
                return bad(handler, str(e))
        if model is None:
            requested_model = body.get("model") or s.model
            requested_provider = (
                body.get("model_provider")
                if "model_provider" in body
                else getattr(s, "model_provider", None)
            )
            _pp_provider, _pp_default, _pp_cfg = _read_profile_model_config(s, requested_provider)
            model, model_provider, normalized_model = _resolve_compatible_session_model_state(
                requested_model,
                requested_provider,
                profile_provider=_pp_provider,
                profile_default_model=_pp_default,
                profile_config=_pp_cfg,
                explicit_model_pick=explicit_model_pick,
            )
            # #6703 parity: same explicit-pick signature stamping on the
            # kickoff-prompt fallback resolution path as /api/chat/start.
            try:
                if explicit_model_pick:
                    from api.models import model_explicit_pick_signature as _mk_sig
                    s.model_explicit_pick_signature = _mk_sig(model, model_provider)
            except Exception:
                logger.warning("Silent exception in _handle_goal_command", exc_info=True)
                pass
        stream_response = _start_chat_stream_for_session(
            s,
            msg=kickoff_prompt,
            attachments=[],
            workspace=workspace,
            model=model,
            model_provider=model_provider,
            normalized_model=normalized_model,
            goal_related=True,
            external_runtime_owned=webui_gateway_chat_enabled(get_config()),
        )
        status = int(stream_response.pop("_status", 200) or 200)
        payload.update(stream_response)
        if status >= 400:
            restore_goal_state(s.session_id, previous_goal_state, profile_home=profile_home)
            payload["ok"] = False
            return j(handler, payload, status=status)

    return j(handler, payload)


def _is_silent_control_message(message) -> bool:
    """Return True only for the scheduler's exact suppression sentinel.

    ``[SILENT]`` is control-plane output, never conversation content. If a wake
    relay POSTs it and 8701 restarts while the turn is pending, recovery
    materializes it as a visible ``_recovered`` user message. Suppress it before
    session lookup or pending-state mutation. Matching stays exact and
    case-sensitive so ordinary user text is unaffected.
    """
    return str(message or "").strip() == "[SILENT]"


def _handle_chat_start(handler, body, diag=None):
    try:
        diag.stage("validate_session_id") if diag else None
        try:
            require(body, "session_id")
        except ValueError as e:
            return bad(handler, str(e))
        if _is_silent_control_message(body.get("message")):
            return j(
                handler,
                {"status": "suppressed", "reason": "silent_control_message"},
                status=200,
            )
        if body.get("regenerate") is True:
            from api.runtime_adapter import runtime_adapter_runner_enabled

            if runtime_adapter_runner_enabled():
                return j(handler, {
                    "error": "Regeneration is not supported by the runner backend.",
                    "code": "unsupported_regeneration_backend",
                }, status=409)
        # Reject a stale local Agent runtime before materialising, claiming, or
        # mutating any session state. Gateway-backed turns run in the gateway's
        # process and do not depend on this WebUI process's imported checkout.
        stale_response = _agent_runtime_barrier_response(runner_local_owned=True)
        if stale_response is not None:
            return j(handler, stale_response, status=409)
        diag.stage("get_session") if diag else None
        try:
            s = _get_or_materialize_session(
                body["session_id"],
                refresh_cli_messages=body.get("regenerate") is not True,
            )
        except KeyError:
            # No WebUI sidecar. If this is a foreign-origin session (CLI,
            # TUI, Desktop) with recoverable state.db messages, claim it by
            # materialising a WebUI-owned Session and persisting it as a
            # sidecar. This closes the GET-vs-POST asymmetry where a
            # TUI/Desktop session loads read-only via GET /api/session but
            # 404s on the first POST /api/chat/start, making the typed
            # message disappear into the empty state.
            synth, reason = _claim_or_synthesize_cli_session(body["session_id"])
            if synth is None:
                # 'was_webui' (deleted WebUI session, client should self-heal
                # via the existing 404 path), 'no_foreign_state' (sid has
                # no recoverable state anywhere), or 'invalid_sid' (path
                # safety violation). All collapse to 404 — the client only
                # knows the right thing to do for "this session is gone".
                return bad(handler, "Session not found", 404)
            if reason == "not_claimable":
                # Foreign store says this session is read-only / owned by
                # a non-WebUI process (messaging, claude_code,
                # external_agent, cron, gateway/unknown, or explicit
                # read_only flag). The session is real and viewable, but
                # the WebUI must not take write ownership of it — that
                # would be an ownership-boundary violation (#4911 review).
                # 403 (not 404) because 404 triggers the frontend's
                # empty-state self-heal handler which strips the URL and
                # clears localStorage; for a legitimately-listed read-only
                # session the user should keep their URL and see a refusal,
                # not have their session vanish.
                return bad(
                    handler,
                    "session is read-only in its foreign store; cannot be claimed writeable in WebUI",
                    403,
                )
            try:
                synth.save()
            except Exception as _save_err:
                # Persisting the sidecar failed: surface a generic 500 to
                # the client (paths sanitised, see _sanitize_error) and log
                # the full exception server-side. Returning the raw str(exc)
                # would leak /root/.hermes/webui/sessions/<sid>.json or any
                # other absolute filesystem path the OSError happened to
                # carry — #4911 review feedback.
                logger.exception(
                    "failed to persist materialised sidecar for foreign session %s",
                    body["session_id"],
                )
                return bad(
                    handler,
                    f"failed to claim session: {_sanitize_error(_save_err)}",
                    500,
                )
            s = synth
            try:
                with LOCK:
                    SESSIONS[s.session_id] = s
                    SESSIONS.move_to_end(s.session_id)
            except Exception:
                # If the in-memory LRU refuses the new session, fall through
                # with the just-persisted sidecar; _start_run will load it
                # from disk if needed.
                logger.warning("Silent exception in _handle_chat_start", exc_info=True)
                pass
        except PermissionError:
            return bad(handler, "Read-only imported sessions cannot be continued from WebUI", 403)
        diag.stage("validate_profile") if diag else None
        requested_profile = str(body.get("profile") or "").strip()
        active_profile = _get_active_profile_name()
        if requested_profile:
            try:
                from api.profiles import _PROFILE_ID_RE

                if requested_profile != "default" and not _PROFILE_ID_RE.fullmatch(requested_profile):
                    return bad(handler, "invalid profile", 400)
            except ImportError:
                requested_profile = ""
        session_profile = getattr(s, "profile", None)
        has_persisted_turns = bool(
            getattr(s, "messages", None)
            or getattr(s, "context_messages", None)
            or getattr(s, "pending_user_message", None)
        )
        if not _session_visible_to_active_profile(session_profile, handler):
            if (
                requested_profile
                and _profiles_match(requested_profile, active_profile)
                and not has_persisted_turns
            ):
                # Empty placeholders can still be retagged when the
                # requested profile matches the active request profile.
                s.profile = requested_profile
            else:
                return bad(handler, "Session not found", 404)
        regeneration = None
        if body.get("regenerate") is True:
            if any(key in body for key in ("message", "attachments", "keep_count", "prompt", "prompt_index")):
                return j(handler, {"error": "regeneration accepts only regeneration_revision", "code": "invalid_regeneration_request"}, status=400)
            if not isinstance(body.get("regeneration_revision"), str):
                return j(handler, {"error": "regeneration_revision is required", "code": "stale_regeneration_revision"}, status=409)
            try:
                from api.session_ops import plan_regeneration, RegenerationUnavailable
                regeneration = plan_regeneration(
                    s, expected_revision=body["regeneration_revision"]
                )
            except RegenerationUnavailable as exc:
                return j(handler, {"error": str(exc), "code": exc.code}, status=exc.status)
            msg = regeneration.turn.message_text
            attachments = copy.deepcopy(regeneration.turn.attachments)[:20]
        else:
            msg = None
            attachments = None
        diag.stage("normalize_message") if diag else None
        msg = str(msg if msg is not None else body.get("message", "")).strip()
        if not msg:
            return bad(handler, "message is required")
        diag.stage("normalize_attachments") if diag else None
        if attachments is None:
            attachments = _normalize_chat_attachments(body.get("attachments") or [])[:20]
        recovery = compression_recovery_payload_for_session(s)
        if recovery and not attachments and is_generic_continuation_intent(msg):
            return j(
                handler,
                {
                    "error": "This session exhausted context compression. Start a focused continuation, then describe the next narrow task.",
                    "type": "compression_recovery_required",
                    "recommended_recovery_action": recovery.get("recommended_action"),
                    "compression_recovery": recovery,
                    "session_id": getattr(s, "session_id", body["session_id"]),
                },
                status=409,
            )
        diag.stage("resolve_workspace") if diag else None
        try:
            if regeneration is not None:
                workspace = _resolve_chat_workspace_for_regeneration(s, body.get("workspace"))
            else:
                workspace = _resolve_chat_workspace_with_recovery(s, body.get("workspace"))
        except WorkspaceBindingPersistenceError as e:
            return bad(handler, str(e), 500)
        except ValueError as e:
            return bad(handler, str(e))
        requested_model = body.get("model") or s.model
        requested_provider = (
            body.get("model_provider")
            if "model_provider" in body
            else getattr(s, "model_provider", None)
        )
        _pp_provider, _pp_default, _pp_cfg = _read_profile_model_config(s, requested_provider)
        explicit_model_pick = bool(body.get("explicit_model_pick"))
        moa_config = None
        config_snapshot = get_config_snapshot()
        gateway_chat_enabled = webui_gateway_chat_enabled(config_snapshot)
        if body.get("moa_config"):
            if gateway_chat_enabled:
                return bad(handler, "MoA override is unavailable on gateway-backed sessions", 409)
            from api.commands import resolve_moa_config

            try:
                moa_config = resolve_moa_config()
            except RuntimeError as e:
                return bad(handler, str(e), 503)
        diag.stage("resolve_model_provider") if diag else None
        model, model_provider, normalized_model = _resolve_compatible_session_model_state(
            requested_model,
            requested_provider,
            profile_provider=_pp_provider,
            profile_default_model=_pp_default,
            profile_config=_pp_cfg,
            explicit_model_pick=explicit_model_pick,
        )
        # #5979: record a SIGNATURE of the deliberately-picked model+provider so
        # the streaming resolver can preserve a custom-proxy vendor namespace on a
        # cold catalog — but ONLY while the routing context still matches. On a
        # fresh explicit pick, stamp the signature of the resolved model+provider;
        # otherwise leave any prior signature in place (it self-invalidates when
        # the model/provider changes, since the streaming side recomputes and
        # compares). This survives same-model follow-up sends (the onchange marker
        # is one-shot) yet can't outlive a real switch.
        try:
            if explicit_model_pick and regeneration is None:
                from api.models import model_explicit_pick_signature as _mk_sig
                s.model_explicit_pick_signature = _mk_sig(model, model_provider)
        except Exception:
            logger.warning("Silent exception in _handle_chat_start", exc_info=True)
            pass
        catalog_profile_provider = _pp_provider
        if catalog_profile_provider is None and isinstance(_pp_cfg, dict):
            profile_model_config = _pp_cfg.get("model") or {}
            if isinstance(profile_model_config, dict):
                catalog_profile_provider = profile_model_config.get("provider")
        model_provider = _repair_foreign_session_model_provider(
            s,
            requested_model=requested_model,
            requested_provider=requested_provider,
            resolved_model=model,
            resolved_provider=model_provider,
            explicit_model_pick=explicit_model_pick,
            profile_provider=catalog_profile_provider,
        )
        if model_provider == "moa" and gateway_chat_enabled:
            from api.config import get_effective_default_model

            model_config = config_snapshot.get("model") if isinstance(config_snapshot, dict) else None
            configured_default, configured_default_provider, configured_default_is_moa = (
                _moa_fast_path_model_state(get_effective_default_model(config_snapshot))
            )
            configured_provider = _clean_session_model_provider(
                model_config.get("provider") if isinstance(model_config, dict) else None
            )
            if configured_provider is None and configured_default_is_moa:
                configured_provider = configured_default_provider
            if (
                configured_provider != "moa"
                or model != configured_default
                or explicit_model_pick
            ):
                return bad(handler, "MoA override is unavailable on gateway-backed sessions", 409)
        elif model_provider == "moa" and moa_config is None:
            from api.commands import resolve_moa_config

            try:
                moa_config = resolve_moa_config(model)
            except RuntimeError as e:
                return bad(handler, str(e), 503)
        # NOTE: runtime-adapter selection is delegated to _start_run (shared
        # with start_session_turn so both entry points behave identically
        # under runtime_adapter_enabled() / runtime_adapter_runner_enabled()
        # — Q-2979-A2 / Copilot discussion_r3305864087/r3305864173).
        start_run_kwargs = {
            "msg": msg,
            "attachments": attachments,
            "workspace": workspace,
            "model": model,
            "model_provider": model_provider,
            "normalized_model": normalized_model,
            "source": "webui",
            "route": "/api/chat/start",
            "diag": diag,
            "gateway_chat_enabled": gateway_chat_enabled,
            "regeneration": regeneration,
        }
        if not gateway_chat_enabled and moa_config is not None:
            start_run_kwargs["moa_config"] = moa_config
        recovery_cleared_for_start = None
        def _restore_cleared_recovery():
            if recovery_cleared_for_start is None:
                return None
            s.compression_recovery = recovery_cleared_for_start
            s.recommended_recovery_action = recovery_cleared_for_start.get("recommended_action")
            try:
                s.save()
            except Exception as restore_err:
                logger.exception("failed to restore compression recovery after chat start rejection for %s", getattr(s, "session_id", None))
                return restore_err
            return None

        if recovery and regeneration is None:
            recovery_cleared_for_start = copy.deepcopy(recovery)
            clear_compression_recovery(s)
        try:
            response = _start_run(
                s,
                **start_run_kwargs,
            )
        except Exception as exc:
            logger.warning("Silent exception in _handle_chat_start", exc_info=True)
            if not getattr(exc, "_regeneration_accepted", False):
                _restore_cleared_recovery()
            raise
        # Map adapter-selection NotImplementedError (501) onto the legacy
        # bad-request response shape that this route exposed historically
        # before the helper extraction.
        if response.get("_status") == 501 and "error" in response:
            restore_err = _restore_cleared_recovery()
            if restore_err is not None:
                return bad(handler, f"failed to restore compression recovery: {_sanitize_error(restore_err)}", 500)
            return j(handler, {"error": response["error"]}, status=501)
        status = int(response.pop("_status", 200) or 200)
        if status >= 400 and recovery_cleared_for_start is not None:
            restore_err = _restore_cleared_recovery()
            if restore_err is not None:
                return bad(handler, f"failed to restore compression recovery: {_sanitize_error(restore_err)}", 500)
        diag.stage("response_write") if diag else None
        return j(handler, response, status=status)
    finally:
        if diag:
            diag.finish()



def _resolve_chat_workspace_with_recovery(s, requested_workspace) -> str:
    """Recover stale implicit session workspaces without hiding explicit errors."""
    explicit = requested_workspace not in (None, "")
    if explicit:
        return str(resolve_trusted_workspace(requested_workspace))
    stored_workspace = getattr(s, "workspace", None)
    workspace, recovered = resolve_implicit_workspace_with_recovery(
        stored_workspace,
        get_last_workspace,
    )
    if not recovered:
        return str(workspace)
    persisted = persist_recovered_workspace_binding(
        s,
        workspace,
        expected_workspace=stored_workspace,
    )
    return str(persisted.workspace)


def _resolve_chat_workspace_for_regeneration(s, requested_workspace) -> str:
    """Resolve regeneration's workspace without persisting before start acceptance."""
    if requested_workspace not in (None, ""):
        return str(resolve_trusted_workspace(requested_workspace))
    workspace, _recovered = resolve_implicit_workspace_with_recovery(
        getattr(s, "workspace", None),
        get_last_workspace,
    )
    return str(workspace)


def _normalize_chat_attachments(raw_attachments):
    """Normalize attachment payloads from the browser.

    Older clients send a list of filenames. Newer clients send upload result
    objects containing name/path/mime/size so image attachments can be supplied
    to Hermes as native multimodal inputs for the current turn.
    """
    normalized = []
    if not isinstance(raw_attachments, list):
        return normalized
    for item in raw_attachments:
        if isinstance(item, dict):
            name = str(item.get("name") or item.get("filename") or "").strip()
            path = str(item.get("path") or "").strip()
            mime = str(item.get("mime") or "").strip()
            att = {"name": name or path, "path": path, "mime": mime}
            size = item.get("size")
            if isinstance(size, int):
                att["size"] = size
            is_image = item.get("is_image")
            if isinstance(is_image, bool):
                att["is_image"] = is_image
            normalized.append(att)
        else:
            value = str(item).strip()
            if value:
                normalized.append({"name": value, "path": "", "mime": ""})
    return normalized


def _handle_chat_sync(handler, body):
    """Fallback synchronous chat endpoint (POST /api/chat). Not used by frontend."""
    stale_response = _agent_runtime_barrier_response(runner_local_owned=False)
    if stale_response is not None:
        return j(handler, stale_response, status=409)
    if _session_is_subagent_view_only(str(body.get("session_id") or "")):
        return bad(handler, "Subagent sessions are view-only and cannot be written from WebUI", 400)
    s = get_session(body["session_id"])
    msg = str(body.get("message", "")).strip()
    if not msg:
        return j(handler, {"error": "empty message"}, status=400)
    try:
        workspace = str(resolve_trusted_workspace(body.get("workspace") or s.workspace))
    except ValueError as e:
        return bad(handler, str(e))
    with _get_session_agent_lock(s.session_id):
        s.workspace = workspace
        _sync_requested_provider = (
            body.get("model_provider") if "model_provider" in body else getattr(s, "model_provider", None)
        )
        _pp_provider, _pp_default, _pp_cfg = _read_profile_model_config(s, _sync_requested_provider)
        model, model_provider = _resolve_compatible_session_model_state(
            body.get("model") or s.model,
            _sync_requested_provider,
            profile_provider=_pp_provider,
            profile_default_model=_pp_default,
            profile_config=_pp_cfg,
        )[:2]
        s.model = model
        s.model_provider = model_provider
    from api.streaming import _ENV_LOCK

    with _ENV_LOCK:
        old_cwd = os.environ.get("TERMINAL_CWD")
        os.environ["TERMINAL_CWD"] = str(workspace)
        old_exec_ask = os.environ.get("HERMES_EXEC_ASK")
        old_agy_exec_ask = os.environ.get("AGY_EXEC_ASK")
        old_session_key = os.environ.get("HERMES_SESSION_KEY")
        old_agy_session_key = os.environ.get("AGY_SESSION_KEY")
        os.environ["AGY_EXEC_ASK"] = os.environ["HERMES_EXEC_ASK"] = "1"
        os.environ["AGY_SESSION_KEY"] = os.environ["HERMES_SESSION_KEY"] = s.session_id
    try:
        AIAgent = require_ai_agent_class()

        with CHAT_LOCK:
            from api.config import (
                resolve_model_provider,
                resolve_custom_provider_connection,
            )

            _model, _provider, _base_url = resolve_model_provider(
                model_with_provider_context(s.model, getattr(s, "model_provider", None))
            )
            # Resolve API key via Hermes runtime provider (matches gateway behaviour)
            _api_key = None
            try:
                from api.oauth import resolve_runtime_provider_with_anthropic_env_lock
                from hermes_cli.runtime_provider import resolve_runtime_provider

                _rt = resolve_runtime_provider_with_anthropic_env_lock(
                    resolve_runtime_provider,
                    requested=_provider,
                )
                _api_key = _rt.get("api_key")
                # Also use runtime provider/base_url if the webui config didn't resolve them
                if not _provider:
                    _provider = _rt.get("provider")
                if not _base_url:
                    _base_url = _rt.get("base_url")
            except Exception as _e:
                logger.debug("resolve_runtime_provider failed: %s", _e)
            if isinstance(_provider, str) and _provider.startswith("custom:"):
                _cp_key, _cp_base = resolve_custom_provider_connection(_provider)
                if not _api_key and _cp_key:
                    _api_key = _cp_key
                if not _base_url and _cp_base:
                    _base_url = _cp_base
            agent = AIAgent(
                model=_model,
                provider=_provider,
                base_url=_base_url,
                api_key=_api_key,
                # Identify browser-originated sessions as WebUI so Hermes Agent
                # does not inject CLI-specific terminal/output guidance.
                platform="webui",
                quiet_mode=True,
                enabled_toolsets=_resolve_cli_toolsets(),
                session_id=s.session_id,
            )
            from api.streaming import (
                _WEBUI_PROGRESS_PROMPT,
                _assign_stable_message_ids,
                _dedupe_replayed_context_messages,
                _merge_display_messages_after_agent_result,
                _restore_display_reasoning_metadata,
                _restore_reasoning_metadata,
                _sanitize_messages_for_agent,
                _compact_session_image_parts_for_persistence,
                _context_messages_for_new_turn,
                _workspace_context_prefix,
            )
            workspace_ctx = _workspace_context_prefix(str(s.workspace))
            workspace_system_msg = (
                f"Active workspace at session start: {s.workspace}\n"
                "Every user message is prefixed with [Workspace::v1: /absolute/path] indicating the "
                "workspace the user has selected in the web UI at the time they sent that message. "
                "This tag is the single authoritative source of the active workspace and updates "
                "with every message. It overrides any prior workspace mentioned in this system "
                "prompt, memory, or conversation history. Always use the value from the most recent "
                "[Workspace::v1: ...] tag as your default working directory for ALL file operations: "
                "write_file, read_file, search_files, terminal workdir, and patch. "
                "Never fall back to a hardcoded path when this tag is present.\n\n"
                f"{_WEBUI_PROGRESS_PROMPT}\n\n"
                "WebUI external-notes/durable-memory policy: Do not copy or dump this browser transcript "
                "into external notes or durable memory by default. Write or update durable "
                "notes only for explicit captures, durable preferences, decisions, blockers/open "
                "issues, runbook-worthy workflows, or other clearly reusable signals; otherwise "
                "leave external notes and durable memory unchanged. When you do write or update a durable note, briefly tell "
                "the user what note or section changed so the write is reviewable."
            )

            _previous_messages = list(s.messages or [])
            _previous_context_messages = list(_context_messages_for_new_turn(s, msg))

            result = agent.run_conversation(
                user_message=workspace_ctx + msg,
                system_message=workspace_system_msg,
                conversation_history=_sanitize_messages_for_agent(
                    _previous_context_messages,
                    cfg=get_config(),
                    effective_model=_model,
                    effective_provider=_provider,
                    effective_base_url=_base_url,
                ),
                task_id=s.session_id,
                persist_user_message=msg,
            )
    finally:
        with _ENV_LOCK:
            if old_cwd is None:
                os.environ.pop("TERMINAL_CWD", None)
            else:
                os.environ["TERMINAL_CWD"] = old_cwd
            if old_exec_ask is None:
                os.environ.pop("HERMES_EXEC_ASK", None)
            else:
                os.environ["HERMES_EXEC_ASK"] = old_exec_ask
            if old_agy_exec_ask is None:
                os.environ.pop("AGY_EXEC_ASK", None)
            else:
                os.environ["AGY_EXEC_ASK"] = old_agy_exec_ask
            if old_session_key is None:
                os.environ.pop("HERMES_SESSION_KEY", None)
            else:
                os.environ["HERMES_SESSION_KEY"] = old_session_key
            if old_agy_session_key is None:
                os.environ.pop("AGY_SESSION_KEY", None)
            else:
                os.environ["AGY_SESSION_KEY"] = old_agy_session_key
    with _get_session_agent_lock(s.session_id):
        _result_messages = result.get("messages") or _previous_context_messages
        _next_context_messages = _restore_reasoning_metadata(
            _previous_context_messages,
            _result_messages,
        )
        # Mint ids on the shared result rows BEFORE dedupe deep-copies any
        # stale-user boundary row, so both arrays share the id (#5564).
        _assign_stable_message_ids(
            _result_messages, _previous_messages, _previous_context_messages
        )
        _next_context_messages = _dedupe_replayed_context_messages(
            _previous_context_messages,
            _next_context_messages,
            msg,
        )
        s.context_messages = _next_context_messages
        s.messages = _merge_display_messages_after_agent_result(
            _previous_messages,
            _previous_context_messages,
            _restore_display_reasoning_metadata(_previous_messages, _result_messages),
            msg,
            source=getattr(s, "pending_user_source", None) or "webui",
        )
        _compact_session_image_parts_for_persistence(s)
        # Only auto-generate title when still default; preserves user renames
        if s.title == "Untitled":
            s.title = title_from(s.messages, s.title)
        s.save()
    # Sync to state.db for /insights (opt-in setting)
    try:
        if load_settings().get("sync_to_insights"):
            from api.state_sync import sync_session_usage

            sync_session_usage(
                session_id=s.session_id,
                input_tokens=s.input_tokens or 0,
                output_tokens=s.output_tokens or 0,
                estimated_cost=s.estimated_cost,
                model=s.model,
                title=s.title,
                message_count=len(s.messages),
                cache_read_tokens=s.cache_read_tokens or 0,
                cache_write_tokens=s.cache_write_tokens or 0,
                # #2762 / #2827 parity with api/streaming.py:5078: pass the
                # session's profile explicitly so a future refactor that
                # backgrounds this handler doesn't silently leak writes to
                # the wrong profile's state.db. HTTP thread today, but
                # defense-in-depth. Opus pre-release advisor MUST-FIX.
                profile=getattr(s, 'profile', None),
            )
    except Exception:
        logger.debug("Failed to update session cost tracking")
    return j(
        handler,
        {
            "answer": result.get("final_response") or "",
            "status": "done" if result.get("completed", True) else "partial",
            "session": public_session_projection(s.compact() | {"messages": s.messages}),
            "result": {k: v for k, v in result.items() if k != "messages"},
        },
    )

