"""Session lifecycle, recovery, lineage, and listing routes.

Extracted from routes.py as part of routes decomposition (Sprint R4).
"""
import logging
import os
import time as _time
from urllib.parse import parse_qs

from api.agent_sessions import read_session_lineage_report
from api.config import SESSION_DIR, load_settings
from api.helpers import _sanitize_error, bad, j, public_session_projection, redact_session_data
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
