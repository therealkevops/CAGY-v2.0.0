"""Session lifecycle, recovery, lineage, and listing routes.

Extracted from routes.py as part of routes decomposition (Sprint R4).
"""
import copy
import json
import logging
import os
import shutil
import threading
import time
import time as _time
import uuid
from urllib.parse import parse_qs

from api.agent_sessions import read_session_lineage_report
from api.config import SESSION_DIR, load_settings
from api.helpers import _sanitize_error, bad, j, public_session_projection, redact_session_data, require
from api.models import _active_state_db_path, get_session
from api.request_diagnostics import RequestDiagnostics
from api.route_approvals import is_session_yolo_enabled
from api.todo_state import attach_todo_state

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
        _routes._handle_session_compress_status(handler, query.get("session_id", [""])[0])
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
    _handle_session_compress_start = _routes._handle_session_compress_start
    _handle_session_compress = _routes._handle_session_compress
    _handle_conversation_rounds = _routes._handle_conversation_rounds
    _handle_handoff_summary = _routes._handle_handoff_summary
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
    _handle_session_import = _routes._handle_session_import
    _handle_session_export_workspace = _routes._handle_session_export_workspace
    _handle_session_import_workspace = _routes._handle_session_import_workspace
    _handle_session_import_cli = _routes._handle_session_import_cli
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

