"""Chat streaming, terminal output, approvals, and clarifiers routes.

Decomposed from routes.py (Sprint R5, Sprint M2.1).
"""
import logging
import queue
import time
from collections import deque
from urllib.parse import parse_qs

from api.config import (
    STREAM_LAST_EVENT_ID,
    STREAMS,
    get_config,
    stream_owner_session_id,
)
from api.helpers import (
    _CLIENT_DISCONNECT_ERRORS,
    _sanitize_error,
    bad,
    j,
    public_session_projection,
    require,
)
from api.models import get_session
from api.oauth import poll_onboarding_oauth_flow
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
from api.sse_chunked import end_sse_headers
from api.streaming import (
    _sse,
    _sse_set_write_deadline,
    cancel_stream,
)
from api.workspace import (
    _is_remote_terminal_backend,
    resolve_trusted_workspace,
)

logger = logging.getLogger(__name__)

_SSE_HEARTBEAT_INTERVAL_SECONDS = 5
_SESSION_SSE_SENT_EVENT_ID_LIMIT = 4096

_EMBEDDED_TERMINAL_GATE_DENIED_MESSAGE = (
    "Embedded terminal is only available from local networks when authentication "
    "is not configured. Configure a password/passkey, or set "
    "HERMES_WEBUI_ONBOARDING_OPEN=1 to allow it on a deliberately-exposed server."
)


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


def _active_run_stream_for_session(session_id: str | None) -> str | None:
    from api import routes as _routes
    return _routes._active_run_stream_for_session(session_id)


def _runtime_runner_client_factory():
    """Return the configured runner-local client."""
    from api.runner_client import HttpRunnerClient
    return HttpRunnerClient.from_env()


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
        return _routes._handle_btw(handler, body)

    if parsed.path == "/api/background":
        return _routes._handle_background(handler, body)

    if parsed.path == "/api/goal":
        return _routes._handle_goal_command(handler, body)

    if parsed.path == "/api/bg-task-complete-ack":
        return _routes._handle_bg_task_complete_ack(handler, body)

    if parsed.path == "/api/chat/start":
        return _routes._handle_chat_start(handler, body, diag=diag)

    if parsed.path == "/api/chat":
        return _routes._handle_chat_sync(handler, body)

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

