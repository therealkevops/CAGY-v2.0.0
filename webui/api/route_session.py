"""Session lifecycle, recovery, lineage, and listing routes.

Extracted from routes.py as part of routes decomposition (Sprint R4).
"""
import copy
import io
import json
import logging
import os
import re
import shutil
import sqlite3
import threading
import time
import time as _time
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import api.config as api_config
from api import webui_session_db
from api.compression_anchor import visible_messages_for_anchor

from api.agent_runtime import (
    AgentRuntimeChangedError,
    ensure_agent_runtime_current,
    require_ai_agent_class,
)
from api.agent_sessions import read_session_lineage_report
from api.session_workspace_export import (
    export_session_to_workspace,
    validate_workspace_file_for_import,
)
from api.config import (
    DEFAULT_MODEL,
    DEFAULT_WORKSPACE,
    LOCK,
    SESSIONS,
    SESSION_DIR,
    _resolve_cli_toolsets,
    load_settings,
)
from api.helpers import (
    _redact_text,
    _sanitize_error,
    bad,
    j,
    public_session_projection,
    redact_session_data,
    require,
    strip_public_internal_fields,
)
from api.models import (
    Session,
    _active_state_db_path,
    _evict_sessions_over_cap,
    _profile_has_user_projects,
    count_conversation_rounds,
    ensure_cron_project,
    get_cli_session_messages,
    get_session,
    get_session_for_scan,
    is_cron_session,
    title_from,
)
from api.profiles import _is_isolated_profile_mode, _profiles_match, get_active_profile_name
from api.request_diagnostics import RequestDiagnostics
from api.route_approvals import is_session_yolo_enabled
from api.session_events import publish_session_list_changed
from api.todo_state import attach_todo_state
from api.workspace import get_last_workspace, resolve_trusted_workspace

logger = logging.getLogger(__name__)


def _handle_get_session(handler, parsed):
    """Handle session lifecycle, recovery, lineage, and listing routes.
    Returns True if handled, None if unhandled.
    """
    from api import routes as _routes

    if parsed.path == "/api/session/worktree/status":
        query = parse_qs(parsed.query)
        sid = query.get("session_id", [""])[0]
        if not sid:
            return bad(handler, "session_id is required", status=400)
        try:
            s = get_session(sid, metadata_only=True)
        except KeyError:
            return bad(handler, "Session not found", status=404)
        try:
            from api.worktrees import worktree_status_for_session

            return j(handler, {"status": worktree_status_for_session(s)})
        except ValueError as exc:
            return bad(handler, str(exc), status=400)
        except Exception as exc:
            logger.exception("failed to read worktree status for session %s", sid)
            return bad(handler, _sanitize_error(exc), status=500)

    if parsed.path == "/api/session/compress/status":
        query = parse_qs(parsed.query)
        _handle_session_compress_status(handler, query.get("session_id", [""])[0])
        return True

    if parsed.path == "/api/session":
        _t0 = _time.monotonic()
        _debug_slow = os.environ.get("AGY_DEBUG_SLOW") or os.environ.get("HERMES_DEBUG_SLOW", "")
        # perf(webui/session-load-latency) tier2c: per-stage breakdown via
        # RequestDiagnostics. maybe_start() returns None for paths not in
        # the allowlist, in which case the existing _tN-driven [SLOW] log
        # is the only signal — same as before.
        _diag = RequestDiagnostics.maybe_start("GET", parsed.path, logger=logger, print_fn=getattr(handler, '_safe_webui_print', None))
        # perf(webui/session-load-latency) tier2c-followup: every early-return
        # in this handler calls `_diag.finish()` before returning so the
        # watchdog's _watchdog_pending dict stays bounded to in-flight requests.
        # Greptile flagged this in PR review — finish() unregisters the
        # pending watchdog entry; without it the entry stays for the full
        # 5s slow-request timeout and emits a spurious "Slow WebUI request
        # still running" log. Idempotent — finish() no-ops if already called.
        query = parse_qs(parsed.query)
        sid = query.get("session_id", [""])[0]
        if not sid:
            if _diag:
                _diag.finish()
            return j(handler, {"error": "session_id is required"}, status=400)
        # ?messages=0 skips the message payload for fast session switching.
        # The frontend uses this when switching conversations in the sidebar
        # (only needs metadata). The full message array is loaded lazily
        # via ?messages=1 when the message panel opens.
        load_messages = query.get("messages", ["1"])[0] != "0"
        resolve_model_default = "1" if load_messages else "0"
        resolve_model = query.get("resolve_model", [resolve_model_default])[0] != "0"
        # ?msg_limit=N returns a tail window containing the last N visible
        # transcript rows. Hidden tool-result rows do not consume the budget;
        # they are included only when they sit inside the selected window and
        # are bounded before serialization. Older rows load on-demand.
        # Clamp to _MAX_MSG_LIMIT so an oversized request (e.g. msg_limit=9999
        # from an outline jump, or a hostile value) can't force an unbounded
        # payload; the existing _messages_truncated signal covers the clamped
        # case (the client sees there are more rows than returned). Parsing +
        # clamping live in _parse_msg_limit so the expression has direct test
        # coverage; None means the bare no-msg_limit path (full transcript).
        msg_limit = _routes._parse_msg_limit(query.get("msg_limit", [None])[0])
        # ?msg_before=N — 0-based index into the full message array.
        # Returns messages before this index (for scroll-to-top lazy loading).
        # Combined with msg_limit for paging.
        _msg_before = query.get("msg_before", [None])[0]
        try:
            msg_before = int(_msg_before) if _msg_before else None
        except (ValueError, TypeError):
            msg_before = None
        # ?expand_renderable=1 is retained for compatibility with older
        # frontends. msg_limit now counts visible transcript rows by default, so
        # the flag no longer changes the server-side pagination semantics.
        _expand_renderable = query.get("expand_renderable", [None])[0]
        expand_renderable = str(_expand_renderable).strip() in ("1", "true", "True")
        try:
            _t1 = _time.monotonic()
            if _diag:
                _diag.stage("t1_after_get_session_check")
            s = get_session(sid, metadata_only=(not load_messages))
            _session_profile = getattr(s, 'profile', None) or None
            if not _routes._session_visible_to_active_profile(_session_profile, handler):
                if _session_profile:
                    # Valid session owned by a KNOWN other profile: 409 so the
                    # client can offer to switch to it (#5419).
                    if _diag:
                        _diag.finish()
                    return j(handler, {
                        "error": "Session belongs to a different profile",
                        "code": "session_profile_mismatch",
                        "session_id": sid,
                        "profile": _session_profile,
                    }, status=409)
                # Unknown/legacy None-profile sidecar: keep the original 404 so
                # the frontend's self-heal (clear stale URL + localStorage) still
                # fires. _profiles_match coerces None->'default', so a truly
                # missing/legacy session under a non-default active profile would
                # otherwise emit a useless 409 with profile=null.
                if _diag:
                    _diag.finish()
                return bad(handler, "Session not found", 404)
            original_stream_id = getattr(s, "active_stream_id", None)
            _routes._clear_stale_stream_state(s)
            cli_meta = _routes._lookup_cli_session_metadata(sid) if _routes._session_requires_cli_metadata_lookup(s) else {}
            is_messaging_session = _routes._is_messaging_session_record(s) or _routes._is_messaging_session_record(cli_meta)
            cli_messages = []
            state_db_messages = []
            metadata_summary = None
            limited_sidecar_messages = None
            state_db_since_timestamp = None
            # Set by the limited-display path when the memoized merge can be
            # reused without loading the state.db rows; must exist for every
            # branch below, including the ones that never probe the cache.
            _display_cache_hit = None
            _display_state_db_signature = None
            if is_messaging_session:
                cli_messages = _routes.get_cli_session_messages(sid)
            elif load_messages:
                if msg_limit is not None:
                    (
                        state_db_since_timestamp,
                        limited_sidecar_messages,
                    ) = _routes._state_db_since_timestamp_for_limited_display(
                        s,
                        msg_limit,
                        msg_before=msg_before,
                    )
                _state_db_reader_kwargs = {"profile": _session_profile}
                if state_db_since_timestamp is not None:
                    _state_db_reader_kwargs["since_timestamp"] = state_db_since_timestamp
                # Apply the display-path row backstop ONLY on provably-safe
                # reads where no truncation_boundary prefix is required for the
                # merge — see _state_db_backstop_limit_for_display. Compressed
                # sessions and msg_before paging need their full prefix rows for
                # correct reconciliation, so those stay uncapped.
                _backstop = _routes._state_db_backstop_limit_for_display(s, msg_before)
                if _backstop is not None:
                    _state_db_reader_kwargs["limit"] = _backstop
                # perf: on the limited-display path the state.db rows are only
                # consumed by the memoized merge below. Now that the cache key
                # is a bounded SQL signature rather than a fingerprint OF these
                # rows, a hit no longer needs them -- and materialising tens of
                # thousands of dicts was the dominant remaining cost (~2.3s on a
                # 36k-row session) even when the merge itself was served from
                # cache. Probe the cache first and skip the load on a hit.
                #
                # Deliberately narrow: only when msg_limit is set (the merge
                # helper below is the sole consumer) and only for inactive
                # sessions, matching the cache's own validity rule. Any miss
                # falls through to the normal full load, so this can only skip
                # work that would have produced an identical merged result.
                _display_cache_hit = None
                if (
                    msg_limit is not None
                    and not getattr(s, "active_stream_id", None)
                    and not getattr(s, "pending_user_message", None)
                ):
                    _display_cache_hit = _routes._display_merge_cached_messages(
                        s,
                        limited_sidecar_messages,
                        msg_before=msg_before,
                    )
                if _display_cache_hit is not None:
                    state_db_messages = []
                else:
                    if (
                        msg_limit is not None
                        and not getattr(s, "active_stream_id", None)
                        and not getattr(s, "pending_user_message", None)
                    ):
                        (
                            state_db_messages,
                            _display_state_db_signature,
                        ) = _routes._load_state_db_messages_with_stable_signature(
                            sid,
                            _session_profile,
                            _state_db_reader_kwargs,
                        )
                    else:
                        state_db_messages = _routes.get_state_db_session_messages(
                            sid,
                            **_state_db_reader_kwargs,
                        )
            elif not is_messaging_session:
                # Metadata-only callers still need the same append-only
                # reconciliation contract as full loads so stale/replayed
                # state.db rows do not make sidebar polling think the
                # transcript is always newer. Helper threads profile= to
                # honor #2827's TLS-vs-thread fix.
                metadata_summary = _routes._metadata_only_message_summary(sid, profile=_session_profile)
            _t2 = _time.monotonic()
            if _diag:
                _diag.stage("t2_after_state_db_load")
            effective_model = (
                _routes._resolve_effective_session_model_for_display(s)
                if resolve_model
                else None
            )
            effective_provider = (
                _routes._resolve_effective_session_model_provider_for_display(s)
                if resolve_model
                else None
            )
            _t3 = _time.monotonic()
            if _diag:
                _diag.stage("t3_after_model_resolve")
            if load_messages:
                if is_messaging_session and cli_messages:
                    # Recovery/aggregate sidecars can intentionally contain a
                    # longer visible conversation than the single state.db
                    # segment for this messaging session id. Prefer the longer
                    # sidecar so repaired WebUI history is not hidden behind the
                    # canonical per-segment transcript. When both sources carry
                    # different slices of the same stitched conversation, merge
                    # them chronologically and dedupe exact repeats.
                    _all_msgs = _routes._merged_session_messages_for_display(s, cli_messages)
                elif msg_limit is not None:
                    if _display_cache_hit is not None:
                        _all_msgs = _display_cache_hit
                    else:
                        _all_msgs = _routes._limited_webui_messages_for_display_with_sidecar(
                            s,
                            limited_sidecar_messages,
                            state_db_messages,
                            state_db_signature=_display_state_db_signature,
                            msg_before=msg_before,
                        )
                else:
                    _all_msgs = _routes.merge_session_messages_append_only(
                        _routes._webui_sidecar_lineage_messages_for_display(s),
                        state_db_messages,
                        truncation_watermark=getattr(s, "truncation_watermark", None),
                        truncation_boundary=getattr(s, "truncation_boundary", None),
                    )
                    _all_msgs = _routes._merged_webui_lineage_messages_for_display(s, _all_msgs)
            else:
                if is_messaging_session and cli_messages:
                    _all_msgs = _routes._merged_session_messages_for_display(s, cli_messages)
                else:
                    if metadata_summary is None:
                        metadata_summary = _routes._message_summary(getattr(s, "messages", []) or [])
                    _summary_message_count = metadata_summary["message_count"]
                    _summary_last_message_at = metadata_summary["last_message_at"]
                    _all_msgs = []
            if not load_messages:
                if metadata_summary is None:
                    metadata_summary = _routes._message_summary(_all_msgs)
                    _summary_message_count = metadata_summary["message_count"]
                    _summary_last_message_at = metadata_summary["last_message_at"]
                if _summary_message_count == 0:
                    # Legacy session with no loaded sidecar and no state.db summary —
                    # fall back to the persisted metadata count from session JSON.
                    # See PR #2605 (LumenYoung): without this, the metadata poll
                    # returns 0 and the active-session external-refresh signal
                    # never trips on legacy sessions.
                    try:
                        metadata_count = getattr(s, "_metadata_message_count", None)
                        if metadata_count is not None:
                            _summary_message_count = max(0, int(metadata_count))
                    except (TypeError, ValueError):
                        pass
            else:
                _summary_message_count = None
                _summary_last_message_at = None
            if load_messages:
                _truncated_msgs, _messages_offset = _routes._message_window_for_display(
                    _all_msgs,
                    msg_limit=msg_limit,
                    msg_before=msg_before,
                    expand_renderable=expand_renderable,
                )
                if msg_limit is not None:
                    _truncated_msgs = _routes._messages_for_limited_payload(_truncated_msgs)
                _truncated_msgs = _routes._hydrate_anchor_activity_scenes(
                    _truncated_msgs,
                    getattr(s, "anchor_activity_scenes", None),
                    message_offset=_messages_offset,
                    tool_calls=getattr(s, "tool_calls", None),
                )
            else:
                _truncated_msgs = []
                _messages_offset = 0
            # Index of the first returned message in the full message array.
            # Frontend uses this as cursor for scroll-to-top paging.
            _windowed_messages = (
                load_messages
                and msg_limit is not None
                and (msg_before is not None or len(_truncated_msgs) < len(_all_msgs))
            )
            # Resolve effective context_length with model-metadata fallback so
            # older sessions (pre-#1318) that have context_length=0 persisted
            # still render a meaningful indicator on load.  Mirrors the
            # SSE-path fallback in api/streaming.py:2333-2342.  Fixes #1436.
            #
            # #1896: pass config_context_length, provider, and custom_providers
            # so explicit config overrides win over the 256K default fallback.
            # Without these, an old session loaded after a user upgraded to a
            # 1M-context model with `model.context_length: 1048576` in
            # config.yaml gets a 256K window in the initial UI indicator and
            # /api/session/get response — the same wrong-window display this
            # fix addresses on the streaming side.
            _persisted_cl = getattr(s, "context_length", 0) or 0
            _threshold_tokens = getattr(s, "threshold_tokens", 0) or 0
            if (not _persisted_cl) or resolve_model:
                _stored_model_for_lookup = getattr(s, "model", "") or ""
                _stored_provider_for_lookup = getattr(s, "model_provider", None) or ""
                _model_for_lookup = (
                    effective_model or _stored_model_for_lookup
                ).strip()
                (
                    _model_for_lookup,
                    _provider_for_lookup,
                    _base_url_for_lookup,
                    _api_key_for_lookup,
                ) = _routes._session_context_length_lookup_state(
                    _model_for_lookup,
                    effective_provider or getattr(s, "model_provider", None) or "",
                )
                _fb_cl = _routes._resolve_context_length_for_session_model(
                    _model_for_lookup,
                    _provider_for_lookup,
                    base_url=_base_url_for_lookup,
                    api_key=_api_key_for_lookup,
                )
                _model_changed_for_context = not _routes._session_model_identity_matches(
                    _stored_model_for_lookup,
                    _stored_provider_for_lookup,
                    _model_for_lookup,
                    _provider_for_lookup,
                )
                if _routes._should_accept_session_context_length_refresh(
                    _persisted_cl,
                    _fb_cl,
                    model_changed=_model_changed_for_context,
                ):
                    if _persisted_cl and _fb_cl != _persisted_cl:
                        # The old threshold belongs to the old window. Hiding it
                        # is less useful than keeping the same compression ratio
                        # against the freshly resolved context length.
                        _threshold_tokens = _routes._rescale_threshold_tokens_for_context_window(
                            _threshold_tokens,
                            _persisted_cl,
                            _fb_cl,
                        )
                    _persisted_cl = _fb_cl
            _session_tool_calls = getattr(s, "tool_calls", []) if load_messages else []
            # Always include session-level tool_calls so the browser can merge
            # them with per-message tool_calls for messages that lack the
            # per-message variant (older messages whose tool_calls live only
            # in the session-level list).  The browser-side
            # _syncToolCallsForLoadedMessages handles deduplication by tid.
            if _windowed_messages:
                _session_tool_calls = _routes._tool_calls_for_message_window(
                    _session_tool_calls,
                    _messages_offset,
                    len(_truncated_msgs),
                )
            _merged_message_count = _summary_message_count if _summary_message_count is not None else len(_all_msgs)
            _merged_last_message_at = _summary_last_message_at if _summary_last_message_at is not None else 0
            if _summary_last_message_at is None and _all_msgs:
                try:
                    _merged_last_message_at = max(
                        float((m or {}).get("timestamp") or 0)
                        for m in _all_msgs
                        if isinstance(m, dict)
                    )
                except (TypeError, ValueError):
                    _merged_last_message_at = 0
            active_stream_ids = _routes._active_stream_ids()
            try:
                compact_session = s.compact(
                    include_runtime=True,
                    active_stream_ids=active_stream_ids,
                )
            except TypeError:
                compact_session = s.compact()
            raw = compact_session | {
                "messages": _truncated_msgs,
                "message_count": _merged_message_count,
                "tool_calls": _session_tool_calls,
                "active_stream_id": getattr(s, "active_stream_id", None),
                "pending_user_message": getattr(s, "pending_user_message", None),
                "pending_attachments": getattr(s, "pending_attachments", []) if load_messages else [],
                "pending_started_at": getattr(s, "pending_started_at", None),
                "pending_user_source": getattr(s, "pending_user_source", None),
                "context_length": _persisted_cl,
                "threshold_tokens": _threshold_tokens,
                "last_prompt_tokens": getattr(s, "last_prompt_tokens", 0) or 0,
            }
            if original_stream_id:
                try:
                    journal = _routes.find_run_summary(original_stream_id)
                except Exception:
                    logger.warning("Silent exception in handle_get", exc_info=True)
                    journal = None
                if journal:
                    journal_active = bool(original_stream_id in active_stream_ids)
                    raw["runtime_journal"] = _routes._run_journal_status_payload(
                        journal,
                        active=journal_active,
                    )
                    if journal_active and (not load_messages or msg_limit is None):
                        try:
                            snapshot = _routes._run_journal_live_snapshot(original_stream_id, handler=handler)
                        except Exception:
                            logger.debug(
                                "Failed to build runtime journal snapshot for %s",
                                original_stream_id,
                                exc_info=True,
                            )
                            snapshot = None
                        if snapshot:
                            raw["runtime_journal_snapshot"] = _routes._runtime_journal_snapshot_for_session_payload(snapshot)
                            raw["pending_attachments"] = getattr(s, "pending_attachments", []) or []
            # Cold-load: derive the latest settled todo snapshot from the full
            # merged transcript, not the truncated display window. This keeps
            # the Todos panel correct after refresh even when the latest todo
            # tool result is outside msg_limit, and treats an explicit empty
            # todo list as the current state instead of falling through to an
            # older non-empty write.
            if load_messages and _all_msgs:
                attach_todo_state(raw, _all_msgs)
            if _merged_last_message_at:
                raw["last_message_at"] = max(
                    float(raw.get("last_message_at") or 0),
                    _merged_last_message_at,
                )
                raw["updated_at"] = max(
                    float(raw.get("updated_at") or 0),
                    _merged_last_message_at,
                )
            # #2980: surface the visible continuation for a hidden pre-compression
            # snapshot so a mobile reload mid-compression can recover to it.
            continuation_sid = _routes._pre_compression_continuation_session_id(s)
            if continuation_sid:
                raw["continuation_session_id"] = continuation_sid
            if cli_meta and _routes._session_source_is_webui(cli_meta):
                raw = _routes._reconcile_session_detail_source_flags(raw, cli_meta)
            elif cli_meta and _routes._is_messaging_session_record(cli_meta):
                raw = _routes._merge_cli_sidebar_metadata(raw, cli_meta)
                # ``message_count`` in /api/session is the display coordinate
                # space used for pagination and the header badge. Messaging
                # state.db metadata can include raw duplicate transport rows that
                # _merged_session_messages_for_display() intentionally dedupes;
                # keep the raw count available as ``actual_message_count`` but
                # do not let it make the frontend expect phantom messages.
                raw["message_count"] = _merged_message_count
            # Signal to the frontend that older messages were omitted. The
            # message window cursor already reflects visible-row pagination and
            # avoids false positives when raw hidden tool rows exceed msg_limit.
            _truncated = load_messages and msg_limit is not None and _messages_offset > 0
            raw["_messages_truncated"] = _truncated
            raw["_messages_offset"] = _messages_offset
            raw["_msg_limit_max"] = _routes._MAX_MSG_LIMIT
            _t4 = _time.monotonic()
            if _diag:
                _diag.stage("t4_after_compact_and_merge")
            if effective_model:
                raw["model"] = effective_model
            if effective_provider:
                raw["model_provider"] = effective_provider
            # A subagent child (#5307) is view-only regardless of what a stale
            # sidecar stored: coerce the serialized flags so the browser never
            # treats an existing subagent sidecar as writable / CLI-classified.
            if (
                (str(raw.get("source_tag") or raw.get("raw_source") or raw.get("session_source") or "").strip().lower() == "subagent")
                or _routes._is_subagent_child_session_id(sid)
            ):
                raw["is_cli_session"] = False
                raw["read_only"] = True
            imported_turn_marker = any(
                isinstance(row, dict) and row.get("_active_turn_token")
                for row in _all_msgs
            )
            if (
                not raw.get("read_only")
                and not _truncated
                and (not raw.get("is_cli_session") or imported_turn_marker)
            ):
                from api.session_ops import regeneration_authority, regeneration_state
                canonical_state = regeneration_state(s)
                revision = regeneration_authority(
                    s,
                    rows=canonical_state[0],
                    context=canonical_state[1],
                    full_transcript=True,
                    canonical_state=canonical_state,
                )
                if revision:
                    raw["regeneration_revision"] = revision
            redact = redact_session_data(raw)
            _t5 = _time.monotonic()
            if _diag:
                _diag.stage("t5_after_redact")
            resp = j(handler, {"session": redact})
            _t6 = _time.monotonic()
            if _diag:
                _diag.stage("t6_after_json_write")
            _total_ms = (_t6 - _t0) * 1000
            # Always log when slow (>2s) so we don't need HERMES_DEBUG_SLOW env var
            # to diagnose latency regressions. Opt-in env var still forces
            # logging on every request for development.
            if _debug_slow or _total_ms >= 2000:
                handler._safe_webui_print(
                    "[SLOW] session_id=%s get_session=%.1fms model_resolve=%.1fms "
                    "compact=%.1fms redact=%.1fms json_write=%.1fms total=%.1fms" % (
                        sid,
                        (_t2-_t1)*1000, (_t3-_t2)*1000, (_t4-_t3)*1000,
                        (_t5-_t4)*1000, (_t6-_t5)*1000, _total_ms,
                    )
                )
            if _diag:
                _diag.finish()
            return resp
        except KeyError:
            if _diag:
                _diag.finish()
            cli_meta = _routes._lookup_cli_session_metadata(sid)
            _session_profile = (cli_meta or {}).get("profile") or None
            _profile_agnostic = _routes._is_profile_agnostic_foreign_session(cli_meta)
            if not _profile_agnostic and not _routes._session_visible_to_active_profile(_session_profile, handler):
                if _session_profile:
                    return j(handler, {
                        "error": "Session belongs to a different profile",
                        "code": "session_profile_mismatch",
                        "session_id": sid,
                        "profile": _session_profile,
                    }, status=409)
                return bad(handler, "Session not found", 404)
            synth, reason = _routes._claim_or_synthesize_cli_session(sid, cli_meta=cli_meta or {})
            if reason == "was_webui":
                return bad(handler, "Session not found", 404)
            if synth is None:
                return bad(handler, "Session not found", 404)
            msgs = list(synth.messages or [])
            sess = {
                "session_id": synth.session_id,
                "title": synth.title,
                "workspace": synth.workspace,
                "model": synth.model,
                "message_count": len(msgs),
                "created_at": synth.created_at,
                "updated_at": synth.updated_at,
                "last_message_at": (
                    (cli_meta or {}).get("last_message_at")
                    or (cli_meta or {}).get("updated_at", 0)
                    or ((msgs or [{}])[-1].get("timestamp", 0))
                ),
                "pinned": bool(getattr(synth, "pinned", False)),
                "archived": bool(getattr(synth, "archived", False)),
                "project_id": getattr(synth, "project_id", None),
                "profile": synth.profile,
                "is_cli_session": bool(getattr(synth, "is_cli_session", False)),
                "source_tag": synth.source_tag,
                "raw_source": synth.raw_source,
                "session_source": synth.session_source,
                "source_label": synth.source_label,
                "read_only": bool(getattr(synth, "read_only", False)),
                "messages": msgs,
                "tool_calls": [],
            }
            attach_todo_state(sess, msgs)
            sess = _routes._merge_cli_sidebar_metadata(sess, cli_meta)
            return j(handler, {"session": public_session_projection(sess)})

    if parsed.path == "/api/session/lineage/report":
        sid = parse_qs(parsed.query).get("session_id", [""])[0]
        if not sid:
            return bad(handler, "session_id required", 400)
        report = read_session_lineage_report(_active_state_db_path(), sid)
        if not report.get("found"):
            return bad(handler, "Session not found", 404)
        return j(handler, report)

    if parsed.path == "/api/session/recovery/audit":
        from api.session_recovery import audit_session_recovery
        return j(handler, audit_session_recovery(SESSION_DIR, state_db_path=_active_state_db_path()))

    if parsed.path == "/api/session/status":
        sid = parse_qs(parsed.query).get("session_id", [""])[0]
        if not sid:
            return bad(handler, "Missing session_id")
        try:
            from api.session_ops import session_status
            _routes._clear_stale_stream_state(get_session(sid, metadata_only=True))
            return j(handler, session_status(sid))
        except KeyError:
            return bad(handler, "Session not found", 404)

    if parsed.path == "/api/session/yolo":
        sid = parse_qs(parsed.query).get("session_id", [""])[0]
        if not sid:
            return bad(handler, "Missing session_id")
        return j(handler, {"yolo_enabled": is_session_yolo_enabled(sid)})

    if parsed.path == "/api/session/usage":
        sid = parse_qs(parsed.query).get("session_id", [""])[0]
        if not sid:
            return bad(handler, "Missing session_id")
        try:
            from api.session_ops import session_usage
            return j(handler, session_usage(sid))
        except KeyError:
            return bad(handler, "Session not found", 404)

    if parsed.path == "/api/background/status":
        sid = parse_qs(parsed.query).get("session_id", [""])[0]
        if not sid:
            return bad(handler, "Missing session_id")
        from api.background import get_results
        return j(handler, {"results": get_results(sid)})

    if parsed.path == "/api/sessions":
        diag = RequestDiagnostics.maybe_start("GET", parsed.path, logger=logger, print_fn=getattr(handler, '_safe_webui_print', None))
        try:
            from api import profiles as profiles_api

            diag.stage("load_settings")
            settings = load_settings()
            show_cli_sessions = bool(settings.get("show_cli_sessions"))
            show_claude_code_sessions = bool(settings.get("show_claude_code_sessions"))
            show_previous_messaging_sessions = bool(
                settings.get("show_previous_messaging_sessions")
            )
            show_cron_sessions = bool(settings.get("show_cron_sessions"))
            show_webhook_sessions = bool(settings.get("show_webhook_sessions"))
            show_kanban_sessions = bool(settings.get("show_kanban_sessions"))
            agent_session_source_filter = settings.get("agent_session_source_filter")
            active_profile = profiles_api.get_active_profile_name()
            all_profiles = _routes._all_profiles_enabled(parsed)
            include_archived = _routes._query_flag(parsed, "include_archived")
            exclude_hidden = _routes._query_flag(parsed, "exclude_hidden")
            archived_limit = _routes._query_positive_int(parsed, "archived_limit", default=None, maximum=2000)
            archived_offset = _routes._query_positive_int(parsed, "archived_offset", default=0, maximum=200000)
            sidebar_source = parse_qs(parsed.query).get("sidebar_source", [""])[0].strip().lower() or None
            if sidebar_source not in ("webui", "cli"):
                sidebar_source = None
            key = _routes._session_list_cache_key(
                active_profile=active_profile,
                all_profiles=all_profiles,
                show_cli_sessions=show_cli_sessions,
                show_claude_code_sessions=show_claude_code_sessions,
                show_previous_messaging_sessions=show_previous_messaging_sessions,
                show_cron_sessions=show_cron_sessions,
                include_archived=include_archived,
                exclude_hidden=exclude_hidden,
                visible_only=True,
                show_webhook_sessions=show_webhook_sessions,
                show_kanban_sessions=show_kanban_sessions,
                source_filter=agent_session_source_filter,
                sidebar_source=sidebar_source,
                archived_limit=archived_limit,
                archived_offset=archived_offset,
            )
            payload = _routes._get_cached_session_list_payload(
                key=key,
                builder=lambda: _routes._build_session_list_cache_payload(
                    active_profile=active_profile,
                    all_profiles=all_profiles,
                    show_cli_sessions=show_cli_sessions,
                    show_claude_code_sessions=show_claude_code_sessions,
                    show_previous_messaging_sessions=show_previous_messaging_sessions,
                    show_cron_sessions=show_cron_sessions,
                    include_archived=include_archived,
                    exclude_hidden=exclude_hidden,
                    visible_only=True,
                    show_webhook_sessions=show_webhook_sessions,
                    show_kanban_sessions=show_kanban_sessions,
                    source_filter=agent_session_source_filter,
                    sidebar_source=sidebar_source,
                    archived_limit=archived_limit,
                    archived_offset=archived_offset,
                    diag=diag,
                ),
                diag=diag,
            )
            diag.stage("response_write")
            return j(handler, _routes._session_list_payload_to_response(payload), pretty=False)
        finally:
            diag.finish()

    return None


def _validate_session_toolsets_shape(toolsets):
    """Validate per-session toolset override shape without catalog lookup."""
    if toolsets is None:
        return None
    if not isinstance(toolsets, list) or not toolsets:
        raise ValueError("toolsets must be a non-empty list or null")
    if not all(isinstance(t, str) and t for t in toolsets):
        raise ValueError("each toolset must be a non-empty string")
    return toolsets


def _resolve_new_session_workspace(body, visible_prev_session_id):
    """Resolve a new-session workspace, recovering only verified inheritance."""
    from api import routes as _routes

    candidate = body.get("workspace")
    if not candidate:
        return None
    if (
        body.get("workspace_inherited_from_prev_session") is not True
        or not visible_prev_session_id
    ):
        return str(_routes.resolve_trusted_workspace(candidate))
    try:
        previous_session = get_session(visible_prev_session_id, metadata_only=True)
    except KeyError:
        return str(_routes.resolve_trusted_workspace(candidate))
    if str(getattr(previous_session, "workspace", None) or "") != str(candidate):
        return str(_routes.resolve_trusted_workspace(candidate))
    workspace, _recovered = _routes.resolve_implicit_workspace_with_recovery(
        candidate,
        _routes.get_last_workspace,
    )
    return str(workspace)


def _handle_post_session(handler, parsed, body):
    """Handle session lifecycle, metadata, branching, recovery, and compression routes.
    Returns True if handled, None if unhandled.
    """
    from api import routes as _routes

    _session_id_visible_to_request_profile = _routes._session_id_visible_to_request_profile
    load_projects = _routes.load_projects
    resolve_trusted_workspace = _routes.resolve_trusted_workspace
    get_last_workspace = _routes.get_last_workspace
    set_last_workspace = _routes.set_last_workspace
    _worktree_default_from_config = _routes._worktree_default_from_config
    _session_model_state_from_request = _routes._session_model_state_from_request
    new_session = _routes.new_session
    publish_session_list_changed = _routes.publish_session_list_changed
    _publish_session_list_changed = _routes._publish_session_list_changed
    _handle_session_compression_recovery_start = _routes._handle_session_compression_recovery_start
    _session_is_subagent_view_only = _routes._session_is_subagent_view_only
    Session = _routes.Session
    LOCK = _routes.LOCK
    SESSIONS = _routes.SESSIONS
    _evict_sessions_over_cap = _routes._evict_sessions_over_cap
    _handle_sessions_cleanup = _routes._handle_sessions_cleanup
    _handle_session_anchor_scene = _routes._handle_session_anchor_scene
    _get_or_materialize_session = _routes._get_or_materialize_session
    _get_session_agent_lock = _routes._get_session_agent_lock
    _sync_session_title_to_insights = _routes._sync_session_title_to_insights
    generate_session_title_for_session = _routes.generate_session_title_for_session
    _persist_generated_session_title = _routes._persist_generated_session_title
    _ensure_full_session_before_mutation = _routes._ensure_full_session_before_mutation
    _resolve_context_length_for_session_model = _routes._resolve_context_length_for_session_model
    is_safe_session_id = _routes.is_safe_session_id
    _lookup_cli_session_metadata = _routes._lookup_cli_session_metadata
    _is_messaging_session_id = _routes._is_messaging_session_id
    _worktree_retained_payload_for_session_id = _routes._worktree_retained_payload_for_session_id
    prune_session_from_index = _routes.prune_session_from_index
    _record_webui_deleted_session_tombstone = _routes._record_webui_deleted_session_tombstone
    _load_branch_source_or_refuse = _routes._load_branch_source_or_refuse
    _session_requires_cli_metadata_lookup = _routes._session_requires_cli_metadata_lookup
    _is_messaging_session_record = _routes._is_messaging_session_record
    get_cli_session_messages = _routes.get_cli_session_messages
    _merged_session_messages_for_display = _routes._merged_session_messages_for_display
    merge_session_messages_append_only = _routes.merge_session_messages_append_only
    _webui_sidecar_lineage_messages_for_display = _routes._webui_sidecar_lineage_messages_for_display
    _merged_webui_lineage_messages_for_display = _routes._merged_webui_lineage_messages_for_display
    _state_db_backstop_limit_for_display = _routes._state_db_backstop_limit_for_display
    get_state_db_session_messages = _routes.get_state_db_session_messages
    _reconcile_api_content_sidecars = _routes._reconcile_api_content_sidecars
    gateway_yolo_handoff = _routes.gateway_yolo_handoff
    set_session_yolo_enabled = _routes.set_session_yolo_enabled
    _enable_session_yolo_and_release_pending = _routes._enable_session_yolo_and_release_pending
    all_sessions = _routes.all_sessions
    _session_counts_toward_pin_quota = _routes._session_counts_toward_pin_quota
    _visible_pinned_lineage_ids = _routes._visible_pinned_lineage_ids
    _session_row_lineage_root_id = _routes._session_row_lineage_root_id
    _session_field = _routes._session_field
    _is_subagent_child_session_id = _routes._is_subagent_child_session_id
    title_from = _routes.title_from
    is_cli_session_row = _routes.is_cli_session_row
    import_cli_session = _routes.import_cli_session
    _worktree_retained_payload = _routes._worktree_retained_payload
    get_active_profile_name = _routes.get_active_profile_name
    _profiles_match = _routes._profiles_match
    if parsed.path == "/api/session/recovery/repair-safe":
        from api.session_recovery import repair_safe_session_recovery
        result = repair_safe_session_recovery(SESSION_DIR, state_db_path=_active_state_db_path())
        return j(handler, result, status=200 if result.get("clean") else 409)

    if parsed.path == "/api/session/new":
        workspace_prev_session_id = body.get("prev_session_id")
        if workspace_prev_session_id and not _session_id_visible_to_request_profile(
            handler, workspace_prev_session_id, emit_error=False
        ):
            workspace_prev_session_id = None
        # Option A: If project_id is provided and the project has a bound default_workspace,
        # prefer it when workspace is not explicitly passed or is merely inherited from prev session.
        req_project_id = body.get("project_id")
        if req_project_id and (not body.get("workspace") or body.get("workspace_inherited_from_prev_session")):
            for p in load_projects():
                if p.get("project_id") == req_project_id and p.get("default_workspace"):
                    body["workspace"] = p["default_workspace"]
                    body["workspace_inherited_from_prev_session"] = False
                    workspace_prev_session_id = None
                    break
        try:
            workspace = _resolve_new_session_workspace(body, workspace_prev_session_id)
        except (TypeError, ValueError) as e:
            return bad(handler, str(e))
        worktree_info = None
        worktree_skipped = None
        # Three-value worktree model (#6022): an explicit body value always
        # wins; an ABSENT key falls back to the agent's config-level
        # ``worktree:`` default so WebUI sessions and CLI sessions agree on
        # isolation for the same repo.  Clients that must never create a
        # worktree (e.g. the boot-time auto-bind) send ``worktree: false``
        # explicitly.
        raw_worktree = body.get("worktree")
        # Presence-based, not truthiness-based: a client that sends the key at
        # all (even ``worktree: null``) has spoken explicitly and never falls
        # through to the config default.  ``null`` parses as non-true below,
        # i.e. an explicit opt-out — only a genuinely ABSENT key inherits.
        worktree_explicit = "worktree" in body
        if worktree_explicit:
            worktree_requested = (
                raw_worktree is True
                or str(raw_worktree).strip().lower() in {"1", "true", "yes", "on"}
            )
        else:
            worktree_requested = _worktree_default_from_config(body.get("profile") or None)
        if worktree_requested:
            try:
                from api.worktrees import create_worktree_for_workspace
                base_workspace = workspace
                if not base_workspace:
                    base_workspace = str(resolve_trusted_workspace(get_last_workspace()))
                worktree_info = create_worktree_for_workspace(base_workspace)
                workspace = worktree_info["path"]
            except (TypeError, ValueError) as e:
                # Explicit requests keep the hard failure.  A config-default
                # request on a non-git workspace (create_worktree_for_workspace
                # raises ValueError) degrades to a plain session instead —
                # otherwise `worktree: true` in config.yaml would 400 every
                # session in every non-git directory.
                if worktree_explicit:
                    return bad(handler, str(e), status=400)
                worktree_info = None
                worktree_skipped = str(e)
            except Exception as e:
                logger.exception("failed to create worktree-backed session")
                return bad(handler, f"Failed to create worktree: {e}", status=500)
        model, model_provider = _session_model_state_from_request(
            body.get("model"),
            body.get("model_provider"),
        )
        try:
            enabled_toolsets = _validate_session_toolsets_shape(body.get("enabled_toolsets"))
        except ValueError as e:
            return bad(handler, str(e), status=400)
        # Use the profile sent by the client tab (if any) so that two tabs on
        # different profiles never clobber each other via the process-level global.
        # ── Memory lifecycle: commit the previous session before starting a new one ──
        prev_session_id = body.get("prev_session_id")
        if prev_session_id:
            if not _session_id_visible_to_request_profile(
                handler, prev_session_id, emit_error=False
            ):
                # Cross-profile hand-off after a profile switch: skip memory
                # commit for the previous profile's session, but still create
                # the new session (#5420).
                prev_session_id = None
            if prev_session_id:
                # Fire-and-forget: commit_memory_session() can take 1-5+ seconds
                # (extraction call to the memory provider), and blocking the
                # response here made "+ New Chat" feel slow/unresponsive.
                # commit_session_memory() already serialises overlapping commits
                # for a session via its own in-flight guard, so running it off
                # the request thread is safe.
                def _commit_prev_session_memory(_sid=prev_session_id):
                    try:
                        from api.session_lifecycle import commit_session_memory
                        from api.config import SESSION_AGENT_CACHE, SESSION_AGENT_CACHE_LOCK
                        prev_agent = None
                        with SESSION_AGENT_CACHE_LOCK:
                            _cached = SESSION_AGENT_CACHE.get(_sid)
                            if _cached:
                                prev_agent = _cached[0]
                        commit_session_memory(_sid, agent=prev_agent)
                    except Exception:
                        logger.warning(
                            "Lifecycle commit for prev_session %s failed",
                            _sid,
                            exc_info=True,
                        )
                    finally:
                        # Self-unregister so the background-commit registry does
                        # not leak completed threads; drain only tracks live ones.
                        try:
                            from api.session_lifecycle import _unregister_background_commit_thread
                            _unregister_background_commit_thread(threading.current_thread())
                        except Exception:
                            logger.debug("Silent exception in _commit_prev_session_memory", exc_info=True)
                            pass

                t = threading.Thread(
                    target=_commit_prev_session_memory,
                    daemon=True,
                    name=f"commit-memory-{prev_session_id}",
                )
                from api.session_lifecycle import _register_background_commit_thread
                # Refused only if shutdown draining has already begun; in that
                # window the inline drain commits the pending generation instead,
                # so skipping the worker start is safe (avoids a late daemon
                # thread the drain snapshot already missed).
                if _register_background_commit_thread(t):
                    t.start()
        s = new_session(
            workspace=workspace,
            model=model,
            model_provider=model_provider,
            profile=body.get("profile") or None,
            project_id=body.get("project_id") or None,
            worktree_info=worktree_info,
            enabled_toolsets=enabled_toolsets,
        )
        if worktree_info:
            publish_session_list_changed(
                "session_new",
                profile=getattr(s, "profile", None),
                session_id=getattr(s, "session_id", None),
            )
        payload = {
            "session": public_session_projection(s.compact() | {"messages": s.messages})
        }
        if worktree_skipped:
            # Config-default worktree was skipped (non-git workspace); tell the
            # client the session is plain so the UI doesn't assume isolation.
            payload["worktree_skipped"] = worktree_skipped
        return j(handler, payload)

    if parsed.path == "/api/session/compression-recovery/start":
        return _handle_session_compression_recovery_start(handler, body)

    if parsed.path == "/api/session/duplicate":
        try:
            sid = body.get("session_id")
            if not sid:
                return bad(handler, "session_id is required")
            if _session_is_subagent_view_only(sid):
                return bad(handler, "Subagent sessions are view-only and cannot be duplicated from WebUI", 400)

            session = Session.load(sid)
            if not session:
                # 404, not 400 — missing resource, not a malformed request.
                return bad(handler, "Session not found", status=404)

            # Deep-copy mutable lists so the duplicate is *actually* independent.
            # `Session.__init__` does `self.messages = messages or []` — plain
            # assignment, no copy. Without deepcopy, both sessions share the same
            # list object in memory; appending to one mutates the other.
            # Items inside `messages` are dicts with mutable values (tool_calls,
            # content arrays), so a shallow `list(...)` is not enough.
            copied_session = Session(
                session_id=uuid.uuid4().hex[:12],
                # Defensive: legacy sessions may have title=None on disk; fall back to 'Untitled'
                # so `+ " (copy)"` doesn't TypeError.
                title=(session.title or "Untitled") + " (copy)",
                workspace=session.workspace,
                model=session.model,
                model_provider=session.model_provider,
                messages=copy.deepcopy(session.messages),
                tool_calls=copy.deepcopy(session.tool_calls),
                # Reset ephemeral / per-session-instance flags. Duplicating an
                # archived conversation should produce a visible (un-archived)
                # copy; pinned status doesn't transfer either.
                pinned=False,
                archived=False,
                project_id=session.project_id,
                profile=session.profile,
                input_tokens=session.input_tokens,
                output_tokens=session.output_tokens,
                estimated_cost=session.estimated_cost,
                cache_read_tokens=getattr(session, "cache_read_tokens", 0),
                cache_write_tokens=getattr(session, "cache_write_tokens", 0),
                # Per-session settings the user may have customized — carry them over
                # so the duplicate behaves identically until further edits. Compression
                # anchor + last_prompt_tokens are intentionally NOT carried — those
                # re-derive on the next turn.
                personality=session.personality,
                enabled_toolsets=getattr(session, "enabled_toolsets", None),
                context_length=getattr(session, "context_length", None),
                threshold_tokens=getattr(session, "threshold_tokens", None),
                truncation_watermark=getattr(session, "truncation_watermark", None),
                truncation_boundary=getattr(session, "truncation_boundary", None),
                # context_messages is the authoritative model-facing prefix — must be
                # deepcopied so the duplicate has its own independent context that won't
                # be mutated when the original session's context changes (#2914).
                context_messages=copy.deepcopy(getattr(session, "context_messages", None) or []),
                # Gateway routing — if the user customized routing for this session,
                # the duplicate should behave identically.
                gateway_routing=copy.deepcopy(getattr(session, "gateway_routing", None)),
                gateway_routing_history=copy.deepcopy(getattr(session, "gateway_routing_history", None) or []),
                # Preserve LLM-generated title flag so we don't regenerate title on duplicate.
                llm_title_generated=getattr(session, "llm_title_generated", False),
                manual_title=getattr(session, "manual_title", False),
                # Composer draft — preserve per-session draft state.
                composer_draft=copy.deepcopy(getattr(session, "composer_draft", None) or {}),
                # Context engine state — preserve so the duplicate's context engine
                # starts from the same point as the original.
                context_engine=getattr(session, "context_engine", None),
                context_engine_state=copy.deepcopy(getattr(session, "context_engine_state", None) or {}),
                created_at=time.time(),
                updated_at=time.time(),
            )

            with LOCK:
                SESSIONS[copied_session.session_id] = copied_session
                SESSIONS.move_to_end(copied_session.session_id)
                _evict_sessions_over_cap()  # #4765: safe LRU eviction (never active/unsaved)
            # Persist immediately. The pre-PR flow (/api/session/new + /api/session/rename)
            # accidentally avoided this because `/api/session/rename` calls `s.save()`.
            # Without this explicit save, the duplicate is in-memory only — if the user
            # refreshes before sending a turn, the duplicate vanishes.
            copied_session.save()
            publish_session_list_changed(
                "session_duplicate",
                profile=getattr(copied_session, "profile", None),
                session_id=getattr(copied_session, "session_id", None),
            )

            return j(
                handler,
                {
                    "session": public_session_projection(
                        copied_session.compact() | {"messages": copied_session.messages}
                    )
                },
            )
        except Exception as e:
            logger.warning("Silent exception in handle_post", exc_info=True)
            return bad(handler, str(e))

    if parsed.path == "/api/sessions/cleanup":
        return _handle_sessions_cleanup(handler, body, zero_only=False)

    if parsed.path == "/api/sessions/cleanup_zero_message":
        return _handle_sessions_cleanup(handler, body, zero_only=True)

    if parsed.path == "/api/session/anchor-scene":
        return _handle_session_anchor_scene(handler, body)

    if parsed.path == "/api/session/rename":
        try:
            require(body, "session_id", "title")
        except ValueError as e:
            return bad(handler, str(e))
        try:
            s = _get_or_materialize_session(body["session_id"])
        except KeyError:
            return bad(handler, "Session not found", 404)
        except PermissionError:
            return bad(handler, "Read-only imported sessions cannot be renamed from WebUI", 403)
        with _get_session_agent_lock(body["session_id"]):
            from api.session_ops import apply_session_title_rename
            apply_session_title_rename(s, body["title"])
            s.save()
        _sync_session_title_to_insights(s)
        publish_session_list_changed(
            "session_rename",
            profile=getattr(s, "profile", None),
            session_id=getattr(s, "session_id", body["session_id"]),
        )
        return j(handler, {"session": s.compact()})


    if parsed.path == "/api/session/title/regenerate":
        try:
            require(body, "session_id")
        except ValueError as e:
            return bad(handler, str(e))
        sid = body["session_id"]
        prefer_latest = bool(body.get("prefer_latest", False))
        try:
            s = _get_or_materialize_session(sid)
        except KeyError:
            return bad(handler, "Session not found", 404)
        except PermissionError:
            return bad(handler, "Read-only imported sessions cannot regenerate titles", 403)
        next_title, reason, raw_preview = generate_session_title_for_session(s, prefer_latest=prefer_latest)
        if not next_title:
            return bad(handler, f"Could not generate a better title ({reason or 'empty'})", 422)
        _persist_generated_session_title(s, next_title, event_reason="session_title_regenerate")
        return j(handler, {
            "session": s.compact(),
            "title": s.title,
            "status": reason,
            "raw_preview": (raw_preview or "")[:240],
        })

    if parsed.path == "/api/personality/set":
        try:
            require(body, "session_id")
        except ValueError as e:
            return bad(handler, str(e))
        if "name" not in body:
            return bad(handler, "Missing required field: name")
        sid = body["session_id"]
        if _session_is_subagent_view_only(sid):
            return bad(handler, "Subagent sessions are view-only and cannot be modified from WebUI", 400)
        name = body["name"].strip()
        try:
            s = get_session(sid)
            s = _ensure_full_session_before_mutation(sid, s)
        except KeyError:
            return bad(handler, "Session not found", 404)
        # Resolve personality from config.yaml agent.personalities section
        # (matches hermes-agent CLI behavior)
        prompt = ""
        if name:
            from api.config import reload_config as _reload_cfg2

            _reload_cfg2()  # pick up config changes without restart
            from api.config import get_config as _get_cfg2

            _cfg2 = _get_cfg2()
            agent_cfg = _cfg2.get("agent", {})
            raw_personalities = agent_cfg.get("personalities", {})
            if not isinstance(raw_personalities, dict) or name not in raw_personalities:
                return bad(
                    handler, f'Personality "{name}" not found in config.yaml', 404
                )
            value = raw_personalities[name]
            # Resolve prompt using the same logic as hermes-agent cli.py
            if isinstance(value, dict):
                parts = [value.get("system_prompt", "") or value.get("prompt", "")]
                if value.get("tone"):
                    parts.append(f"Tone: {value['tone']}")
                if value.get("style"):
                    parts.append(f"Style: {value['style']}")
                prompt = "\n".join(p for p in parts if p)
            else:
                prompt = str(value)
        with _get_session_agent_lock(sid):
            s.personality = name if name else None
            s.save()
        return j(handler, {"ok": True, "personality": s.personality, "prompt": prompt})

    if parsed.path == "/api/session/toolsets":
        """Set or clear per-session toolset override (#493).

        POST body: { session_id, toolsets: [...] | null }
        - toolsets: list of toolset names to restrict the session to, or null to clear.
        """
        try:
            require(body, "session_id")
        except ValueError as e:
            return bad(handler, str(e))
        sid = body["session_id"]
        if _session_is_subagent_view_only(sid):
            return bad(handler, "Subagent sessions are view-only and cannot be modified from WebUI", 400)
        toolsets = body.get("toolsets")
        try:
            toolsets = _validate_session_toolsets_shape(toolsets)
        except ValueError as e:
            return bad(handler, str(e), status=400)
        try:
            s = get_session(sid)
        except KeyError:
            return bad(handler, "Session not found", 404)
        with _get_session_agent_lock(sid):
            s.enabled_toolsets = toolsets
            s.save()
        return j(handler, {"ok": True, "enabled_toolsets": s.enabled_toolsets})

    if parsed.path == "/api/session/draft":
        # GET ?session_id=X  → return current draft
        # POST body          → save draft { session_id, text?, files? }
        # HTTP method is in handler.command (e.g. "POST", "GET"), parsed has no .method
        import time as _draft_time
        _draft_t0 = _draft_time.monotonic()
        _draft_stages = []

        def _draft_mark(name):
            _draft_stages.append((name, _draft_time.monotonic()))
        _draft_mark("enter")
        if handler.command == "GET":
            query = parse_qs(parsed.query)
            sid = query.get("session_id", [""])[0] if parsed.query else ""
            if not sid:
                return bad(handler, "session_id is required", 400)
            try:
                s = get_session(sid)
            except KeyError:
                return bad(handler, "Session not found", 404)
            draft = getattr(s, "composer_draft", {}) or {}
            return j(handler, {"draft": draft})
        # POST
        try:
            require(body, "session_id")
        except ValueError as e:
            return bad(handler, str(e))
        sid = body["session_id"]
        if _session_is_subagent_view_only(sid):
            return bad(handler, "Subagent sessions are view-only and cannot store a draft from WebUI", 400)
        text = body.get("text")
        files = body.get("files")
        # Stage-326 hardening (per Opus advisor): size + type validation on
        # the draft inputs. Without this, a misbehaving or malicious client
        # can persist multi-MB strings into the session JSON on every keystroke
        # via the 400ms debounced auto-save.
        _MAX_DRAFT_TEXT = 50_000  # 50 KB cap on textarea content
        _MAX_DRAFT_FILES = 50  # max number of attached file references
        if text is not None and not isinstance(text, str):
            text = ""
        if isinstance(text, str) and len(text) > _MAX_DRAFT_TEXT:
            text = text[:_MAX_DRAFT_TEXT]
        if files is not None and not isinstance(files, list):
            files = []
        if isinstance(files, list) and len(files) > _MAX_DRAFT_FILES:
            files = files[:_MAX_DRAFT_FILES]
        try:
            s = get_session(sid)
        except KeyError:
            return bad(handler, "Session not found", 404)
        _draft_mark("after_get_session")
        unchanged = False
        with _get_session_agent_lock(sid):
            _draft_mark("acquired_lock")
            current_draft = dict(getattr(s, "composer_draft", {}) or {})
            next_draft = dict(current_draft)
            if text is not None:
                next_draft["text"] = text
            if files is not None:
                next_draft["files"] = files
            if next_draft == current_draft:
                unchanged = True
                saved_draft = current_draft
            else:
                s.composer_draft = next_draft
                # Draft persistence is not conversation activity. Touching updated_at
                # here makes the active-session external-refresh poll force-reload the
                # current chat every few seconds while the user is typing, and that
                # delayed reload can restore an older draft over newer local input.
                _draft_mark("before_save")
                s.save(touch_updated_at=False, skip_index=True)
                _draft_mark("after_save")
                saved_draft = s.composer_draft
        _draft_mark("released_lock")
        payload = {"ok": True, "draft": saved_draft}
        if unchanged:
            payload["unchanged"] = True
        _draft_mark("before_json")
        j(handler, payload)
        _draft_mark("after_json")
        _draft_stages.append(("end", _draft_time.monotonic()))
        if _draft_stages[-1][1] - _draft_t0 > 0.2:
            parts = " ".join(
                f"{n}={((t - prev[1]) * 1000):.1f}ms"
                for (n, t), prev in zip(_draft_stages[1:], _draft_stages[:-1], strict=True)
            )
            handler._safe_webui_print(
                "[SLOW] /api/session/draft total=%.1fms stages: %s" % (
                    (_draft_stages[-1][1] - _draft_t0) * 1000,
                    parts,
                )
            )
        return True

    if parsed.path == "/api/session/update":
        try:
            require(body, "session_id")
        except ValueError as e:
            return bad(handler, str(e))
        try:
            s = _get_or_materialize_session(body["session_id"])
        except KeyError:
            return bad(handler, "Session not found", 404)
        except PermissionError:
            return bad(handler, "Read-only imported sessions cannot be updated from WebUI", 403)
        old_ws = getattr(s, "workspace", "")
        old_model = getattr(s, "model", None)
        old_provider = getattr(s, "model_provider", None)
        try:
            new_ws = str(resolve_trusted_workspace(body.get("workspace", s.workspace)))
        except ValueError as e:
            return bad(handler, str(e))
        with _get_session_agent_lock(body["session_id"]):
            s.workspace = new_ws
            if "model" in body or "model_provider" in body:
                model, provider = _session_model_state_from_request(
                    body.get("model", s.model),
                    body.get("model_provider") if "model_provider" in body else None,
                    getattr(s, "model_provider", None),
                )
                if model is not None:
                    s.model = model
                s.model_provider = provider
                if (
                    str(old_model or "") != str(getattr(s, "model", "") or "")
                    or str(old_provider or "") != str(getattr(s, "model_provider", "") or "")
                ):
                    s.context_length = _resolve_context_length_for_session_model(
                        getattr(s, "model", None),
                        getattr(s, "model_provider", None),
                    )
                    s.threshold_tokens = 0
                    s.last_prompt_tokens = 0
                    from api.config import _evict_session_agent

                    _evict_session_agent(body["session_id"])
            s.save()
        if str(old_ws or "") != str(new_ws or ""):
            try:
                from api.terminal import close_terminal
                close_terminal(body["session_id"])
            except Exception:
                logger.debug("Failed to close workspace terminal after workspace update")
        set_last_workspace(new_ws)
        return j(
            handler,
            {"session": public_session_projection(s.compact() | {"messages": s.messages})},
        )
    if parsed.path == "/api/session/worktree/remove":
        sid = body.get("session_id", "")
        if not sid or not isinstance(sid, str) or not sid.strip():
            return bad(handler, "session_id must be a non-empty string", status=400)
        sid = sid.strip()
        if not is_safe_session_id(sid):
            return bad(handler, "Invalid session_id", 400)
        try:
            s = get_session(sid, metadata_only=True)
        except KeyError:
            return bad(handler, "Session not found", status=404)
        force = bool(body.get("force", False))
        try:
            from api.worktrees import remove_worktree_for_session

            result = remove_worktree_for_session(s, force=force)
            return j(handler, result)
        except ValueError as exc:
            return bad(handler, str(exc), status=400)
        except Exception as exc:
            logger.exception("failed to remove worktree for session %s", sid)
            return bad(handler, _sanitize_error(exc), status=500)

    if parsed.path == "/api/session/delete":
        sid = body.get("session_id", "")
        if not sid:
            return bad(handler, "session_id is required")
        if not is_safe_session_id(sid):
            return bad(handler, "Invalid session_id", 400)
        cli_meta_for_delete = _lookup_cli_session_metadata(sid)
        if cli_meta_for_delete.get("read_only"):
            return bad(handler, "Read-only imported sessions cannot be deleted from WebUI", 400)
        # A delegated subagent child (#5307) is view-only and owned by the
        # delegate runner. Deleting it here would call delete_cli_session() and
        # erase the child's state.db transcript — refuse it.
        if _session_is_subagent_view_only(sid):
            return bad(handler, "Subagent sessions are view-only and cannot be deleted from WebUI", 400)
        is_messaging_session = _is_messaging_session_id(sid)
        worktree_retained = _worktree_retained_payload_for_session_id(sid)
        try:
            event_profile = getattr(get_session(sid, metadata_only=True), "profile", None)
        except KeyError:
            event_profile = None
        except Exception:
            logger.debug("Failed to resolve profile for deleted session %s", sid, exc_info=True)
            event_profile = None
        # Serialize with recovery, but bound contention so a browser timeout
        # cannot be followed by a delayed server-side delete.
        session_lock = _get_session_agent_lock(sid)
        if not session_lock.acquire(timeout=5):
            return bad(handler, "Session busy, try again", 503)
        try:
            with LOCK:
                SESSIONS.pop(sid, None)
            try:
                p = (SESSION_DIR / f"{sid}.json").resolve()
                p.relative_to(SESSION_DIR.resolve())
            except Exception:
                logger.warning("Silent exception in handle_post", exc_info=True)
                return bad(handler, "Invalid session_id", 400)
            sidecar_deleted = False
            try:
                p.unlink(missing_ok=True)
            except Exception:
                logger.debug("Failed to unlink session file %s", p)
            sidecar_deleted = not p.exists()
            try:
                prune_session_from_index(sid)
            except Exception:
                logger.debug("Failed to prune deleted session from index: %s", sid, exc_info=True)
            try:
                p.with_suffix('.json.bak').unlink(missing_ok=True)
            except Exception:
                logger.debug("Failed to unlink session backup file %s", p.with_suffix('.json.bak'))
            if sidecar_deleted and not is_messaging_session:
                try:
                    _record_webui_deleted_session_tombstone(sid)
                except Exception:
                    logger.debug("Failed to tombstone deleted WebUI session %s", sid, exc_info=True)
        finally:
            session_lock.release()
        # Evict outside the mutation lock: lifecycle commit may perform provider
        # I/O and must not hold a per-session Session lock.
        from api.config import _evict_session_agent
        _evict_session_agent(sid)
        try:
            from api.upload import _session_attachment_dir

            shutil.rmtree(_session_attachment_dir(sid), ignore_errors=True)
        except Exception:
            logger.debug("Failed to clean attachment dir for deleted session %s", sid)
        # Remove the turn-journal shards and the run-journal directory so a
        # deleted conversation is not recoverable from disk. The session JSON +
        # state.db rows are cleared above, but these journals retain the user's
        # messages (turn journal) and the full request/response payloads (run
        # journal) in plaintext. (#3802)
        try:
            from api.turn_journal import delete_turn_journal

            delete_turn_journal(sid)
        except Exception:
            logger.debug("Failed to delete turn journal for deleted session %s", sid)
        try:
            from api.run_journal import delete_run_journal

            delete_run_journal(sid)
        except Exception:
            logger.debug("Failed to delete run journal for deleted session %s", sid)
        # The weak lock registry releases this entry automatically after all
        # holders and waiters drop their strong references.
        # Prune the completion-dedup entry too. The reaper sweeps it once the
        # completion is delivered (drained from PENDING); a session deleted
        # while a completion is still pending would otherwise keep its entry.
        try:
            from api.background_process import forget_bg_task_completion_dedup

            forget_bg_task_completion_dedup(sid)
        except Exception:
            logger.debug("Failed to prune bg-task dedup entry for deleted session %s", sid)
        try:
            from api.terminal import close_terminal
            close_terminal(sid)
        except Exception:
            logger.debug("Failed to close workspace terminal for deleted session %s", sid)
        # Also delete from CLI state.db for CLI sessions shown in sidebar,
        # but never erase external messaging channel memory via WebUI delete.
        state_db_cleanup_failed = False
        if not is_messaging_session:
            try:
                from api.models import delete_cli_session

                state_db_cleanup_failed = not delete_cli_session(sid)
            except Exception:
                state_db_cleanup_failed = True
                logger.warning("Failed to delete CLI session %s", sid, exc_info=True)
        _publish_session_list_changed("session_delete", profile=event_profile)
        return j(
            handler,
            {
                "ok": True,
                "state_db_cleanup_failed": state_db_cleanup_failed,
                **worktree_retained,
            },
        )

    if parsed.path == "/api/session/clear":
        try:
            require(body, "session_id")
        except ValueError as e:
            return bad(handler, str(e))
        if _session_is_subagent_view_only(body["session_id"]):
            return bad(handler, "Subagent sessions are view-only and cannot be modified from WebUI", 400)
        try:
            s = get_session(body["session_id"])
        except KeyError:
            return bad(handler, "Session not found", 404)
        sid = body["session_id"]
        with _get_session_agent_lock(sid):
            had_sidecar_messages = bool(s.messages or [])
            # Clear is a full truncate-to-empty: route through the SAME helper the
            # /api/session/truncate handler uses (single source of truth) so the
            # display + context arrays are emptied AND the truncation watermark is
            # set via _truncation_watermark_for([]) == 0.0 — the #2914
            # truncate-to-empty sentinel that blocks state.db replay. Before this,
            # /clear wiped s.messages but left the watermark unset, so the
            # append-only state.db merge treated it as "keep everything" and the
            # cleared history resurrected on the next /api/session read (#5532).
            from api.session_ops import truncate_session_at_keep
            truncate_session_at_keep(s, 0)
            s.tool_calls = []
            # A compressed-continuation child keeps its archived transcript in a
            # parent sidecar marked pre_compression_snapshot;
            # _webui_sidecar_lineage_messages_for_display() stitches that parent
            # back with truncation_watermark=None, so the 0.0 sentinel on the
            # CHILD does NOT stop the parent from resurrecting the cleared history
            # on refresh. Detach the compression lineage (#5532/#5553) — but ONLY
            # when the parent is actually a pre_compression_snapshot; a genuine
            # fork parent (session_source="fork" from /api/session/branch) must
            # keep its link so the child still nests + shows "Forked from"
            # (sessions.js:5720/5964/7105). Dropping every parent broke that
            # (#5532 Codex gate).
            _parent_sid = getattr(s, "parent_session_id", None)
            if _parent_sid:
                _parent_is_compression_snapshot = False
                try:
                    _parent = get_session(_parent_sid, metadata_only=True)
                    _parent_is_compression_snapshot = bool(
                        getattr(_parent, "pre_compression_snapshot", False)
                    )
                except Exception:
                    logger.warning("Silent exception in handle_post", exc_info=True)
                    _parent_is_compression_snapshot = False
                if _parent_is_compression_snapshot:
                    s.parent_session_id = None
                    s.compression_anchor_visible_idx = None
                    s.compression_anchor_message_key = None
            s.active_stream_id = None
            s.pending_user_message = None
            s.pending_attachments = []
            s.pending_started_at = None
            s.pending_user_source = None
            s.clear_generation = uuid.uuid4().hex if had_sidecar_messages else None
            # Reset the title via the rename helper so clearing a manually-named
            # session also clears manual_title/llm_title_generated — otherwise the
            # reused session keeps its manual-title protection and never auto-names
            # again (#3542 lifecycle gap).
            from api.session_ops import apply_session_title_rename
            apply_session_title_rename(s, "Untitled")
            s.save()
            persisted_clear = False
            try:
                persisted = json.loads(s.path.read_text(encoding="utf-8"))
                persisted_clear = (
                    persisted.get("messages") == []
                    and persisted.get("context_messages") == []
                    and persisted.get("truncation_watermark") == 0.0
                    and persisted.get("truncation_boundary") == 0.0
                    and persisted.get("active_stream_id") is None
                    and persisted.get("pending_user_message") is None
                    and persisted.get("pending_attachments") == []
                    and persisted.get("pending_started_at") is None
                    and persisted.get("pending_user_source") is None
                    and persisted.get("clear_generation") == s.clear_generation
                )
            except (OSError, json.JSONDecodeError, ValueError):
                logger.warning("session clear could not verify persisted empty state for %s", sid, exc_info=True)
            if had_sidecar_messages and persisted_clear:
                try:
                    s.path.with_suffix('.json.bak').unlink(missing_ok=True)
                except OSError:
                    logger.warning("session clear could not remove stale backup for %s", sid, exc_info=True)
        # Evict cached agent outside the per-session lock.  Eviction may run a
        # boundary memory commit for batch-extraction providers, and provider
        # I/O must not hold the session mutation lock.
        from api.config import _evict_session_agent
        _evict_session_agent(sid)
        return j(handler, {"ok": True, "session": s.compact()})

    if parsed.path == "/api/session/truncate":
        try:
            require(body, "session_id")
        except ValueError as e:
            return bad(handler, str(e))
        if _session_is_subagent_view_only(body["session_id"]):
            return bad(handler, "Subagent sessions are view-only and cannot be modified from WebUI", 400)
        if body.get("keep_count") is None:
            return bad(handler, "Missing required field(s): keep_count")
        try:
            s = get_session(body["session_id"])
        except KeyError:
            return bad(handler, "Session not found", 404)
        # Validate keep_count before it reaches the destructive `messages[:keep]`
        # slice. A non-numeric value would raise ValueError and surface as a
        # confusing 500; a NEGATIVE value slices as `messages[:-N]`, which
        # silently DELETES the most recent N messages (e.g. keep_count=-5 on a
        # 3-message session wipes the whole transcript) and then persists it via
        # save(). Mirror the explicit guard the /api/session/branch handler
        # already applies to its own keep_count. (Opus pre-release follow-up.)
        try:
            keep = int(body["keep_count"])
        except (ValueError, TypeError):
            return bad(handler, "keep_count must be an integer")
        if keep < 0:
            return bad(handler, "keep_count must be non-negative")
        with _get_session_agent_lock(body["session_id"]):
            from api.session_ops import truncate_session_at_keep

            old_msg_count, old_ctx_count = truncate_session_at_keep(s, keep)
            s.save()
            logger.info(
                "truncate %s: messages %d→%d, context_messages %d→%d, watermark=%.2f",
                body["session_id"], old_msg_count, len(s.messages or []),
                old_ctx_count, len(getattr(s, 'context_messages', None) or []),
                s.truncation_watermark or 0,
            )
        from api.config import _evict_session_agent
        _evict_session_agent(body["session_id"])
        return j(
            handler,
            {
                "ok": True,
                "session": public_session_projection(
                    s.compact() | {"messages": s.messages}
                ),
            },
        )

    if parsed.path == "/api/session/branch":
        # Fork a conversation from any message point (#465).
        # Accepts: {session_id, keep_count?, title?}
        #   keep_count: number of messages to copy (0=empty, undefined=full history)
        #   title: custom title (defaults to "<original title> (fork)")
        try:
            require(body, "session_id")
        except ValueError as e:
            return bad(handler, str(e))
        # Reject non-string session_id explicitly so the failure surfaces as a
        # 400 instead of a generic 500 from get_session() raising TypeError.
        # (Opus pre-release follow-up.)
        if not isinstance(body["session_id"], str):
            return bad(handler, "session_id must be a string")
        source = _load_branch_source_or_refuse(handler, body["session_id"])
        if source is None:
            return True

        keep_count = body.get("keep_count")
        if keep_count is not None:
            try:
                keep_count = int(keep_count)
            except (ValueError, TypeError):
                return bad(handler, "keep_count must be an integer")
            # Negative slice (`messages[:-N]`) returns "all but last N", which
            # is a confusing fork semantic. Reject explicitly so the user
            # doesn't accidentally fork a session with the tail truncated when
            # they meant to copy the prefix. (Opus pre-release follow-up.)
            if keep_count < 0:
                return bad(handler, "keep_count must be non-negative")

        custom_title = body.get("title")
        if custom_title:
            custom_title = str(custom_title).strip()[:80] or None

        # Build messages slice in the same coordinate space exposed by GET
        # /api/session so frontend keep_count values from merged messaging
        # transcripts do not silently become full sidecar copies.
        try:
            if not getattr(source, "_branch_source_readonly", False):
                source.save()
        except Exception:
            logger.warning("Silent exception in handle_post", exc_info=True)
            pass
        cli_meta = _lookup_cli_session_metadata(source.session_id) if _session_requires_cli_metadata_lookup(source) else {}
        is_messaging_session = _is_messaging_session_record(source) or _is_messaging_session_record(cli_meta)
        cli_messages = get_cli_session_messages(source.session_id) if is_messaging_session else []
        if is_messaging_session:
            if cli_messages:
                source_messages = _merged_session_messages_for_display(source, cli_messages)
            else:
                # Match GET /api/session: a messaging session with no CLI
                # transcript does not fall back to state.db rows.
                source_messages = merge_session_messages_append_only(
                    _webui_sidecar_lineage_messages_for_display(source),
                    [],
                    truncation_watermark=getattr(source, "truncation_watermark", None),
                    truncation_boundary=getattr(source, "truncation_boundary", None),
                )
                source_messages = _merged_webui_lineage_messages_for_display(source, source_messages)
        else:
            # Match GET /api/session's full-transcript display path exactly:
            # sidecar lineage stitched across compression snapshots, merged
            # append-only with state.db rows, then parent-row backfill for
            # partial continuations. The frontend's keep_count is an index
            # into THAT merged list; slicing the raw sidecar instead landed
            # the cut too early whenever the merged view deduplicates rows
            # (replayed sidecar/state.db doubles, filtered prefixes), so the
            # fork stopped mid tool-run and dropped the final conclusion.
            _state_db_reader_kwargs = {
                "profile": getattr(source, "profile", None) or None,
            }
            _backstop = _state_db_backstop_limit_for_display(source, None)
            if _backstop is not None:
                _state_db_reader_kwargs["limit"] = _backstop
            source_messages = merge_session_messages_append_only(
                _webui_sidecar_lineage_messages_for_display(source),
                get_state_db_session_messages(
                    source.session_id,
                    **_state_db_reader_kwargs,
                ),
                truncation_watermark=getattr(source, "truncation_watermark", None),
                truncation_boundary=getattr(source, "truncation_boundary", None),
            )
            source_messages = _merged_webui_lineage_messages_for_display(source, source_messages)
        if keep_count is not None:
            forked_messages = source_messages[:keep_count]
        else:
            forked_messages = list(source_messages)

        # Derive title
        if custom_title:
            branch_title = custom_title
        else:
            source_title = source.title or "Untitled"
            branch_title = f"{source_title} (fork)"

        # Create new session inheriting workspace/model/profile
        from api.session_ops import truncate_context_for_display_keep

        fork_keep = keep_count if keep_count is not None else len(source_messages)
        forked_context = copy.deepcopy(
            truncate_context_for_display_keep(
                getattr(source, "context_messages", None),
                source_messages,
                fork_keep,
            )
        )
        # `truncate_context_for_display_keep` aligns rows by visible display
        # identity but intentionally does not carry provider replay sidecars.
        # Reconcile only the retained prefix before constructing the branch so
        # its persisted model context receives the same conservative
        # api_content bytes as the forked display messages.  The helper mutates
        # the freshly aligned context copy; the source session and
        # forked_messages ownership remain untouched.
        _reconcile_api_content_sidecars(forked_context, forked_messages)
        branch = Session(
            workspace=source.workspace,
            model=source.model,
            model_provider=getattr(source, "model_provider", None),
            profile=getattr(source, "profile", None),
            title=branch_title,
            messages=forked_messages,
            project_id=getattr(source, "project_id", None),
            personality=getattr(source, "personality", None),
            enabled_toolsets=getattr(source, "enabled_toolsets", None),
            context_length=getattr(source, "context_length", None),
            threshold_tokens=getattr(source, "threshold_tokens", None),
            # context_messages — truncated to fork prefix (not full parent copy)
            context_messages=copy.deepcopy(forked_context),
            # Gateway routing — inherit from source
            gateway_routing=copy.deepcopy(getattr(source, "gateway_routing", None)),
            # Context engine — inherit state so branch's context engine starts correctly
            context_engine=getattr(source, "context_engine", None),
            context_engine_state=copy.deepcopy(getattr(source, "context_engine_state", None) or {}),
            parent_session_id=source.session_id,
            session_source="fork",
        )
        with LOCK:
            SESSIONS[branch.session_id] = branch
            SESSIONS.move_to_end(branch.session_id)
            _evict_sessions_over_cap()  # #4765: safe LRU eviction (never active/unsaved)

        # Persist only if there are messages (matches new_session pattern)
        if forked_messages:
            branch.save()
            publish_session_list_changed(
                "session_branch",
                profile=getattr(branch, "profile", None),
                session_id=getattr(branch, "session_id", None),
            )

        return j(handler, {
            "session_id": branch.session_id,
            "title": branch_title,
            "parent_session_id": source.session_id,
        })

    if parsed.path == "/api/session/compress/start":
        return _handle_session_compress_start(handler, body)

    if parsed.path == "/api/session/compress":
        return _handle_session_compress(handler, body)

    if parsed.path == "/api/session/conversation-rounds":
        return _handle_conversation_rounds(handler, body)

    if parsed.path == "/api/session/handoff-summary":
        return _handle_handoff_summary(handler, body)

    if parsed.path == "/api/session/retry":
        try:
            require(body, "session_id")
        except ValueError as e:
            return bad(handler, str(e))
        if _session_is_subagent_view_only(body["session_id"]):
            return bad(handler, "Subagent sessions are view-only and cannot be modified from WebUI", 400)
        try:
            from api.session_ops import retry_last
            result = retry_last(body["session_id"])
            return j(handler, {"ok": True, **result})
        except KeyError:
            return bad(handler, "Session not found", 404)
        except ValueError as e:
            return j(handler, {"error": str(e)})

    if parsed.path == "/api/session/undo":
        try:
            require(body, "session_id")
        except ValueError as e:
            return bad(handler, str(e))
        if _session_is_subagent_view_only(body["session_id"]):
            return bad(handler, "Subagent sessions are view-only and cannot be modified from WebUI", 400)
        try:
            from api.session_ops import undo_last
            result = undo_last(body["session_id"])
            return j(handler, {"ok": True, **result})
        except KeyError:
            return bad(handler, "Session not found", 404)
        except ValueError as e:
            return j(handler, {"error": str(e)})

    # ── YOLO mode toggle (POST) ──
    # Session-scoped only — stored in-memory on the server side.
    # Important lifecycle notes:
    #   • Page reload: state PERSISTS (frontend re-fetches via GET endpoint)
    #   • Cross-tab: state is SHARED (same server-side flag per session)
    #   • Server restart: state is LOST (in-memory only)
    #   • Cross-session: isolated (each session has its own flag)
    # Fixes #467
    if parsed.path == "/api/session/yolo":
        try:
            require(body, "session_id")
        except ValueError as e:
            return bad(handler, str(e))
        sid = str(body["session_id"] or "").strip()
        enabled = bool(body.get("enabled", True))
        if not enabled:
            with gateway_yolo_handoff(sid):
                set_session_yolo_enabled(sid, False)
                return j(handler, {"ok": True, "yolo_enabled": bool(is_session_yolo_enabled(sid))})

        payload, status = _enable_session_yolo_and_release_pending(sid, choice="once")
        return j(handler, payload, status=status)

    if parsed.path == "/api/session/pin":
        try:
            require(body, "session_id")
        except ValueError as e:
            return bad(handler, str(e))
        if _session_is_subagent_view_only(body["session_id"]):
            return bad(handler, "Subagent sessions are view-only and cannot be modified from WebUI", 400)
        try:
            s = get_session(body["session_id"])
            s = _ensure_full_session_before_mutation(body["session_id"], s)
        except KeyError:
            return bad(handler, "Session not found", 404)
        pin_requested = bool(body.get("pinned", True))
        # TOCTOU guard (Opus stage-389): the count check and the pin write
        # must happen under the same lock, otherwise two parallel pin
        # requests can both pass `len(pinned_ids) >= 3` against the same
        # snapshot and both succeed, leaving the user with 4 pins. The check
        # must be careful not to nest `all_sessions()` (which acquires LOCK
        # internally) inside a `with LOCK:` block — that's a deadlock since
        # LOCK is a non-reentrant `threading.Lock`. We snapshot the
        # persisted index outside the lock, then re-check the in-memory
        # mutation set inside the lock and commit the pin atomically.
        if pin_requested and not getattr(s, "pinned", False):
            # Pre-snapshot from persisted index (acquires LOCK internally,
            # so must run outside our own LOCK acquire below).
            persisted_rows = [
                existing for existing in all_sessions()
                if _session_counts_toward_pin_quota(existing)
            ]
            with LOCK:
                # Final authoritative count: merge persisted pinned rows with the
                # in-memory SESSIONS snapshot. Count logical sidebar-visible pin
                # lineages rather than raw session rows so continuation siblings
                # in the same visible lineage do not consume extra pin quota.
                candidate_rows = list(persisted_rows)
                candidate_rows.extend(
                    existing.compact() for existing in SESSIONS.values()
                    if _session_counts_toward_pin_quota(existing)
                )
                target_row = s.compact()
                candidate_rows.append(target_row)
                pinned_lineage_ids = _visible_pinned_lineage_ids(candidate_rows)
                target_lineage = _session_row_lineage_root_id(
                    target_row,
                    {
                        str(_session_field(row, "session_id", "") or ""): row
                        for row in candidate_rows
                        if _session_field(row, "session_id", None)
                    },
                )
                pinned_lineage_ids.discard(target_lineage)
                pinned_sessions_limit = int(load_settings().get("pinned_sessions_limit", 3) or 3)
                if len(pinned_lineage_ids) >= pinned_sessions_limit:
                    return bad(handler, f"Up to {pinned_sessions_limit} sessions can be pinned. Unpin one before pinning another.", 400)
                # Mark in-memory pin state under LOCK so concurrent pin
                # requests see the increment immediately, even before
                # save() finishes flushing to disk.
                s.pinned = True
            with _get_session_agent_lock(body["session_id"]):
                s.save()
        else:
            with _get_session_agent_lock(body["session_id"]):
                s.pinned = pin_requested
                s.save()
        publish_session_list_changed(
            "session_pin",
            profile=getattr(s, "profile", None),
            session_id=getattr(s, "session_id", body["session_id"]),
        )
        return j(handler, {"ok": True, "session": s.compact()})

    # ── Session archive (POST) ──
    if parsed.path == "/api/session/archive":
        try:
            require(body, "session_id")
        except ValueError as e:
            return bad(handler, str(e))
        sid = body["session_id"]
        if _session_is_subagent_view_only(sid):
            return bad(handler, "Subagent sessions are view-only and cannot be archived from WebUI", 400)
        try:
            s = get_session(sid)
            # #1558: save() refuses metadata-only session stubs because their
            # messages list is intentionally empty. If a sidebar/status preload
            # left one in the LRU cache, upgrade to a full disk load before
            # mutating archived state so the guard stays intact.
            if getattr(s, "_loaded_metadata_only", False):
                s = Session.load(sid)
                if s is None:
                    raise KeyError(sid)
                with LOCK:
                    SESSIONS[sid] = s
        except KeyError:
            cli_meta = _lookup_cli_session_metadata(sid)
            if not cli_meta:
                return bad(handler, "Session not found", 404)
            if cli_meta.get("read_only"):
                return bad(handler, "Read-only imported sessions cannot be archived from WebUI", 400)
            # Delegated subagent children (#5307) are view-only and owned by the
            # delegate runner — never materialize one into a writable WebUI
            # sidecar via the archive fallback (the 3rd of the shared
            # import_cli_session write paths).
            _arch_source_tag = (cli_meta.get("source_tag") or cli_meta.get("raw_source") or "").strip().lower()
            if _arch_source_tag == "subagent" or _is_subagent_child_session_id(sid):
                return bad(handler, "Subagent sessions cannot be archived from WebUI", 400)
            if _is_messaging_session_record(cli_meta):
                s = Session(
                    session_id=sid,
                    title=cli_meta.get("title") or title_from(get_cli_session_messages(sid), "CLI Session"),
                    workspace=get_last_workspace(),
                    messages=[],
                    model=cli_meta.get("model") or "unknown",
                    created_at=cli_meta.get("created_at"),
                    updated_at=cli_meta.get("updated_at"),
                )
                s.is_cli_session = is_cli_session_row(cli_meta)
                s.source_tag = cli_meta.get("source_tag")
                s.raw_source = cli_meta.get("raw_source") or cli_meta.get("source_tag")
                s.session_source = cli_meta.get("session_source")
                s.source_label = cli_meta.get("source_label")
                s.user_id = cli_meta.get("user_id")
                s.chat_id = cli_meta.get("chat_id")
                s.chat_type = cli_meta.get("chat_type")
                s.thread_id = cli_meta.get("thread_id")
                s.session_key = cli_meta.get("session_key")
                s.platform = cli_meta.get("platform")
                s.save(touch_updated_at=False)
            else:
                msgs = get_cli_session_messages(sid)
                if not msgs:
                    return bad(handler, "Session not found", 404)
                s = import_cli_session(
                    sid,
                    cli_meta.get("title") or title_from(msgs, "CLI Session"),
                    msgs,
                    cli_meta.get("model") or "unknown",
                    profile=cli_meta.get("profile"),
                    created_at=cli_meta.get("created_at"),
                    updated_at=cli_meta.get("updated_at"),
                )
                s.is_cli_session = is_cli_session_row(cli_meta)
                s.source_tag = cli_meta.get("source_tag")
                s.raw_source = cli_meta.get("raw_source") or cli_meta.get("source_tag")
                s.session_source = cli_meta.get("session_source")
                s.source_label = cli_meta.get("source_label")
                s.user_id = cli_meta.get("user_id")
                s.chat_id = cli_meta.get("chat_id")
                s.chat_type = cli_meta.get("chat_type")
                s.thread_id = cli_meta.get("thread_id")
                s.session_key = cli_meta.get("session_key")
                s.platform = cli_meta.get("platform")
        with _get_session_agent_lock(sid):
            s.archived = bool(body.get("archived", True))
            s.save(touch_updated_at=False)
        publish_session_list_changed(
            "session_archive",
            profile=getattr(s, "profile", None),
            session_id=getattr(s, "session_id", sid),
        )
        return j(handler, {"ok": True, "session": s.compact(), **_worktree_retained_payload(s)})

    # ── Session move to project (POST) ──
    if parsed.path == "/api/session/move":
        try:
            require(body, "session_id")
        except ValueError as e:
            return bad(handler, str(e))
        try:
            s = _get_or_materialize_session(body["session_id"])
        except KeyError:
            return bad(handler, "Session not found", 404)
        except PermissionError:
            return bad(handler, "Read-only imported sessions cannot be moved from WebUI", 403)
        # #1614: refuse moves into a project owned by another profile.
        target_pid = body.get("project_id") or None
        if target_pid:
            # Use the session's own profile for authorization, not the global
            # active profile. A session belongs to a specific profile set at
            # creation; projects from that profile should always be assignable,
            # regardless of which profile is "active" at the process level.
            # Matches the same principle as the profile chip fix — prefer
            # session-scoped state over global active profile. (#3325 follow-up)
            _session_profile = getattr(s, 'profile', None) or get_active_profile_name()
            target = next(
                (p for p in load_projects() if p["project_id"] == target_pid),
                None,
            )
            if not target:
                return bad(handler, "Project not found", 404)
            if not _profiles_match(target.get("profile"), _session_profile):
                return bad(handler, "Project not found", 404)
        # #3746: acquire the per-session agent lock with a bounded timeout
        # instead of blocking indefinitely. The streaming thread holds this same
        # lock during checkpoint saves; on slow file I/O (e.g. WSL/DrvFs) a bare
        # blocking acquire could outlast the client's 30s abort and surface as a
        # silent "Request timed out" toast with no server-side signal. Bounding
        # the wait converts that into an actionable HTTP 503 the client can retry.
        # We keep the lock (rather than dropping it for this metadata-only write)
        # because s.save() still races the streaming thread's atomic writer.
        _move_lock = _get_session_agent_lock(body["session_id"])
        if not _move_lock.acquire(timeout=5):
            return j(
                handler,
                {"error": "Session is busy (streaming). Please try again in a moment."},
                status=503,
            )
        try:
            s.project_id = target_pid
            if target_pid and body.get("sync_workspace"):
                target_p = next((p for p in load_projects() if p["project_id"] == target_pid), None)
                if target_p and target_p.get("default_workspace"):
                    try:
                        s.workspace = str(resolve_trusted_workspace(target_p["default_workspace"]))
                    except Exception:
                        logger.warning("Silent exception in handle_post", exc_info=True)
                        s.workspace = target_p["default_workspace"]
            s.save()
        finally:
            _move_lock.release()
        publish_session_list_changed(
            "session_move",
            profile=getattr(s, "profile", None),
            session_id=getattr(s, "session_id", body["session_id"]),
        )
        return j(handler, {"ok": True, "session": s.compact()})

    # ── Project CRUD (POST) ──
    if parsed.path == "/api/session/import":
        return _handle_session_import(handler, body)

    # ── Session export to workspace (POST) ──
    if parsed.path == "/api/session/export/workspace":
        return _handle_session_export_workspace(handler, body)

    # ── Session import from workspace (POST) ──
    if parsed.path == "/api/session/import/workspace":
        return _handle_session_import_workspace(handler, body)

    # ── Self-update (POST) ──
    if parsed.path == "/api/session/import_cli":
        return _handle_session_import_cli(handler, body)

    # ── Auth endpoints (POST) ──
    return None



# ── Manual Compression Jobs State (Sprint M6.1) ──────────────────────────────
_MANUAL_COMPRESSION_JOBS: dict[str, dict] = {}
_MANUAL_COMPRESSION_JOBS_LOCK = threading.Lock()
_MANUAL_COMPRESSION_JOB_TTL_SECONDS = 10 * 60


# ── Session Compression & Handoff Summary (Sprint M6.1) ──────────────────────
class _ManualCompressionMemoryHandler:
    def __init__(self):
        self.wfile = io.BytesIO()
        self.status = None
        self.sent_headers = {}

    def send_response(self, status):
        self.status = status

    def send_header(self, key, value):
        self.sent_headers[key] = value

    def end_headers(self):
        pass

    def payload(self):
        raw = self.wfile.getvalue().decode("utf-8")
        return json.loads(raw) if raw else {}


def _manual_compression_cleanup_locked(now=None):
    now = time.time() if now is None else now
    for sid, job in list(_MANUAL_COMPRESSION_JOBS.items()):
        if job.get("status") == "running":
            continue
        updated_at = float(job.get("updated_at") or job.get("started_at") or now)
        if now - updated_at > _MANUAL_COMPRESSION_JOB_TTL_SECONDS:
            _MANUAL_COMPRESSION_JOBS.pop(sid, None)


def _manual_compression_status_payload(job):
    status = job.get("status") or "running"
    payload = {
        "ok": status not in {"error", "cancelled"},
        "status": status,
        "session_id": job.get("session_id"),
        "focus_topic": job.get("focus_topic"),
        "started_at": job.get("started_at"),
        "updated_at": job.get("updated_at"),
    }
    if status == "done":
        result = job.get("result")
        if isinstance(result, dict):
            payload.update(result)
        payload["status"] = "done"
        payload["ok"] = True
    elif status == "error":
        payload["ok"] = False
        payload["error"] = job.get("error") or "Compression failed"
        payload["error_status"] = int(job.get("error_status") or 400)
        if job.get("error_type"):
            payload["type"] = job["error_type"]
        if job.get("retryable") is not None:
            payload["retryable"] = bool(job["retryable"])
    elif status == "cancelled":
        payload["ok"] = False
        payload["error"] = job.get("error") or "Compression cancelled"
        payload["error_status"] = int(job.get("error_status") or 409)
    return payload


def _run_manual_compression_job(sid, body):
    memory_handler = _ManualCompressionMemoryHandler()
    try:
        try:
            session = get_session(sid)
        except KeyError:
            session = None
        if session is not None:
            from api import profiles as profiles_api

            with profiles_api.profile_env_for_background_worker(session, "manual compression", logger_override=logger):
                _handle_session_compress(memory_handler, body)
        else:
            _handle_session_compress(memory_handler, body)
        status = int(memory_handler.status or 500)
        payload = memory_handler.payload()
        with _MANUAL_COMPRESSION_JOBS_LOCK:
            job = _MANUAL_COMPRESSION_JOBS.get(sid)
            if not job:
                return
            now = time.time()
            if status >= 400 or not isinstance(payload, dict) or payload.get("error"):
                job.update(
                    {
                        "status": "error",
                        "error": str((payload or {}).get("error") or "Compression failed"),
                        "error_status": status,
                        "error_type": (payload or {}).get("type"),
                        "retryable": (payload or {}).get("retryable"),
                        "updated_at": now,
                    }
                )
            else:
                job.update(
                    {
                        "status": "done",
                        "result": payload,
                        "updated_at": now,
                    }
                )
    except AgentRuntimeChangedError as exc:
        logger.warning("Manual compression worker found stale Agent runtime for session %s", sid)
        with _MANUAL_COMPRESSION_JOBS_LOCK:
            job = _MANUAL_COMPRESSION_JOBS.get(sid)
            if job:
                job.update(
                    {
                        "status": "error",
                        "error": str(exc),
                        "error_status": 409,
                        "error_type": "agent_runtime_stale",
                        "retryable": True,
                        "updated_at": time.time(),
                    }
                )
    except Exception as exc:
        logger.warning("Manual compression worker failed for session %s: %s", sid, exc)
        with _MANUAL_COMPRESSION_JOBS_LOCK:
            job = _MANUAL_COMPRESSION_JOBS.get(sid)
            if job:
                job.update(
                    {
                        "status": "error",
                        "error": f"Compression failed: {_sanitize_error(exc)}",
                        "error_status": 500,
                        "updated_at": time.time(),
                    }
                )


def _handle_session_compress_start(handler, body):
    try:
        require(body, "session_id")
    except ValueError as e:
        return bad(handler, str(e))

    sid = str(body.get("session_id") or "").strip()
    if not sid:
        return bad(handler, "session_id is required")
    try:
        s = get_session(sid)
    except KeyError:
        return bad(handler, "Session not found", 404)
    if getattr(s, "active_stream_id", None):
        return bad(handler, "Session is still streaming; wait for the current turn to finish.", 409)

    focus_topic = str(body.get("focus_topic") or body.get("topic") or "").strip()[:500] or None
    job_body = {"session_id": sid}
    if focus_topic:
        job_body["focus_topic"] = focus_topic

    # Repeated start requests observe an existing running job and do not admit
    # new Agent work, so preserve that idempotent read even if the checkout has
    # since changed. Do not hold the job lock while running Git subprocesses.
    now = time.time()
    with _MANUAL_COMPRESSION_JOBS_LOCK:
        _manual_compression_cleanup_locked(now)
        existing = _MANUAL_COMPRESSION_JOBS.get(sid)
        if existing:
            existing_payload = _manual_compression_status_payload(existing)
            if existing_payload.get("status") == "running":
                return j(handler, existing_payload)

    # Reject a stale local Agent runtime before creating the asynchronous job.
    try:
        ensure_agent_runtime_current()
    except AgentRuntimeChangedError as exc:
        return j(
            handler,
            {
                "error": str(exc),
                "type": "agent_runtime_stale",
                "retryable": True,
            },
            status=409,
        )

    # Another start request may have admitted a job while the runtime check ran.
    # Re-check under the lock so only one worker is created.
    now = time.time()
    with _MANUAL_COMPRESSION_JOBS_LOCK:
        _manual_compression_cleanup_locked(now)
        existing = _MANUAL_COMPRESSION_JOBS.get(sid)
        if existing:
            existing_payload = _manual_compression_status_payload(existing)
            if existing_payload.get("status") == "running":
                return j(handler, existing_payload)
            # Stage-344 Opus SHOULD-FIX (#2128): always start fresh on re-invoke.
            # The prior implementation short-circuited and returned a stale `done`
            # payload for the full 10-minute TTL window when /compress/start was
            # re-invoked, so a user closing the tab mid-compress and re-running
            # /compress on a fresh open would get the previous result back rather
            # than a new compression. Drop the entry and fall through to the
            # fresh-worker path below.
            _MANUAL_COMPRESSION_JOBS.pop(sid, None)
        job = {
            "session_id": sid,
            "focus_topic": focus_topic,
            "status": "running",
            "started_at": now,
            "updated_at": now,
        }
        _MANUAL_COMPRESSION_JOBS[sid] = job

    worker = threading.Thread(
        target=_run_manual_compression_job,
        args=(sid, job_body),
        name=f"manual-compress-{sid[:8]}",
        daemon=True,
    )
    worker.start()

    with _MANUAL_COMPRESSION_JOBS_LOCK:
        return j(handler, _manual_compression_status_payload(_MANUAL_COMPRESSION_JOBS.get(sid, job)))


def _handle_session_compress_status(handler, sid):
    sid = str(sid or "").strip()
    if not sid:
        return bad(handler, "session_id is required")
    with _MANUAL_COMPRESSION_JOBS_LOCK:
        _manual_compression_cleanup_locked()
        job = _MANUAL_COMPRESSION_JOBS.get(sid)
        if not job:
            return j(handler, {"ok": True, "status": "idle", "session_id": sid})
        payload = _manual_compression_status_payload(job)
        # Stage-344 Opus SHOULD-FIX (#2128): do not pop the job on first
        # read of a `done` payload. The session may be open in multiple
        # tabs, and the first tab's poll would otherwise leave the second
        # tab with `idle` and a "Compression job is no longer available"
        # toast. Let the 10-minute TTL handle eviction so all open tabs
        # see the same terminal payload.
        return j(handler, payload)


def _handle_session_compress(handler, body):
    from api.routes import _session_is_subagent_view_only
    def _anchor_message_key(m):
        if not isinstance(m, dict):
            return None
        role = str(m.get("role") or "")
        if not role or role == "tool":
            return None
        content = m.get("content", "")
        if isinstance(content, list):
            text = "\n".join(
                str(p.get("text") or p.get("content") or "")
                for p in content
                if isinstance(p, dict) and p.get("type") == "text"
            )
        else:
            text = str(content or "")
        norm = " ".join(text.split()).strip()[:160]
        ts = m.get("_ts") or m.get("timestamp")
        attachments = m.get("attachments")
        attach_count = len(attachments) if isinstance(attachments, list) else 0
        if not norm and not attach_count and not ts:
            return None
        return {"role": role, "ts": ts, "text": norm, "attachments": attach_count}

    def _compression_summary_from_messages(messages):
        text = None
        for m in reversed(messages or []):
            if not isinstance(m, dict):
                continue
            role = str(m.get("role") or "").lower()
            if role != "assistant":
                continue
            if not isinstance(m.get("content"), str):
                continue
            content = str(m.get("content") or "").strip()
            if not content:
                continue
            norm = re.sub(r"\s+", " ", content).strip()
            if (
                "context compaction" in norm.lower()
                or "context compression" in norm.lower()
            ):
                return norm
        return None

    def _compact_summary_text(raw_text):
        if not isinstance(raw_text, str):
            return None
        txt = raw_text.strip()
        if not txt:
            return None
        return re.sub(r"\s+", " ", txt)

    try:
        require(body, "session_id")
    except ValueError as e:
        return bad(handler, str(e))

    sid = str(body.get("session_id") or "").strip()
    if not sid:
        return bad(handler, "session_id is required")
    if _session_is_subagent_view_only(sid):
        return bad(handler, "Subagent sessions are view-only and cannot be compressed from WebUI", 400)

    # Cap focus_topic to 500 chars — matches the defensive input-size pattern
    # used elsewhere (session title :80, first-exchange snippets :500) and
    # prevents a user from forwarding an unbounded string into the compressor
    # prompt path. No privilege boundary here (user prompting themself), just
    # cheap bound-checking.
    focus_topic = str(body.get("focus_topic") or body.get("topic") or "").strip()[:500] or None

    try:
        s = get_session(sid)
    except KeyError:
        return bad(handler, "Session not found", 404)

    if getattr(s, "active_stream_id", None):
        return bad(handler, "Session is still streaming; wait for the current turn to finish.", 409)

    try:
        from api.streaming import _sanitize_messages_for_api

        messages = _sanitize_messages_for_api(s.messages)
        if len(messages) < 4:
            return bad(handler, "Not enough conversation to compress (need at least 4 messages).")

        def _fallback_estimate_messages_tokens_rough(msgs):
            """Fallback heuristic token estimate when runtime metadata helpers are absent.

            Uses whitespace token-like word counting only. This intentionally
            over/under-estimates BPE token counts (roughly around x3/x4 scale),
            and is only for resilient fallback behavior.
            """
            total = 0
            for m in msgs or []:
                if not isinstance(m, dict):
                    continue
                content = m.get("content", "")
                if isinstance(content, list):
                    content_text = "\n".join(
                        str(p.get("text") or p.get("content") or "")
                        for p in content
                        if isinstance(p, dict)
                    )
                else:
                    content_text = str(content or "")
                total += len(content_text.split())
            return max(1, total)

        def _fallback_summarize_manual_compression(original_messages, compressed_messages, before_tokens, after_tokens, focus_topic=None):
            """Lightweight fallback summary to keep /session/compress usable in tests/runtime."""
            after_tokens = after_tokens if after_tokens is not None else _fallback_estimate_messages_tokens_rough(compressed_messages)
            headline = f"Compressed: {len(original_messages)} \u2192 {len(compressed_messages)} messages"
            summary = {
                "headline": headline,
                "token_line": f"Rough transcript estimate: ~{before_tokens} \u2192 ~{after_tokens} tokens",
                "note": f"Focus: {focus_topic}" if focus_topic else None,
            }
            summary["reference_message"] = (
                f"[CONTEXT COMPACTION \u2014 REFERENCE ONLY] {headline}\n"
                f"{summary['token_line']}\n"
                + (summary["note"] + "\n" if summary.get("note") else "")
                + "Compression completed."
            )
            return summary

        def _estimate_messages_tokens_rough(msgs):
            try:
                from agent.model_metadata import estimate_messages_tokens_rough

                return estimate_messages_tokens_rough(msgs)
            except Exception:
                logger.debug("Silent exception in _estimate_messages_tokens_rough", exc_info=True)
                return _fallback_estimate_messages_tokens_rough(msgs)

        def _summarize_manual_compression(
            original_messages,
            compressed_messages,
            before_tokens,
            after_tokens,
            focus_topic=None,
        ):
            try:
                from agent.manual_compression_feedback import summarize_manual_compression

                return summarize_manual_compression(
                    original_messages,
                    compressed_messages,
                    before_tokens,
                    after_tokens,
                )
            except Exception:
                logger.debug("Silent exception in _summarize_manual_compression", exc_info=True)
                return _fallback_summarize_manual_compression(
                    original_messages,
                    compressed_messages,
                    before_tokens,
                    after_tokens,
                    focus_topic,
                )

        ensure_agent_runtime_current()
        import api.config as _cfg
        from api.oauth import resolve_runtime_provider_with_anthropic_env_lock
        AIAgent = require_ai_agent_class()

        resolved_model, resolved_provider, resolved_base_url = _cfg.resolve_model_provider(
            _cfg.model_with_provider_context(s.model, getattr(s, "model_provider", None))
        )

        resolved_api_key = None
        try:
            import hermes_cli.runtime_provider as _runtime_provider
            _rt = resolve_runtime_provider_with_anthropic_env_lock(
                _runtime_provider.resolve_runtime_provider,
                requested=resolved_provider,
            )
            resolved_api_key = _rt.get("api_key")
            if not resolved_provider:
                resolved_provider = _rt.get("provider")
            if not resolved_base_url:
                resolved_base_url = _rt.get("base_url")
        except Exception as _e:
            logger.warning("resolve_runtime_provider failed for compression: %s", _e)

        if isinstance(resolved_provider, str) and resolved_provider.startswith("custom:"):
            _cp_key, _cp_base = _cfg.resolve_custom_provider_connection(resolved_provider)
            if not resolved_api_key and _cp_key:
                resolved_api_key = _cp_key
            if not resolved_base_url and _cp_base:
                resolved_base_url = _cp_base

        if not resolved_api_key:
            return bad(handler, "No provider configured -- cannot compress.")

        # Compute compression *outside* the lock — the LLM round-trip can take
        # many seconds and we must not block cancel_stream or other writers.
        # Lock contract: hold for the in-memory mutation only, never across
        # network I/O.
        original_messages = list(messages)
        original_stream_state = (
            getattr(s, "active_stream_id", None),
            getattr(s, "pending_user_message", None),
            copy.deepcopy(getattr(s, "pending_attachments", None)),
            getattr(s, "pending_started_at", None),
        )
        approx_tokens = _estimate_messages_tokens_rough(original_messages)

        agent = AIAgent(
            model=resolved_model,
            provider=resolved_provider,
            base_url=resolved_base_url,
            api_key=resolved_api_key,
            # Identify browser-originated sessions as WebUI so Hermes Agent
            # does not inject CLI-specific terminal/output guidance.
            platform="webui",
            quiet_mode=True,
            enabled_toolsets=_resolve_cli_toolsets(),
            session_id=sid,
        )
        compressed = agent.context_compressor.compress(
            original_messages,
            current_tokens=approx_tokens,
            focus_topic=focus_topic,
        )
        new_tokens = _estimate_messages_tokens_rough(compressed)
        summary = _summarize_manual_compression(
            original_messages,
            compressed,
            approx_tokens,
            new_tokens,
            focus_topic=focus_topic,
        )

        with _cfg._get_session_agent_lock(sid):
            # Re-read messages to detect concurrent edits during the LLM call.
            # If the history changed, the compression result is stale — abort.
            current_stream_state = (
                getattr(s, "active_stream_id", None),
                getattr(s, "pending_user_message", None),
                copy.deepcopy(getattr(s, "pending_attachments", None)),
                getattr(s, "pending_started_at", None),
            )
            if current_stream_state != original_stream_state:
                return bad(handler, "Session stream state changed during compression; please retry.", 409)
            if _sanitize_messages_for_api(s.messages) != original_messages:
                return bad(handler, "Session was modified during compression; please retry.", 409)

            from api.session_ops import _truncation_watermark_for
            from api.streaming import _stamp_missing_message_timestamps

            compressed_copy = copy.deepcopy(compressed)
            _stamp_missing_message_timestamps(compressed_copy)
            s.context_messages = compressed_copy
            s.active_stream_id = None
            s.pending_user_message = None
            s.pending_attachments = []
            s.pending_started_at = None
            s.pending_user_source = None
            visible_after = visible_messages_for_anchor(s.messages, auto_compression=False)
            s.compression_anchor_visible_idx = max(0, len(visible_after) - 1) if visible_after else None
            s.compression_anchor_message_key = _anchor_message_key(visible_after[-1]) if visible_after else None
            summary_text = None
            if isinstance(summary, dict):
                summary_text = summary.get("reference_message") or summary.get("token_line") or summary.get("headline")
            s.compression_anchor_summary = _compact_summary_text(
                summary_text or _compression_summary_from_messages(compressed) or ""
            )
            # Persist an intentional-shrink boundary so append-only state.db
            # reconciliation does not replay pre-compression rows (#4836).
            compress_watermark = _truncation_watermark_for(compressed_copy)
            s.truncation_watermark = compress_watermark
            s.truncation_boundary = compress_watermark
            s.compression_anchor_mode = "manual"
            s.last_prompt_tokens = new_tokens
            s.save()
            # Drop stale backups that would undo an intentional manual compress.
            try:
                s.path.with_suffix(".json.bak").unlink(missing_ok=True)
            except OSError:
                pass

        session_payload = redact_session_data(
            s.compact() | {
                "messages": s.messages,
                "tool_calls": s.tool_calls,
                "active_stream_id": s.active_stream_id,
                "pending_user_message": s.pending_user_message,
                "pending_attachments": s.pending_attachments,
                "pending_started_at": s.pending_started_at,
                "compression_anchor_visible_idx": getattr(s, "compression_anchor_visible_idx", None),
                "compression_anchor_message_key": getattr(s, "compression_anchor_message_key", None),
            }
        )
        return j(
            handler,
            {
                "ok": True,
                "session": session_payload,
                "summary": summary,
                "focus_topic": focus_topic,
            },
        )
    except AgentRuntimeChangedError as e:
        return j(handler, {
            "error": str(e),
            "type": "agent_runtime_stale",
            "retryable": True,
        }, status=409)
    except Exception as e:
        logger.warning("Manual session compression failed: %s", e)
        return bad(handler, f"Compression failed: {_sanitize_error(e)}")


def _handle_conversation_rounds(handler, body):
    """Return conversation-round count for a gateway session.

    Request body::

        { "session_id": "...", "since": <unix_ts_or_iso> }

    Response::

        { "ok": true, "rounds": 12, "threshold": 10, "should_show": true }
    """
    try:
        require(body, "session_id")
    except ValueError as e:
        return bad(handler, str(e))

    sid = str(body.get("session_id") or "").strip()
    if not sid:
        return bad(handler, "session_id is required")

    since = body.get("since")
    if since is not None:
        try:
            since = float(since)
        except (TypeError, ValueError):
            return bad(handler, "since must be a unix timestamp (number)")

    from api.models import count_conversation_rounds, CONVERSATION_ROUND_THRESHOLD

    rounds = count_conversation_rounds(sid, since=since)
    return j(handler, {
        "ok": True,
        "rounds": rounds,
        "threshold": CONVERSATION_ROUND_THRESHOLD,
        "should_show": rounds >= CONVERSATION_ROUND_THRESHOLD,
    })


def _build_handoff_summary_tool_message(
    sid: str,
    summary: str,
    channel: str | None,
    rounds: int | None = None,
    fallback: bool = False,
) -> dict:
    """Build a compact tool-role transcript marker for persistence."""
    now = time.time()
    return {
        "role": "tool",
        # Keep this intentionally empty so API-history sanitization drops it from
        # model context (it is display-only data).
        "tool_call_id": "",
        "name": "handoff_summary",
        "timestamp": now,
        "_ts": now,
        "content": json.dumps({
            "_handoff_summary_card": True,
            "session_id": sid,
            "summary": str(summary or "").strip(),
            "channel": (str(channel or "").strip() or None),
            "rounds": rounds,
            "fallback": bool(fallback),
            "generated_at": now,
        }, ensure_ascii=False),
    }


def _extract_handoff_summary_payload(message: dict) -> dict | None:
    """Return a normalized handoff-summary payload if *message* is a tool marker."""
    if not isinstance(message, dict):
        return None
    if message.get("role") != "tool" or message.get("name") != "handoff_summary":
        return None

    content = message.get("content")
    if isinstance(content, dict):
        payload = content
    else:
        try:
            payload = json.loads(content or "")
        except Exception:
            logger.debug("Silent exception in _extract_handoff_summary_payload", exc_info=True)
            return None

    if not isinstance(payload, dict) or not payload.get("_handoff_summary_card"):
        return None
    if payload.get("session_id") is None:
        return None
    return {
        "session_id": str(payload.get("session_id")),
        "summary": str(payload.get("summary", "")),
        "channel": payload.get("channel"),
        "rounds": payload.get("rounds"),
        "fallback": bool(payload.get("fallback")),
        "_handoff_summary_card": True,
    }


def _is_matching_handoff_summary_message(existing: dict, target: dict) -> bool:
    """Return True when two message payloads represent the same handoff summary."""
    existing_payload = _extract_handoff_summary_payload(existing)
    target_payload = _extract_handoff_summary_payload(target)
    if not existing_payload or not target_payload:
        return False
    return (
        existing_payload.get("session_id") == target_payload.get("session_id") and
        existing_payload.get("summary") == target_payload.get("summary") and
        existing_payload.get("channel") == target_payload.get("channel") and
        existing_payload.get("rounds") == target_payload.get("rounds") and
        existing_payload.get("fallback") == target_payload.get("fallback") and
        existing_payload.get("_handoff_summary_card") == target_payload.get("_handoff_summary_card")
    )


def _is_matching_handoff_summary_content(content: object, target_payload: dict | None) -> bool:
    """Return True if DB content JSON matches an expected handoff summary payload."""
    if target_payload is None:
        return False
    try:
        payload = json.loads(content or "")
    except Exception:
        logger.debug("Silent exception in _is_matching_handoff_summary_content", exc_info=True)
        return False
    if not isinstance(payload, dict):
        return False
    if payload.get("session_id") is None:
        return False
    return (
        payload.get("_handoff_summary_card") is True and
        str(payload.get("session_id")) == str(target_payload.get("session_id")) and
        str(payload.get("summary", "")) == str(target_payload.get("summary", "")) and
        payload.get("channel") == target_payload.get("channel") and
        payload.get("rounds") == target_payload.get("rounds") and
        bool(payload.get("fallback")) == bool(target_payload.get("fallback"))
    )


def _persist_handoff_summary_locally(sid: str, message: dict) -> bool:
    """Persist a handoff summary marker into a local WebUI session file."""
    try:
        from api.models import get_session

        s = get_session(sid)
    except KeyError:
        return False

    try:
        if s.messages and _is_matching_handoff_summary_message(s.messages[-1], message):
            return True
        s.messages.append(message)
        s.save()
        return True
    except Exception as e:
        logger.warning("Failed to persist handoff summary marker in local session %s: %s", sid, e)
        return False


def _persist_handoff_summary_to_state_db(sid: str, message: dict) -> bool:
    """Persist a handoff summary marker into CLI sessions state.db.

    This keeps summary cards available after hard-refresh for imported gateway
    sessions that are not in local session JSON yet.
    """
    import os

    try:
        import sqlite3
    except ImportError:
        return False

    try:
        from api.profiles import get_active_agy_home

        hermes_home = Path(get_active_agy_home()).expanduser().resolve()
    except Exception:
        logger.debug("Silent exception in _persist_handoff_summary_to_state_db", exc_info=True)
        hermes_home = Path(os.getenv("HERMES_HOME", str(Path.home() / ".hermes"))).expanduser().resolve()

    db_path = hermes_home / "state.db"
    if not db_path.exists():
        return False

    ts = message.get("timestamp", time.time())
    content = message.get("content", "")
    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False)

    marker_payload = _extract_handoff_summary_payload(message)
    try:
        with webui_session_db.open_db_writable(db_path) as conn:
            try:
                if marker_payload is not None:
                    cur = conn.execute(
                        "SELECT content FROM messages WHERE session_id = ? AND role = 'tool' "
                        "ORDER BY rowid DESC LIMIT 1",
                        (sid,),
                    )
                    row = cur.fetchone()
                    if row is not None and _is_matching_handoff_summary_content(row[0], marker_payload):
                        return True
            except Exception:
                # If tail-read fails, continue with a best-effort write.
                logger.debug("Unable to read tail handoff marker from state.db for %s", sid)

            conn.execute(
                "INSERT INTO messages (session_id, role, content, timestamp) "
                "VALUES (?, 'tool', ?, ?)",
                (sid, content, ts),
            )
            # Keep session row message_count/last-activity aligned with displayed
            # transcript length. session rows are optional in some test DBs, so
            # this update is best-effort.
            conn.execute(
                "UPDATE sessions SET message_count = COALESCE(message_count, 0) + 1 "
                "WHERE id = ?",
                (sid,),
            )
            conn.commit()
        return True
    except Exception as e:
        logger.warning("Failed to persist handoff summary marker in state.db for %s: %s", sid, e)
        return False


def _persist_handoff_summary(sid: str, summary: str, channel: str | None, rounds: int | None, fallback: bool = False) -> dict:
    from api.routes import _is_messaging_session_id
    """Persist a handoff summary marker across local/session backends."""
    marker = _build_handoff_summary_tool_message(sid, summary, channel, rounds, fallback)
    is_messaging_session = _is_messaging_session_id(sid)
    if is_messaging_session:
        _persist_handoff_summary_to_state_db(sid, marker)
        _persist_handoff_summary_locally(sid, marker)
        return marker
    persisted_local = _persist_handoff_summary_locally(sid, marker)
    if persisted_local:
        return marker
    return marker if _persist_handoff_summary_to_state_db(sid, marker) else marker


def _handle_handoff_summary(handler, body):
    from api.routes import _is_messaging_session_id, _session_is_subagent_view_only
    """Generate an on-demand handoff summary for a gateway session.

    Request body::

        { "session_id": "...", "since": <unix_ts_or_iso> }

    Uses the session's configured model to produce a concise summary of
    recent conversation activity.  Returns the summary text so the caller
    can display it in a tool-card.
    """
    try:
        require(body, "session_id")
    except ValueError as e:
        return bad(handler, str(e))

    sid = str(body.get("session_id") or "").strip()
    if not sid:
        return bad(handler, "session_id is required")

    since = body.get("since")
    if since is not None:
        try:
            since = float(since)
        except (TypeError, ValueError):
            return bad(handler, "since must be a unix timestamp (number)")

    from api.models import get_cli_session_messages, count_conversation_rounds, CONVERSATION_ROUND_THRESHOLD

    if _session_is_subagent_view_only(sid):
        return bad(handler, "Subagent sessions are view-only and cannot be summarized from WebUI", 400)
    rounds = count_conversation_rounds(sid, since=since)
    if rounds < CONVERSATION_ROUND_THRESHOLD:
        return bad(handler, "Not enough conversation rounds to generate a summary.", 400)

    # Filter messages by ``since``.
    all_msgs = get_cli_session_messages(sid)
    if since is not None:
        import datetime as _dt
        filtered = []
        for m in all_msgs:
            ts_raw = m.get("timestamp")
            if ts_raw is None:
                continue
            try:
                if isinstance(ts_raw, (int, float)):
                    ts_val = float(ts_raw)
                else:
                    ts_val = _dt.datetime.fromisoformat(
                        str(ts_raw).replace("Z", "+00:00")
                    ).timestamp()
                if ts_val > since:
                    filtered.append(m)
            except Exception:
                logger.warning("Silent exception in _handle_handoff_summary", exc_info=True)
                pass
        msgs = filtered
    else:
        msgs = all_msgs

    # Cap to last 50 messages.
    msgs = msgs[-50:]

    if len(msgs) < 2:
        return bad(handler, "Not enough messages to summarize.", 400)

    def _extract_handoff_text(raw_content):
        if isinstance(raw_content, list):
            return " ".join(
                str(p.get("text") or p.get("content") or "")
                for p in raw_content
                if isinstance(p, dict)
            ).strip()
        return str(raw_content or "").strip()

    def _contains_chinese(text):
        return any("\u4e00" <= ch <= "\u9fff" for ch in str(text))

    transcript_is_chinese = any(
        _contains_chinese(_extract_handoff_text(m.get("content")))
        for m in msgs
    )
    # Build a lightweight conversation transcript for the LLM.
    lines = []
    for m in msgs:
        role = m.get("role", "")
        content = _extract_handoff_text(m.get("content"))
        content = str(content or "").strip()[:1000]
        if role in ("user", "assistant") and content:
            lines.append(content)
    transcript = "\n".join(lines)

    def _fallback_handoff_summary(items):
        """Return a deterministic summary when LLM summary generation is unavailable."""
        user_points = []
        assistant_points = []

        def _summarize_snippet(raw_text, max_len=78):
            text = " ".join(str(raw_text or "").split()).strip()
            if not text:
                return ""
            if len(text) <= max_len:
                return text
            return text[: max_len - 1].rstrip() + "…"

        for m in items:
            role = m.get("role", "")
            content = _summarize_snippet(_extract_handoff_text(m.get("content")), 82)
            if role in ("user", "assistant") and content:
                if role == "user":
                    user_points.append(content)
                else:
                    assistant_points.append(content)
        if not user_points and not assistant_points:
            return (
                "近期可读文本不足，无法生成更完整的交接摘要，请补充一条消息后重试。"
                if transcript_is_chinese
                else "Not enough readable text to create a useful handoff summary; please send one more message and retry."
            )

        if transcript_is_chinese:
            bullets = []
            if user_points:
                bullets.append(f"- 你刚讨论了：{user_points[-1]}。")
            if assistant_points:
                bullets.append(f"- 助手已回复：{assistant_points[-1]}。")
            if len(user_points) + len(assistant_points) >= 2:
                bullets.append("- 当前对话存在尚未确认的后续动作。")
            else:
                bullets.append("- 当前信息偏少，建议补充关键点后再切换。")
            return "\n".join(bullets)

        bullets = []
        if user_points:
            bullets.append(f"- You asked: {user_points[-1]}.")
        if assistant_points:
            bullets.append(f"- The assistant responded: {assistant_points[-1]}.")
        if len(user_points) + len(assistant_points) >= 2:
            bullets.append("- There is pending context to continue next.")
        else:
            bullets.append("- The conversation is still short; add one more turn before summarizing.")
        return "\n".join(bullets)

    def _summary_output_incomplete(text):
        """Best-effort guard for truncated summaries when LLM signals are unavailable."""
        if not isinstance(text, str):
            text = str(text or "")
        text = text.strip()
        if not text:
            return True
        if text.endswith("...") or text.endswith("…"):
            return True
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return True
        last_line = lines[-1]
        if re.search(r"[。！？；!?.；]$", last_line):
            return False
        if len(last_line) >= 56 and not re.search(r"\b(and|or|so|then|because|if|when|but|so|as)\b$", last_line, re.IGNORECASE):
            return True
        return bool(re.search(r"\b(and|or|but|so|because|if|when)$", last_line, re.IGNORECASE))

    def _agent_summary_incomplete(summary_result):
        if not isinstance(summary_result, dict):
            return True
        reason = (summary_result.get("finish_reason") or "").strip().lower()
        if reason == "length":
            return True
        stop_reason = (summary_result.get("stop_reason") or "").strip().lower()
        if stop_reason in {"max_tokens", "length"}:
            return True
        return _summary_output_incomplete(summary_result.get("text", ""))

    def _resolve_handoff_channel_label():
        channel_label = None
        try:
            from api.models import get_session as _get_session, get_cli_sessions

            session_meta = _get_session(sid)
            channel_label = (
                session_meta.source_label
                or session_meta.raw_source
                or session_meta.source_tag
                or session_meta.session_source
            )
            if not channel_label:
                for candidate in get_cli_sessions():
                    if candidate.get("session_id") == sid:
                        channel_label = (
                            candidate.get("source_label")
                            or candidate.get("raw_source")
                            or candidate.get("source_tag")
                            or candidate.get("source")
                        )
                        break
        except Exception:
            logger.debug("Silent exception in _resolve_handoff_channel_label", exc_info=True)
            pass
        return channel_label

    def _agent_text_completion(agent, system_prompt, user_text, max_tokens=700):
        """Use the current Hermes Agent transport without mutating conversation history."""
        api_messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ]
        result = {
            "text": "",
            "finish_reason": None,
            "stop_reason": None,
            "incomplete": True,
        }
        disabled_reasoning = {"enabled": False}
        previous_reasoning = getattr(agent, "reasoning_config", None)
        try:
            agent.reasoning_config = disabled_reasoning
            if getattr(agent, "api_mode", "") == "codex_responses":
                codex_kwargs = agent._build_api_kwargs(api_messages)
                codex_kwargs.pop("tools", None)
                codex_provider = str(getattr(agent, "provider", "") or "").strip().lower()
                codex_base_url = str(getattr(agent, "base_url", "") or "").strip().lower()
                is_chatgpt_codex = (
                    codex_provider == "openai-codex"
                    or (
                        urlsplit(codex_base_url).hostname == "chatgpt.com"
                        and "/backend-api/codex" in codex_base_url
                    )
                )
                if not is_chatgpt_codex:
                    codex_kwargs["max_output_tokens"] = max_tokens
                resp = agent._run_codex_stream(codex_kwargs)
                assistant_message, _ = agent._normalize_codex_response(resp)
                result["text"] = str((assistant_message.content or "") if assistant_message else "").strip()
                result["incomplete"] = _summary_output_incomplete(result["text"])
                return result

            if getattr(agent, "api_mode", "") == "anthropic_messages":
                from agent.anthropic_adapter import build_anthropic_kwargs, normalize_anthropic_response

                ant_kwargs = build_anthropic_kwargs(
                    model=agent.model,
                    messages=api_messages,
                    tools=None,
                    max_tokens=max_tokens,
                    reasoning_config=disabled_reasoning,
                    is_oauth=getattr(agent, "_is_anthropic_oauth", False),
                    preserve_dots=agent._anthropic_preserve_dots(),
                    base_url=getattr(agent, "_anthropic_base_url", None),
                )
                resp = agent._anthropic_messages_create(ant_kwargs)
                assistant_message, _ = normalize_anthropic_response(
                    resp,
                    strip_tool_prefix=getattr(agent, "_is_anthropic_oauth", False),
                )
                result["text"] = str((assistant_message.content or "") if assistant_message else "").strip()
                result["incomplete"] = _summary_output_incomplete(result["text"])
                return result

            api_kwargs = agent._build_api_kwargs(api_messages)
            api_kwargs.pop("tools", None)
            api_kwargs["temperature"] = 0.2
            api_kwargs["timeout"] = 30.0
            if "max_completion_tokens" in api_kwargs:
                api_kwargs["max_completion_tokens"] = max_tokens
            else:
                api_kwargs["max_tokens"] = max_tokens
            resp = agent._ensure_primary_openai_client(reason="handoff_summary").chat.completions.create(
                **api_kwargs,
            )
            choice = (getattr(resp, "choices", None) or [None])[0]
            msg = getattr(choice, "message", None) if choice is not None else None
            result["text"] = str(getattr(msg, "content", "") or "").strip()
            result["finish_reason"] = getattr(choice, "finish_reason", None)
            result["stop_reason"] = getattr(choice, "stop_reason", None)
            result["incomplete"] = _agent_summary_incomplete(result)
            return result
        finally:
            agent.reasoning_config = previous_reasoning

        # Call LLM for summary.
    try:
        ensure_agent_runtime_current()
        import api.config as _cfg
        from api.oauth import resolve_runtime_provider_with_anthropic_env_lock
        AIAgent = require_ai_agent_class()

        # Try to resolve model from an existing session, fall back to default.
        resolved_model = None
        resolved_provider = None
        resolved_base_url = None
        session_model_provider = None
        try:
            from api.models import get_session
            s_obj = get_session(sid)
            resolved_model = getattr(s_obj, "model", None)
            session_model_provider = getattr(s_obj, "model_provider", None)
        except Exception:
            logger.warning("Silent exception in _handle_handoff_summary", exc_info=True)
            pass

        model_for_resolution = _cfg.model_with_provider_context(
            resolved_model, session_model_provider
        )
        resolved_model, resolved_provider, resolved_base_url = _cfg.resolve_model_provider(model_for_resolution)

        resolved_api_key = None
        try:
            import hermes_cli.runtime_provider as _runtime_provider
            _rt = resolve_runtime_provider_with_anthropic_env_lock(
                _runtime_provider.resolve_runtime_provider,
                requested=resolved_provider,
            )
            resolved_api_key = _rt.get("api_key")
            if not resolved_provider:
                resolved_provider = _rt.get("provider")
            if not resolved_base_url:
                resolved_base_url = _rt.get("base_url")
        except Exception as _e:
            logger.warning("resolve_runtime_provider failed for handoff summary: %s", _e)

        if isinstance(resolved_provider, str) and resolved_provider.startswith("custom:"):
            _cp_key, _cp_base = _cfg.resolve_custom_provider_connection(resolved_provider)
            if not resolved_api_key and _cp_key:
                resolved_api_key = _cp_key
            if not resolved_base_url and _cp_base:
                resolved_base_url = _cp_base

        if not resolved_api_key:
            summary_text = _fallback_handoff_summary(msgs)
            try:
                _persist_handoff_summary(
                    sid,
                    summary_text,
                    _resolve_handoff_channel_label(),
                    rounds,
                    fallback=True,
                )
            except Exception:
                logger.warning("Silent exception in _handle_handoff_summary", exc_info=True)
                pass
            return j(handler, {
                "ok": True,
                "summary": summary_text,
                "message_count": len(msgs),
                "rounds": rounds,
                "fallback": True,
            })

        agent = AIAgent(
            model=resolved_model,
            provider=resolved_provider,
            base_url=resolved_base_url,
            api_key=resolved_api_key,
            platform="webui",
            quiet_mode=True,
            enabled_toolsets=[],
            session_id=sid,
        )

        summary_system_prompt = (
            "You are summarizing an external-channel conversation so a Web UI reader "
            "can quickly catch up after switching contexts.\n\n"
            "Only use the latest messages, and never copy raw transcript lines.\n"
            "Do not output role labels (no “你:” / “assistant:” / “user:” / “assistant”).\n"
            "Use direct 2–5 bullet points in the conversation language.\n"
            "English: speak using “you”.\n"
            "中文: 使用“你”。\n\n"
            "Focus on:\n"
            "- Unfinished tasks or action items\n"
            "- Pending questions that need replies\n"
            "- Key decisions made\n"
            "- Open disagreements or TBD items\n\n"
            "If the conversation is purely casual with no actionable items, "
            "say so in one sentence."
        )
        summary_user_text = f"Conversation transcript:\n{transcript}"

        try:
            first_pass = _agent_text_completion(
                agent,
                summary_system_prompt,
                summary_user_text,
                max_tokens=700,
            )
            summary_text = first_pass.get("text") if isinstance(first_pass, dict) else ""
            if _agent_summary_incomplete(first_pass):
                second_pass = _agent_text_completion(
                    agent,
                    summary_system_prompt,
                    summary_user_text,
                    max_tokens=1400,
                )
                summary_text = second_pass.get("text") if isinstance(second_pass, dict) else ""
                if _agent_summary_incomplete(second_pass):
                    summary_text = _fallback_handoff_summary(msgs)
                    fallback = True
                else:
                    fallback = False
            else:
                fallback = False
        finally:
            try:
                agent.release_clients()
            except Exception:
                logger.warning("Silent exception in _handle_handoff_summary", exc_info=True)
                pass
        if not summary_text:
            summary_text = _fallback_handoff_summary(msgs)
            fallback = True
        elif _summary_output_incomplete(summary_text):
            if not fallback:
                fallback = True

        channel_label = _resolve_handoff_channel_label()
        _persist_handoff_summary(
            sid,
            summary_text,
            channel_label,
            rounds,
            fallback=fallback,
        )

        return j(handler, {
            "ok": True,
            "summary": summary_text,
            "message_count": len(msgs),
            "rounds": rounds,
            "fallback": fallback,
        })
    except AgentRuntimeChangedError as e:
        return j(handler, {
            "error": str(e),
            "type": "agent_runtime_stale",
            "retryable": True,
        }, status=409)
    except api_config.AmbiguousCustomProviderError as e:
        # A custom-provider slug collision is a user-fixable misconfiguration,
        # not a transient summary failure. Return 400 with the actionable rename
        # message so the UI shows it, instead of degrading to a 200 local
        # fallback that the client treats as success and that hides the fix.
        logger.warning("Handoff summary blocked by ambiguous custom provider: %s", e.message)
        return j(handler, {
            "error": e.message,
            "type": "custom_provider_ambiguous",
        }, status=400)
    except Exception as e:
        logger.warning("Handoff summary generation failed: %s", e)
        summary_text = _fallback_handoff_summary(msgs)
        try:
            _persist_handoff_summary(
                sid,
                summary_text,
                _resolve_handoff_channel_label(),
                rounds,
                fallback=True,
            )
        except Exception:
            logger.warning("Silent exception in _handle_handoff_summary", exc_info=True)
            pass
        return j(handler, {
            "ok": True,
            "summary": summary_text,
            "message_count": len(msgs),
            "rounds": rounds,
            "fallback": True,
            "warning": f"Summary generation used local fallback: {_sanitize_error(e)}",
        })


# ── Session Export & Search Handlers (Sprint M6.2) ───────────────────────────
def _all_profiles_enabled(parsed):
    from api import routes as _routes
    return _routes._all_profiles_enabled(parsed)


def _all_sessions():
    from api import routes as _routes
    return _routes.all_sessions()


def _publish_session_list_changed(*args, **kwargs):
    from api import routes as _routes
    return _routes._publish_session_list_changed(*args, **kwargs)


def _queue_generated_title_for_imported_session(*args, **kwargs):
    from api import routes as _routes
    return _routes._queue_generated_title_for_imported_session(*args, **kwargs)


def _import_cli_session_call(*args, **kwargs):
    from api import routes as _routes
    return _routes.import_cli_session(*args, **kwargs)


def _redact_sidebar_title_fields(*args, **kwargs):
    from api import routes as _routes
    return _routes._redact_sidebar_title_fields(*args, **kwargs)


def _normalize_import_profile_value(*args, **kwargs):
    from api import routes as _routes
    return _routes._normalize_import_profile_value(*args, **kwargs)


def _request_wants_all_profiles_import(*args, **kwargs):
    from api import routes as _routes
    return _routes._request_wants_all_profiles_import(*args, **kwargs)


def _session_visible_to_active_profile(*args, **kwargs):
    from api import routes as _routes
    return _routes._session_visible_to_active_profile(*args, **kwargs)


def _resolve_cli_import_metadata(*args, **kwargs):
    from api import routes as _routes
    return _routes._resolve_cli_import_metadata(*args, **kwargs)


def _is_subagent_child_session_id(*args, **kwargs):
    from api import routes as _routes
    return _routes._is_subagent_child_session_id(*args, **kwargs)


def _handle_session_export(handler, parsed):
    sid = parse_qs(parsed.query).get("session_id", [""])[0]
    if not sid:
        return bad(handler, "session_id is required")
    try:
        s = get_session(sid)
    except KeyError:
        return bad(handler, "Session not found", 404)
    active_profile = get_active_profile_name()
    if not _profiles_match(getattr(s, "profile", None), active_profile):
        return bad(handler, "Session not found", 404)
    # ``public_session_projection`` supersedes the narrower
    # ``redact_session_data`` path so export context_messages uses the same
    # alias-stripping boundary as the visible transcript.
    safe = public_session_projection(s.__dict__)
    qs = parse_qs(parsed.query)
    fmt = qs.get("format", ["json"])[0].lower()
    if fmt == "html":
        from api.session_export_html import render_session_html
        theme = qs.get("theme", ["dark"])[0].lower()
        palette: dict | None = None
        raw_palette = qs.get("palette", [""])[0]
        if raw_palette:
            try:
                import base64 as _b64
                decoded = _b64.b64decode(raw_palette, validate=False).decode("utf-8")
                parsed_palette = json.loads(decoded)
                if isinstance(parsed_palette, dict):
                    # Cap payload so a hostile client can't blow up the response.
                    if len(parsed_palette) <= 64:
                        palette = parsed_palette
            except Exception:
                logger.warning("Silent exception in _handle_session_export", exc_info=True)
                palette = None
        payload = render_session_html(safe, theme=theme, palette=palette)
        content_type = "text/html; charset=utf-8"
        ext = "html"
    else:
        payload = json.dumps(safe, ensure_ascii=False, indent=2)
        content_type = "application/json; charset=utf-8"
        ext = "json"
    handler.send_response(200)
    handler.send_header("Content-Type", content_type)
    handler.send_header(
        "Content-Disposition", f'attachment; filename="agy-{sid}.{ext}"'
    )
    handler.send_header("Content-Length", str(len(payload.encode("utf-8"))))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(payload.encode("utf-8"))
    return True


def _handle_session_export_workspace(handler, body):
    """Export a session directly to the workspace filesystem."""
    if not body or not isinstance(body, dict):
        return bad(handler, "Request body must be a JSON object")
    sid = body.get("session_id", "")
    if not sid:
        return bad(handler, "session_id is required")
    try:
        s = get_session(sid)
    except KeyError:
        return bad(handler, "Session not found", 404)
    active_profile = get_active_profile_name()
    if not _profiles_match(getattr(s, "profile", None), active_profile):
        return bad(handler, "Session not found", 404)
    safe = public_session_projection(s.__dict__)

    workspace = body.get("workspace") or getattr(s, "workspace", None)
    subfolder = body.get("subfolder", "transcripts")
    fmt = body.get("format", "md")
    filename = body.get("filename")
    content = body.get("content")
    theme = body.get("theme", "dark")
    palette = body.get("palette")
    if not isinstance(palette, dict):
        palette = None

    try:
        from api.session_workspace_export import export_session_to_workspace
        res = export_session_to_workspace(
            session_data=safe,
            workspace_path=workspace,
            subfolder=subfolder,
            format=fmt,
            filename=filename,
            content=content,
            theme=theme,
            palette=palette,
        )
        return j(handler, res)
    except ValueError as e:
        return bad(handler, str(e), 400)
    except Exception as e:
        logger.exception("Failed to export session to workspace")
        return bad(handler, f"Failed to export session: {e}", 500)


def _session_search_message_text(message):
    content = message.get("content") if isinstance(message, dict) else ""
    if isinstance(content, list):
        return " ".join(
            str(part.get("text", ""))
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return str(content or "")


def _session_search_preview(text, query, max_len=124):
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    q = re.sub(r"\s+", " ", str(query or "")).strip()
    if not normalized or not q:
        return ""
    idx = normalized.lower().find(q.lower())
    if idx < 0:
        return ""

    max_len = max(32, int(max_len or 124))
    if len(normalized) <= max_len:
        return normalized

    context = max(12, (max_len - len(q)) // 2)
    start = max(0, idx - context)
    end = min(len(normalized), idx + len(q) + context)
    if start > 0:
        while start < idx and normalized[start] != " ":
            start += 1
        if start >= idx:
            start = max(0, idx - context)
    if end < len(normalized):
        while end > idx + len(q) and normalized[end - 1] != " ":
            end -= 1
        if end <= idx + len(q):
            end = min(len(normalized), idx + len(q) + context)
    excerpt = normalized[start:end].strip()
    if start > 0:
        excerpt = "..." + excerpt
    if end < len(normalized):
        excerpt = excerpt + "..."
    return excerpt


def _handle_sessions_search(handler, parsed):
    qs = parse_qs(parsed.query)
    q = qs.get("q", [""])[0].lower().strip()
    content_search = qs.get("content", ["1"])[0] == "1"
    from api.profiles import get_active_profile_name
    active_profile = get_active_profile_name()
    all_profiles = _all_profiles_enabled(parsed)
    sessions = _all_sessions()
    if not all_profiles:
        sessions = [
            s for s in sessions
            if _profiles_match(s.get("profile"), active_profile)
        ]
    # Reject a malformed depth instead of letting int() raise ValueError and
    # surface as a confusing 500. Clamp to >= 0 so a negative value can't reach
    # the messages[:depth] slice below — messages[:-n] would silently exclude
    # the most recent messages from the content search instead of capping it.
    # (depth == 0 keeps its existing meaning: search the full transcript.)
    try:
        depth = max(0, int(qs.get("depth", ["5"])[0]))
    except (ValueError, TypeError):
        depth = 5
    # Read the redaction setting ONCE for the whole response (mirrors the
    # /api/sessions read-once optimization, #4662) and thread it through every
    # branch + the shared title-field redactor so search rows redact the same
    # fields as the sidebar list.
    try:
        _search_redact_enabled = bool(load_settings().get("api_redact_enabled", True))
    except Exception:
        logger.warning("Silent exception in _handle_sessions_search", exc_info=True)
        _search_redact_enabled = True  # fail safe: redact when settings unreadable
    if not q:
        safe_sessions = []
        for s in sessions:
            item = dict(s)
            if isinstance(item.get("title"), str):
                item["title"] = _redact_text(item["title"], _enabled=_search_redact_enabled)
            _redact_sidebar_title_fields(item, _search_redact_enabled)
            safe_sessions.append(item)
        return j(handler, {
            "sessions": safe_sessions,
            "all_profiles": all_profiles,
            "active_profile": active_profile,
        })
    results = []
    for s in sessions:
        title_match = q in (s.get("title") or "").lower()
        if title_match:
            item = dict(s, match_type="title")
            if isinstance(item.get("title"), str):
                item["title"] = _redact_text(item["title"], _enabled=_search_redact_enabled)
            _redact_sidebar_title_fields(item, _search_redact_enabled)
            results.append(item)
            continue
        if content_search:
            try:
                # Scan accessor, not get_session(): a content search walks every
                # session, and routing that through the LRU would evict the
                # user's working set on every keystroke-debounced search.
                sess = get_session_for_scan(s["session_id"])
                if sess is None:
                    continue
                msgs = sess.messages[:depth] if depth else sess.messages
                for m in msgs:
                    c = _session_search_message_text(m)
                    if q in str(c).lower():
                        item = dict(s, match_type="content")
                        preview = _session_search_preview(c, q)
                        if preview:
                            item["match_preview"] = _redact_text(preview, _enabled=_search_redact_enabled)
                        if isinstance(item.get("title"), str):
                            item["title"] = _redact_text(item["title"], _enabled=_search_redact_enabled)
                        _redact_sidebar_title_fields(item, _search_redact_enabled)
                        results.append(item)
                        break
            except (KeyError, Exception):
                pass
    return j(handler, {
        "sessions": results,
        "query": q,
        "count": len(results),
        "all_profiles": all_profiles,
        "active_profile": active_profile,
    })


# ── Session Import & Refresh Handlers (Sprint M6.2) ──────────────────────────
def _normalize_message_for_import_refresh(message: object) -> object:
    """Normalize message payloads for import refresh prefix checks.

    The strict dict comparison previously failed when existing messages held
    integer timestamps while refreshed messages held floating-point timestamps.
    Strip timing keys before comparison so we can safely treat semantic
    prefixes as equivalent.
    """
    if not isinstance(message, dict):
        return message
    normalized = dict(message)
    normalized.pop("timestamp", None)
    normalized.pop("_ts", None)
    # These are WebUI/Agent replay bookkeeping aliases at the message's top
    # level.  Strip only those exact keys; nested business payloads are opaque
    # and must remain part of the semantic import comparison.
    for key in ("api_content", "_state_db_row_id", "_db_row_id", "state_db_row_id"):
        normalized.pop(key, None)
    return normalized


def _message_has_cli_tool_metadata(message: object) -> bool:
    if not isinstance(message, dict):
        return False
    if message.get("role") == "assistant" and message.get("tool_calls"):
        return True
    if message.get("role") == "tool" and (message.get("tool_call_id") or message.get("tool_name") or message.get("name")):
        return True
    return False


def _strip_cli_tool_metadata_for_refresh(message: object) -> object:
    if not isinstance(message, dict):
        return _normalize_message_for_import_refresh(message)
    normalized = _normalize_message_for_import_refresh(message)
    if not isinstance(normalized, dict):
        return normalized
    for key in ("tool_calls", "tool_call_id", "tool_name", "name"):
        normalized.pop(key, None)
    return normalized


def _is_cli_tool_metadata_enrichment(existing_messages: list, fresh_messages: list) -> bool:
    """Return True when fresh messages only add CLI tool metadata.

    Older imports from get_cli_session_messages() persisted assistant/tool rows
    without tool_calls, tool_call_id, or tool_name. After #1772 the refreshed
    transcript can have the same length but richer metadata, so re-imports must
    rebuild the stored sidecar even without a new row.
    """
    if not isinstance(existing_messages, list) or not isinstance(fresh_messages, list):
        return False
    if len(existing_messages) != len(fresh_messages):
        return False
    if any(_message_has_cli_tool_metadata(m) for m in existing_messages):
        return False
    if not any(_message_has_cli_tool_metadata(m) for m in fresh_messages):
        return False
    for idx, existing_message in enumerate(existing_messages):
        if _strip_cli_tool_metadata_for_refresh(existing_message) != _strip_cli_tool_metadata_for_refresh(fresh_messages[idx]):
            return False
    return True


def _is_messages_refresh_prefix_match(existing_messages: list, fresh_messages: list) -> bool:
    """Return True when existing_messages is a prefix of fresh_messages by value.

    This is a semantic comparison intended for import refresh, not deep
    structural equality. It intentionally ignores timing fields that may differ
    in type/precision between storage layers.
    """
    if not isinstance(existing_messages, list) or not isinstance(fresh_messages, list):
        return False
    if len(existing_messages) > len(fresh_messages):
        return False
    for idx, existing_message in enumerate(existing_messages):
        fresh_message = fresh_messages[idx]
        if _normalize_message_for_import_refresh(existing_message) != _normalize_message_for_import_refresh(fresh_message):
            return False
    return True


def _handle_session_import_cli(handler, body):
    """Import a single CLI session into the WebUI store."""
    try:
        require(body, "session_id")
    except ValueError as e:
        return bad(handler, str(e))

    sid = str(body["session_id"])
    requested_profile = _normalize_import_profile_value((body or {}).get("profile"))
    if requested_profile == "":
        return bad(handler, "invalid profile", 400)
    allow_all_profiles = _request_wants_all_profiles_import(body)
    if allow_all_profiles and _is_isolated_profile_mode():
        return bad(handler, "all_profiles import is not allowed in isolated profile mode", 403)
    if allow_all_profiles and not requested_profile:
        return bad(handler, "profile is required for all_profiles import", 400)

    # Check if already imported — refresh messages from CLI store if new ones arrived
    existing = Session.load(sid)
    if existing:
        # Cross-profile boundary: an unqualified (non-all-profiles) request must not
        # read or refresh a session that belongs to another profile, even though the
        # WebUI session store (SESSION_DIR) is a single global directory. This mirrors
        # the /api/session detail and /api/session/export profile-scoping gates.
        # An explicit all_profiles import is still allowed, but only when the request's
        # profile matches the stored session's profile.
        existing_profile = getattr(existing, "profile", None)
        if allow_all_profiles:
            if requested_profile and not _profiles_match(existing_profile, requested_profile):
                return bad(handler, "Session not found in CLI store", 404)
        elif not _session_visible_to_active_profile(existing_profile, handler):
            return bad(handler, "Session not found in CLI store", 404)
        refresh_profile = requested_profile or existing_profile
        cli_meta = _resolve_cli_import_metadata(
            sid,
            requested_profile=refresh_profile,
            allow_all_profiles=allow_all_profiles,
        )
        fresh_msgs = get_cli_session_messages(
            sid,
            profile=(cli_meta or {}).get("profile") or refresh_profile,
        )
        changed = False
        if fresh_msgs and len(fresh_msgs) > len(existing.messages):
            # Prefix-equality guard: only extend if existing messages are a prefix of
            # the fresh CLI messages. Prevents silently dropping WebUI-added messages
            # on hybrid sessions (user sent messages via WebUI while CLI continued).
            if _is_messages_refresh_prefix_match(existing.messages, fresh_msgs):
                existing.messages = fresh_msgs
                changed = True
        elif fresh_msgs and _is_cli_tool_metadata_enrichment(existing.messages, fresh_msgs):
            # Same row count, richer payload: rebuild sidecars imported before
            # CLI tool metadata was preserved (#1772).
            existing.messages = fresh_msgs
            changed = True
        if cli_meta:
            # A subagent child must never be flipped to CLI-classified /
            # writable on an existing-session refresh either (#5307).
            _existing_is_sa = (
                (existing.source_tag or existing.raw_source or "").strip().lower() == "subagent"
                or (cli_meta.get("source_tag") or cli_meta.get("raw_source") or "").strip().lower() == "subagent"
                or _is_subagent_child_session_id(sid)
            )
            updates = {
                "is_cli_session": (False if _existing_is_sa else True),
                "source_tag": existing.source_tag or cli_meta.get("source_tag"),
                "raw_source": existing.raw_source or cli_meta.get("raw_source") or cli_meta.get("source_tag"),
                "session_source": existing.session_source or cli_meta.get("session_source"),
                "source_label": existing.source_label or cli_meta.get("source_label"),
                "parent_session_id": existing.parent_session_id or cli_meta.get("parent_session_id"),
            }
            # A subagent child is view-only: also coerce read_only=True on the
            # persisted sidecar so a stale writable (pre-fix) sidecar can't be
            # used to start a WebUI turn (#5307).
            if _existing_is_sa:
                updates["read_only"] = True
            for attr, value in updates.items():
                if getattr(existing, attr, None) != value:
                    setattr(existing, attr, value)
                    changed = True
        else:
            _existing_is_sa = (
                (existing.source_tag or existing.raw_source or "").strip().lower() == "subagent"
                or _is_subagent_child_session_id(sid)
            )
        if changed:
            existing.save(touch_updated_at=False)
            publish_session_list_changed(
                "session_import_cli",
                profile=getattr(existing, "profile", None),
            )
        return j(
            handler,
            {
                "session": public_session_projection(
                    existing.compact()
                    | {
                        "messages": existing.messages,
                        "is_cli_session": (False if _existing_is_sa else True),
                        # Greptile #4911 follow-up: read read_only from
                        # the persisted Session, NOT from cli_meta.  This
                        # refresh path is for an already-WebUI-owned
                        # session; the WebUI's persisted view is the
                        # source of truth for the response, not the
                        # foreign store's current value.  (Mirrors the
                        # GET /api/session fix.)
                        "read_only": bool(getattr(existing, "read_only", False)),
                    }
                ),
                "imported": False,
            },
        )

    # Fetch messages from CLI store
    cli_meta = _resolve_cli_import_metadata(
        sid,
        requested_profile=requested_profile,
        allow_all_profiles=allow_all_profiles,
    )
    profile = cli_meta.get("profile") if cli_meta else (requested_profile if allow_all_profiles else None)
    msgs = get_cli_session_messages(sid, profile=profile)
    if not msgs:
        return bad(handler, "Session not found in CLI store", 404)

    # Get profile, model, timestamps, and title from CLI session metadata
    created_at = cli_meta.get("created_at") if cli_meta else None
    updated_at = cli_meta.get("updated_at") if cli_meta else None
    cli_title = cli_meta.get("title") if cli_meta else None
    cli_source_tag = cli_meta.get("source_tag") if cli_meta else None
    model = cli_meta.get("model", "unknown") if cli_meta else "unknown"
    cli_raw_source = cli_meta.get("raw_source") if cli_meta else None
    cli_session_source = cli_meta.get("session_source") if cli_meta else None
    cli_source_label = cli_meta.get("source_label") if cli_meta else None
    cli_user_id = cli_meta.get("user_id") if cli_meta else None
    cli_chat_id = cli_meta.get("chat_id") if cli_meta else None
    cli_chat_type = cli_meta.get("chat_type") if cli_meta else None
    cli_thread_id = cli_meta.get("thread_id") if cli_meta else None
    cli_session_key = cli_meta.get("session_key") if cli_meta else None
    cli_platform = cli_meta.get("platform") if cli_meta else None
    cli_parent_session_id = cli_meta.get("parent_session_id") if cli_meta else None
    cli_read_only = bool((cli_meta or {}).get("read_only"))
    # Delegated subagent children (#5307) are recovered VIEW-ONLY: they must
    # never be materialized as a writable WebUI sidecar via this endpoint, or a
    # subsequent chat-start/composer write would take ownership of a session
    # that belongs to the delegate runner. Treat them like an explicitly
    # read-only source (return the read-only stub payload, do not import), and
    # keep them out of the _isExternalSession frontend gates (is_cli_session=False).
    _sa_child = _is_subagent_child_session_id(sid)
    # Also treat a resolved-metadata subagent source as view-only: with
    # all_profiles=true, cli_meta is resolved from the requested (possibly
    # non-active) profile, so the active-profile state.db check (_sa_child)
    # can miss it (#5307 cross-profile edge).
    _cli_sa = (cli_source_tag or cli_raw_source or "").strip().lower() == "subagent"
    _sa_child = _sa_child or _cli_sa
    _read_only_view = cli_read_only or _sa_child

    # Use the CLI session title if available (e.g., cron job name), otherwise derive from messages
    title = cli_title or title_from(msgs, "CLI Session")

    # Auto-assign cron sessions to the dedicated "Cron Jobs" project (#1079),
    # gated on whether this profile has opted into project organization (#5379)
    cron_project_id = None
    if is_cron_session(sid, cli_source_tag):
        cron_project_id = ensure_cron_project(create=_profile_has_user_projects())

    if _read_only_view:
        session_payload = {
            "session_id": sid,
            "title": title,
            "workspace": str(get_last_workspace()),
            "model": model,
            "message_count": len(msgs),
            "created_at": created_at,
            "updated_at": updated_at,
            "last_message_at": updated_at or created_at,
            "pinned": False,
            "archived": False,
            "project_id": None,
            "profile": profile,
            # Subagent children (#5307) are recovered view-only and must NOT be
            # CLI-classified (keeps them out of the frontend _isExternalSession
            # gates); other explicitly-read-only sources keep is_cli_session=True.
            "is_cli_session": (False if _sa_child else True),
            "source_tag": cli_source_tag,
            "raw_source": cli_raw_source or cli_source_tag,
            "session_source": cli_session_source,
            "source_label": cli_source_label,
            "parent_session_id": cli_parent_session_id,
            "read_only": True,
            "messages": msgs,
            "tool_calls": [],
        }
        return j(
            handler,
            {
                "session": public_session_projection(session_payload),
                "imported": False,
            },
        )

    s = _import_cli_session_call(
        sid,
        title,
        msgs,
        model,
        profile=profile,
        created_at=created_at,
        updated_at=updated_at,
        parent_session_id=cli_parent_session_id,
    )
    if cron_project_id:
        s.project_id = cron_project_id
    s.is_cli_session = True
    s.source_tag = cli_source_tag
    s.raw_source = cli_raw_source or cli_source_tag
    s.session_source = cli_session_source
    s.source_label = cli_source_label
    s.user_id = cli_user_id
    s.chat_id = cli_chat_id
    s.chat_type = cli_chat_type
    s.thread_id = cli_thread_id
    s.session_key = cli_session_key
    s.platform = cli_platform
    s._cli_origin = sid
    s.save(touch_updated_at=False)
    publish_session_list_changed(
        "session_import_cli",
        profile=getattr(s, "profile", None),
    )
    _queue_generated_title_for_imported_session(
        s,
        {
            "title": cli_title,
            "source_tag": cli_source_tag,
            "raw_source": cli_raw_source,
            "session_source": cli_session_source,
            "source_label": cli_source_label,
            "read_only": cli_read_only,
        },
    )
    return j(
        handler,
        {
            "session": public_session_projection(
                s.compact()
                | {
                    "messages": msgs,
                    "is_cli_session": True,
                }
            ),
            "imported": True,
        },
    )


def _handle_session_import(handler, body):
    """Import a session from a JSON export. Creates a new session with a new ID."""
    if not body or not isinstance(body, dict):
        return bad(handler, "Request body must be a JSON object")
    messages = strip_public_internal_fields(
        body.get("messages"),
        message_records=True,
    )
    if not isinstance(messages, list):
        return bad(handler, 'JSON must contain a "messages" array')
    raw_tool_calls = body.get("tool_calls", [])
    if not isinstance(raw_tool_calls, list):
        return bad(handler, 'JSON "tool_calls" must be an array')
    title = body.get("title", "Imported session")
    try:
        workspace = str(resolve_trusted_workspace(body.get("workspace", str(DEFAULT_WORKSPACE))))
    except (TypeError, ValueError):
        workspace = str(resolve_trusted_workspace(str(DEFAULT_WORKSPACE)))
    model = body.get("model", DEFAULT_MODEL)
    s = Session(
        title=title,
        workspace=workspace,
        model=model,
        messages=messages,
        tool_calls=strip_public_internal_fields(raw_tool_calls),
        profile=get_active_profile_name(),
    )
    s.pinned = body.get("pinned", False)
    with LOCK:
        SESSIONS[s.session_id] = s
        SESSIONS.move_to_end(s.session_id)
        _evict_sessions_over_cap()  # #4765: safe LRU eviction (never active/unsaved)
    s.save()
    publish_session_list_changed("session_import")
    return j(
        handler,
        {
            "ok": True,
            "session": public_session_projection(s.compact() | {"messages": s.messages}),
        },
    )


def _handle_session_import_workspace(handler, body):
    """Import a session from a JSON file residing within a trusted workspace."""
    if not body or not isinstance(body, dict):
        return bad(handler, "Request body must be a JSON object")
    file_path = body.get("path")
    if not file_path:
        return bad(handler, "path is required")
    try:
        from api.session_workspace_export import validate_workspace_file_for_import
        validated_path = validate_workspace_file_for_import(file_path)
        with open(validated_path, "r", encoding="utf-8") as f:
            session_json = json.load(f)
        if not isinstance(session_json, dict):
            return bad(handler, "File does not contain a valid JSON session object")
        return _handle_session_import(handler, session_json)
    except ValueError as e:
        return bad(handler, str(e), 400)
    except json.JSONDecodeError as e:
        return bad(handler, f"Invalid JSON format in file: {e}", 400)
    except Exception as e:
        logger.exception("Failed to import session from workspace file")
        return bad(handler, f"Failed to import session: {e}", 500)
