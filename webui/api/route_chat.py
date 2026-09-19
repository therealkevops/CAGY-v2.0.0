"""Chat streaming, terminal output, approvals, and clarifiers routes.

Extracted from routes.py as part of routes decomposition (Sprint R5).
"""
import logging
from urllib.parse import parse_qs

from api.config import STREAMS, stream_owner_session_id
from api.helpers import bad, j
from api.oauth import poll_onboarding_oauth_flow
from api.route_approvals import retire_gateway_pending_mirror
from api.streaming import cancel_stream

logger = logging.getLogger(__name__)


def _handle_get_chat_and_stream(handler, parsed):
    """Handle chat streaming, terminal output, approvals, and clarifiers routes.
    Returns True if handled, None if unhandled.
    """
    from api import routes as _routes

    if parsed.path == "/api/chat/stream/status":
        stream_id = parse_qs(parsed.query).get("stream_id", [""])[0]
        if not _routes._stream_id_visible_to_request_profile(handler, stream_id):
            return True
        active = stream_id in STREAMS
        payload = {"active": active, "stream_id": stream_id, "replay_available": False}
        try:
            journal = _routes.find_run_summary(stream_id) if stream_id else None
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
        if not _routes._stream_id_visible_to_request_profile(handler, stream_id):
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
        return _routes._handle_sse_stream(handler, parsed)

    if parsed.path == "/api/terminal/output":
        return _routes._handle_terminal_output(handler, parsed)

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
        return _routes._handle_terminal_start(handler, body)

    if parsed.path == "/api/terminal/input":
        return _routes._handle_terminal_input(handler, body)

    if parsed.path == "/api/terminal/resize":
        return _routes._handle_terminal_resize(handler, body)

    if parsed.path == "/api/terminal/close":
        return _routes._handle_terminal_close(handler, body)

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
