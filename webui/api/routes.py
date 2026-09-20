"""
Antigravity Web UI -- Route handlers for GET and POST endpoints.
Extracted from server.py (Sprint 11) so server.py is a thin shell.
"""

import html as _html
import copy
import hashlib
import inspect
import errno
import io
import gzip
import json
from api.sse_chunked import end_sse_headers
import logging
import os
import queue
import re
import platform
import shlex
import shutil
import sqlite3
import stat as _stat
import subprocess
import sys
import threading
import time
import uuid
import http.client
import socket as _socket
from collections import defaultdict, deque, OrderedDict
from pathlib import Path
from contextlib import closing
from urllib.parse import parse_qs, quote, unquote, urljoin, urlsplit
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener
try:
    from api import webui_session_db
except ImportError:
    try:
        from webui.api import webui_session_db
    except ImportError:
        import webui_session_db
from api.agent_runtime import (
    AgentRuntimeChangedError,
    ensure_agent_runtime_current,
    require_ai_agent_class,
)
from api.agent_sessions import (
    MESSAGING_SOURCES,
    _looks_like_default_cli_title,
    is_cli_session_row,
    is_cli_session_row_visible,
    read_session_lineage_report,
)
from api.compression_anchor import visible_messages_for_anchor
from api.compression_recovery import (
    COMPRESSION_RECOVERY_ACTION_START_FOCUSED,
    clear_compression_recovery,
    compression_recovery_payload_for_session,
    is_generic_continuation_intent,
)
from api.session_events import (
    add_session_list_changed_listener,
    publish_session_list_changed,
    subscribe_session_events,
    unsubscribe_session_events,
)
from api.gateway_restart import restart_active_profile_gateway
from api.shares import load_share

logger = logging.getLogger(__name__)


def _publish_session_list_changed(
    reason: str,
    *,
    profile: str | None = None,
    session_id: str | None = None,
) -> None:
    """Publish scoped session changes while tolerating legacy test doubles."""
    if not profile and not session_id:
        publish_session_list_changed(reason)
        return
    try:
        publish_session_list_changed(reason, profile=profile, session_id=session_id)
    except TypeError:
        # Some focused tests monkeypatch the route-level publisher with the
        # historical one-argument or profile-only shape. Preserve the old signal instead of
        # turning unrelated session mutations into 500s.
        if profile:
            try:
                publish_session_list_changed(reason, profile=profile)
                return
            except TypeError:
                pass
        publish_session_list_changed(reason)


def _sync_session_title_to_insights(session) -> None:
    """Write title-only session metadata updates through to state.db when enabled."""
    try:
        if not load_settings().get("sync_to_insights"):
            return
        from api.state_sync import sync_session_usage

        messages = getattr(session, "messages", None) or []
        sync_session_usage(
            session_id=session.session_id,
            input_tokens=getattr(session, "input_tokens", None) or 0,
            output_tokens=getattr(session, "output_tokens", None) or 0,
            estimated_cost=getattr(session, "estimated_cost", 0.0),
            model=getattr(session, "model", ""),
            title=session.title,
            message_count=len(messages),
            profile=getattr(session, "profile", None),
            cache_read_tokens=getattr(session, "cache_read_tokens", None) or 0,
            cache_write_tokens=getattr(session, "cache_write_tokens", None) or 0,
        )
    except Exception:
        logger.debug("Failed to update session title in state.db", exc_info=True)


def _persist_generated_session_title(
    session,
    next_title: str,
    *,
    event_reason: str,
    require_default_title: bool = False,
) -> str:
    normalized_title = str(next_title or "").strip()[:80] or "Untitled"
    sid = str(getattr(session, "session_id", "") or "")
    original_session = session
    with _get_session_agent_lock(sid):
        with LOCK:
            latest = SESSIONS.get(sid)
            if latest is not None and str(getattr(latest, "session_id", "") or "") != sid:
                SESSIONS.pop(sid, None)
                latest = None
            elif latest is not None:
                SESSIONS.move_to_end(sid)
        if latest is None:
            latest = Session.load(sid)
            if latest is None:
                raise KeyError(sid)
        session = _ensure_full_session_before_mutation(sid, latest)
        if getattr(session, "read_only", False):
            raise PermissionError(f"Session {sid} is read-only")
        if require_default_title:
            latest_meta = {
                "title": getattr(session, "title", None),
                "source_tag": getattr(session, "source_tag", None),
                "raw_source": getattr(session, "raw_source", None),
                "session_source": getattr(session, "session_source", None),
                "source_label": getattr(session, "source_label", None),
            }
            if not _looks_like_default_cli_title(latest_meta):
                return session.title
        session.title = normalized_title
        from api.session_ops import mark_session_title_generated

        # mark_session_title_generated sets s.llm_title_generated = True and clears manual_title.
        mark_session_title_generated(session)
        session.save(touch_updated_at=False)
        with LOCK:
            SESSIONS[sid] = session
            SESSIONS.move_to_end(sid)
            _evict_sessions_over_cap()  # #4765: safe LRU eviction (never active/unsaved)
    _sync_session_title_to_insights(session)
    _publish_session_list_changed(
        event_reason,
        profile=getattr(session, "profile", None),
        session_id=sid,
    )
    if original_session is not session:
        original_session.title = session.title
        original_session.llm_title_generated = session.llm_title_generated
        original_session.manual_title = session.manual_title
    return session.title


def _queue_generated_title_for_imported_session(session, cli_meta: dict | None) -> None:
    try:
        cli_meta = dict(cli_meta or {})
        if not session or cli_meta.get("read_only") or not _looks_like_default_cli_title(cli_meta):
            return
        sid = str(getattr(session, "session_id", "") or "")
        if not sid:
            return

        def _run() -> None:
            try:
                current = Session.load(sid)
                if not current:
                    return
                current = _ensure_full_session_before_mutation(sid, current)
                if getattr(current, "read_only", False):
                    return
                current_meta = {
                    "title": getattr(current, "title", None),
                    "source_tag": getattr(current, "source_tag", None),
                    "raw_source": getattr(current, "raw_source", None),
                    "session_source": getattr(current, "session_source", None),
                    "source_label": getattr(current, "source_label", None),
                }
                if not _looks_like_default_cli_title(current_meta):
                    return
                next_title, _reason, _raw_preview = generate_session_title_for_session(current)
                normalized_current = str(getattr(current, "title", "") or "").strip()
                normalized_next = str(next_title or "").strip()
                if not normalized_next or normalized_next == normalized_current:
                    return
                _persist_generated_session_title(
                    current,
                    normalized_next,
                    event_reason="session_title_regenerate",
                    require_default_title=True,
                )
            except Exception:
                logger.debug("Failed to generate imported session title for %s", sid, exc_info=True)

        threading.Thread(target=_run, daemon=True, name=f"imported-title-{sid}").start()
    except Exception:
        logger.debug(
            "Failed to queue imported session title generation for %s",
            getattr(session, "session_id", None),
            exc_info=True,
        )


def _on_session_list_changed(profile: str | None = None) -> None:
    """Invalidate in-process /api/sessions cache when sidebar state mutates."""
    _clear_session_list_cache(profile)
    # #4842: also drop the inner CLI/cron projection cache. While a turn streams
    # that cache is frozen on a stable streaming marker (so per-token message
    # writes don't bust it), which means it no longer self-invalidates via the
    # state.db content fingerprint mid-stream. In-app structural mutations
    # (session create/rename/archive/delete/branch/pin/move/import, attention)
    # fire this listener — and never fire per streamed token — so clearing here
    # restores prompt freshness for those without reintroducing the per-poll
    # rebuild the freeze removed. Note: externally-driven changes that do NOT go
    # through this listener (a scheduled cron job completing, or an external CLI
    # writing rows directly) are not cleared here mid-stream; for those the 30s
    # streaming TTL is the backstop — they surface within one streaming-TTL
    # window (≤30s) rather than instantly. That bound is the deliberate
    # latency/CPU trade-off of the freeze.
    try:
        from api.models import clear_cli_sessions_cache
        clear_cli_sessions_cache()
    except Exception:
        logger.debug("Failed to clear CLI sessions cache on session list change", exc_info=True)


try:
    add_session_list_changed_listener(_on_session_list_changed)
except Exception:
    logger.debug("Failed to register session list cache invalidation listener", exc_info=True)


# ── Cron run tracking ────────────────────────────────────────────────────────
# Track job IDs currently being executed so the frontend can poll status.
from api.route_tools_mcp import (  # noqa: F401 — re-exports for backward compat
    _CRON_CREATE_SNAPSHOT_LOCK,
    _CRON_OUTPUT_CONTENT_LIMIT,
    _CRON_OUTPUT_HEADER_CONTEXT,
    _RUNNING_CRON_JOBS,
    _RUNNING_CRON_LOCK,
)

from api.route_session import (  # noqa: F401 — re-exports for backward compat
    _MANUAL_COMPRESSION_JOBS,
    _MANUAL_COMPRESSION_JOBS_LOCK,
    _MANUAL_COMPRESSION_JOB_TTL_SECONDS,
)
_MESSAGING_RAW_SOURCES = {str(s).strip().lower() for s in MESSAGING_SOURCES}
_MESSAGING_SESSION_METADATA_CACHE: dict[str, object] = {
    "path": None,
    "mtime": None,
    "identity": {},
}
_MESSAGING_SESSION_METADATA_LOCK = threading.Lock()
_STALE_MESSAGING_END_REASONS = {"session_reset", "session_switch"}
_CSP_REPORT_LOGGER = logging.getLogger("csp_report")
_CSP_REPORT_RATE_LIMIT: dict[str, list[float]] = {}
_CSP_REPORT_RATE_LIMIT_LOCK = threading.Lock()
_CSP_REPORT_RATE_LIMIT_WINDOW_SECONDS = 60
_CSP_REPORT_RATE_LIMIT_MAX = 100
_CSP_REPORT_MAX_BODY_BYTES = 64 * 1024
_CLIENT_EVENT_LOGGER = logging.getLogger("client_event")
_CLIENT_EVENT_RATE_LIMIT: dict[str, list[float]] = {}
_CLIENT_EVENT_RATE_LIMIT_LOCK = threading.Lock()
_CLIENT_EVENT_RATE_LIMIT_WINDOW_SECONDS = 60
_CLIENT_EVENT_RATE_LIMIT_MAX = 30
_CLIENT_EVENT_MAX_BODY_BYTES = 4 * 1024
_EXTENSION_SIDECAR_PROXY_MAX_RESPONSE_BYTES = 512 * 1024
_CLIENT_EVENT_ALLOWED_FIELDS = {
    "event": 64,
    "source": 80,
    "session_id": 128,
    "stream_id": 128,
    "visibility_state": 32,
    "url_path": 256,
    "reason": 160,
}


from api.route_tools_mcp import (  # noqa: F401 — re-exports for backward compat
    _latest_cron_session_info_for_jobs,
    _normalize_cron_job_ids,
)



def _session_field(session, field, default=None):
    if isinstance(session, dict):
        return session.get(field, default)
    return getattr(session, field, default)


def _session_counts_toward_pin_quota(session) -> bool:
    """Return True when a pinned session should consume visible pin quota."""
    if not _session_field(session, "pinned", False):
        return False
    if _session_field(session, "archived", False):
        return False
    if isinstance(session, dict):
        row = session
    elif hasattr(session, "compact"):
        row = session.compact()
    else:
        row = {
            "pre_compression_snapshot": _session_field(session, "pre_compression_snapshot", False),
            "source_tag": _session_field(session, "source_tag", None),
            "default_hidden": _session_field(session, "default_hidden", False),
        }
    return not _hide_from_default_sidebar(row)


def _session_row_lineage_root_id(session, sessions_by_id) -> str:
    sid = str(_session_field(session, "session_id", "") or "")
    explicit = _session_field(session, "_lineage_root_id", None)
    if explicit:
        return str(explicit)
    # A branch/fork is an independent, separately-visible session (it carries a
    # parent_session_id purely for provenance), so it must count as its OWN pin
    # lineage — only compression/continuation rows should collapse to a shared
    # root. Without this, two pinned forks of the same parent would collapse to a
    # single quota lineage and let the user exceed pinned_sessions_limit (#3288).
    if _session_field(session, "session_source", None) == "fork":
        return sid
    current = sid
    seen = {sid} if sid else set()
    parent = _session_field(session, "parent_session_id", None)
    while parent:
        parent = str(parent)
        if parent in seen:
            break
        current = parent
        seen.add(parent)
        parent_row = sessions_by_id.get(parent)
        if not parent_row:
            break
        parent = _session_field(parent_row, "parent_session_id", None)
    return current or sid


def _visible_pinned_lineage_ids(session_rows) -> set[str]:
    sessions_by_id = {}
    for row in session_rows:
        sid = str(_session_field(row, "session_id", "") or "")
        if sid:
            sessions_by_id[sid] = row
    roots: set[str] = set()
    for row in session_rows:
        if not _session_counts_toward_pin_quota(row):
            continue
        root = _session_row_lineage_root_id(row, sessions_by_id)
        if root:
            roots.add(root)
    return roots


# ── Profile-scoped session/project filtering (#1611, #1614) ────────────────
#
# Sessions and projects are stored in the WebUI sidecar without per-row
# isolation by default — they're tagged with a `profile` field but every
# query saw all rows. The fix scopes both endpoints to the active profile
# by default, with `?all_profiles=1` opting into aggregate mode.
#
# Renamed-root profile handling (#1612): a row tagged `profile='default'`
# matches the active root regardless of the root's display name, and a row
# tagged with the renamed-root display name (e.g. 'kinni') likewise matches
# when the active profile is `'default'`. _is_root_profile() is the
# canonical check.

# Canonical helper now lives in api.profiles so out-of-process consumers
# (mcp_server.py) can import it without duplicating the visibility model.
# Re-exported here so existing `_profiles_match(...)` call sites in this
# module keep resolving without per-call-site refactors.
from api.profiles import (  # noqa: F401, E402  (re-export)
    _profiles_match,
    _is_isolated_profile_mode,
    _is_root_profile,
    _SKILLS_STATS_CACHE,
    get_active_profile_name,
    get_active_profile_name as _get_active_profile_name,
    get_active_agy_home,
    list_profiles_api,
    profile_scope_for_detached_worker,
)


def _all_profiles_query_flag(parsed_url) -> bool:
    """Return True if the request URL has `?all_profiles=1` (or true/yes).

    Centralizes the opt-in parsing so /api/sessions and /api/projects use
    the same shape. Accepts 1/true/yes (case-insensitive) for ergonomics.
    """
    qs = parse_qs(parsed_url.query)
    raw = qs.get('all_profiles', [''])[0].strip().lower()
    return raw in ('1', 'true', 'yes', 'on')


def _all_profiles_enabled(parsed_url) -> bool:
    """Enable aggregate profile reads only when the request asks and mode allows it."""
    return _all_profiles_query_flag(parsed_url) and not _is_isolated_profile_mode()


def _query_flag(parsed_url, name: str) -> bool:
    """Return True for a truthy query flag value."""
    qs = parse_qs(parsed_url.query)
    raw = qs.get(name, [''])[0].strip().lower()
    return raw in ('1', 'true', 'yes', 'on')


def _query_positive_int(parsed_url, name: str, *, default=None, maximum: int | None = None):
    """Return a non-negative integer query parameter, or default when absent/invalid."""
    qs = parse_qs(parsed_url.query)
    raw = qs.get(name, [''])[0]
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    if value < 0:
        return default
    if maximum is not None:
        value = min(value, int(maximum))
    return value


def _session_visible_to_active_profile(session_profile, handler=None) -> bool:
    """Return whether a detail-load session belongs to the active profile.

    Real request handlers must enforce the same profile boundary as
    /api/sessions, even when the request has no hermes_profile cookie and the
    process-level active profile is the default/root profile. Direct unit-callers
    without a request handler keep the historical metadata-load behavior.
    """
    if handler is None:
        return True
    active_profile = _get_active_profile_name()
    if not isinstance(session_profile, str):
        session_profile = None
    return _profiles_match(session_profile, active_profile)


def _is_profile_agnostic_foreign_session(cli_meta) -> bool:
    """Return whether a foreign-session row lives outside the Hermes profile tree.

    Claude Code transcripts are scanned straight out of ``~/.claude/projects``
    by ``get_claude_code_sessions()``, which stamps ``profile: None`` on every
    row because the JSONL files belong to no Hermes profile at all. The sidebar
    lists them under whichever profile is active, but ``_profiles_match``
    coerces ``None`` to ``'default'``, so the detail-load profile gate 404s
    every one of them as soon as the active profile is a named (non-root) one —
    the session shows in the list and then renders "Session not available in
    web UI." when clicked.

    Exempt these profile-less external-agent rows from the gate so opening one
    behaves identically on the root profile and on named profiles. Rows that
    DO carry a profile (every state.db-backed CLI/messaging/cron session) stay
    fully scoped.
    """
    if not isinstance(cli_meta, dict):
        return False
    if cli_meta.get("profile"):
        return False
    sources = {
        str(cli_meta.get("source_tag") or "").strip().lower(),
        str(cli_meta.get("raw_source") or "").strip().lower(),
    }
    # Profile-less external-agent rows that live outside the Hermes profile tree.
    # Claude Code: scanned from ~/.claude/projects; Codex: scanned from ~/.codex/
    profile_agnostic_sources = {CLAUDE_CODE_SOURCE}
    try:
        from api.codex_sessions import CODEX_SOURCE
        profile_agnostic_sources.add(CODEX_SOURCE)
    except ImportError:
        pass
    return bool(sources & profile_agnostic_sources)


def _request_session_visibility_exempt(method: str, path: str | None) -> bool:
    if not path:
        return False
    if method == "GET" and path == "/api/session":
        # Detail-load owns profile mismatch handling so the frontend can switch
        # to the session's profile instead of treating a valid cross-profile
        # deep link as a deleted/stale session.
        return True
    if method != "POST":
        return False
    # Import routes create/claim sessions before normal ownership exists, and
    # chat/start has inline placeholder-retag rules that must run before the
    # generic request-session guard.
    return path in {
        "/api/session/import",
        "/api/session/import/workspace",
        "/api/session/import_cli",
        "/api/chat/start",
    }


def _session_id_visible_to_request_profile(handler, sid, *, emit_error: bool = True) -> bool:
    """Return whether ``sid`` belongs to the active profile."""
    if not isinstance(sid, str) or not sid:
        return True
    if not is_safe_session_id(sid):
        return True
    try:
        session = get_session(sid, metadata_only=True)
    except KeyError:
        return True
    if not _session_visible_to_active_profile(getattr(session, "profile", None), handler):
        if emit_error:
            bad(handler, "Session not found", 404)
        return False
    return True


def _stream_id_owner_session_id(stream_id: str | None) -> str | None:
    """Resolve stream owner session_id via active-run registry first, fallback to journal."""
    stream_id = str(stream_id or "").strip()
    if not stream_id:
        return None
    try:
        with ACTIVE_RUNS_LOCK:
            raw = (ACTIVE_RUNS or {}).get(stream_id)
        if isinstance(raw, dict):
            owner = str(raw.get("session_id") or "").strip()
            if owner:
                return owner
    except Exception:
        logger.debug("Failed reading ACTIVE_RUNS owner for stream %s", stream_id, exc_info=True)
    try:
        owner = stream_owner_session_id(stream_id)
        if owner:
            return owner
    except Exception:
        logger.debug("Failed reading registered owner for stream %s", stream_id, exc_info=True)
    if not is_safe_session_id(stream_id):
        return None
    try:
        summary = find_run_summary(stream_id)
        if isinstance(summary, dict):
            owner = str(summary.get("session_id") or "").strip()
            return owner or None
    except Exception:
        logger.debug("Failed reading run summary for stream %s", stream_id, exc_info=True)
    return None


def _stream_id_visible_to_request_profile(
    handler,
    stream_id: str | None,
    *,
    emit_error: bool = True,
) -> bool:
    """Return whether the stream owner is visible to the request's profile."""
    owner_session_id = _stream_id_owner_session_id(stream_id)
    if not owner_session_id:
        return True
    return _session_id_visible_to_request_profile(handler, owner_session_id, emit_error=emit_error)


def _guard_request_session_visibility(handler, parsed, body=None, method="GET") -> bool:
    """Apply request session-profile visibility check to request-supplied IDs.

    Covers top-level `session_id` in the query/body. Routes that accept session
    IDs under other keys must enforce their own visibility checks.
    """
    method = str(method).upper()
    if _request_session_visibility_exempt(method, getattr(parsed, "path", "")):
        return True
    sid = parse_qs(getattr(parsed, "query", "") or "").get("session_id", [None])[0]
    if not _session_id_visible_to_request_profile(handler, sid):
        return False
    if isinstance(body, dict) and not _session_id_visible_to_request_profile(handler, body.get("session_id")):
        return False
    return True


from api.route_tools_mcp import (  # noqa: F401 — re-exports for backward compat
    _active_skill_search_dirs,
    _active_skills_dir,
    _skill_category_from_path,
    _skill_path_within,
)


def _worktree_retained_payload(session) -> dict:
    """Return explicit no-cleanup metadata for worktree-backed session actions."""
    worktree_path = getattr(session, "worktree_path", None) if session else None
    if not worktree_path:
        return {}
    payload = {
        "worktree_retained": True,
        "worktree_path": worktree_path,
    }
    worktree_branch = getattr(session, "worktree_branch", None)
    worktree_repo_root = getattr(session, "worktree_repo_root", None)
    if worktree_branch:
        payload["worktree_branch"] = worktree_branch
    if worktree_repo_root:
        payload["worktree_repo_root"] = worktree_repo_root
    return payload


def _worktree_retained_payload_for_session_id(sid: str) -> dict:
    try:
        return _worktree_retained_payload(get_session(sid, metadata_only=True))
    except KeyError:
        return {}
    except Exception:
        logger.debug("Failed to read worktree metadata for deleted session %s", sid)
        return {}


from api.route_tools_mcp import (  # noqa: F401 — re-exports for backward compat
    MAX_DESCRIPTION_LENGTH,
    _EXCLUDED_SKILL_DIRS,
    _active_profile_config_path,
    _find_skill_in_dir,
    _find_skill_in_dirs,
    _get_disabled_skill_names_for_profile,
    _linked_files_for_skill,
    _normalize_disabled_set,
    _parse_config_string_list,
    _parse_frontmatter,
    _parse_tags,
    _skill_not_found_payload,
    _skill_view_from_active_dir,
    _skill_view_from_file,
    _skills_list_from_dir,
    _sort_skills,
    iter_skill_index_files,
    skill_matches_platform,
)

# ── SSE app-level heartbeat (#1623) ────────────────────────────────────────
#
# Kernel TCP keepalive (server.py setsockopt block) declares a peer dead at
# KEEPIDLE (10s) + KEEPINTVL (5s) * KEEPCNT (3) = 25s in the worst case. The
# app-level SSE heartbeat must fire well below that window so flaky-network
# probes never get the chance to kill an idle stream during long LLM thinking
# phases. 5s gives the kernel ~5x headroom: probe at 10s, heartbeat byte at
# every 5s of idle keeps the socket warm.
#
# Cost: ~12 bytes per heartbeat * 12 extra heartbeats/min = ~150B/min idle.
# Trivial; many production SSE deployments run 5-15s heartbeats specifically
# to handle proxies and mobile NAT.
_SSE_HEARTBEAT_INTERVAL_SECONDS = 5
_SESSION_SSE_SENT_EVENT_ID_LIMIT = 4096


def _normalize_messaging_source(raw_source) -> str:
    return str(raw_source or "").strip().lower()


def _is_known_messaging_source(raw_source) -> bool:
    return _normalize_messaging_source(raw_source) in _MESSAGING_RAW_SOURCES


def _safe_first(*values):
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _gateway_session_metadata_path():
    try:
        from api.profiles import get_active_agy_home
        hermes_home = Path(get_active_agy_home()).expanduser().resolve()
    except Exception:
        logger.debug("Silent exception in _gateway_session_metadata_path", exc_info=True)
        hermes_home = Path(os.getenv("HERMES_HOME", str(Path.home() / ".hermes"))).expanduser().resolve()
    return hermes_home / "sessions" / "sessions.json"


def _load_gateway_session_identity_map() -> dict[str, dict]:
    path = _gateway_session_metadata_path()
    if not path.exists():
        return {}

    try:
        st = path.stat()
        cache = _MESSAGING_SESSION_METADATA_CACHE
        with _MESSAGING_SESSION_METADATA_LOCK:
            if cache["path"] == str(path) and cache["mtime"] == st.st_mtime:
                return cache["identity"].copy()
    except Exception:
        logger.debug("Silent exception in _load_gateway_session_identity_map", exc_info=True)
        return {}

    try:
        raw_sessions = json.loads(path.read_text(encoding="utf-8"))
    except Exception as _json_err:
        logger.debug("Failed to parse gateway sessions metadata from %s: %s", path, _json_err)
        return {}

    mapping: dict[str, dict] = {}
    if isinstance(raw_sessions, dict):
        for _entry in raw_sessions.values():
            if not isinstance(_entry, dict):
                continue
            session_id = _safe_first(_entry.get("session_id"))
            if not session_id:
                continue
            origin = _entry.get("origin") if isinstance(_entry.get("origin"), dict) else {}
            platform = _safe_first(origin.get("platform"), _entry.get("platform"))
            mapping[session_id] = {
                "session_key": _safe_first(_entry.get("session_key"), _entry.get("key")),
                "chat_id": _safe_first(origin.get("chat_id"), _entry.get("chat_id")),
                "thread_id": _safe_first(origin.get("thread_id"), _entry.get("thread_id")),
                "chat_type": _safe_first(origin.get("chat_type"), _entry.get("chat_type")),
                "user_id": _safe_first(origin.get("user_id"), _entry.get("user_id")),
                "platform": platform,
                "raw_source": platform,
            }

    with _MESSAGING_SESSION_METADATA_LOCK:
        _MESSAGING_SESSION_METADATA_CACHE["path"] = str(path)
        _MESSAGING_SESSION_METADATA_CACHE["mtime"] = st.st_mtime
        _MESSAGING_SESSION_METADATA_CACHE["identity"] = mapping
    return mapping.copy()


def _gateway_status_payload() -> dict:
    import datetime

    identity_map = _load_gateway_session_identity_map()
    sessions_path = _gateway_session_metadata_path()

    # Detect whether the gateway process is alive, independent of connected
    # messaging platforms. An empty identity_map means zero connected
    # platforms, not necessarily a stopped gateway.
    health = build_agent_health_payload()
    alive = health.get("alive")
    details = health.get("details") if isinstance(health.get("details"), dict) else {}
    health_reason = details.get("reason")
    health_state = details.get("state")
    health_gateway_state = details.get("gateway_state")
    if alive is True:
        running = True
        configured = True
    elif alive is False:
        running = False
        configured = True
    else:
        gateway_running_metadata = (
            health_reason == "gateway_stale_running_state"
            or health_gateway_state == "running"
        )
        configured = True if gateway_running_metadata else bool(identity_map)
        running = bool(identity_map)

    platforms_set: set[str] = set()
    for meta in identity_map.values():
        raw = meta.get("raw_source") or meta.get("platform") or ""
        norm = _normalize_messaging_source(raw)
        if norm:
            platforms_set.add(norm)
    platform_labels = {
        "telegram": "Telegram",
        "discord": "Discord",
        "slack": "Slack",
        "email": "Email",
        "web": "Web",
        "api": "API",
    }
    platforms = sorted(
        [{"name": p, "label": platform_labels.get(p, p.title())} for p in platforms_set],
        key=lambda x: x["label"],
    )
    last_active = ""
    if running and sessions_path.exists():
        try:
            mtime = sessions_path.stat().st_mtime
            last_active = datetime.datetime.fromtimestamp(mtime).isoformat()
        except Exception:
            logger.debug("Silent exception in _gateway_status_payload", exc_info=True)
            pass
    return {
        "running": running,
        "configured": configured,
        "platforms": platforms,
        "last_active": last_active,
        "session_count": len(identity_map),
        "health": {
            "state": health_state,
            "reason": health_reason,
            "gateway_state": health_gateway_state,
        },
    }


_GATEWAY_LIFECYCLE_TIMEOUT_SECONDS = 60

# Server-side single-flight guard for gateway lifecycle actions. The client
# disables its button while a request is in flight, but a scripted authed
# client could still fire overlapping start/stop/restart calls, spawning
# concurrent `hermes gateway` subprocesses. Serialize them here (mirrors the
# self-update _apply_lock pattern): a non-blocking acquire returns 409 on
# contention rather than launching a second overlapping subprocess.
_GATEWAY_ACTION_LOCK = threading.Lock()


def _run_gateway_lifecycle_command(action: str) -> subprocess.CompletedProcess:
    if action not in {"start", "stop", "restart"}:
        raise ValueError("unsupported gateway action")

    from api import config as api_config
    from api.profiles import get_active_profile_name

    agent_dir = getattr(api_config, "_AGENT_DIR", None)
    if not agent_dir:
        raise FileNotFoundError("Antigravity agent checkout not found")
    agent_dir = Path(agent_dir).expanduser().resolve()
    main_py = agent_dir / "hermes_cli" / "main.py"
    if not main_py.exists():
        raise FileNotFoundError("Antigravity agent CLI entrypoint not found")

    cmd = [str(getattr(api_config, "PYTHON_EXE", sys.executable)), str(main_py)]
    profile_name = ""
    try:
        profile_name = str(get_active_profile_name() or "").strip()
    except Exception as exc:
        logger.debug("Could not resolve active profile for gateway lifecycle: %s", exc)
    if profile_name and profile_name != "default":
        cmd.extend(["--profile", profile_name])
    cmd.extend(["gateway", action])

    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("BROWSER", "echo")
    return subprocess.run(
        cmd,
        cwd=str(agent_dir),
        env=env,
        capture_output=True,
        text=True,
        timeout=_GATEWAY_LIFECYCLE_TIMEOUT_SECONDS,
    )


def _handle_gateway_lifecycle(handler, action: str, body: dict):
    del body  # Reserved for future per-gateway naming without changing the route contract.
    # Reject overlapping lifecycle actions instead of spawning concurrent
    # `hermes gateway` subprocesses (a non-blocking acquire — the action holds
    # the lock for at most _GATEWAY_LIFECYCLE_TIMEOUT_SECONDS).
    if action not in {"start", "stop", "restart"}:
        return bad(handler, "unsupported gateway action", 400)
    if not _GATEWAY_ACTION_LOCK.acquire(blocking=False):
        return j(
            handler,
            {
                "ok": False,
                "error": "Another gateway action is already in progress; try again shortly.",
                "action": action,
            },
            status=409,
        )
    try:
        result = _run_gateway_lifecycle_command(action)
    except ValueError as exc:
        return bad(handler, str(exc), 400)
    except FileNotFoundError as exc:
        return j(handler, {"ok": False, "error": _sanitize_error(exc), "action": action}, status=500)
    except subprocess.TimeoutExpired as exc:
        logger.warning(
            "Gateway %s command timed out after %ss; stdout=%r stderr=%r",
            action,
            _GATEWAY_LIFECYCLE_TIMEOUT_SECONDS,
            exc.stdout,
            exc.stderr,
        )
        return j(
            handler,
            {
                "ok": False,
                "error": f"Gateway {action} timed out after {_GATEWAY_LIFECYCLE_TIMEOUT_SECONDS} seconds",
                "action": action,
            },
            status=504,
        )
    except Exception as exc:
        logger.exception("Gateway %s command failed before completion", action)
        return j(handler, {"ok": False, "error": _sanitize_error(exc), "action": action}, status=500)
    finally:
        _GATEWAY_ACTION_LOCK.release()

    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()
    if result.returncode != 0:
        logger.warning(
            "Gateway %s command failed with exit code %s; stdout=%r stderr=%r",
            action,
            result.returncode,
            stdout,
            stderr,
        )
        return j(
            handler,
            {
                "ok": False,
                "error": f"Gateway {action} failed with exit code {result.returncode}",
                "action": action,
                "returncode": result.returncode,
            },
            status=500,
        )

    return j(
        handler,
        {
            "ok": True,
            "action": action,
            # Do NOT return captured stdout/stderr — the `hermes gateway` CLI
            # prints service/PID/status details the browser shouldn't receive
            # (mirrors the failure path, which already suppresses them). The
            # frontend localizes its own success copy; the refreshed status
            # payload carries the user-facing state.
            "message": f"Gateway {action} completed.",
            "status": _gateway_status_payload(),
        },
    )


from api.route_tools_mcp import (  # noqa: F401 — re-exports for backward compat
    _AGENT_CRON_IMPORT_PATH_LOCK,
    _AGENT_CRON_IMPORT_PATH_READY,
    _available_cron_profile_names,
    _cron_job_for_api,
    _cron_job_subprocess_main,
    _cron_jobs_cross_profile,
    _cron_jobs_for_api,
    _cron_output_content_window,
    _cron_response_marker_index,
    _cron_subprocess_result_timeout_seconds,
    _ensure_agent_cron_import_path,
    _event_profile_for_cron_job,
    _is_cron_running,
    _mark_cron_done,
    _mark_cron_running,
    _normalize_cron_profile_value,
    _profile_home_for_cron_job,
    _run_cron_job_in_profile_subprocess,
    _run_cron_tracked,
)

_PROVIDER_ALIASES = {
    "claude": "anthropic",
    "gpt": "openai",
    "gemini": "google",
    "openai-codex": "openai",
    "openai-api": "openai",
    "google-gemini": "google",
    "google-ai-studio": "google",
    "claude-code": "anthropic",
}

from api.route_config_models import (
    _OPENAI_COMPAT_ENDPOINTS,
    _LIVE_MODELS_CACHE_TTL,
    _LIVE_MODELS_CACHE,
    _LIVE_MODELS_CACHE_LOCK,
    _active_profile_for_live_models_cache,
    _live_models_cache_key,
    _get_cached_live_models,
    _set_cached_live_models,
    _clear_live_models_cache,
)


from api import route_session_list_cache as _route_session_list_cache

_SESSIONS_CACHE = _route_session_list_cache._SESSIONS_CACHE
_SESSIONS_CACHE_INFLIGHT = _route_session_list_cache._SESSIONS_CACHE_INFLIGHT
_SESSIONS_CACHE_LOCK = _route_session_list_cache._SESSIONS_CACHE_LOCK
_SESSIONS_CACHE_MAX_ENTRIES = _route_session_list_cache._SESSIONS_CACHE_MAX_ENTRIES
_SESSIONS_CACHE_PROFILE_INVALIDATION_VERSION = (
    _route_session_list_cache._SESSIONS_CACHE_PROFILE_INVALIDATION_VERSION
)
_SESSIONS_CACHE_STALE_WAIT_SECONDS = _route_session_list_cache._SESSIONS_CACHE_STALE_WAIT_SECONDS
_SESSIONS_CACHE_STREAMING_TTL_SECONDS = (
    _route_session_list_cache._SESSIONS_CACHE_STREAMING_TTL_SECONDS
)
_SESSIONS_CACHE_TTL_SECONDS = _route_session_list_cache._SESSIONS_CACHE_TTL_SECONDS
_SESSIONS_CACHE_WAIT_SECONDS = _route_session_list_cache._SESSIONS_CACHE_WAIT_SECONDS
_clear_session_list_cache = _route_session_list_cache._clear_session_list_cache
_session_list_cache_clear = _route_session_list_cache._session_list_cache_clear
_session_list_cache_claim_rebuild = _route_session_list_cache._session_list_cache_claim_rebuild
_session_list_cache_done = _route_session_list_cache._session_list_cache_done
_session_list_cache_get = _route_session_list_cache._session_list_cache_get
_session_list_cache_invalidation_stamp = _route_session_list_cache._session_list_cache_invalidation_stamp
_route_session_list_cache_key = _route_session_list_cache._session_list_cache_key
_session_list_cache_overlay_runtime_rows = _route_session_list_cache._session_list_cache_overlay_runtime_rows
_session_list_cache_path_stamp = _route_session_list_cache._session_list_cache_path_stamp
_session_list_cache_profile_scope = _route_session_list_cache._session_list_cache_profile_scope
_session_list_row_is_runtime_active = _route_session_list_cache._session_list_row_is_runtime_active
_session_list_row_numeric_value = _route_session_list_cache._session_list_row_numeric_value
_session_list_row_timestamp = _route_session_list_cache._session_list_row_timestamp
_session_list_runtime_sort_key = _route_session_list_cache._session_list_runtime_sort_key
_session_list_cache_set = _route_session_list_cache._session_list_cache_set
_session_list_cache_source_stamp = _route_session_list_cache._session_list_cache_source_stamp
_session_list_cache_state_db_fingerprint = _route_session_list_cache._session_list_cache_state_db_fingerprint
_session_list_cache_stale_reason = _route_session_list_cache._session_list_cache_stale_reason
_session_list_cache_streaming_freeze_marker = _route_session_list_cache._session_list_cache_streaming_freeze_marker


def _callable_accepts_kwarg(callable_obj, kwarg_name: str) -> bool:
    try:
        signature = inspect.signature(callable_obj)
    except (TypeError, ValueError):
        return True
    if kwarg_name in signature.parameters:
        return True
    return any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )


def _session_list_cache_key(
    active_profile: str | None,
    all_profiles: bool,
    show_cli_sessions: bool,
    show_previous_messaging_sessions: bool,
    show_cron_sessions: bool,
    include_archived: bool = False,
    exclude_hidden: bool = False,
    visible_only: bool = False,
    show_webhook_sessions: bool = False,
    show_kanban_sessions: bool = False,
    source_filter: str | None = None,
    sidebar_source: str | None = None,
    archived_limit: int | None = None,
    archived_offset: int = 0,
    show_claude_code_sessions: bool = True,
) -> tuple:
    return _route_session_list_cache_key(
        active_profile=active_profile,
        all_profiles=all_profiles,
        show_cli_sessions=show_cli_sessions,
        show_previous_messaging_sessions=show_previous_messaging_sessions,
        show_cron_sessions=show_cron_sessions,
        include_archived=include_archived,
        exclude_hidden=exclude_hidden,
        visible_only=visible_only,
        show_webhook_sessions=show_webhook_sessions,
        show_kanban_sessions=show_kanban_sessions,
        source_filter=source_filter,
        sidebar_source=sidebar_source,
        archived_limit=archived_limit,
        archived_offset=archived_offset,
    ) + (bool(show_claude_code_sessions),)

_ROUTE_SESSION_LIST_CACHE_DYNAMIC_EXPORTS = {
    "_SESSIONS_CACHE_ALL_PROFILES_INVALIDATION_VERSION",
    "_SESSIONS_CACHE_GLOBAL_INVALIDATION_VERSION",
    "_session_list_cache_settings_write_version",
}


def __getattr__(name):
    if name in _ROUTE_SESSION_LIST_CACHE_DYNAMIC_EXPORTS:
        return getattr(_route_session_list_cache, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _prune_orphaned_webui_zero_message_sessions(rows, *, diag_stage=None):
    """#4985 second-pass orphan prune for native-WebUI rows whose ``state.db.messages`` is empty.

    Takes the post-``#3238`` ``webui_sessions`` list (i.e. rows that already
    survived the #3238/#4591 CLI/API-server prune) and returns a NEW list with
    any row whose backing ``state.db.messages`` table is empty removed.
    Removed sids are also persisted to the tombstone via
    ``_record_webui_zero_message_orphan_tombstone`` so
    ``recover_missing_index_sidecars`` does not re-add them to the sidebar
    index on the next poll (avoids the cache-thrash loop where every poll
    does one fsync'd index write + one state.db probe per orphan, forever).

    Invariants preserved:

    - Rows with ``active_stream_id`` / ``has_pending_user_message`` /
      ``worktree_path`` set are NEVER pruned — the inflight / worktree-bound
      / pending safety contract from ``IC_kwDOR1LuPM8AAAABHrkF1Q``.
    - Rows whose ``state.db.messages`` is empty AND that survived the
      upstream ``all_sessions()`` ``#1171`` keep-filter (i.e. titled OR has
      positive ``message_count``) ARE pruned — the post-#1171-survivor
      shape #4985 actually describes (a row that lingers VISIBLY in the
      sidebar because of a stale positive ``message_count`` or a title set
      before the first turn committed).

    This helper is intentionally extracted out of the ``if show_cli_sessions:``
    branch so the prune fires in BOTH branches of
    ``_build_session_list_cache_payload``. Established installs have
    ``settings.show_cli_sessions`` pinned to ``False`` (per
    ``api/config.py:7637-7648``) and those are exactly the long-time users
    who have accumulated the #4985 404 orphans — without hoisting, the
    ``else:`` branch silently skipped the prune and the sidebar kept
    dangling rows that 404 on click (review
    ``IC_kwDOR1LuPM8AAAABHsyFGg``).
    """
    if not rows:
        return list(rows) if rows is not None else []
    _diag = diag_stage if callable(diag_stage) else (lambda *_a, **_k: None)
    # #4985 self-healing: the tombstone is NOT a blind-drop filter at the
    # top of the helper. A row whose sid is in the tombstone is allowed
    # into the gate predicate like any other row — and the post-probe
    # logic below explicitly distinguishes four cases:
    #
    #   1. probe says NOT empty AND sid IS tombstoned → SELF-HEAL: the row
    #      has actually gained messages, so clear the tombstone and keep
    #      the row (do NOT add to missing_webui_orphan_ids). This is the
    #      primary fix for review IC_kwDOR1LuPM8AAAABHvY-dw.
    #   2. probe says empty AND sid IS tombstoned → tombstone persists
    #      (orphan shape unchanged), but the row is excluded from the
    #      returned list so the tombstone continues to suppress it on
    #      this poll too. Do NOT redundantly prune+tombstone (would
    #      cycle).
    #   3. probe says empty AND sid is NOT tombstoned → new orphan: prune
    #      from index, record tombstone, diag_stage.
    #   4. probe says NOT empty AND sid is NOT tombstoned → row has
    #      messages, retain (gate already passes anyway).
    #
    # A blind-drop at the top (the previous behavior) is strictly worse
    # than the orphan it suppresses — it would silently swallow a
    # legitimately-resurfaced row forever, even after the user actually
    # sent messages. The self-healing case is what makes the tombstone a
    # recoverable "this sid is currently empty" signal rather than a
    # permanent hide-list.
    if not rows:
        return []
    # Gate predicate mirrors the inline block that lived here before the
    # helper extract. The (title!='Untitled' OR count>0) clause is what makes
    # this gate actually reach a row #1171 kept — without it, the gate is a
    # no-op because ``all_sessions()`` at ``api/models.py:3892-3898`` (and
    # its full-scan fallback at 3946-3952) has already stripped every
    # (Untitled ∧ count==0 ∧ ¬active_stream_id ∧ ¬has_pending_user_message ∧
    # ¬worktree_path) row before our prune block runs.
    _webui_orphan_probe_rows = [
        s for s in rows
        if _session_source_is_webui(s)
        and not s.get("active_stream_id")
        and not s.get("has_pending_user_message")
        and not s.get("worktree_path")
        and (
            s.get("title", "Untitled") != "Untitled"
            or _numeric_count(s.get("message_count")) > 0
        )
    ]
    if not _webui_orphan_probe_rows:
        return list(rows)
    rows_by_profile_webui: dict[object, list[dict]] = defaultdict(list)
    for row in _webui_orphan_probe_rows:
        rows_by_profile_webui[row.get("profile")].append(row)
    _tombstoned = _load_webui_zero_message_orphan_tombstone()
    self_healed_ids: set[str] = set()
    missing_webui_orphan_ids: set[str] = set()
    still_hidden_ids: set[str] = set()
    for profile_key, profile_rows in rows_by_profile_webui.items():
        probe_ids = [
            str(row.get("session_id")).strip()
            for row in profile_rows
            if str(row.get("session_id") or "").strip()
        ]
        zero_message_sids = agent_session_zero_message_sids(
            probe_ids,
            profile=profile_key if isinstance(profile_key, str) and profile_key else None,
        )
        # Iterate over the actual rows (not just probe_ids) so each sid
        # decision can probe the sidecar for real ``messages``. The r5
        # signal keyed off the row's cached ``message_count`` (which is
        # stale-positive on the very phantom rows #4985 exists to prune:
        # sidecar ``messages`` empty but cached count > 0), so the r5
        # retain branch kept the phantom and re-opened the bug (maintainer
        # review 4584722701, supersedes the r5 cached-count signal). The
        # r6 signal probes ``Session.load(sid).messages`` directly — but
        # ONLY for ``state.db``-empty candidates (the small set; the
        # common live-row path takes the ``else`` branch and pays
        # nothing). Full ``Session.load`` is intentional (vs
        # ``load_metadata_only`` which zeroes the messages array at
        # ``api/models.py:1210``).
        for row in profile_rows:
            sid = str(row.get("session_id") or "").strip()
            if not sid:
                continue
            is_empty = sid in zero_message_sids
            is_tombstoned = sid in _tombstoned
            if is_empty:
                # ``state.db.messages`` is empty. Probe the sidecar JSON
                # for real messages — the cached ``message_count`` alone
                # is stale-positive on phantom rows (sidecar ``messages``
                # empty but cached count > 0) and would retain the very
                # phantom this feature exists to prune (maintainer review
                # 4584722701, supersedes the r5 cached-count signal).
                # Full ``Session.load`` is intentional (vs
                # ``load_metadata_only`` which zeros the messages array
                # at ``api/models.py:1210``); the common live-row path
                # pays nothing because it skips the load via the
                # ``else`` branch below.
                try:
                    from api.models import Session as _Session
                    _loaded = _Session.load(sid)
                    sidecar_has_messages = bool(
                        _loaded is not None and len(_loaded.messages or []) > 0
                    )
                except Exception:
                    logger.debug(
                        "Failed to load sidecar for webui orphan decision %s; "
                        "treating as empty for prune purposes",
                        sid,
                        exc_info=True,
                    )
                    sidecar_has_messages = False
            else:
                # ``state.db.messages`` is non-empty — the conversation is real.
                sidecar_has_messages = True
            if sidecar_has_messages:
                # Real transcript (state.db OR loaded sidecar). Retain; if
                # tombstoned, self-heal so it stops thrashing on recovery.
                if is_tombstoned:
                    self_healed_ids.add(sid)
                continue
            if not is_empty and is_tombstoned:
                # Case 1: SELF-HEAL — clear tombstone, keep row.
                self_healed_ids.add(sid)
            elif is_empty and is_tombstoned:
                # Case 2: still-empty tombstoned row stays hidden this
                # poll (do not add to missing_webui_orphan_ids — would
                # cycle through prune_session_from_index + record).
                still_hidden_ids.add(sid)
            elif is_empty and not is_tombstoned:
                # Case 3: new orphan.
                missing_webui_orphan_ids.add(sid)
            # Case 4 (not empty + not tombstoned): row has messages, retain.
    if self_healed_ids:
        for _sid in self_healed_ids:
            try:
                _clear_webui_zero_message_orphan_tombstone(_sid)
                logger.debug(
                    "self-heal: cleared webui zero-message orphan tombstone "
                    "for %s (state.db.messages now non-empty)",
                    _sid,
                )
            except Exception:
                logger.debug(
                    "Failed to clear webui zero-message orphan tombstone for %s",
                    _sid,
                    exc_info=True,
                )
        _diag("self_heal_webui_zero_message_orphan")
    if missing_webui_orphan_ids:
        for _sid in missing_webui_orphan_ids:
            try:
                prune_session_from_index(_sid)
                _diag("prune_orphaned_webui_zero_message")
            except Exception:
                logger.debug(
                    "Failed to prune orphaned webui zero-message row %s",
                    _sid,
                    exc_info=True,
                )
            # Tombstone the sid in a SECOND step so a tombstone-write failure
            # never blocks the prune itself (the prune still removes the row
            # from the sidebar; only the re-prune avoidance would degrade).
            try:
                _record_webui_zero_message_orphan_tombstone(_sid)
            except Exception:
                logger.debug(
                    "Failed to tombstone webui zero-message orphan %s",
                    _sid,
                    exc_info=True,
                )
    # Return rows excluding both the freshly-pruned orphans AND the
    # tombstoned rows that the probe confirmed are still empty (case 2).
    # Self-healed rows (case 1) and live rows (case 4) stay in the result.
    _hidden = missing_webui_orphan_ids | still_hidden_ids
    return [
        s for s in rows
        if str(s.get("session_id") or "").strip() not in _hidden
    ]


def _build_session_list_cache_payload(
    active_profile: str | None,
    all_profiles: bool,
    show_cli_sessions: bool,
    show_previous_messaging_sessions: bool,
    show_cron_sessions: bool,
    show_claude_code_sessions: bool = True,
    include_archived: bool = False,
    exclude_hidden: bool = False,
    visible_only: bool = False,
    show_webhook_sessions: bool = False,
    show_kanban_sessions: bool = False,
    source_filter: str | None = None,
    sidebar_source: str | None = None,
    archived_limit: int | None = None,
    archived_offset: int = 0,
    diag=None,
) -> dict:
    diag_stage = diag.stage if diag is not None else lambda *_a, **_k: None

    def _session_has_server_visible_messages(session: dict) -> bool:
        """Return True when a non-active sidebar row has a visibility signal.

        Keep this mirror of the non-active server filter narrow and local to
        route behavior so model-layer behavior remains unchanged.
        """
        if not isinstance(session, dict):
            return False
        if _numeric_count(session.get("message_count")) > 0:
            return True

        attention = session.get("attention")
        if not (isinstance(attention, dict) and attention.get("kind")):
            attention = _session_attention_summary(str(session.get("session_id") or ""))
        if isinstance(attention, dict) and attention.get("kind"):
            if _numeric_count(attention.get("count")) > 0:
                return True

        return bool(
            session.get("is_streaming")
            or session.get("active_stream_id")
            or session.get("pending_user_message")
            or session.get("has_pending_user_message")
        )

    def _all_sessions_for_sidebar():
        if _callable_accepts_kwarg(all_sessions, "include_lineage_metadata"):
            return all_sessions(diag=diag, include_lineage_metadata=False)
        # Focused tests and third-party callers sometimes monkeypatch
        # routes.all_sessions with the historical diag-only signature.
        return all_sessions(diag=diag)

    diag_stage("all_sessions")
    webui_sessions = _all_sessions_for_sidebar()
    diag_stage("reconcile_stale_stream_state")
    if _reconcile_stale_stream_state_for_session_rows(webui_sessions):
        diag_stage("all_sessions_after_stale_stream_reconcile")
        webui_sessions = _all_sessions_for_sidebar()
    diag_stage("normalize_cli_rows")
    show_cli_sessions = bool(show_cli_sessions)
    show_previous_messaging_sessions = bool(show_previous_messaging_sessions)
    show_cron_sessions = bool(show_cron_sessions)
    show_webhook_sessions = bool(show_webhook_sessions)
    show_kanban_sessions = bool(show_kanban_sessions)
    webui_sessions = [_normalize_sidebar_source_flags(s) for s in webui_sessions]
    if show_cli_sessions:
        diag_stage("get_cli_sessions")
        if _callable_accepts_kwarg(get_cli_sessions, "include_claude_code"):
            cli = get_cli_sessions(
                source_filter=source_filter,
                all_profiles=all_profiles,
                include_claude_code=show_claude_code_sessions,
            )
        else:
            # Focused tests sometimes monkeypatch routes.get_cli_sessions with
            # the historical two-keyword signature.
            cli = get_cli_sessions(
                source_filter=source_filter,
                all_profiles=all_profiles,
            )
        diag_stage("merge_cli_sessions")
        cli_by_id = {s["session_id"]: s for s in cli}
        # #3238/#4591: reconcile orphaned imported sidecars. When a CLI or
        # API-server session is clicked in WebUI it gets a WebUI-owned sidecar
        # that all_sessions() returns independently of state.db. If the user
        # later deletes the backing agent session outside WebUI, the sidecar is
        # never pruned and the stale row lingers in the sidebar forever (there
        # is no WebUI delete affordance for read-only imported rows).
        # Drop rows whose backing agent row is genuinely gone. We probe
        # state.db directly (agent_session_rows_existing) rather than trust
        # cli_by_id absence, because get_cli_sessions() caps at
        # CLI_VISIBLE_SESSION_LIMIT (20) — an existing session can fall
        # out of that window and look deleted. Native WebUI sessions
        # (source == "webui") that merely have a CLI ancestor are never
        # pruned by this path.
        #
        # #4985: parallel pass for native-WebUI rows that have a backing
        # agent row in state.db but zero messages (a `+`-click that opened a
        # row but the first turn never committed, or a sidebar nav that
        # opened then closed before any message landed). The same #3238
        # helper doesn't catch these because source == "webui" is excluded
        # above, and the WebUI delete affordance isn't exposed for them,
        # so they would otherwise linger forever. Inflight first-turn
        # safety is preserved by gating on `active_stream_id` (after
        # _reconcile_stale_stream_state has cleared stale stream ids).
        _orphan_probe_rows = []
        _kept_after_orphan_prune = []
        for s in webui_sessions:
            _sid = s.get("session_id")
            if (
                _sid
                and (is_cli_session_row(s) or _is_api_server_sidecar_row(s))
                and not _session_source_is_webui(s)
                and _sid not in cli_by_id
            ):
                _orphan_probe_rows.append(s)
            else:
                _kept_after_orphan_prune.append(s)
        if _orphan_probe_rows:
            rows_by_profile: dict[object, list[dict]] = defaultdict(list)
            for row in _orphan_probe_rows:
                rows_by_profile[row.get("profile")].append(row)
            missing_orphan_ids: set[str] = set()
            for profile_key, rows in rows_by_profile.items():
                probe_ids = [
                    str(row.get("session_id")).strip()
                    for row in rows
                    if str(row.get("session_id") or "").strip()
                ]
                existing = agent_session_rows_existing(
                    probe_ids,
                    profile=profile_key if isinstance(profile_key, str) and profile_key else None,
                )
                for row in rows:
                    _sid = str(row.get("session_id") or "").strip()
                    if _sid and _sid not in existing:
                        missing_orphan_ids.add(_sid)
            for s in _orphan_probe_rows:
                _sid = str(s.get("session_id") or "").strip()
                if _sid in missing_orphan_ids:
                    try:
                        prune_session_from_index(_sid)
                    except Exception:
                        logger.debug(
                            "Failed to prune orphaned agent sidecar %s",
                            _sid,
                            exc_info=True,
                        )
                    diag_stage("prune_orphaned_agent_sidecar")
                    continue
                _kept_after_orphan_prune.append(s)
        # #4985 second pass — probe state.db.messages for native-WebUI rows
        # that *survived* the upstream all_sessions() #1171 keep-filter (so
        # the row is TITLED or has a POSITIVE message_count, meaning it IS
        # shown in the sidebar — and the 404 click reported in #4985 happens),
        # BUT whose actual state.db.messages table is empty (the ground-truth
        # probe). This is the orphan shape #4985 actually describes: a row
        # that lingers VISIBLY in the sidebar because of a stale positive
        # message_count or a title set before the first turn committed.
        #
        # The (title!='Untitled' OR count>0) clause is the part that makes
        # this gate actually reach a row #1171 kept. Without it, the gate is
        # a no-op because all_sessions() at api/models.py:3892-3898 and
        # 3946-3952 has already stripped every (Untitled ∧ count==0 ∧
        # ¬active_stream_id ∧ ¬has_pending_user_message ∧ ¬worktree_path)
        # row before this point — making the earlier 6-condition gate a
        # no-op against the real pipeline (review IC_kwDOR1LuPM8AAAABHrkF1Q).
        #
        # Implementation lives in ``_prune_orphaned_webui_zero_message_sessions``
        # above so the prune runs in BOTH branches of this function
        # (``if show_cli_sessions:`` AND ``else:``). Established installs
        # have ``settings.show_cli_sessions`` pinned to False (per
        # api/config.py:7637-7648) and those are exactly the long-time
        # users who accumulated the #4985 404 orphans — without hoisting,
        # the ``else:`` branch silently skipped the prune
        # (review IC_kwDOR1LuPM8AAAABHsyFGg).
        #
        # Inflight / worktree / pending safety: same as before — any row
        # still carrying active_stream_id / has_pending_user_message /
        # worktree_path is never pruned, even if its messages table is
        # momentarily empty. _reconcile_stale_stream_state_for_session_rows
        # at line 2224 has already cleared stale stream ids above this point.
        webui_sessions = _prune_orphaned_webui_zero_message_sessions(
            _kept_after_orphan_prune,
            diag_stage=diag_stage,
        )
        for s in webui_sessions:
            meta = cli_by_id.get(s.get("session_id"))
            if not meta:
                continue
            if _is_messaging_session_record(meta):
                s.update(_merge_cli_sidebar_metadata(s, meta))
                if s.get("session_id") != meta.get("session_id"):
                    s["session_id"] = meta.get("session_id")
            else:
                for key in ("source_tag", "raw_source", "session_source", "source_label"):
                    if not s.get(key) and meta.get(key):
                        s[key] = meta[key]
        webui_sessions = [_normalize_sidebar_source_flags(s) for s in webui_sessions]
        # Apply the same CLI visibility semantics to imported local copies so
        # low-value imported artifacts do not leak into the sidebar.
        webui_sessions = [s for s in webui_sessions if is_cli_session_row_visible(s)]
        represented_webui_ids = set()
        for s in webui_sessions:
            represented_webui_ids.update(_session_lineage_ids(s))
        deduped_cli = _dedupe_cli_sidebar_sessions_for_api(
            cli,
            represented_webui_ids,
            show_cron_sessions=show_cron_sessions,
            show_webhook_sessions=show_webhook_sessions,
            show_kanban_sessions=show_kanban_sessions,
            source_filter=source_filter,
        )
    else:
        diag_stage("filter_webui_sessions")
        webui_sessions = [s for s in webui_sessions if not _is_cli_session_for_settings(s)]
        # #4985 second pass — see _prune_orphaned_webui_zero_message_sessions
        # for the gate predicate and the post-#1171-survivor rationale. The
        # prune MUST run here too: established installs have
        # ``settings.show_cli_sessions`` pinned to False
        # (api/config.py:7637-7648) and those are exactly the long-time
        # users who accumulated the 404 orphans — review
        # IC_kwDOR1LuPM8AAAABHsyFGg. Without this call the else branch
        # silently skipped the prune and the sidebar kept dangling rows.
        webui_sessions = _prune_orphaned_webui_zero_message_sessions(
            webui_sessions,
            diag_stage=diag_stage,
        )
        deduped_cli = []
    diag_stage("sort_sessions")
    merged = webui_sessions + deduped_cli
    merged.sort(
        key=lambda s: s.get("last_message_at") or s.get("updated_at", 0) or 0,
        reverse=True,
    )
    # ── Profile scoping (#1611) ────────────────────────────────────────
    # Default: filter to the active profile. ?all_profiles=1 opts into
    # the aggregate view used by the "All profiles" sidebar toggle.
    # The other_profile_count is always returned so the UI can render
    # the "Show N from other profiles" affordance without sending the
    # cross-profile rows by default.
    #
    # IMPORTANT: scope BEFORE _keep_latest_messaging_session_per_source.
    # _messaging_source_key is profile-blind (#1614 follow-up): if the
    # same Slack/Telegram identity has sessions in profiles A and B, a
    # profile-blind dedupe would discard the older one even when scoped
    # to its own profile, leaving that profile with zero rows for that
    # source. Filter first so the dedupe operates only within the active
    # profile's rows.
    diag_stage("profile_scope")
    if all_profiles:
        scoped = merged
        other_profile_count = 0
    else:
        scoped = [s for s in merged if _profiles_match(s.get("profile"), active_profile)]
        other_profile_count = 0 if _is_isolated_profile_mode() else len(merged) - len(scoped)
    diag_stage("messaging_dedupe")
    archived_scoped = _keep_latest_messaging_session_per_source(
        list(scoped),
        show_previous_messaging_sessions=show_previous_messaging_sessions,
    )
    visible_scoped = _keep_latest_messaging_session_per_source(
        [s for s in scoped if not s.get("archived")],
        show_previous_messaging_sessions=show_previous_messaging_sessions,
    )
    if show_cli_sessions:
        diag_stage("cli_cap")
        archived_scoped = _cap_recent_cli_sessions(archived_scoped, cli_cap=CLI_VISIBLE_SESSION_CAP)
        visible_scoped = _cap_recent_cli_sessions(visible_scoped, cli_cap=CLI_VISIBLE_SESSION_CAP)
    if visible_only:
        archived_scoped = [
            s for s in archived_scoped if _session_has_server_visible_messages(s)
        ]
        visible_scoped = [
            s for s in visible_scoped if _session_has_server_visible_messages(s)
        ]
    if exclude_hidden:
        archived_scoped = [s for s in archived_scoped if not s.get("default_hidden")]
        visible_scoped = [s for s in visible_scoped if not s.get("default_hidden")]
    archived_webui_count = sum(
        1 for s in archived_scoped
        if s.get("archived") and not _is_cli_session_for_settings(s)
    )
    archived_cli_count = sum(
        1 for s in archived_scoped
        if s.get("archived") and _is_cli_session_for_settings(s)
    )
    archived_count = archived_webui_count + archived_cli_count
    def _filter_sidebar_source(rows: list[dict]) -> list[dict]:
        if sidebar_source == "webui":
            return [s for s in rows if not _is_cli_session_for_settings(s)]
        if sidebar_source == "cli":
            return [s for s in rows if _is_cli_session_for_settings(s)]
        return list(rows)

    full_scoped_all_sources = archived_scoped if include_archived else visible_scoped
    webui_session_count = sum(
        1 for s in full_scoped_all_sources
        if not _is_cli_session_for_settings(s)
    )
    cli_session_count = sum(
        1 for s in full_scoped_all_sources
        if _is_cli_session_for_settings(s)
    )
    visible_scoped_filtered = _filter_sidebar_source(visible_scoped)
    archived_scoped_filtered = _filter_sidebar_source(archived_scoped)
    scoped = _filter_sidebar_source(full_scoped_all_sources)
    if include_archived and archived_limit is not None:
        try:
            normalized_archived_limit = max(0, int(archived_limit))
        except (TypeError, ValueError):
            normalized_archived_limit = None
        try:
            normalized_archived_offset = max(0, int(archived_offset or 0))
        except (TypeError, ValueError):
            normalized_archived_offset = 0
        if normalized_archived_limit is not None:
            visible_rows_for_page = [s for s in visible_scoped_filtered if not s.get("archived")]
            archived_rows_for_page = [s for s in archived_scoped_filtered if s.get("archived")]
            scoped = visible_rows_for_page + archived_rows_for_page[
                normalized_archived_offset: normalized_archived_offset + normalized_archived_limit
            ]
    sidebar_reference_sessions: list[dict] = []
    if not include_archived:
        sidebar_reference_sessions = _hidden_archived_sidebar_reference_sessions(
            visible_scoped_filtered,
            archived_scoped_filtered,
        )
    if not include_archived:
        diag_stage("filter_archived_sessions")
    diag_stage("visible_lineage_metadata")
    _enrich_sidebar_lineage_metadata(scoped)
    # Delegated subagent children (#5307) are view-only, owned by the delegate
    # runner. The model-layer batch overlay above has already applied the
    # authoritative source and view-only flags before this route runs.
    def _coerce_subagent_rows(_rows):
        for _r in _rows:
            if not isinstance(_r, dict):
                continue
            _src = (
                str(_r.get("source_tag") or _r.get("raw_source")
                    or _r.get("session_source") or _r.get("source") or "").strip().lower()
            )
            if _src == "subagent":
                _r["read_only"] = True
                _r["is_cli_session"] = False
    _coerce_subagent_rows(scoped)
    _coerce_subagent_rows(sidebar_reference_sessions)
    return {
        "sessions": [
            dict(s) if isinstance(s, dict) else {}
            for s in scoped
        ],
        "sidebar_reference_sessions": [
            dict(s) if isinstance(s, dict) else {}
            for s in sidebar_reference_sessions
        ],
        "cli_count": len(deduped_cli),
        "archived_count": archived_count,
        "archived_webui_count": archived_webui_count,
        "archived_cli_count": archived_cli_count,
        "webui_session_count": webui_session_count,
        "cli_session_count": cli_session_count,
        "include_archived": include_archived,
        "archived_limit": archived_limit,
        "archived_offset": archived_offset,
        "all_profiles": all_profiles,
        "active_profile": active_profile,
        "other_profile_count": other_profile_count,
        "settings": {
            "show_cli_sessions": show_cli_sessions,
            "show_previous_messaging_sessions": show_previous_messaging_sessions,
            "show_cron_sessions": show_cron_sessions,
            "show_claude_code_sessions": show_claude_code_sessions if show_cli_sessions else False,
            "show_webhook_sessions": show_webhook_sessions,
            "show_kanban_sessions": show_kanban_sessions,
        },
    }


def _session_list_payload_to_response(payload: dict) -> dict:
    safe_merged = []
    runtime_rows = _session_list_cache_overlay_runtime_rows(payload.get("sessions", []) or [])
    # Read the redaction setting ONCE for the whole response and thread it through
    # every row, instead of letting each row's _redact_text() re-read settings.json
    # from disk (per title). The _sidebar_session_response_item -> _redact_text(_enabled=...)
    # plumbing already exists; this wires the caller so the sidebar list path gets the
    # same read-once optimization redact_session_data() already uses. On a large list
    # this was the multi-second response_write stage in /api/sessions diagnostics. (#4662 Phase 3)
    # load_settings is imported at module scope (below); this function only runs at
    # request time, well after module load, so no lazy import is needed.
    try:
        _redact_enabled = bool(load_settings().get("api_redact_enabled", True))
    except Exception:
        logger.debug("Silent exception in _session_list_payload_to_response", exc_info=True)
        _redact_enabled = True  # fail safe: redact when settings are unreadable
    for s in runtime_rows:
        item = _sidebar_session_response_item(s, redact_enabled=_redact_enabled) if isinstance(s, dict) else {}
        safe_merged.append(item)
    safe_reference = []
    for s in payload.get("sidebar_reference_sessions", []) or []:
        item = _sidebar_session_response_item(s, redact_enabled=_redact_enabled) if isinstance(s, dict) else {}
        if item:
            item["_sidebar_reference_only"] = True
        safe_reference.append(item)
    response = {
        "sessions": safe_merged,
        "sidebar_reference_sessions": safe_reference,
        "cli_count": int(payload.get("cli_count", 0)),
        "archived_count": int(payload.get("archived_count", 0)),
        "archived_webui_count": int(payload.get("archived_webui_count", 0)),
        "archived_cli_count": int(payload.get("archived_cli_count", 0)),
        "include_archived": bool(payload.get("include_archived", False)),
        "all_profiles": bool(payload.get("all_profiles", False)),
        "active_profile": payload.get("active_profile"),
        "other_profile_count": int(payload.get("other_profile_count", 0)),
        "server_time": time.time(),
        "server_tz": time.strftime("%z"),
    }
    if "webui_session_count" in payload:
        response["webui_session_count"] = int(payload.get("webui_session_count", 0))
    if "cli_session_count" in payload:
        response["cli_session_count"] = int(payload.get("cli_session_count", 0))
    if payload.get("archived_limit") is not None:
        response["archived_limit"] = int(payload.get("archived_limit") or 0)
        response["archived_offset"] = int(payload.get("archived_offset") or 0)
    return response


def _hidden_archived_sidebar_reference_sessions(
    visible_rows: list[dict],
    archived_rows: list[dict],
) -> list[dict]:
    """Return hidden archived ancestors needed for client-side sidebar nesting.

    The default sidebar payload intentionally omits archived sessions. The
    browser still needs a tiny reference row for an archived parent/ancestor so
    `_attachChildSessionsToSidebarRows()` can suppress its visible child rows
    instead of rendering them as orphan top-level conversations (#4293).
    """
    archived_by_id = {
        str(row.get("session_id")): row
        for row in archived_rows
        if isinstance(row, dict) and row.get("archived") and row.get("session_id")
    }
    if not archived_by_id:
        return []

    references: list[dict] = []
    added: set[str] = set()
    visible_ids = {
        str(row.get("session_id"))
        for row in visible_rows
        if isinstance(row, dict) and row.get("session_id")
    }

    for row in visible_rows:
        if not isinstance(row, dict):
            continue
        parent_id = str(row.get("parent_session_id") or "").strip()
        seen: set[str] = set()
        while parent_id and parent_id not in seen:
            seen.add(parent_id)
            if parent_id in visible_ids:
                break
            parent = archived_by_id.get(parent_id)
            if not parent:
                break
            if parent_id not in added:
                references.append(parent)
                added.add(parent_id)
            parent_id = str(parent.get("parent_session_id") or "").strip()

    return references


def _get_cached_session_list_payload(
    *,
    key: tuple,
    builder,
    diag=None,
) -> dict:
    if diag is not None:
        try:
            diag.stage("session_list_cache_lookup")
        except Exception:
            logger.debug("Silent exception in _get_cached_session_list_payload", exc_info=True)
            pass

    cached, is_fresh = _session_list_cache_get(key, allow_stale=True)
    if cached is not None and is_fresh:
        if diag is not None:
            try:
                diag.stage("session_list_cache_hit")
            except Exception:
                logger.debug("Silent exception in _get_cached_session_list_payload", exc_info=True)
                pass
        return cached

    stale = cached  # now actually a stale payload when one exists, else None
    stale_reason = _session_list_cache_stale_reason(key) if stale is not None else None
    if stale is not None and stale_reason != "source":
        event, is_owner = _session_list_cache_claim_rebuild(key)
        if is_owner:
            if diag is not None:
                try:
                    diag.stage("session_list_cache_stale_background_rebuild")
                except Exception:
                    logger.debug("Silent exception in _get_cached_session_list_payload", exc_info=True)
                    pass

            def _rebuild_stale_session_list_cache():
                try:
                    rebuild_attempts = 0
                    while True:
                        invalidation_stamp = _session_list_cache_invalidation_stamp(key)
                        try:
                            payload = builder()
                        except Exception:
                            logger.exception(
                                "session list stale-cache background rebuild failed"
                            )
                            return
                        if _session_list_cache_invalidation_stamp(key) == invalidation_stamp:
                            _session_list_cache_set(key, payload)
                            return
                        rebuild_attempts += 1
                        if rebuild_attempts >= 3:
                            return
                finally:
                    _session_list_cache_done(key, event)

            try:
                thread = threading.Thread(
                    target=_rebuild_stale_session_list_cache,
                    name="session-list-cache-rebuild",
                    daemon=True,
                )
                thread.start()
            except Exception:
                logger.debug("Silent exception in _get_cached_session_list_payload", exc_info=True)
                _session_list_cache_done(key, event)
        elif diag is not None:
            try:
                diag.stage("session_list_cache_stale_return")
            except Exception:
                logger.debug("Silent exception in _get_cached_session_list_payload", exc_info=True)
                pass
        return stale

    event, is_owner = _session_list_cache_claim_rebuild(key)
    if is_owner:
        if diag is not None:
            try:
                diag.stage("session_list_cache_rebuild_owner")
            except Exception:
                logger.debug("Silent exception in _get_cached_session_list_payload", exc_info=True)
                pass
        try:
            rebuild_attempts = 0
            while True:
                invalidation_stamp = _session_list_cache_invalidation_stamp(key)
                payload = builder()
                if _session_list_cache_invalidation_stamp(key) == invalidation_stamp:
                    _session_list_cache_set(key, payload)
                    if diag is not None:
                        try:
                            diag.stage("session_list_cache_stored")
                        except Exception:
                            logger.debug("Silent exception in _get_cached_session_list_payload", exc_info=True)
                            pass
                    return payload
                rebuild_attempts += 1
                if diag is not None:
                    try:
                        diag.stage("session_list_cache_invalidated_during_rebuild")
                    except Exception:
                        logger.debug("Silent exception in _get_cached_session_list_payload", exc_info=True)
                        pass
                if rebuild_attempts >= 3:
                    return payload
        finally:
            _session_list_cache_done(key, event)

    if diag is not None:
        try:
            if stale is not None:
                diag.stage("session_list_cache_wait_stale")
            else:
                diag.stage("session_list_cache_wait")
        except Exception:
            logger.debug("Silent exception in _get_cached_session_list_payload", exc_info=True)
            pass

    if stale is not None:
        timeout = _SESSIONS_CACHE_STALE_WAIT_SECONDS
    else:
        timeout = _SESSIONS_CACHE_WAIT_SECONDS
    event.wait(timeout)

    latest, is_fresh = _session_list_cache_get(key, allow_stale=False)
    if latest is not None:
        if diag is not None:
            try:
                diag.stage("session_list_cache_wait_hit")
            except Exception:
                logger.debug("Silent exception in _get_cached_session_list_payload", exc_info=True)
                pass
        return latest

    if stale is not None:
        if diag is not None:
            try:
                diag.stage("session_list_cache_wait_stale_fallback")
            except Exception:
                logger.debug("Silent exception in _get_cached_session_list_payload", exc_info=True)
                pass
        return stale

    # Safety path if the owner died before storing anything.
    if diag is not None:
        try:
            diag.stage("session_list_cache_fallback_rebuild")
        except Exception:
            logger.debug("Silent exception in _get_cached_session_list_payload", exc_info=True)
            pass
    invalidation_stamp = _session_list_cache_invalidation_stamp(key)
    payload = builder()
    if _session_list_cache_invalidation_stamp(key) == invalidation_stamp:
        _session_list_cache_set(key, payload)
    return payload

from api.config import (
    STATE_DIR,
    SESSION_DIR,
    DEFAULT_WORKSPACE,
    DEFAULT_MODEL,
    SESSIONS,
    SESSIONS_MAX,
    LOCK,
    STREAMS,
    STREAMS_LOCK,
    CANCEL_FLAGS,
    STREAM_LAST_EVENT_ID,
    SERVER_START_TIME,
    _resolve_cli_toolsets,
    get_available_models,
    get_available_models_for_session_visit,
    _provider_is_known_or_configured,
    IMAGE_EXTS,
    MD_EXTS,
    MIME_MAP,
    MAX_FILE_BYTES,
    MAX_UPLOAD_BYTES,
    ACTIVE_RUNS,
    ACTIVE_RUNS_LOCK,
    register_stream_owner,
    register_session_writeback_owner,
    clear_session_writeback_owner_if_owned,
    stream_owner_session_id,
    unregister_stream_owner,
    CHAT_LOCK,
    _get_session_agent_lock,
    CUSTOM_MODELS_ENDPOINT_TIMEOUT_SECONDS,
    load_settings,
    persisted_speech_settings_keys,
    save_settings,
    SETTINGS_FILE,
    set_hermes_default_model,
    canonical_model_provider_lane,
    model_with_provider_context,
    get_reasoning_status,
    set_reasoning_display,
    set_reasoning_effort,
    create_stream_channel,
    get_config,
    get_webui_session_save_mode,
    get_config_snapshot,
    STREAM_GOAL_RELATED,
    PENDING_GOAL_CONTINUATION,
    _get_config_path,
    _load_yaml_config_file,
    _save_yaml_config_file,
    reload_config,
    get_config_for_profile_home,
    _cfg_lock,
    PENDING_BG_TASK_COMPLETIONS,
    _parse_provider_qualified_model_id,
)
from api import config as api_config
from api.helpers import (
    require,
    bad,
    safe_resolve,
    j,
    t,
    read_body,
    MAX_BODY_BYTES,
    _security_headers,
    _sanitize_error,
    redact_session_data,
    public_session_projection,
    strip_public_internal_fields,
    _redact_text,
    _CLIENT_DISCONNECT_ERRORS,
)
from api.agent_health import build_agent_health_payload
from api.gateway_chat import gateway_chat_config_status
from api.request_diagnostics import RequestDiagnostics
from api.system_health import build_system_health_payload


# A cancelled worker that stays in ACTIVE_RUNS longer than this is treated as
# stuck (e.g. blocked in C-level provider I/O and never reaching its finally).
# Once the cancel has been outstanding past this grace window, the run row can
# no longer protect the session's active_stream_id/pending_* from stale
# cleanup: _clear_stale_stream_state() clears them, and every delayed cancel
# finalizer (api/streaming.py _finalize_cancelled_turn) is generation-guarded
# under the session lock — it no-ops unless the session still points at the
# cancelled stream — so clearing early cannot clobber a newer turn (#6623).
_STALE_CANCELLED_RUN_GRACE_SECONDS = 60.0


def _cancelled_run_is_stale(run_entry) -> bool:
    """Return True when an ACTIVE_RUNS row belongs to a cancel that has been
    outstanding longer than the stale grace window.

    ``cancelled_at`` is stamped by cancel_stream() when it flips the run to
    phase="cancelling". ``started_at`` is accepted as a fallback anchor so runs
    cancelled before the stamp was introduced are still reclaimed eventually.
    """
    try:
        from api import config as _live_config

        return _live_config.active_run_cancel_is_stale(
            run_entry,
            grace_seconds=_STALE_CANCELLED_RUN_GRACE_SECONDS,
        )
    except Exception:
        logger.debug("Silent exception in _cancelled_run_is_stale", exc_info=True)
        return False


def _clear_stale_stream_state(session) -> bool:
    """Clear persisted streaming flags when the in-memory stream no longer exists.

    A server restart or worker crash can leave active_stream_id/pending_* in the
    session JSON while STREAMS is empty. The frontend then keeps reconnecting to
    a dead stream and shows a permanent running/thinking state.

    SAFETY (#1558): If ``session`` was loaded with ``metadata_only=True``, its
    ``messages`` array is empty by design and calling ``save()`` would
    atomically overwrite the on-disk JSON, wiping the conversation. In that
    case we re-load the full session before mutating, so the persisted
    write carries the real messages forward.
    """
    stream_id = getattr(session, "active_stream_id", None)
    if not stream_id:
        return False
    with STREAMS_LOCK:
        stream_alive = stream_id in STREAMS
    if stream_alive:
        return False
    try:
        from api import config as _live_config
        with _live_config.ACTIVE_RUNS_LOCK:
            worker_alive = stream_id in (_live_config.ACTIVE_RUNS or {})
    except Exception:
        logger.debug("Silent exception in _clear_stale_stream_state", exc_info=True)
        worker_alive = False
    if worker_alive:
        # #6623: a worker stuck in C-level I/O may never reach its finally to
        # unregister the run, so ACTIVE_RUNS could hold the row forever and
        # block stale cleanup indefinitely. A *cancelled* run (cancel_stream()
        # stamped phase="cancelling" + cancelled_at) that has not unwound past
        # the grace window is treated as stale — clear the session anyway. The
        # _stream_writeback_is_current() guard rejects any eventual writeback
        # from the stuck worker, so this cannot clobber a newer turn.
        try:
            with _live_config.ACTIVE_RUNS_LOCK:
                run_entry = dict((_live_config.ACTIVE_RUNS or {}).get(stream_id) or {})
        except Exception:
            logger.debug("Silent exception in _clear_stale_stream_state", exc_info=True)
            run_entry = {}
        if not _cancelled_run_is_stale(run_entry):
            logger.debug(
                "_clear_stale_stream_state: stream %s for session %s missing SSE channel "
                "but worker bookkeeping is still active; deferring stale cleanup",
                stream_id,
                getattr(session, "session_id", "?"),
            )
            return False
        logger.info(
            "_clear_stale_stream_state: stream %s for session %s missing SSE channel and "
            "cancelled run is stale (cancelled_at=%s); clearing stale stream state (#6623)",
            stream_id,
            getattr(session, "session_id", "?"),
            run_entry.get("cancelled_at"),
        )
    grace_seconds = 30.0
    try:
        from api.models import _REPAIR_STALE_PENDING_GRACE_SECONDS
        grace_seconds = float(_REPAIR_STALE_PENDING_GRACE_SECONDS)
        pending_started_at = getattr(session, "pending_started_at", None)
        pending_age = time.time() - float(pending_started_at) if pending_started_at else None
    except Exception:
        logger.debug("Silent exception in _clear_stale_stream_state", exc_info=True)
        pending_age = None
    if (
        getattr(session, "pending_user_message", None)
        and pending_age is not None
        and pending_age < grace_seconds
    ):
        logger.debug(
            "_clear_stale_stream_state: stream %s for session %s missing SSE channel "
            "but pending turn is %.1fs old; waiting for %.1fs stale-repair grace",
            stream_id,
            getattr(session, "session_id", "?"),
            pending_age,
            grace_seconds,
        )
        return False

    # ── #1558 P0 safety: if we were handed a metadata-only stub, reload the
    # full session before touching persisted state. The original
    # metadata-only object is left untouched so the caller's read path is
    # unaffected.
    original_stub = session  # SHOULD-FIX #1 (Opus): keep reference so we can
                             # patch the caller's in-memory copy after a
                             # successful clear, avoiding one ghost SSE
                             # reconnect on the very next /api/session GET.
    if getattr(session, "_loaded_metadata_only", False):
        try:
            from api.models import get_session as _get_session
            session = _get_session(session.session_id, metadata_only=False)
        except Exception:
            # If we cannot upgrade to a full load (file gone, decode error,
            # etc.) bail without clearing — better to leave a stale
            # active_stream_id than to wipe the conversation.
            logger.warning(
                "_clear_stale_stream_state: refused to clear stale stream %s "
                "for session %s — full reload failed and we will not save a "
                "metadata-only stub. See #1558.",
                stream_id, getattr(session, "session_id", "?"),
            )
            return False
        if session is None:
            return False
        # The full-load path may have already repaired stale pending fields
        # via _repair_stale_pending(); only re-assert if still set.
        if not getattr(session, "active_stream_id", None):
            # Patch the caller's stub so its read path also sees the cleared
            # field (matches the Opus SHOULD-FIX #1 — without this, /api/session
            # would briefly return the stale active_stream_id and the frontend
            # would attempt one ghost SSE reconnect before recovering).
            try:
                original_stub.active_stream_id = None
                if hasattr(original_stub, "pending_user_message"):
                    original_stub.pending_user_message = None
                if hasattr(original_stub, "pending_attachments"):
                    original_stub.pending_attachments = []
                if hasattr(original_stub, "pending_started_at"):
                    original_stub.pending_started_at = None
                if hasattr(original_stub, "pending_user_source"):
                    original_stub.pending_user_source = None
            except Exception:
                logger.debug("Silent exception in _clear_stale_stream_state", exc_info=True)
                pass
            return False

    # ── #1533 race fix: acquire the per-session lock and re-read
    # active_stream_id under it. A concurrent chat_start may have already
    # registered a new stream after our STREAMS_LOCK check above; in that
    # case we must NOT clobber its session.active_stream_id.
    with _get_session_agent_lock(session.session_id):
        if getattr(session, "active_stream_id", None) != stream_id:
            return False
        if getattr(session, "pending_user_message", None):
            try:
                from api.models import _apply_core_sync_or_error_marker, _get_profile_home
                profile_home = _get_profile_home(getattr(session, "profile", None))
                core_path = profile_home / "sessions" / f"session_{session.session_id}.json"
                repaired = _apply_core_sync_or_error_marker(
                    session,
                    core_path,
                    stream_id_for_recheck=stream_id,
                    touch_updated_at=False,
                )
            except Exception:
                logger.exception(
                    "_clear_stale_stream_state: failed to repair stale pending stream %s "
                    "for session %s",
                    stream_id, getattr(session, "session_id", "?"),
                )
                repaired = False
            if repaired:
                if original_stub is not session:
                    try:
                        original_stub.active_stream_id = None
                        if hasattr(original_stub, "pending_user_message"):
                            original_stub.pending_user_message = None
                        if hasattr(original_stub, "pending_attachments"):
                            original_stub.pending_attachments = []
                        if hasattr(original_stub, "pending_started_at"):
                            original_stub.pending_started_at = None
                        if hasattr(original_stub, "pending_user_source"):
                            original_stub.pending_user_source = None
                    except Exception:
                        logger.debug("Silent exception in _clear_stale_stream_state", exc_info=True)
                        pass
                return True
            if getattr(session, "active_stream_id", None) != stream_id:
                return False
        _materialize_pending_user_turn_before_error(session)
        session.active_stream_id = None
        if hasattr(session, "pending_user_message"):
            session.pending_user_message = None
        if hasattr(session, "pending_attachments"):
            session.pending_attachments = []
        if hasattr(session, "pending_started_at"):
            session.pending_started_at = None
        if hasattr(session, "pending_user_source"):
            session.pending_user_source = None
        try:
            # Runtime cleanup is not user activity; do not bubble old sessions
            # to the top of the sidebar just because a stale stream flag was
            # repaired during a read/list path.
            session.save(touch_updated_at=False)
        except Exception:
            logger.exception(
                "_clear_stale_stream_state: save() failed for session %s",
                getattr(session, "session_id", "?"),
            )
    # Patch the caller's stub (if different from the full-load object) so
    # its in-memory active_stream_id matches what just got persisted.
    if original_stub is not session:
        try:
            original_stub.active_stream_id = None
            if hasattr(original_stub, "pending_user_message"):
                original_stub.pending_user_message = None
            if hasattr(original_stub, "pending_attachments"):
                original_stub.pending_attachments = []
            if hasattr(original_stub, "pending_started_at"):
                original_stub.pending_started_at = None
            if hasattr(original_stub, "pending_user_source"):
                original_stub.pending_user_source = None
        except Exception:
            logger.debug("Silent exception in _clear_stale_stream_state", exc_info=True)
            pass
    return True


def _run_journal_status_payload(summary: dict, *, active: bool = False) -> dict:
    terminal = bool(summary.get("terminal"))
    terminal_state = summary.get("terminal_state")
    if not active and not terminal:
        terminal_state = "lost-worker-bookkeeping"
    return {
        "session_id": summary.get("session_id"),
        "run_id": summary.get("run_id"),
        "last_seq": summary.get("last_seq"),
        "last_event_id": summary.get("last_event_id"),
        "last_event": summary.get("last_event"),
        "terminal": terminal,
        "terminal_state": terminal_state,
    }


_RUN_JOURNAL_TOOL_ID_KEYS = ("tid", "id", "tool_call_id", "tool_use_id", "call_id")


def _run_journal_snapshot_tool_id(payload: dict | None) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in _RUN_JOURNAL_TOOL_ID_KEYS:
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    return ""


def _truncate_journal_snapshot_value(value, *, limit: int = 120):
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + "..."
    if isinstance(value, dict):
        return {str(k): _truncate_journal_snapshot_value(v, limit=limit) for k, v in value.items()}
    if isinstance(value, list):
        return [_truncate_journal_snapshot_value(v, limit=limit) for v in value[:20]]
    return value


def _run_journal_snapshot_recovery_args(payload: dict | None):
    if not isinstance(payload, dict):
        return {}
    args = payload.get("args")
    return bound_run_journal_snapshot_args(args)


def _run_journal_snapshot_arg_detail_score(value) -> int:
    if value in (None, "", [], {}):
        return 0
    if isinstance(value, str):
        return len(value)
    if isinstance(value, dict):
        return sum(
            len(str(key)) + _run_journal_snapshot_arg_detail_score(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return sum(_run_journal_snapshot_arg_detail_score(item) for item in value)
    return 1


def _run_journal_snapshot_merge_args(existing, incoming):
    if not incoming:
        return existing, False
    if not isinstance(existing, dict) or not existing:
        return incoming, True
    if not isinstance(incoming, dict):
        return existing, False
    merged = copy.deepcopy(existing)
    changed = False
    for key, value in incoming.items():
        current = merged.get(key)
        if key not in merged or (
            _run_journal_snapshot_arg_detail_score(value)
            > _run_journal_snapshot_arg_detail_score(current)
        ):
            merged[key] = value
            changed = True
    return merged, changed


def _run_journal_envelope_run_id_result(event: dict) -> tuple[str | None, bool]:
    raw_run_id = event.get("run_id")
    if raw_run_id is None:
        return None, False
    if not isinstance(raw_run_id, str):
        return None, True
    run_id = raw_run_id.strip()
    if not run_id:
        return None, True
    raw_event_id = event.get("event_id")
    event_id = str(raw_event_id or "").strip()
    if event_id:
        event_run_id, event_seq = _shared_parse_run_journal_event_id(event_id)
        if event_run_id and event_seq is not None and event_run_id != run_id:
            return None, True
    return run_id, False


def _run_journal_snapshot_event_id_for_run(
    event: dict,
    run_id: str,
    event_seq: int,
) -> str | None:
    raw_event_id = event.get("event_id")
    event_id = str(raw_event_id or "").strip()
    if event_id:
        event_run_id, parsed_seq = _shared_parse_run_journal_event_id(event_id)
        if event_run_id == run_id and parsed_seq is not None:
            return event_id
    return f"{run_id}:{event_seq}" if event_seq else None


def _run_journal_live_snapshot(stream_id: str | None, *, handler=None) -> dict | None:
    stream_id = str(stream_id or "").strip()
    if not stream_id:
        return None
    if handler is not None and not _stream_id_visible_to_request_profile(
        handler,
        stream_id,
        emit_error=False,
    ):
        return None
    summary = find_run_summary(stream_id)
    if not summary:
        return None
    session_id = str(summary.get("session_id") or "")
    if not session_id:
        return None
    journal = read_run_events(session_id, stream_id)
    events = [event for event in (journal.get("events") or []) if isinstance(event, dict)]
    if not events:
        return None
    event_run_ids: set[str] = set()
    malformed_envelope_run_id = False
    for event in events:
        event_run_id, event_run_id_malformed = _run_journal_envelope_run_id_result(event)
        if event_run_id is not None:
            event_run_ids.add(event_run_id)
        if event_run_id_malformed:
            malformed_envelope_run_id = True
    # The event envelope is the durable identity authority. Older summaries
    # are keyed by the transport id, so only use that fallback when the journal
    # does not provide one unambiguous run id.
    run_id = (
        next(iter(event_run_ids))
        if not malformed_envelope_run_id and len(event_run_ids) == 1
        else str(summary.get("run_id") or stream_id).strip()
    )

    assistant_text = ""
    reasoning_text = ""
    messages: list[dict] = []
    tool_calls: list[dict] = []
    activity_burst_anchors: list[dict] = []
    current_activity_burst_id = 0
    fresh_segment = True
    last_ts = None
    reasoning_first_tool_count: int | None = None

    def mark_boundary() -> int:
        nonlocal current_activity_burst_id
        text_end = len(assistant_text)
        if text_end <= 0:
            return current_activity_burst_id
        last_end = max(
            [int(anchor.get("textEnd") or 0) for anchor in activity_burst_anchors]
            or [0]
        )
        if text_end > last_end:
            current_activity_burst_id += 1
            activity_burst_anchors.append(
                {"id": current_activity_burst_id, "textEnd": text_end}
            )
        return current_activity_burst_id

    def update_completed_tool(payload: dict) -> None:
        tool_id = _run_journal_snapshot_tool_id(payload)
        name = str(payload.get("name") or "").strip()
        for call in reversed(tool_calls):
            if call.get("done"):
                continue
            call_id = _run_journal_snapshot_tool_id(call)
            if (tool_id and call_id == tool_id) or (not tool_id and name and call.get("name") == name):
                call["done"] = True
                merged_args, args_changed = _run_journal_snapshot_merge_args(
                    call.get("args"),
                    _run_journal_snapshot_recovery_args(payload),
                )
                if args_changed:
                    call["args"] = merged_args
                if payload.get("preview") is not None:
                    call["snippet"] = str(payload.get("preview") or "")
                    call["preview"] = call.get("preview") or call["snippet"]
                if payload.get("duration") is not None:
                    call["duration"] = payload.get("duration")
                if payload.get("is_error") is not None:
                    call["is_error"] = bool(payload.get("is_error"))
                return

        if not name or name == "clarify":
            return
        call = {
            "name": name,
            "preview": str(payload.get("preview") or ""),
            "snippet": str(payload.get("preview") or ""),
            "args": _run_journal_snapshot_recovery_args(payload),
            "done": True,
            "_live": True,
            "_journal_snapshot": True,
            "_journal_stream_id": stream_id,
        }
        tool_id = _run_journal_snapshot_tool_id(payload)
        if tool_id:
            call["tid"] = tool_id
        for key in _RUN_JOURNAL_TOOL_ID_KEYS:
            if payload.get(key):
                call[key] = str(payload.get(key))
        if current_activity_burst_id:
            call["activityBurstId"] = current_activity_burst_id
            call["activitySegmentSeq"] = current_activity_burst_id
        tool_calls.append(call)

    def reasoning_echo_tail_matches(text: str) -> bool:
        candidate = _compact_for_echo_compare(text)
        if not candidate:
            return False
        return _compact_for_echo_compare(reasoning_text).endswith(candidate)

    def strip_reasoning_echo_tail(text: str) -> bool:
        nonlocal reasoning_text, reasoning_first_tool_count
        next_reasoning, did_remove = _strip_compact_echo_suffix(reasoning_text, text)
        if did_remove:
            reasoning_text = next_reasoning
            if not _compact_for_echo_compare(reasoning_text):
                reasoning_first_tool_count = None
        return did_remove

    for event in events:
        event_name = str(event.get("event") or event.get("type") or "")
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        last_ts = event.get("created_at", last_ts)
        if event_name == "token":
            text = str(payload.get("text") or "")
            if text:
                assistant_text += text
                fresh_segment = False
            continue
        if event_name == "reasoning":
            text = str(payload.get("text") or "")
            if text and reasoning_first_tool_count is None:
                reasoning_first_tool_count = len(tool_calls)
            reasoning_text += text
            continue
        if event_name == "interim_assistant":
            visible = str(payload.get("text") or "").strip()
            if visible:
                if payload.get("reasoning_echo") or reasoning_echo_tail_matches(visible):
                    strip_reasoning_echo_tail(visible)
                if payload.get("already_streamed"):
                    if not assistant_text:
                        assistant_text = visible
                else:
                    assistant_text = f"{assistant_text}\n\n{visible}" if assistant_text else visible
                mark_boundary()
                fresh_segment = True
            continue
        if event_name == "tool":
            name = str(payload.get("name") or "").strip()
            if not name or name == "clarify":
                continue
            boundary_id = mark_boundary()
            tool_id = _run_journal_snapshot_tool_id(payload)
            call = {
                "name": name,
                "preview": str(payload.get("preview") or ""),
                "args": _run_journal_snapshot_recovery_args(payload),
                "done": False,
                "_live": True,
                "_journal_snapshot": True,
                "_journal_stream_id": stream_id,
            }
            if tool_id:
                call["tid"] = tool_id
            for key in _RUN_JOURNAL_TOOL_ID_KEYS:
                if payload.get(key):
                    call[key] = str(payload.get(key))
            if boundary_id:
                call["activityBurstId"] = boundary_id
                call["activitySegmentSeq"] = boundary_id
            tool_calls.append(call)
            fresh_segment = True
            continue
        if event_name == "tool_complete":
            update_completed_tool(payload)
            fresh_segment = True

    if assistant_text or reasoning_text:
        message = {
            "role": "assistant",
            "content": assistant_text,
            "_live": True,
            "_journal_snapshot": True,
            "_journal_stream_id": stream_id,
        }
        if reasoning_text:
            message["reasoning"] = reasoning_text
        if last_ts is not None:
            message["_ts"] = last_ts
        messages.append(message)

    def scene_group(segment_seq: int | None = None, burst_id: int | None = None) -> dict:
        group: dict = {}
        if segment_seq:
            group["group_key"] = f"segment:{segment_seq}"
            group["activity_segment_seq"] = segment_seq
        elif burst_id:
            group["group_key"] = f"burst:{burst_id}"
            group["activity_burst_id"] = burst_id
        else:
            group["group_key"] = "activity:0"
        if burst_id:
            group["activity_burst_id"] = burst_id
        return group

    def scene_prose_row(text: str, *, burst_id: int | None, segment_seq: int, status: str) -> dict | None:
        clean = str(text or "").strip()
        if not clean:
            return None
        local_id = f"live-prose:{stream_id}:{segment_seq}"
        return {
            "row_id": local_id,
            "order_index": len(anchor_activity_rows),
            "kind": "process_prose",
            "role": "prose",
            "display_hint": "main_prose",
            "display_hints": {
                "compact_worklog": "main_prose",
                "transparent_stream": "chronological_activity",
            },
            "source_event_type": "token",
            "event_id": None,
            "local_id": local_id,
            "run_id": run_id,
            "stream_id": stream_id,
            "seq": None,
            "status": status,
            "created_at": last_ts,
            "identity": {
                "event_id": None,
                "local_id": local_id,
                "run_id": run_id,
                "stream_id": stream_id,
                "seq": None,
            },
            "group": scene_group(segment_seq, burst_id),
            "text": clean,
            "thinking": None,
            "tool_call_id": "",
            "tool": None,
            "payload": {
                "text": clean,
                "activitySegmentSeq": segment_seq,
                "activityBurstId": burst_id or 0,
            },
        }

    def scene_thinking_row(text: str, *, status: str) -> dict | None:
        clean = str(text or "").strip()
        if not clean:
            return None
        preview = " ".join(clean.split())
        local_id = f"live-thinking:{stream_id}:1"
        return {
            "row_id": local_id,
            "order_index": len(anchor_activity_rows),
            "kind": "reasoning",
            "role": "thinking",
            "display_hint": "collapsed_thinking",
            "display_hints": {
                "compact_worklog": "collapsed_thinking",
                "transparent_stream": "chronological_activity",
            },
            "source_event_type": "reasoning",
            "event_id": None,
            "local_id": local_id,
            "run_id": run_id,
            "stream_id": stream_id,
            "seq": None,
            "status": status,
            "created_at": last_ts,
            "identity": {
                "event_id": None,
                "local_id": local_id,
                "run_id": run_id,
                "stream_id": stream_id,
                "seq": None,
            },
            "group": scene_group(),
            "text": clean,
            "thinking": {
                "text": clean,
                "preview": (preview[:177] + "...") if len(preview) > 180 else preview,
                "dedupe_key": f"thinking:{preview.lower()}" if preview else "",
            },
            "tool_call_id": "",
            "tool": None,
            "payload": {
                "text": clean,
            },
        }

    def scene_tool_row(call: dict, *, fallback_order: int) -> dict | None:
        if not isinstance(call, dict):
            return None
        name = str(call.get("name") or "").strip()
        if not name:
            return None
        tool_id = _run_journal_snapshot_tool_id(call)
        burst_id = int(call.get("activityBurstId") or 0) or None
        segment_seq = int(call.get("activitySegmentSeq") or burst_id or 0) or None
        status = "error" if call.get("is_error") else ("completed" if call.get("done") else "running")
        row_id = f"tool:{tool_id or name}:{fallback_order}"
        args = call.get("args") if isinstance(call.get("args"), dict) else {}
        preview = str(call.get("preview") or "")
        snippet = str(call.get("snippet") or "")
        tool = {
            "id": tool_id,
            "tid": tool_id,
            "name": name,
            "args": args,
            "preview": preview,
            "snippet": snippet,
            "done": bool(call.get("done")),
            "is_error": bool(call.get("is_error")),
            "duration": call.get("duration"),
            "started_at": call.get("started_at"),
        }
        payload = {
            "name": name,
            "args": args,
            "preview": preview,
            "snippet": snippet,
            "tid": tool_id,
            "id": tool_id,
            "is_error": bool(call.get("is_error")),
            "duration": call.get("duration"),
            "activitySegmentSeq": segment_seq,
            "activityBurstId": burst_id or 0,
        }
        return {
            "row_id": row_id,
            "order_index": len(anchor_activity_rows),
            "kind": "tool_completed" if call.get("done") else "tool_started",
            "role": "tool",
            "display_hint": "tool_row",
            "display_hints": {
                "compact_worklog": "tool_row",
                "transparent_stream": "chronological_activity",
            },
            "source_event_type": "tool_complete" if call.get("done") else "tool",
            "event_id": None,
            "local_id": tool_id or row_id,
            "run_id": run_id,
            "stream_id": stream_id,
            "seq": None,
            "status": status,
            "created_at": last_ts,
            "identity": {
                "event_id": None,
                "local_id": tool_id or row_id,
                "run_id": run_id,
                "stream_id": stream_id,
                "seq": None,
            },
            "group": scene_group(segment_seq, burst_id),
            "text": snippet or preview,
            "thinking": None,
            "tool_call_id": tool_id,
            "tool": tool,
            "payload": payload,
        }

    anchor_activity_rows: list[dict] = []
    thinking_row_inserted = False
    tool_rows_rendered = 0

    def append_thinking_row(*, force: bool = False) -> None:
        nonlocal thinking_row_inserted
        if thinking_row_inserted:
            return
        if not force and reasoning_first_tool_count and tool_rows_rendered < reasoning_first_tool_count:
            return
        row = scene_thinking_row(reasoning_text, status="running")
        if not row:
            return
        row["order_index"] = len(anchor_activity_rows)
        anchor_activity_rows.append(row)
        thinking_row_inserted = True

    tool_rows_by_burst: dict[int, list[tuple[int, dict]]] = {}
    ungrouped_tool_rows: list[tuple[int, dict]] = []
    for order, call in enumerate(tool_calls):
        burst_id = int(call.get("activityBurstId") or 0) if isinstance(call, dict) else 0
        row = scene_tool_row(call, fallback_order=order)
        if not row:
            continue
        if burst_id:
            tool_rows_by_burst.setdefault(burst_id, []).append((order, row))
        else:
            ungrouped_tool_rows.append((order, row))

    consumed_tools: set[int] = set()
    text_start = 0
    sorted_anchors = sorted(
        [
            anchor
            for anchor in activity_burst_anchors
            if int(anchor.get("textEnd") or 0) > 0
        ],
        key=lambda anchor: int(anchor.get("textEnd") or 0),
    )
    for anchor in sorted_anchors:
        burst_id = int(anchor.get("id") or 0) or None
        text_end = min(len(assistant_text), int(anchor.get("textEnd") or 0))
        segment_seq = burst_id or (len(anchor_activity_rows) + 1)
        prose = scene_prose_row(
            assistant_text[text_start:text_end],
            burst_id=burst_id,
            segment_seq=segment_seq,
            status="completed",
        )
        if prose:
            anchor_activity_rows.append(prose)
            append_thinking_row()
        for order, row in tool_rows_by_burst.get(burst_id or 0, []):
            row["order_index"] = len(anchor_activity_rows)
            anchor_activity_rows.append(row)
            consumed_tools.add(order)
            tool_rows_rendered += 1
            append_thinking_row()
        text_start = max(text_start, text_end)

    if text_start < len(assistant_text):
        segment_seq = max(len(sorted_anchors) + 1, 1)
        tail = scene_prose_row(
            assistant_text[text_start:],
            burst_id=None,
            segment_seq=segment_seq,
            status="running",
        )
        if tail:
            anchor_activity_rows.append(tail)
            append_thinking_row()

    if not assistant_text:
        append_thinking_row()

    for order, row in sorted(ungrouped_tool_rows, key=lambda item: item[0]):
        if order in consumed_tools:
            continue
        row["order_index"] = len(anchor_activity_rows)
        anchor_activity_rows.append(row)
        tool_rows_rendered += 1
        append_thinking_row()

    append_thinking_row(force=True)

    # Keep a live anchor shell during session-switch replay even before the
    # journal has projected visible prose or tool rows from the first events.
    if not anchor_activity_rows and events:
        anchor_activity_rows.append(
            {
                "row_id": f"lifecycle:{stream_id}:running",
                "order_index": 0,
                "kind": "lifecycle_status",
                "role": "lifecycle",
                "display_hint": "quiet_lifecycle_row",
                "display_hints": {
                    "compact_worklog": "quiet_lifecycle_row",
                    "transparent_stream": "chronological_activity",
                },
                "source_event_type": "runtime_journal_snapshot",
                "event_id": None,
                "local_id": f"lifecycle:{stream_id}:running",
                "run_id": run_id,
                "stream_id": stream_id,
                "seq": None,
                "status": "running",
                "created_at": last_ts,
                "identity": {
                    "event_id": None,
                    "local_id": f"lifecycle:{stream_id}:running",
                    "run_id": run_id,
                    "stream_id": stream_id,
                    "seq": None,
                },
                "group": scene_group(),
                "text": "Working",
                "thinking": None,
                "tool_call_id": "",
                "tool": None,
                "payload": {},
            }
        )

    visible_anchors = [
        anchor
        for anchor in activity_burst_anchors
        if int(anchor.get("textEnd") or 0) < len(assistant_text)
    ]
    segment_count = len(visible_anchors) + (1 if assistant_text else 0)
    current_live_segment_seq = max(segment_count, len(activity_burst_anchors), 0)
    try:
        summary_last_seq = max(0, int(summary.get("last_seq") or 0))
    except (TypeError, ValueError):
        summary_last_seq = 0
    try:
        event_last_seq = max(0, int(events[-1].get("seq") or 0))
    except (TypeError, ValueError):
        event_last_seq = 0
    if event_last_seq >= summary_last_seq:
        last_seq = event_last_seq
        last_event_id = _run_journal_snapshot_event_id_for_run(
            events[-1],
            run_id,
            event_last_seq,
        ) or summary.get("last_event_id")
    else:
        last_seq = summary_last_seq
        last_event_id = summary.get("last_event_id") or events[-1].get("event_id")

    # Keep returning a live snapshot even when the journal has events but no
    # projected message/tool rows yet. The frontend treats the empty activity
    # scene as "nothing renderable yet" while preserving the live cursor.
    return {
        "session_id": session_id,
        "stream_id": stream_id,
        "last_seq": last_seq,
        "last_event_id": last_event_id,
        "event_count": len(events),
        "fresh_segment": fresh_segment,
        "messages": messages,
        "tool_calls": tool_calls,
        "last_assistant_text": assistant_text,
        "last_reasoning_text": reasoning_text,
        "activity_burst_anchors": activity_burst_anchors,
        "current_activity_burst_id": current_activity_burst_id,
        "current_live_segment_seq": current_live_segment_seq,
        "anchor_activity_scene": {
            "version": "activity_scene_v1",
            "mode": "compact_worklog",
            "identity": {
                "session_id": session_id,
                "stream_id": stream_id,
                "run_id": run_id,
                "source_message_refs": [],
            },
            "lifecycle": {
                "status": "running",
                "terminal_state": None,
            },
            "final_answer": "",
            "final_message_ref": None,
            "terminal_state": None,
            "activity_rows": anchor_activity_rows,
        },
    }


def _runtime_journal_snapshot_for_session_payload(snapshot: dict | None) -> dict | None:
    """Return the non-mutating, display-equivalent transport form of a live snapshot.

    The canonical recovery snapshot intentionally keeps fallback representations.
    Sending all of them duplicates each tool result in top-level ``tool_calls``
    and row ``text``, ``tool``, and ``payload`` fields. Keep one authoritative
    display source for each value at the HTTP boundary; the durable journal and
    the canonical in-process snapshot remain unchanged.
    """
    if not isinstance(snapshot, dict):
        return snapshot

    projected = dict(snapshot)
    # The frontend reconstructs this single live assistant row from the
    # authoritative last_* strings when messages is empty. Preserve the row's
    # timestamp separately so the synthesized message keeps stable identity.
    live_messages = [
        message
        for message in (projected.get("messages") or [])
        if isinstance(message, dict) and message.get("role") == "assistant"
    ]
    if live_messages:
        last_message_ts = live_messages[-1].get("_ts")
        if last_message_ts is not None:
            projected["last_message_ts"] = last_message_ts
    if projected.get("last_assistant_text") or projected.get("last_reasoning_text"):
        projected["messages"] = []

    scene = projected.get("anchor_activity_scene")
    compact_calls = []
    for raw_call in projected.get("tool_calls") or []:
        if not isinstance(raw_call, dict):
            continue
        call = dict(raw_call)
        if call.get("preview") == call.get("snippet"):
            call.pop("preview", None)
        compact_calls.append(call)
    # Retain the compact top-level list as the degraded-render fallback. The
    # Anchor scene is normally authoritative, but session reattach deliberately
    # falls back to INFLIGHT.toolCalls when scene rendering is unavailable.
    projected["tool_calls"] = compact_calls

    if not isinstance(scene, dict):
        return projected
    compact_scene = dict(scene)
    compact_rows = []
    row_keys = (
        "row_id", "local_id", "kind", "role", "source_event_type", "status",
        "created_at", "group", "text", "thinking", "tool_call_id", "tool",
    )
    for raw_row in scene.get("activity_rows") or []:
        if not isinstance(raw_row, dict):
            continue
        row = {
            key: raw_row[key]
            for key in row_keys
            if key in raw_row and raw_row[key] not in (None, "")
        }
        if str(raw_row.get("role") or "") == "tool":
            tool = raw_row.get("tool") if isinstance(raw_row.get("tool"), dict) else {}
            compact_tool = dict(tool)
            if compact_tool.get("preview") == compact_tool.get("snippet"):
                compact_tool.pop("preview", None)
            if compact_tool.get("tid") == compact_tool.get("id"):
                compact_tool.pop("tid", None)
            row["tool"] = compact_tool
            # Tool cards consume args/snippet from row.tool. row.payload and
            # row.text are byte-for-byte fallbacks of those same values.
            row.pop("text", None)
        else:
            thinking = row.get("thinking")
            if isinstance(thinking, dict) and thinking.get("text") == row.get("text"):
                row.pop("thinking", None)
        compact_rows.append(row)
    compact_scene["activity_rows"] = compact_rows
    projected["anchor_activity_scene"] = compact_scene
    return projected


def _ensure_full_session_before_mutation(sid: str, session):
    """Reload cached metadata-only sessions before mutating persisted fields.

    Session.save() intentionally refuses metadata-only stubs (#1558) because
    their messages list is empty by design. Mutation routes that save session
    metadata must upgrade the cached stub first so they do not trip that guard
    or risk writing an incomplete object.
    """
    if not getattr(session, "_loaded_metadata_only", False):
        return session
    full_session = Session.load(sid)
    if full_session is None:
        raise KeyError(sid)
    with LOCK:
        SESSIONS[sid] = full_session
        SESSIONS.move_to_end(sid)
        _evict_sessions_over_cap()  # #4765: safe LRU eviction (never active/unsaved)
    return full_session


_ANCHOR_ACTIVITY_SCENE_MAX_BYTES = 256_000
_ANCHOR_ACTIVITY_SCENE_MAX_ROWS = 1_000


def _assistant_anchor_scene_message_ref(message) -> str:
    if not isinstance(message, dict):
        return ""
    payload = _assistant_anchor_scene_message_ref_payload(message)
    return _anchor_scene_message_ref_digest(payload)


def _assistant_anchor_scene_message_ref_payload(message) -> dict:
    role = str(message.get("role") or "")
    content = message.get("content")
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict):
                parts.append(str(part.get("text") or part.get("content") or part.get("input_text") or ""))
            else:
                parts.append(str(part or ""))
        content_text = "\n".join(parts)
    else:
        content_text = str(content or "")
    payload = {
        "role": role,
        "content": " ".join(content_text.split()),
        "timestamp": message.get("_ts") or message.get("timestamp") or "",
    }
    return payload


def _anchor_scene_message_ref_digest(payload: dict) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _sanitize_anchor_activity_scene(scene):
    if not isinstance(scene, dict):
        raise ValueError("scene must be an object")
    if str(scene.get("version") or "") != "activity_scene_v1":
        raise ValueError("scene.version must be activity_scene_v1")
    rows = scene.get("activity_rows")
    if not isinstance(rows, list):
        raise ValueError("scene.activity_rows must be a list")
    if len(rows) > _ANCHOR_ACTIVITY_SCENE_MAX_ROWS:
        raise ValueError("scene.activity_rows is too large")
    scene_copy = copy.deepcopy(scene)
    encoded = json.dumps(scene_copy, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
    if len(encoded) > _ANCHOR_ACTIVITY_SCENE_MAX_BYTES:
        raise ValueError("scene payload is too large")
    return json.loads(encoded.decode("utf-8"))


def _anchor_scene_int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _anchor_scene_message_index_from_request(body):
    if not isinstance(body, dict):
        return None
    message_index = _anchor_scene_int_or_none(body.get("message_index"))
    message_offset = _anchor_scene_int_or_none(body.get("message_offset"))
    message_window_index = _anchor_scene_int_or_none(body.get("message_window_index"))
    if (
        message_window_index is not None
        and message_offset is not None
        and message_offset > 0
        and (message_index is None or message_index == message_window_index)
    ):
        return message_window_index + message_offset
    return message_index


def _anchor_scene_candidate_matches_scene(candidate, scene) -> bool:
    if not isinstance(scene, dict):
        return True
    final_key = _anchor_scene_text_key(scene.get("final_answer") or "")
    if not final_key:
        return True
    candidate_key = _anchor_scene_text_key(_anchor_scene_message_text(candidate))
    if not candidate_key:
        return False
    if candidate_key == final_key:
        return True
    if len(final_key) >= 16 and final_key in candidate_key:
        return True
    if len(candidate_key) >= 16 and candidate_key in final_key:
        return True
    return _anchor_scene_text_has_long_overlap(candidate_key, final_key)


def _find_anchor_scene_message(messages, *, message_index=None, message_ref="", scene=None):
    if not isinstance(messages, list):
        return None, None
    normalized_message_ref = _normalize_anchor_scene_message_ref(message_ref)
    candidate = None
    if isinstance(message_index, int) and 0 <= message_index < len(messages):
        maybe_candidate = messages[message_index]
        if isinstance(maybe_candidate, dict) and maybe_candidate.get("role") == "assistant":
            candidate = maybe_candidate
    if normalized_message_ref:
        matches = [
            (idx, message)
            for idx, message in enumerate(messages)
            if isinstance(message, dict)
            and message.get("role") == "assistant"
            and _assistant_anchor_scene_message_ref(message) == normalized_message_ref
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            return None, None
        if candidate is None:
            return None, None
    if candidate is not None:
        # Content can be normalized or split during settlement; use the explicit
        # index as the durability fallback only after a unique ref did not pick a
        # different assistant message. The index is the full transcript index.
        if normalized_message_ref and not _anchor_scene_candidate_matches_scene(candidate, scene):
            return None, None
        return message_index, candidate
    for idx in range(len(messages) - 1, -1, -1):
        message = messages[idx]
        if isinstance(message, dict) and message.get("role") == "assistant":
            return idx, message
    return None, None


def _normalize_anchor_scene_message_ref(message_ref) -> str:
    ref = str(message_ref or "").strip()
    if not ref:
        return ""
    if re.fullmatch(r"[0-9a-fA-F]{64}", ref):
        return ref.lower()
    try:
        payload = json.loads(ref)
    except (TypeError, ValueError):
        return ref
    if not isinstance(payload, dict):
        return ref
    canonical = {
        "role": str(payload.get("role") or ""),
        "content": " ".join(str(payload.get("content") or "").split()),
        "timestamp": payload.get("timestamp") or "",
    }
    return _anchor_scene_message_ref_digest(canonical)


def _anchor_scene_records(session) -> dict:
    records = getattr(session, "anchor_activity_scenes", None)
    return records if isinstance(records, dict) else {}


def _anchor_scene_message_text(message) -> str:
    if not isinstance(message, dict):
        return ""
    content = message.get("content", "")
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict):
                parts.append(str(part.get("text") or part.get("content") or part.get("input_text") or ""))
            else:
                parts.append(str(part or ""))
        return "\n".join(parts)
    return str(content or "")


def _anchor_scene_content_text(part) -> str:
    if part is None:
        return ""
    if isinstance(part, str):
        return part
    if not isinstance(part, dict):
        return str(part or "")
    return str(
        part.get("text")
        or part.get("content")
        or part.get("input_text")
        or part.get("output_text")
        or part.get("thinking")
        or part.get("reasoning")
        or part.get("summary")
        or ""
    )


def _anchor_scene_content_visible_text(part) -> str:
    if part is None:
        return ""
    if isinstance(part, str):
        return part
    if not isinstance(part, dict):
        return str(part or "")
    part_type = str(part.get("type") or "")
    if part_type in ("thinking", "reasoning"):
        return ""
    content_text = part.get("content") if part_type in ("text", "input_text", "output_text") else ""
    return str(part.get("text") or part.get("input_text") or part.get("output_text") or content_text or "")


def _anchor_scene_message_has_content_tool_use(message) -> bool:
    content = message.get("content") if isinstance(message, dict) else None
    return isinstance(content, list) and any(
        isinstance(part, dict) and part.get("type") == "tool_use" for part in content
    )


def _anchor_scene_final_answer_text(message) -> str:
    if not _anchor_scene_message_has_content_tool_use(message):
        return _anchor_scene_message_text(message)
    content = message.get("content") if isinstance(message, dict) else []
    last_tool_index = -1
    for idx, part in enumerate(content):
        if isinstance(part, dict) and part.get("type") == "tool_use":
            last_tool_index = idx
    tail_text = "\n".join(
        text
        for text in (_anchor_scene_content_visible_text(part) for part in content[last_tool_index + 1 :])
        if _anchor_scene_clean_text(text)
    )
    return tail_text if _anchor_scene_clean_text(tail_text) else ""


def _anchor_scene_message_reasoning_text(message) -> str:
    if not isinstance(message, dict):
        return ""
    for key in ("reasoning", "_reasoning", "reasoning_content", "thinking"):
        value = message.get(key)
        if not value:
            continue
        if isinstance(value, list):
            parts = []
            for part in value:
                if isinstance(part, dict):
                    parts.append(
                        str(
                            part.get("text")
                            or part.get("content")
                            or part.get("reasoning")
                            or part.get("summary")
                            or ""
                        )
                    )
                else:
                    parts.append(str(part or ""))
            return "\n".join(parts)
        if isinstance(value, dict):
            return str(
                value.get("text")
                or value.get("content")
                or value.get("reasoning")
                or value.get("summary")
                or ""
            )
        return str(value or "")
    return ""


def _anchor_scene_clean_text(value) -> str:
    return " ".join(str(value or "").split()).strip()


def _anchor_scene_text_key(value) -> str:
    return _anchor_scene_clean_text(value).lower()


_ANCHOR_SCENE_SETTLED_SNIPPET_CAP = 4000


def _anchor_scene_string_payload(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value)
    except Exception:
        logger.debug("Silent exception in _anchor_scene_string_payload", exc_info=True)
        return str(value)


def _anchor_scene_is_bounded_tool_body_preview(settled, full) -> bool:
    settled_text = _anchor_scene_string_payload(settled)
    full_text = _anchor_scene_string_payload(full)
    return bool(
        settled_text
        and full_text
        and len(full_text) > len(settled_text)
        and len(settled_text) >= _ANCHOR_SCENE_SETTLED_SNIPPET_CAP
        and full_text.startswith(settled_text)
    )


def _anchor_scene_row_looks_like_final_answer(row_text_key: str, final_key: str) -> bool:
    if not row_text_key or not final_key:
        return False
    if row_text_key == final_key:
        return True
    # #4587: align with the renderer's _anchorSceneProseMatchesFinalAnswer — a
    # prefix-like overlap only counts as "the final answer" when it's a
    # NEAR-complete match (ratio >= 0.9). A shorter intermediate-prose row that
    # is merely a prefix of the final answer is legitimate progress narration and
    # must be preserved in the persisted scene, not filtered out.
    if not (final_key.startswith(row_text_key) or row_text_key.startswith(final_key)):
        return False
    shorter = min(len(row_text_key), len(final_key))
    longer = max(len(row_text_key), len(final_key))
    return shorter >= 80 and longer > 0 and (shorter / longer) >= 0.9


def _anchor_scene_text_has_long_overlap(text_key: str, final_key: str) -> bool:
    if len(text_key) < 80 or len(final_key) < 80:
        return False
    shorter, longer = (text_key, final_key) if len(text_key) <= len(final_key) else (final_key, text_key)
    window = 64
    scan_limit = min(len(shorter), 1400)
    if scan_limit < window:
        return False
    for start in range(0, scan_limit - window + 1, 24):
        chunk = shorter[start : start + window].strip()
        if len(chunk) >= 48 and chunk in longer:
            return True
    text_tokens = set(re.findall(r"[a-z0-9_./:-]{3,}", text_key))
    final_tokens = set(re.findall(r"[a-z0-9_./:-]{3,}", final_key))
    if text_tokens and final_tokens:
        common = text_tokens & final_tokens
        shorter_count = min(len(text_tokens), len(final_tokens))
        if shorter_count >= 3 and len(common) >= min(5, shorter_count) and (len(common) / shorter_count) >= 0.5:
            return True
    text_compact = re.sub(r"[\s`*_#|\[\](){}<>.,;:!?，。；：！？、/\\-]+", "", text_key)
    final_compact = re.sub(r"[\s`*_#|\[\](){}<>.,;:!?，。；：！？、/\\-]+", "", final_key)
    if len(text_compact) >= 40 and len(final_compact) >= 40:
        text_grams = {text_compact[idx : idx + 4] for idx in range(0, len(text_compact) - 3)}
        final_grams = {final_compact[idx : idx + 4] for idx in range(0, len(final_compact) - 3)}
        common_grams = text_grams & final_grams
        shorter_grams = min(len(text_grams), len(final_grams))
        if shorter_grams and len(common_grams) >= 12 and (len(common_grams) / shorter_grams) >= 0.35:
            return True
    return False


def _anchor_scene_row_is_stale_token_answer(row, row_text_key: str, final_key: str) -> bool:
    if not isinstance(row, dict) or row.get("role") not in ("prose", "thinking"):
        return False
    source_type = str(row.get("source_event_type") or row.get("source") or "")
    if source_type != "token":
        return False
    return _anchor_scene_text_has_long_overlap(row_text_key, final_key)


def _anchor_scene_message_turn_duration(message):
    if not isinstance(message, dict):
        return None
    for key in ("_turnDuration", "_turn_duration", "turn_duration"):
        value = message.get(key)
        if isinstance(value, (int, float)) and value >= 0:
            return value
    return None


def _anchor_scene_tool_id(tool) -> str:
    if not isinstance(tool, dict):
        return ""
    return str(
        tool.get("tid")
        or tool.get("id")
        or tool.get("tool_call_id")
        or tool.get("tool_use_id")
        or tool.get("call_id")
        or ""
    ).strip()


def _anchor_scene_tool_name(tool) -> str:
    if not isinstance(tool, dict):
        return "tool"
    fn = tool.get("function") if isinstance(tool.get("function"), dict) else {}
    return str(tool.get("name") or tool.get("tool_name") or fn.get("name") or "tool").strip() or "tool"


def _anchor_scene_tool_args(tool):
    if not isinstance(tool, dict):
        return {}
    for key in ("args", "input"):
        value = tool.get(key)
        if isinstance(value, dict):
            return copy.deepcopy(value)
    fn = tool.get("function") if isinstance(tool.get("function"), dict) else {}
    raw = fn.get("arguments")
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            logger.debug("Silent exception in _anchor_scene_tool_args", exc_info=True)
            return {}
    return {}


def _anchor_scene_content_tool(part):
    if not isinstance(part, dict):
        return {}
    fn = part.get("function") if isinstance(part.get("function"), dict) else {}
    tool_id = (
        part.get("id")
        or part.get("tid")
        or part.get("tool_call_id")
        or part.get("tool_use_id")
        or part.get("call_id")
    )
    return {
        "id": tool_id,
        "tid": part.get("tid") or tool_id,
        "tool_call_id": part.get("tool_call_id"),
        "tool_use_id": part.get("tool_use_id"),
        "call_id": part.get("call_id"),
        "name": part.get("name") or part.get("tool_name") or fn.get("name") or "tool",
        "tool_name": part.get("tool_name"),
        "args": part.get("args"),
        "input": part.get("input"),
        "function": copy.deepcopy(part.get("function")) if isinstance(part.get("function"), dict) else None,
        "command": part.get("command") or part.get("raw_command") or part.get("original_command") or part.get("display_command"),
        "preview": part.get("preview") or part.get("summary"),
        "snippet": part.get("snippet") or part.get("result") or part.get("output"),
        "result": copy.deepcopy(part.get("result")),
        "output": copy.deepcopy(part.get("output")),
        "is_error": part.get("is_error"),
        "error": part.get("error"),
        "duration": part.get("duration"),
        "started_at": part.get("started_at"),
    }


def _anchor_scene_row_base(role, kind, source_event_type, order_index, message_index, stream_id=""):
    return {
        "row_id": f"hydrated:{stream_id or 'stream'}:{role}:{message_index}:{order_index}",
        "order_index": order_index,
        "kind": kind,
        "role": role,
        "display_hint": {
            "prose": "main_prose",
            "thinking": "collapsed_thinking",
            "tool": "tool_row",
            "terminal": "terminal_status_row",
        }.get(role, "activity_row"),
        "display_hints": {
            "compact_worklog": {
                "prose": "main_prose",
                "thinking": "collapsed_thinking",
                "tool": "tool_row",
                "terminal": "terminal_status_row",
            }.get(role, "activity_row"),
            "transparent_stream": "chronological_activity",
        },
        "source_event_type": source_event_type,
        "event_id": None,
        "local_id": None,
        "run_id": None,
        "stream_id": stream_id or None,
        "seq": order_index,
        "status": "completed",
        "created_at": None,
        "identity": {"event_id": None, "local_id": None, "run_id": None, "stream_id": stream_id or None, "seq": order_index},
        "group": {
            "group_key": f"assistant:{message_index}" if isinstance(message_index, int) else f"activity:{order_index}",
            "activity_burst_id": None,
            "activity_segment_seq": None,
            "assistant_msg_idx": message_index if isinstance(message_index, int) else None,
        },
        "text": "",
        "thinking": None,
        "tool_call_id": None,
        "tool": None,
        "payload": {"assistant_msg_idx": message_index if isinstance(message_index, int) else None},
    }


def _anchor_scene_prose_row(text, order_index, message_index, stream_id=""):
    row = _anchor_scene_row_base("prose", "process_prose", "settled_message", order_index, message_index, stream_id)
    row["text"] = str(text or "")
    row["payload"]["text"] = row["text"]
    return row


def _anchor_scene_thinking_row(text, order_index, message_index, stream_id=""):
    row = _anchor_scene_row_base("thinking", "reasoning", "reasoning", order_index, message_index, stream_id)
    row["text"] = str(text or "")
    preview = _anchor_scene_clean_text(text)
    row["thinking"] = {
        "text": row["text"],
        "preview": (preview[:177] + "...") if len(preview) > 180 else preview,
        "dedupe_key": f"thinking:{preview.lower()}" if preview else "",
    }
    row["payload"]["text"] = row["text"]
    return row


def _anchor_scene_tool_row(tool, order_index, message_index, stream_id=""):
    row = _anchor_scene_row_base("tool", "tool_completed", "tool_complete", order_index, message_index, stream_id)
    tid = _anchor_scene_tool_id(tool)
    name = _anchor_scene_tool_name(tool)
    args = _anchor_scene_tool_args(tool)
    preview = str((tool or {}).get("preview") or (tool or {}).get("summary") or "")
    snippet = str((tool or {}).get("snippet") or (tool or {}).get("result") or (tool or {}).get("output") or "")
    row["row_id"] = f"hydrated:{stream_id or 'stream'}:tool:{tid}" if tid else row["row_id"]
    row["tool_call_id"] = tid or None
    row["tool"] = {
        "id": tid or None,
        "name": name,
        "args": args,
        "preview": preview,
        "snippet": snippet,
        "result": copy.deepcopy((tool or {}).get("result")) if isinstance(tool, dict) else None,
        "output": copy.deepcopy((tool or {}).get("output")) if isinstance(tool, dict) else None,
        "done": True,
        "is_error": bool((tool or {}).get("is_error") or (tool or {}).get("error")),
        "duration": (tool or {}).get("duration") if isinstance(tool, dict) else None,
        "started_at": (tool or {}).get("started_at") if isinstance(tool, dict) else None,
        "signature": f"{name}|{tid}|{json.dumps(args, sort_keys=True, default=str)}",
    }
    row["payload"].update({"tid": tid, "id": tid, "name": name, "args": args, "preview": preview, "snippet": snippet})
    return row


def _anchor_scene_tool_row_id(row) -> str:
    if not isinstance(row, dict):
        return ""
    tool = row.get("tool") if isinstance(row.get("tool"), dict) else {}
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    return str(
        row.get("tool_call_id")
        or tool.get("id")
        or tool.get("tid")
        or tool.get("tool_call_id")
        or tool.get("tool_use_id")
        or tool.get("call_id")
        or payload.get("tid")
        or payload.get("id")
        or ""
    ).strip()


def _anchor_scene_tool_row_name(row) -> str:
    if not isinstance(row, dict):
        return ""
    tool = row.get("tool") if isinstance(row.get("tool"), dict) else {}
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    return str(tool.get("name") or payload.get("name") or "tool").strip().lower()


def _anchor_scene_tool_rows_have_compatible_names(existing, incoming) -> bool:
    existing_name = _anchor_scene_tool_row_name(existing)
    incoming_name = _anchor_scene_tool_row_name(incoming)
    return (
        not existing_name
        or not incoming_name
        or existing_name == "tool"
        or incoming_name == "tool"
        or existing_name == incoming_name
    )


def _anchor_scene_tool_row_args(row):
    if not isinstance(row, dict):
        return None
    tool = row.get("tool") if isinstance(row.get("tool"), dict) else {}
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    args = tool.get("args") if isinstance(tool.get("args"), dict) else payload.get("args")
    return args if isinstance(args, dict) and args else None


def _anchor_scene_object_contains_subset(base, subset) -> bool:
    if not isinstance(base, dict) or not isinstance(subset, dict):
        return False
    for key, value in subset.items():
        if key not in base:
            return False
        if json.dumps(base[key], sort_keys=True, separators=(",", ":")) != json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
        ):
            return False
    return True


def _anchor_scene_tool_rows_have_compatible_invocation(existing, incoming) -> bool:
    if not isinstance(existing, dict) or not isinstance(incoming, dict):
        return False
    existing_tool = existing.get("tool") if isinstance(existing.get("tool"), dict) else {}
    incoming_tool = incoming.get("tool") if isinstance(incoming.get("tool"), dict) else {}
    existing_payload = existing.get("payload") if isinstance(existing.get("payload"), dict) else {}
    incoming_payload = incoming.get("payload") if isinstance(incoming.get("payload"), dict) else {}
    existing_command = str(existing_tool.get("command") or existing_payload.get("command") or "").strip()
    incoming_command = str(incoming_tool.get("command") or incoming_payload.get("command") or "").strip()
    if existing_command and incoming_command:
        return existing_command == incoming_command
    existing_args = _anchor_scene_tool_row_args(existing)
    incoming_args = _anchor_scene_tool_row_args(incoming)
    if not existing_args or not incoming_args:
        return False
    return _anchor_scene_object_contains_subset(
        existing_args,
        incoming_args,
    ) or _anchor_scene_object_contains_subset(incoming_args, existing_args)


def _anchor_scene_tool_row_has_invocation_evidence(row) -> bool:
    if not isinstance(row, dict):
        return False
    tool = row.get("tool") if isinstance(row.get("tool"), dict) else {}
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    command = str(tool.get("command") or payload.get("command") or "").strip()
    args = _anchor_scene_tool_row_args(row)
    return bool(command or args)


def _anchor_scene_tool_rows_can_name_match(existing, incoming) -> bool:
    if not _anchor_scene_tool_rows_have_compatible_names(existing, incoming):
        return False
    if _anchor_scene_tool_row_has_invocation_evidence(existing) and _anchor_scene_tool_row_has_invocation_evidence(incoming):
        return _anchor_scene_tool_rows_have_compatible_invocation(existing, incoming)
    return True


def _anchor_scene_tool_rows_have_different_explicit_ids(existing, incoming) -> bool:
    existing_id = _anchor_scene_tool_row_id(existing)
    incoming_id = _anchor_scene_tool_row_id(incoming)
    return bool(existing_id and incoming_id and existing_id != incoming_id)


def _anchor_scene_tool_row_started_at(row) -> str:
    if not isinstance(row, dict):
        return ""
    tool = row.get("tool") if isinstance(row.get("tool"), dict) else {}
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    value = tool.get("started_at")
    if value is None or value == "":
        value = payload.get("started_at")
    return str(value) if value is not None and value != "" else ""


def _anchor_scene_tool_rows_have_same_started_at(existing, incoming) -> bool:
    existing_started_at = _anchor_scene_tool_row_started_at(existing)
    incoming_started_at = _anchor_scene_tool_row_started_at(incoming)
    return bool(existing_started_at and incoming_started_at and existing_started_at == incoming_started_at)


def _anchor_scene_tool_row_body_text(row) -> str:
    if not isinstance(row, dict):
        return ""
    tool = row.get("tool") if isinstance(row.get("tool"), dict) else {}
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    for value in (
        tool.get("snippet"),
        payload.get("snippet"),
        tool.get("output"),
        payload.get("output"),
        tool.get("result"),
        payload.get("result"),
        tool.get("preview"),
        payload.get("preview"),
    ):
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _anchor_scene_tool_rows_have_compatible_body(existing, incoming) -> bool:
    existing_body = _anchor_scene_tool_row_body_text(existing)
    incoming_body = _anchor_scene_tool_row_body_text(incoming)
    return bool(
        existing_body
        and incoming_body
        and (
            existing_body == incoming_body
            or existing_body.startswith(incoming_body)
            or incoming_body.startswith(existing_body)
        )
    )


def _anchor_scene_matching_content_tool_row_index(
    rows,
    content_tool_indexes,
    incoming_row,
    ordinal,
    used_indexes,
    incoming_total=0,
    id_flexible_indexes=None,
):
    if not isinstance(rows, list) or not isinstance(content_tool_indexes, list) or not isinstance(incoming_row, dict):
        return None
    incoming_id = _anchor_scene_tool_row_id(incoming_row)
    for index in content_tool_indexes:
        if index in used_indexes or index < 0 or index >= len(rows):
            continue
        existing_id = _anchor_scene_tool_row_id(rows[index])
        if existing_id and incoming_id and existing_id == incoming_id:
            return index
    if len(content_tool_indexes) == 1 and incoming_total == 1:
        index = content_tool_indexes[0]
        if (
            index not in used_indexes
            and 0 <= index < len(rows)
            and _anchor_scene_tool_rows_can_name_match(rows[index], incoming_row)
        ):
            return index
    available_indexes = [
        index for index in content_tool_indexes if index not in used_indexes and 0 <= index < len(rows)
    ]
    if len(available_indexes) == 1:
        index = available_indexes[0]
        if incoming_total == 1 and _anchor_scene_tool_rows_can_name_match(rows[index], incoming_row):
            return index
        if _anchor_scene_tool_rows_have_compatible_names(
            rows[index],
            incoming_row,
        ) and _anchor_scene_tool_rows_have_compatible_invocation(rows[index], incoming_row):
            return index
    reusable_indexes = [
        index for index in content_tool_indexes if index in used_indexes and 0 <= index < len(rows)
    ]
    if len(reusable_indexes) == 1 and incoming_total == 1:
        index = reusable_indexes[0]
        existing_id = _anchor_scene_tool_row_id(rows[index])
        id_flexible = isinstance(id_flexible_indexes, set) and index in id_flexible_indexes
        if _anchor_scene_tool_rows_have_compatible_names(
            rows[index],
            incoming_row,
        ) and (
            (existing_id and incoming_id and existing_id == incoming_id)
            or (
                id_flexible
                and _anchor_scene_tool_rows_have_same_started_at(rows[index], incoming_row)
                and _anchor_scene_tool_rows_have_compatible_body(rows[index], incoming_row)
            )
        ) and _anchor_scene_tool_rows_have_compatible_invocation(rows[index], incoming_row):
            return index
    for index in content_tool_indexes:
        if index in used_indexes or index < 0 or index >= len(rows):
            continue
        existing_id = _anchor_scene_tool_row_id(rows[index])
        if not existing_id and not incoming_id and _anchor_scene_tool_rows_can_name_match(rows[index], incoming_row):
            return index
    return None


def _anchor_scene_content_rows(message, order_index, message_index, stream_id="", *, is_final_message=False):
    if not _anchor_scene_message_has_content_tool_use(message):
        return None
    rows = []
    content = message.get("content") if isinstance(message, dict) else []
    last_tool_index = -1
    for idx, part in enumerate(content):
        if isinstance(part, dict) and part.get("type") == "tool_use":
            last_tool_index = idx
    for idx, part in enumerate(content):
        if not isinstance(part, dict):
            if is_final_message and idx > last_tool_index:
                continue
            text = _anchor_scene_content_text(part)
            if _anchor_scene_clean_text(text):
                rows.append(_anchor_scene_prose_row(text, order_index + len(rows), message_index, stream_id))
            continue
        part_type = part.get("type")
        if part_type in ("text", "input_text", "output_text"):
            if is_final_message and idx > last_tool_index and _anchor_scene_content_visible_text(part):
                continue
            text = _anchor_scene_content_text(part)
            if _anchor_scene_clean_text(text):
                rows.append(_anchor_scene_prose_row(text, order_index + len(rows), message_index, stream_id))
            continue
        if part_type in ("thinking", "reasoning"):
            text = _anchor_scene_content_text(part)
            if _anchor_scene_clean_text(text):
                rows.append(_anchor_scene_thinking_row(text, order_index + len(rows), message_index, stream_id))
            continue
        if part_type == "tool_use":
            rows.append(
                _anchor_scene_tool_row(
                    _anchor_scene_content_tool(part),
                    order_index + len(rows),
                    message_index,
                    stream_id,
                )
            )
    return rows


def _anchor_scene_row_key(row) -> str:
    if not isinstance(row, dict):
        return ""
    if row.get("role") == "tool":
        tool = row.get("tool") if isinstance(row.get("tool"), dict) else {}
        return "tool:" + str(
            row.get("tool_call_id")
            or tool.get("id")
            or tool.get("tid")
            or tool.get("tool_call_id")
            or tool.get("tool_use_id")
            or tool.get("call_id")
            or row.get("row_id")
            or ""
        )
    if row.get("role") in ("prose", "thinking"):
        return f"{row.get('role')}:{_anchor_scene_text_key(row.get('text'))}"
    if row.get("role") == "lifecycle":
        source_type = str(row.get("source_event_type") or row.get("source") or "")
        if source_type in ("compressing", "compressed"):
            return "lifecycle:compression"
    return f"{row.get('role') or row.get('kind')}:{row.get('source_event_type') or ''}:{row.get('status') or ''}:{row.get('row_id') or ''}"


def _anchor_scene_row_has_live_identity(row) -> bool:
    if not isinstance(row, dict):
        return False
    values = [row.get("row_id"), row.get("local_id"), row.get("event_id")]
    identity = row.get("identity") if isinstance(row.get("identity"), dict) else {}
    values.extend([identity.get("local_id"), identity.get("event_id")])
    if any(str(value or "").startswith("live-") for value in values):
        return True
    group = row.get("group") if isinstance(row.get("group"), dict) else {}
    has_stream_owner = bool(
        row.get("stream_id")
        or row.get("run_id")
        or identity.get("stream_id")
        or identity.get("run_id")
    )
    has_assistant_message_index = group.get("assistant_msg_idx") is not None
    return has_stream_owner and not has_assistant_message_index


def _anchor_scene_settle_live_running_row(row, *, has_settled_thinking: bool):
    if not isinstance(row, dict):
        return row
    role = row.get("role")
    if role not in ("thinking", "prose", "tool"):
        return row
    if str(row.get("status") or "").lower() != "running":
        return row
    if not _anchor_scene_row_has_live_identity(row):
        return row
    if role == "thinking" and has_settled_thinking:
        return None
    next_row = copy.deepcopy(row)
    next_row["status"] = "completed"
    payload = next_row.get("payload")
    if isinstance(payload, dict):
        payload["status"] = "completed"
        if role == "tool":
            payload["done"] = True
    tool = next_row.get("tool")
    if role == "tool" and isinstance(tool, dict):
        tool["done"] = True
    return next_row


def _complete_hydrated_anchor_scene(messages, scene, message_index, *, message_offset=0, tool_calls=None, stream_id=""):
    if not isinstance(messages, list) or not isinstance(scene, dict) or not isinstance(message_index, int):
        return scene
    local_final_idx = message_index - int(message_offset or 0)
    if local_final_idx < 0 or local_final_idx >= len(messages):
        return scene
    final_message = messages[local_final_idx]
    if not isinstance(final_message, dict) or final_message.get("role") != "assistant":
        return scene
    turn_start = -1
    for idx in range(local_final_idx - 1, -1, -1):
        message = messages[idx]
        if isinstance(message, dict) and message.get("role") == "user":
            turn_start = idx
            break
    message_final_answer = _anchor_scene_final_answer_text(final_message)
    scene_final_answer = scene.get("final_answer") if isinstance(scene.get("final_answer"), str) else ""
    final_answer = message_final_answer if _anchor_scene_clean_text(message_final_answer) else scene_final_answer
    final_key = _anchor_scene_text_key(final_answer)
    rows = []
    seen = {}

    def merge_duplicate_tool_row(existing, incoming, *, prefer_incoming_body=False):
        if not isinstance(existing, dict) or not isinstance(incoming, dict):
            return existing
        merged = copy.deepcopy(existing)
        merged_tool = merged.get("tool") if isinstance(merged.get("tool"), dict) else {}
        incoming_tool = incoming.get("tool") if isinstance(incoming.get("tool"), dict) else {}
        merged_payload = merged.get("payload") if isinstance(merged.get("payload"), dict) else {}
        incoming_payload = incoming.get("payload") if isinstance(incoming.get("payload"), dict) else {}

        def empty(value):
            return value is None or value == "" or value == {}

        def merge_missing_args(existing_args, incoming_args):
            if not isinstance(incoming_args, dict) or not incoming_args:
                return existing_args, False
            base = copy.deepcopy(existing_args) if isinstance(existing_args, dict) else {}
            changed = not isinstance(existing_args, dict)
            for key, value in incoming_args.items():
                if key not in base:
                    base[key] = copy.deepcopy(value)
                    changed = True
            return base, changed

        for key in ("snippet", "result", "output"):
            incoming_value = incoming_tool.get(key)
            if not empty(incoming_value) and (
                empty(merged_tool.get(key))
                or (
                    prefer_incoming_body
                    and _anchor_scene_is_bounded_tool_body_preview(merged_tool.get(key), incoming_value)
                )
            ):
                merged_tool[key] = copy.deepcopy(incoming_value)
            incoming_value = incoming_payload.get(key)
            if not empty(incoming_value) and (
                empty(merged_payload.get(key))
                or (
                    prefer_incoming_body
                    and _anchor_scene_is_bounded_tool_body_preview(merged_payload.get(key), incoming_value)
                )
            ):
                merged_payload[key] = copy.deepcopy(incoming_value)
        for key in ("preview", "command", "duration", "started_at"):
            incoming_value = incoming_tool.get(key)
            if not empty(incoming_value) and empty(merged_tool.get(key)):
                merged_tool[key] = copy.deepcopy(incoming_value)
            incoming_value = incoming_payload.get(key)
            if not empty(incoming_value) and empty(merged_payload.get(key)):
                merged_payload[key] = copy.deepcopy(incoming_value)
        merged_args, args_changed = merge_missing_args(merged_tool.get("args"), incoming_tool.get("args"))
        if args_changed:
            merged_tool["args"] = merged_args
        merged_payload_args, payload_args_changed = merge_missing_args(
            merged_payload.get("args"),
            incoming_payload.get("args"),
        )
        if payload_args_changed:
            merged_payload["args"] = merged_payload_args
        merged["tool"] = merged_tool
        merged["payload"] = merged_payload
        return merged

    def push(row, *, prefer_incoming_tool_body=False):
        if not isinstance(row, dict):
            return
        row = _anchor_scene_settle_live_running_row(
            row,
            has_settled_thinking=any(existing.get("role") == "thinking" for existing in rows),
        )
        if row is None or not isinstance(row, dict):
            return
        text_key = _anchor_scene_text_key(row.get("text"))
        if row.get("role") in ("prose", "thinking") and _anchor_scene_row_looks_like_final_answer(text_key, final_key):
            return
        if _anchor_scene_row_is_stale_token_answer(row, text_key, final_key):
            return
        key = _anchor_scene_row_key(row)
        if key and key in seen:
            if key.startswith("tool:"):
                index = seen[key]
                rows[index] = merge_duplicate_tool_row(
                    rows[index],
                    row,
                    prefer_incoming_body=prefer_incoming_tool_body,
                )
                return
            if key == "lifecycle:compression":
                index = seen[key]
                next_row = copy.deepcopy(row)
                next_row["order_index"] = index
                next_row["seq"] = index
                rows[index] = next_row
            return
        if key:
            seen[key] = len(rows)
        next_row = copy.deepcopy(row)
        next_row["order_index"] = len(rows)
        next_row["seq"] = len(rows)
        rows.append(next_row)

    order = 0
    content_tool_indexes_by_idx = {}
    used_content_tool_indexes_by_idx = {}
    id_flexible_content_tool_indexes_by_idx = {}
    for local_idx in range(turn_start + 1, local_final_idx + 1):
        message = messages[local_idx]
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        absolute_idx = int(message_offset or 0) + local_idx
        text = _anchor_scene_message_text(message)
        content_rows = _anchor_scene_content_rows(
            message,
            order,
            absolute_idx,
            stream_id,
            is_final_message=local_idx == local_final_idx,
        )
        content_tool_indexes = []
        used_content_tool_indexes = set()
        id_flexible_content_tool_indexes = set()
        if content_rows:
            for row in content_rows:
                previous_len = len(rows)
                push(row)
                if row.get("role") == "tool" and len(rows) > previous_len:
                    content_tool_indexes.append(len(rows) - 1)
                order += 1
            if content_tool_indexes:
                content_tool_indexes_by_idx[absolute_idx] = content_tool_indexes
                used_content_tool_indexes_by_idx[absolute_idx] = used_content_tool_indexes
                id_flexible_content_tool_indexes_by_idx[absolute_idx] = id_flexible_content_tool_indexes
        elif _anchor_scene_clean_text(text):
            push(_anchor_scene_prose_row(text, order, absolute_idx, stream_id))
            order += 1
        reasoning = _anchor_scene_message_reasoning_text(message)
        if _anchor_scene_clean_text(reasoning) and _anchor_scene_text_key(reasoning) != _anchor_scene_text_key(text):
            push(_anchor_scene_thinking_row(reasoning, order, absolute_idx, stream_id))
            order += 1
        for key in ("tool_calls", "_partial_tool_calls"):
            calls = message.get(key)
            if isinstance(calls, list):
                for tool_ordinal, call in enumerate(calls):
                    row = _anchor_scene_tool_row(call, order, absolute_idx, stream_id)
                    content_match_index = _anchor_scene_matching_content_tool_row_index(
                        rows,
                        content_tool_indexes,
                        row,
                        tool_ordinal,
                        used_content_tool_indexes,
                        len(calls),
                        id_flexible_content_tool_indexes,
                    )
                    if content_match_index is not None:
                        if _anchor_scene_tool_rows_have_different_explicit_ids(
                            rows[content_match_index],
                            row,
                        ):
                            id_flexible_content_tool_indexes.add(content_match_index)
                        rows[content_match_index] = merge_duplicate_tool_row(rows[content_match_index], row)
                        incoming_key = _anchor_scene_row_key(row)
                        if incoming_key:
                            seen[incoming_key] = content_match_index
                        used_content_tool_indexes.add(content_match_index)
                        order += 1
                        continue
                    push(row)
                    order += 1
    external_tool_counts = {}
    for call in tool_calls or []:
        if not isinstance(call, dict):
            continue
        try:
            absolute_idx = int(call.get("assistant_msg_idx"))
        except (TypeError, ValueError):
            continue
        external_tool_counts[absolute_idx] = external_tool_counts.get(absolute_idx, 0) + 1
    external_tool_ordinals = {}
    for call in tool_calls or []:
        if not isinstance(call, dict):
            continue
        try:
            absolute_idx = int(call.get("assistant_msg_idx"))
        except (TypeError, ValueError):
            continue
        local_idx = absolute_idx - int(message_offset or 0)
        if not (turn_start < local_idx <= local_final_idx):
            continue
        row = _anchor_scene_tool_row(call, order, absolute_idx, stream_id)
        tool_ordinal = external_tool_ordinals.get(absolute_idx, 0)
        external_tool_ordinals[absolute_idx] = tool_ordinal + 1
        content_match_index = _anchor_scene_matching_content_tool_row_index(
            rows,
            content_tool_indexes_by_idx.get(absolute_idx, []),
            row,
            tool_ordinal,
            used_content_tool_indexes_by_idx.setdefault(absolute_idx, set()),
            external_tool_counts.get(absolute_idx, 0),
            id_flexible_content_tool_indexes_by_idx.setdefault(absolute_idx, set()),
        )
        if content_match_index is not None:
            if _anchor_scene_tool_rows_have_different_explicit_ids(rows[content_match_index], row):
                id_flexible_content_tool_indexes_by_idx.setdefault(absolute_idx, set()).add(content_match_index)
            rows[content_match_index] = merge_duplicate_tool_row(
                rows[content_match_index],
                row,
                prefer_incoming_body=True,
            )
            incoming_key = _anchor_scene_row_key(row)
            if incoming_key:
                seen[incoming_key] = content_match_index
            used_content_tool_indexes_by_idx[absolute_idx].add(content_match_index)
            order += 1
            continue
        push(row, prefer_incoming_tool_body=True)
        order += 1
    for row in scene.get("activity_rows") or []:
        if isinstance(row, dict) and row.get("role") != "terminal":
            push(row)
    for row in scene.get("activity_rows") or []:
        if isinstance(row, dict) and row.get("role") == "terminal":
            push(row)
    repaired = copy.deepcopy(scene)
    repaired["version"] = "activity_scene_v1"
    repaired["mode"] = repaired.get("mode") or "compact_worklog"
    repaired["final_answer"] = final_answer if _anchor_scene_clean_text(final_answer) else repaired.get("final_answer", "")
    repaired["final_message_ref"] = _assistant_anchor_scene_message_ref(final_message)
    if repaired.get("turn_duration") is None:
        duration = _anchor_scene_message_turn_duration(final_message)
        if duration is not None:
            repaired["turn_duration"] = duration
    repaired["activity_rows"] = rows
    identity = repaired.get("identity") if isinstance(repaired.get("identity"), dict) else {}
    identity = dict(identity)
    identity["source_message_refs"] = [
        _assistant_anchor_scene_message_ref(message)
        for message in messages[turn_start + 1 : local_final_idx + 1]
        if isinstance(message, dict) and message.get("role") == "assistant"
    ]
    repaired["identity"] = identity
    return repaired


def _hydrate_anchor_activity_scenes(messages, records, *, message_offset=0, tool_calls=None):
    if not isinstance(messages, list) or not isinstance(records, dict) or not records:
        return messages
    by_ref = {}
    by_index = {}
    for key, record in records.items():
        if not isinstance(record, dict):
            continue
        scene = record.get("scene")
        if not isinstance(scene, dict):
            continue
        ref = str(record.get("message_ref") or key or "")
        if ref:
            by_ref[ref] = record
        try:
            idx = int(record.get("message_index"))
        except (TypeError, ValueError):
            idx = None
        if idx is not None:
            by_index[idx] = record
    out = list(messages)
    # Read-side ref-ambiguity guard (parity with the write-side
    # _find_anchor_scene_message, which returns None when a ref matches >1
    # message). If two assistant messages ever share a ref (byte-identical
    # whitespace-normalized content + identical _ts), attaching the same scene
    # to both would render duplicate worklog groups. Count ref occurrences and
    # fall through to the index-based match (which is positional, unambiguous)
    # for any ref that resolves to more than one assistant message.
    _ref_counts: dict[str, int] = {}
    for _m in messages:
        if isinstance(_m, dict) and _m.get("role") == "assistant":
            _r = _assistant_anchor_scene_message_ref(_m)
            if _r:
                _ref_counts[_r] = _ref_counts.get(_r, 0) + 1
    for local_idx, message in enumerate(messages):
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        absolute_idx = int(message_offset or 0) + local_idx
        _msg_ref = _assistant_anchor_scene_message_ref(message)
        record = by_ref.get(_msg_ref) if _ref_counts.get(_msg_ref, 0) <= 1 else None
        if not record:
            candidate = by_index.get(absolute_idx)
            if candidate and _anchor_scene_candidate_matches_scene(message, candidate.get("scene") or {}):
                record = candidate
        if not record:
            continue
        scene = record.get("scene")
        if not isinstance(scene, dict):
            continue
        next_message = dict(message)
        stream_id = record.get("stream_id")
        next_message["_anchor_activity_scene"] = _complete_hydrated_anchor_scene(
            messages,
            scene,
            absolute_idx,
            message_offset=message_offset,
            tool_calls=tool_calls,
            stream_id=str(stream_id or ""),
        )
        if stream_id:
            next_message["_anchor_stream_id"] = str(stream_id)
        out[local_idx] = next_message
    return out


def _handle_session_anchor_scene(handler, body):
    try:
        require(body, "session_id", "scene")
    except ValueError as exc:
        return bad(handler, str(exc))
    sid = str(body.get("session_id") or "").strip()
    if not sid:
        return bad(handler, "session_id is required", 400)
    message_index = _anchor_scene_message_index_from_request(body)
    message_ref = str(body.get("message_ref") or "")
    try:
        scene = _sanitize_anchor_activity_scene(body.get("scene"))
    except ValueError as exc:
        return bad(handler, str(exc), 400)
    try:
        s = _get_or_materialize_session(sid)
    except KeyError:
        return bad(handler, "Session not found", 404)
    except PermissionError:
        return bad(handler, "Read-only imported sessions cannot persist anchor scenes", 403)
    # Active-profile visibility guard (parity with GET /api/session, routes.py:~8922).
    # _get_or_materialize_session loads by id with no profile scoping, so without
    # this an authenticated request under profile A could persist anchor scenes
    # onto a session owned by profile B (cross-profile write). Reject as 404 —
    # same shape the read path uses — and leave anchor_activity_scenes untouched.
    if not _session_visible_to_active_profile(getattr(s, "profile", None) or None, handler):
        return bad(handler, "Session not found", 404)
    with _get_session_agent_lock(sid):
        idx, message = _find_anchor_scene_message(
            getattr(s, "messages", None) or [],
            message_index=message_index,
            message_ref=message_ref,
            scene=scene,
        )
        if message is None or idx is None:
            return bad(handler, "Assistant message not found", 404)
        if scene.get("turn_duration") is None:
            duration = _anchor_scene_message_turn_duration(message)
            if duration is not None:
                scene["turn_duration"] = duration
        ref = _assistant_anchor_scene_message_ref(message)
        records = dict(_anchor_scene_records(s))
        records[ref or f"index:{idx}"] = {
            "version": "anchor_activity_scene_record_v1",
            "message_index": idx,
            "message_ref": ref,
            "stream_id": str(body.get("stream_id") or ""),
            "scene": scene,
            "updated_at": time.time(),
        }
        if len(records) > 256:
            ordered = sorted(
                records.items(),
                key=lambda item: float((item[1] or {}).get("updated_at") or 0),
            )
            records = dict(ordered[-256:])
        s.anchor_activity_scenes = records
        s.save(touch_updated_at=False, skip_index=True)
    return j(handler, {"ok": True, "message_index": idx, "message_ref": ref})


def _get_or_materialize_session(sid: str, *, refresh_cli_messages: bool = False):
    """Get a session, materializing from CLI/agent metadata if not in WebUI store.

    Mirrors the fallback logic in /api/session/archive (routes.py:~8530).
    Raises:
        KeyError: session not found in any store
        PermissionError: session is read-only (messaging/Claude Code)
    """
    try:
        s = get_session(sid)
        s = _ensure_full_session_before_mutation(sid, s)
        # Read-only guard on the happy path too: an already-stored read-only /
        # imported session must not be mutated via rename/update/move
        # (Session.save() does not enforce this). Scope this to the explicit
        # read_only flag — a stored messaging session already owns its sidecar,
        # so the messaging-fork concern only applies to the materialize fallback
        # below (and the heuristic record-check would mis-trip on mock sessions).
        if getattr(s, "read_only", False):
            raise PermissionError("read-only imported session")
        # A previously-persisted subagent sidecar (#5307) is view-only and
        # owned by the delegate runner — even if it was stored with
        # read_only=False (e.g. materialized before this fix), it must not be
        # mutated / used as a writable chat session. This mirrors the
        # missing-sidecar subagent guard below on the happy path.
        if (
            (getattr(s, "source_tag", "") or getattr(s, "raw_source", "") or "").strip().lower() == "subagent"
            or _is_subagent_child_session_id(sid)
        ):
            raise PermissionError("read-only subagent child session")
        if refresh_cli_messages and getattr(s, "is_cli_session", False):
            latest_messages = get_cli_session_messages(
                sid,
                profile=getattr(s, "profile", None),
            )
            current_messages = list(getattr(s, "messages", None) or [])
            if (
                latest_messages
                and len(latest_messages) >= len(current_messages)
                and _session_messages_have_prefix(latest_messages, current_messages)
            ):
                # Keep the stitched CLI transcript authoritative on the first
                # WebUI continuation path without clobbering later divergent
                # WebUI-owned turns.
                s.messages = list(latest_messages)
        return s
    except KeyError:
        pass

    # Fallback: try to materialize from CLI/agent session metadata
    cli_meta = _lookup_cli_session_metadata(sid)

    # Delegated subagent children (#5307) are view-only: their transcript lives
    # in state.db and ownership belongs to the delegate runner, not WebUI. They
    # must never be materialized as a writable sidecar here — this is the shared
    # chokepoint reached by POST /api/chat/start (_get_or_materialize_session),
    # so gating it closes the write path that bypasses the GET/import_cli guards.
    # Checked via state.db source (independent of cli_meta, which is often empty
    # for a server-side subagent child).
    _mat_source_tag = (
        (cli_meta or {}).get("source_tag") or (cli_meta or {}).get("raw_source") or ""
    ).strip().lower()
    if _mat_source_tag == "subagent" or _is_subagent_child_session_id(sid):
        raise PermissionError("read-only subagent child session")

    if not cli_meta:
        raise KeyError(sid)

    # Read-only guard: messaging sessions and Claude Code imports cannot be
    # mutated. Reject BOTH an explicit read_only flag AND any messaging-source
    # record — agent rows normalize messaging sources without setting read_only,
    # and state.db (not a WebUI sidecar) is the source of truth for them, so
    # materializing a writable sidecar would fork the title/state.
    if cli_meta.get("read_only") or _is_messaging_session_record(cli_meta):
        raise PermissionError("read-only imported session")

    # Preserve source metadata fields
    def _apply_source_meta(s):
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

    if _is_messaging_session_record(cli_meta):
        # Messaging sessions: lightweight Session with no messages (state.db is source of truth)
        s = Session(
            session_id=sid,
            title=cli_meta.get("title") or title_from(get_cli_session_messages(sid), "CLI Session"),
            workspace=get_last_workspace(),
            model=cli_meta.get("model") or "unknown",
            created_at=cli_meta.get("created_at"),
            updated_at=cli_meta.get("updated_at"),
        )
        _apply_source_meta(s)
        s.save(touch_updated_at=False)
    else:
        # Regular CLI/agent sessions: import full message history
        msgs = get_cli_session_messages(sid)
        if not msgs:
            raise KeyError(sid)
        s = import_cli_session(
            sid,
            cli_meta.get("title") or title_from(msgs, "CLI Session"),
            msgs,
            cli_meta.get("model") or "unknown",
            profile=cli_meta.get("profile"),
            created_at=cli_meta.get("created_at"),
            updated_at=cli_meta.get("updated_at"),
        )
        _apply_source_meta(s)

    return s


def _share_snapshot_messages_for_session(session, *, cli_meta: dict | None = None) -> list:
    """Return the visible transcript that a public share should snapshot.

    External sessions (Telegram/Discord/Slack/CLI/etc.) may have no WebUI sidecar
    or may persist only local metadata in the sidecar while the transcript lives
    in state.db. Public sharing should snapshot the same visible conversation the
    session page renders, not the bare local sidecar payload.
    """
    sid = str(getattr(session, "session_id", "") or "").strip()
    current_messages = list(getattr(session, "messages", None) or [])
    if not sid:
        return current_messages
    profile = getattr(session, "profile", None)
    is_messaging = (
        _is_messaging_session_record(session)
        or _is_messaging_session_record(cli_meta)
    )
    if is_messaging or not current_messages:
        cli_messages = get_cli_session_messages(sid, profile=profile)
        if cli_messages:
            if is_messaging:
                return _merged_session_messages_for_display(session, cli_messages)
            return list(cli_messages)
    return current_messages


def _build_share_metadata_sidecar(
    sid: str,
    snapshot_session,
    *,
    cli_meta: dict | None = None,
):
    """Create a minimal WebUI sidecar for share metadata on external sessions."""
    cli_meta = dict(cli_meta or {})
    workspace = (
        cli_meta.get("workspace")
        or cli_meta.get("cwd")
        or getattr(snapshot_session, "workspace", None)
    )
    if not workspace:
        workspace = get_last_workspace()
    session = Session(
        session_id=sid,
        title=(
            cli_meta.get("title")
            or getattr(snapshot_session, "title", None)
            or title_from(getattr(snapshot_session, "messages", None) or [], "CLI Session")
        ),
        workspace=workspace,
        messages=[],
        model=cli_meta.get("model") or getattr(snapshot_session, "model", None) or "unknown",
        model_provider=(
            cli_meta.get("model_provider")
            or getattr(snapshot_session, "model_provider", None)
        ),
        created_at=cli_meta.get("created_at") or getattr(snapshot_session, "created_at", None),
        updated_at=cli_meta.get("updated_at") or getattr(snapshot_session, "updated_at", None),
        profile=cli_meta.get("profile") or getattr(snapshot_session, "profile", None),
    )
    session.is_cli_session = bool(
        getattr(snapshot_session, "is_cli_session", False)
        or is_cli_session_row(cli_meta)
    )
    session.source_tag = cli_meta.get("source_tag") or getattr(snapshot_session, "source_tag", None)
    session.raw_source = (
        cli_meta.get("raw_source")
        or getattr(snapshot_session, "raw_source", None)
        or session.source_tag
    )
    session.session_source = (
        cli_meta.get("session_source")
        or getattr(snapshot_session, "session_source", None)
    )
    session.source_label = (
        cli_meta.get("source_label")
        or getattr(snapshot_session, "source_label", None)
    )
    session.read_only = bool(
        cli_meta.get("read_only") or getattr(snapshot_session, "read_only", False)
    )
    for attr in (
        "user_id",
        "chat_id",
        "chat_type",
        "thread_id",
        "session_key",
        "platform",
        "origin_chat_id",
        "origin_user_id",
        "parent_session_id",
    ):
        value = cli_meta.get(attr)
        if value is None:
            value = getattr(snapshot_session, attr, None)
        if value is not None:
            setattr(session, attr, value)
    return session


def _resolve_share_session_pair(sid: str, handler):
    """Resolve a shareable session plus the sidecar that stores share metadata.

    Returns ``(snapshot_session, stored_session_or_none, cli_meta)``. The
    snapshot session always carries the transcript that should become the public
    share payload. ``stored_session`` is the WebUI-owned sidecar to mutate for
    share_token/share_created_at persistence; it may be absent for pure external
    sessions that have not yet created local metadata.
    """
    try:
        stored_session = get_session(sid)
        cli_meta = (
            _lookup_cli_session_metadata(sid)
            if _session_requires_cli_metadata_lookup(stored_session)
            else {}
        )
        effective_profile = (
            (cli_meta or {}).get("profile")
            or getattr(stored_session, "profile", None)
            or None
        )
        if not _session_visible_to_active_profile(effective_profile, handler):
            raise KeyError(sid)
        stored_session = _ensure_full_session_before_mutation(sid, stored_session)
        snapshot_session = copy.copy(stored_session)
        snapshot_session.messages = _share_snapshot_messages_for_session(
            stored_session,
            cli_meta=cli_meta,
        )
        return snapshot_session, stored_session, cli_meta or {}
    except KeyError:
        cli_meta = _lookup_cli_session_metadata(sid) or {}
        effective_profile = cli_meta.get("profile") or None
        if not _session_visible_to_active_profile(effective_profile, handler):
            raise KeyError(sid) from None
        synth, reason = _claim_or_synthesize_cli_session(sid, cli_meta=cli_meta)
        if reason == "was_webui" or synth is None:
            raise KeyError(sid) from None
        return synth, None, cli_meta


def _reconcile_stale_stream_state_for_session_rows(session_rows) -> bool:
    """Clear stale persisted stream fields before /api/sessions serializes rows."""
    changed = False
    for row in session_rows:
        if not isinstance(row, dict):
            continue
        sid = row.get("session_id")
        if not sid or not row.get("active_stream_id"):
            continue
        if row.get("is_streaming") is True:
            continue
        try:
            session = get_session(sid, metadata_only=True)
        except Exception:
            logger.debug(
                "Failed to load session %s while reconciling stale stream state",
                sid,
                exc_info=True,
            )
            continue
        if session is None:
            continue
        changed = _clear_stale_stream_state(session) or changed
    return changed

# ── CSRF: validate Origin/Referer on POST ────────────────────────────────────
import re as _re


def _normalize_host_port(value: str) -> tuple[str, str | None]:
    """Split a host or host:port string into (hostname, port|None).
    Handles IPv6 bracket notation, e.g. [::1]:8080."""
    value = value.strip().lower()
    if not value:
        return '', None
    if value.startswith('['):
        end = value.find(']')
        if end != -1:
            host = value[1:end]
            rest = value[end + 1 :]
            if rest.startswith(':') and rest[1:].isdigit():
                return host, rest[1:]
            return host, None
    if value.count(':') == 1:
        host, port = value.rsplit(':', 1)
        if port.isdigit():
            return host, port
    return value, None


def _ports_match(origin_scheme: str, origin_port: str | None, allowed_port: str | None) -> bool:
    """Return True when two ports should be considered equivalent, scheme-aware.

    Treats an absent port as the scheme default: port 80 for http, port 443 for https.
    Port 80 is NOT treated as equivalent to 443 (different protocols = different origins).
    """
    if origin_port == allowed_port:
        return True
    # Determine the default port for the origin's scheme
    default = '443' if origin_scheme == 'https' else '80'
    if not origin_port and allowed_port == default:
        return True
    if not allowed_port and origin_port == default:
        return True
    return False


def _allowed_public_origins() -> set[str]:
    """Parse HERMES_WEBUI_ALLOWED_ORIGINS env var (comma-separated) into a set.

    Each entry must include the scheme, e.g. https://myapp.example.com:8000.
    Entries without a scheme are silently skipped and a warning is printed.
    """
    raw = os.getenv('AGY_WEBUI_ALLOWED_ORIGINS') or os.getenv('HERMES_WEBUI_ALLOWED_ORIGINS', '')
    result = set()
    for value in raw.split(','):
        value = value.strip().rstrip('/').lower()
        if not value:
            continue
        if not (value.startswith('http://') or value.startswith('https://')):
            import sys  # noqa: F401 – kept for historical compat
            logging.warning(
                f"[webui] HERMES_WEBUI_ALLOWED_ORIGINS entry {value!r} is missing "
                f"the scheme (expected https://hostname or http://hostname). Entry ignored."
            )
            continue
        result.add(value)
    return result


def _is_browser_unsafe_request(handler) -> bool:
    """Return True when request headers identify a browser unsafe request.

    Non-browser API clients, including the MCP bridge and curl-style scripts,
    normally send no Origin/Referer and remain compatible with the existing
    same-machine API contract. Browsers send Origin for unsafe fetch/form POSTs;
    Referer is retained for older paths and proxies.
    """
    return bool(handler.headers.get("Origin") or handler.headers.get("Referer"))


def _check_same_origin_browser_request(handler, *, require_provenance: bool = False) -> bool:
    _clear_csrf_failure_reason(handler)
    origin = handler.headers.get("Origin", "")
    referer = handler.headers.get("Referer", "")
    host = handler.headers.get("Host", "")
    sec_fetch_site = handler.headers.get("Sec-Fetch-Site", "").strip().lower()
    if not (origin or referer or sec_fetch_site):
        return not require_provenance or _set_csrf_failure_reason(handler, "origin_mismatch")
    if sec_fetch_site == "cross-site":
        return _set_csrf_failure_reason(handler, "origin_mismatch")
    target = origin or referer
    if not target:
        if sec_fetch_site == "none":
            return True
        if sec_fetch_site == "same-origin":
            return not require_provenance or _set_csrf_failure_reason(
                handler, "origin_mismatch"
            )
        return _set_csrf_failure_reason(handler, "origin_mismatch")
    m = _re.match(r"^https?://([^/]+)", target)
    if not m:
        return _set_csrf_failure_reason(handler, "origin_mismatch")
    origin_host = m.group(1)
    origin_scheme = m.group(0).split('://')[0].lower()
    origin_name, origin_port = _normalize_host_port(origin_host)
    origin_allowed = False
    origin_value = m.group(0).rstrip('/').lower()
    if origin_value in _allowed_public_origins():
        origin_allowed = True
    if not origin_allowed:
        allowed_hosts = [h.strip() for h in [host] if h.strip()]
        trust_forwarded_host = (os.getenv("AGY_WEBUI_TRUST_FORWARDED_HOST") or os.getenv("HERMES_WEBUI_TRUST_FORWARDED_HOST", "")).strip().lower()
        if trust_forwarded_host in ("1", "true", "yes", "on"):
            allowed_hosts.extend(
                h.strip()
                for h in [
                    handler.headers.get("X-Forwarded-Host", ""),
                    handler.headers.get("X-Real-Host", ""),
                ]
                if h.strip()
            )
        for allowed in allowed_hosts:
            allowed_name, allowed_port = _normalize_host_port(allowed)
            if origin_name == allowed_name and _ports_match(origin_scheme, origin_port, allowed_port):
                origin_allowed = True
                break
    if not origin_allowed:
        return _set_csrf_failure_reason(handler, "origin_mismatch")
    return True


def apply_cors_preflight_headers(handler) -> None:
    """Emit CORS preflight headers on ``handler`` for a same-origin/allowlisted
    request; emit nothing for a disallowed origin (browser treats the header-less
    200 as a preflight denial).

    Echoes the request Origin only when it is same-origin or explicitly
    allowlisted via HERMES_WEBUI_ALLOWED_ORIGINS — the exact policy the CSRF gate
    enforces for real requests. Reuses _check_same_origin_browser_request so the
    preflight can never advertise wider access (`*`) than an actual request would
    be granted. A wildcard here would let any site read authenticated responses
    on a deployment with no password set. Kept in api/ so server.py stays a thin
    dispatcher.
    """
    origin = handler.headers.get("Origin", "").strip()
    if not origin or not _check_same_origin_browser_request(handler):
        return
    handler.send_header("Access-Control-Allow-Origin", origin)
    handler.send_header("Vary", "Origin")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")


def _csrf_exempt_path(path: str) -> bool:
    """Paths that cannot or must not carry a session CSRF token."""
    return path in {
        "/api/auth/login",
        "/api/auth/passkey/options",
        "/api/auth/passkey/login",
        "/api/csp-report",
    }


_CSRF_FAILURE_ATTR = "_agy_csrf_failure_reason"


def _set_csrf_failure_reason(handler, reason: str) -> bool:
    try:
        setattr(handler, _CSRF_FAILURE_ATTR, reason)
    except Exception:
        logger.debug("Silent exception in _set_csrf_failure_reason", exc_info=True)
        pass
    return False


def _clear_csrf_failure_reason(handler) -> None:
    try:
        if hasattr(handler, _CSRF_FAILURE_ATTR):
            delattr(handler, _CSRF_FAILURE_ATTR)
    except Exception:
        logger.debug("Silent exception in _clear_csrf_failure_reason", exc_info=True)
        pass


def _csrf_rejection_error(handler) -> str:
    reason = getattr(handler, _CSRF_FAILURE_ATTR, "")
    if reason == "origin_mismatch":
        return "Cross-origin mismatch - check reverse proxy headers"
    if reason == "token_mismatch":
        return "Session expired - reload the page"
    return "Cross-origin request rejected"


def _check_csrf(handler) -> bool:
    """Reject cross-origin or tokenless authenticated browser unsafe requests."""
    if not _check_same_origin_browser_request(handler):
        return False
    if not _is_browser_unsafe_request(handler):
        return True  # non-browser clients (curl, MCP, agent) have no Origin/Referer

    from api.auth import CSRF_HEADER_NAME, LEGACY_CSRF_HEADER_NAME, is_auth_enabled, parse_cookie, verify_csrf_token

    if not is_auth_enabled():
        return True
    cookie_val = parse_cookie(handler)
    submitted = (
        handler.headers.get("X-Agy-CSRF-Token")
        or handler.headers.get(CSRF_HEADER_NAME)
        or handler.headers.get(LEGACY_CSRF_HEADER_NAME)
        or handler.headers.get("X-Hermes-CSRF-Token")
        or handler.headers.get("X-CSRF-Token")
    )
    if verify_csrf_token(cookie_val or "", submitted or ""):
        return True
    return _set_csrf_failure_reason(handler, "token_mismatch")


_EXTENSION_SIDECAR_PROXY_RE = _re.compile(
    r"^/api/extensions/(?P<extension_id>[^/]+)/sidecar(?:/(?P<proxy_path>.*))?$"
)
_HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-connection",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


def _connection_bound_header_names(headers) -> set[str]:
    names = set(_HOP_BY_HOP_HEADERS)
    if not headers or not hasattr(headers, "items"):
        return names
    connection_values = []
    if hasattr(headers, "get_all"):
        connection_values.extend(headers.get_all("Connection", []))
    else:
        for name, value in headers.items():
            if str(name).lower() == "connection":
                connection_values.append(value)
    for value in connection_values:
        for token in str(value).split(","):
            normalized = token.strip().lower()
            if normalized:
                names.add(normalized)
    return names


def _match_extension_sidecar_proxy_path(path: str) -> tuple[str, str] | None:
    match = _EXTENSION_SIDECAR_PROXY_RE.match(path or "")
    if not match:
        return None
    return match.group("extension_id"), match.group("proxy_path") or ""


def _read_body_bytes(handler) -> bytes:
    raw_length = handler.headers.get("Content-Length", 0)
    try:
        length = int(raw_length)
    except (TypeError, ValueError):
        try:
            handler.close_connection = True
        except Exception:
            logger.debug("Silent exception in _read_body_bytes", exc_info=True)
            pass
        raise ValueError(f"Invalid Content-Length: {raw_length!r}") from None
    if length < 0:
        try:
            handler.close_connection = True
        except Exception:
            logger.debug("Silent exception in _read_body_bytes", exc_info=True)
            pass
        raise ValueError(f"Invalid Content-Length: {length}")
    if length > MAX_BODY_BYTES:
        try:
            handler.close_connection = True
        except Exception:
            logger.debug("Silent exception in _read_body_bytes", exc_info=True)
            pass
        raise ValueError(f"Request body too large ({length} bytes, max {MAX_BODY_BYTES})")
    return handler.rfile.read(length) if length else b""


def _extension_sidecar_proxy_request_headers(handler) -> dict[str, str]:
    headers = {}
    raw_headers = getattr(handler, "headers", None)
    if not raw_headers or not hasattr(raw_headers, "items"):
        return headers
    blocked_headers = _connection_bound_header_names(raw_headers)
    for name, value in raw_headers.items():
        lower = str(name).lower()
        if (
            lower in blocked_headers
            or lower in {"authorization", "cookie", "content-length", "host", "origin", "referer"}
            or lower.startswith("x-csrf")
            or lower.startswith("x-agy-")
            or lower.startswith("x-hermes-")
        ):
            continue
        headers[str(name)] = str(value)
    return headers


def _send_extension_sidecar_proxy_response(handler, status: int, body: bytes, headers) -> bool:
    handler.send_response(status)
    sent_content_type = False
    blocked_headers = _connection_bound_header_names(headers)
    if headers and hasattr(headers, "items"):
        for name, value in headers.items():
            lower = str(name).lower()
            if (
                lower in blocked_headers
                or lower in {"content-length", "set-cookie"}
                or lower.startswith("x-agy-")
                or lower.startswith("x-hermes-")
            ):
                continue
            if lower == "content-type":
                sent_content_type = True
            handler.send_header(str(name), str(value))
    if not sent_content_type:
        handler.send_header("Content-Type", "application/octet-stream")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    _security_headers(handler)
    handler.end_headers()
    handler.wfile.write(body)
    return True


def _read_extension_sidecar_proxy_body(stream) -> bytes:
    body = stream.read(_EXTENSION_SIDECAR_PROXY_MAX_RESPONSE_BYTES + 1)
    if len(body) > _EXTENSION_SIDECAR_PROXY_MAX_RESPONSE_BYTES:
        raise ValueError("Extension sidecar response too large")
    return body


def _extension_sidecar_proxy_redirect_url(
    allowed_origin: str,
    request_url: str,
    redirect_url: str,
) -> str | None:
    resolved = urljoin(request_url, redirect_url or "")
    allowed = urlsplit(allowed_origin or "")
    parts = urlsplit(resolved)
    if not allowed.scheme or not allowed.netloc or not parts.scheme or not parts.netloc:
        return None
    allowed_scheme = allowed.scheme.lower()
    redirect_scheme = parts.scheme.lower()
    if redirect_scheme != allowed_scheme:
        return None
    allowed_name, allowed_port = _normalize_host_port(allowed.netloc)
    redirect_name, redirect_port = _normalize_host_port(parts.netloc)
    if redirect_name != allowed_name or not _ports_match(
        allowed_scheme,
        redirect_port,
        allowed_port,
    ):
        return None
    return resolved


def _extension_sidecar_proxy_same_origin_opener(allowed_origin: str):
    class _SameOriginRedirectHandler(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            resolved = _extension_sidecar_proxy_redirect_url(
                allowed_origin,
                req.full_url,
                newurl,
            )
            if not resolved:
                raise URLError("Extension sidecar redirect crossed declared origin")
            return super().redirect_request(req, fp, code, msg, headers, resolved)

    return build_opener(ProxyHandler({}), _SameOriginRedirectHandler)


def _handle_extension_sidecar_proxy(
    handler,
    parsed,
    method: str,
    *,
    read_request_body: bool = False,
):
    matched = _match_extension_sidecar_proxy_path(parsed.path)
    if matched is None:
        return False
    # Require same-origin browser provenance on EVERY proxied method, not just
    # GET. Browser extensions (the only legitimate caller) always send Origin/
    # Referer/Sec-Fetch-Site, so this costs nothing on the real path while
    # closing the GET-vs-unsafe-method asymmetry: without it, POST/PATCH/PUT/
    # DELETE fell through the CSRF compatibility path that intentionally admits
    # non-browser clients, giving unsafe methods weaker provenance than GET.
    if not _check_same_origin_browser_request(handler, require_provenance=True):
        return j(handler, {"error": _csrf_rejection_error(handler)}, status=403)
    try:
        request_body = _read_body_bytes(handler) if read_request_body else None
    except ValueError as exc:
        status = 413 if "too large" in str(exc).lower() else 400
        return bad(handler, str(exc), status=status)
    from api.extensions import (
        ExtensionSidecarProxyError,
        resolve_extension_sidecar_proxy_target,
    )

    extension_id, proxy_path = matched
    try:
        target = resolve_extension_sidecar_proxy_target(
            extension_id,
            proxy_path,
            query=parsed.query,
        )
        proxied_headers = _extension_sidecar_proxy_request_headers(handler)
        # token-v1: inject the per-extension shared secret core minted. The
        # inbound x-hermes-* strip above guarantees the client cannot have
        # forged this header.
        _auth_token = target.get("auth_token")
        if _auth_token:
            proxied_headers["X-Hermes-Sidecar-Token"] = _auth_token
        request = Request(
            target["upstream_url"],
            data=request_body,
            headers=proxied_headers,
            method=method,
        )
        opener = _extension_sidecar_proxy_same_origin_opener(target["origin"])
        with opener.open(request, timeout=10) as response:
            body = _read_extension_sidecar_proxy_body(response)
            return _send_extension_sidecar_proxy_response(
                handler,
                getattr(response, "status", 200),
                body,
                response.headers,
            )
    except ExtensionSidecarProxyError as exc:
        return bad(handler, str(exc), status=exc.status)
    except ValueError as exc:
        return bad(handler, str(exc), status=502)
    except HTTPError as exc:
        try:
            body = _read_extension_sidecar_proxy_body(exc)
        except ValueError as read_exc:
            return bad(handler, str(read_exc), status=502)
        return _send_extension_sidecar_proxy_response(
            handler,
            exc.code,
            body,
            exc.headers,
        )
    except (TimeoutError, URLError, OSError):
        logger.warning(
            "extension sidecar proxy failed for %s %s",
            method,
            parsed.path,
            exc_info=True,
        )
        return bad(handler, "Failed to reach extension sidecar", status=502)


def _client_ip_for_rate_limit(handler) -> str:
    try:
        address = getattr(handler, "client_address", None)
        if address:
            return str(address[0])
    except Exception:
        logger.debug("Silent exception in _client_ip_for_rate_limit", exc_info=True)
        pass
    return "unknown"


def _truthy_env(*names: str) -> bool:
    for name in names:
        if os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}:
            return True
    return False


def _request_client_ip(handler) -> str:
    try:
        address = getattr(handler, "client_address", None)
        if address:
            return str(address[0] or "")
    except Exception:
        logger.debug("Silent exception in _request_client_ip", exc_info=True)
        pass
    return ""


def _ip_is_loopback_or_private(raw: str):
    """Parse an IP string; return (parsed_ok, is_loopback_or_private).

    Returns (False, False) for empty/malformed input so callers fail closed.
    """
    import ipaddress

    raw = (raw or "").strip()
    if not raw:
        return (False, False)
    try:
        addr = ipaddress.ip_address(raw)
    except ValueError:
        return (False, False)
    return (True, bool(addr.is_loopback or addr.is_private))


def _trusted_proxy_networks():
    """Networks whose socket peer is allowed to assert a forwarded client IP.

    Loopback is ALWAYS trusted implicitly (the common same-host reverse-proxy
    deployment). Operators fronting the WebUI with a LAN/remote proxy add its
    address(es) via HERMES_WEBUI_TRUSTED_PROXY_CIDRS (comma-separated CIDRs or
    bare IPs). Malformed entries are skipped, never widening trust.
    """
    import ipaddress

    nets = [
        ipaddress.ip_network("127.0.0.0/8"),
        ipaddress.ip_network("::1/128"),
        ipaddress.ip_network("::ffff:127.0.0.0/104"),
    ]
    raw = (os.getenv("AGY_WEBUI_TRUSTED_PROXY_CIDRS") or os.getenv("HERMES_WEBUI_TRUSTED_PROXY_CIDRS", "") or "")
    for token in raw.replace(";", ",").split(","):
        token = token.strip()
        if not token:
            continue
        try:
            nets.append(ipaddress.ip_network(token, strict=False))
        except ValueError:
            # Invalid CIDR/IP → skip (fail closed: never widens trust).
            continue
    return nets


def _ip_in_networks(addr, networks) -> bool:
    """Family-aware membership test.

    Checks the parsed address against each network, and — for an IPv4-mapped
    IPv6 address (e.g. ``::ffff:10.9.9.9``) — ALSO checks its embedded IPv4 form
    against IPv4 networks. Without this, a mapped-IPv6 proxy peer would never
    match an IPv4 CIDR allowlist: the trusted proxy would be treated as
    untrusted (locking out legitimate clients behind it) and, inside an XFF
    chain, a mapped trusted hop would be mis-returned as the client (admitting a
    public client that preceded it). See #5764.
    """
    candidates = [addr]
    mapped = getattr(addr, "ipv4_mapped", None)
    if mapped is not None:
        candidates.append(mapped)
    for cand in candidates:
        for net in networks:
            try:
                if cand in net:
                    return True
            except TypeError:
                # IPv4/IPv6 family mismatch between candidate and net → skip.
                continue
    return False


def _raw_peer_is_trusted_proxy(handler) -> bool:
    """True when the immediate socket peer is loopback or an allowlisted proxy.

    Only such a peer is allowed to assert a forwarded client IP. Judged on the
    RAW socket address (never a header), so it cannot be spoofed.
    """
    import ipaddress

    raw = _request_client_ip(handler)
    if not raw:
        return False
    try:
        addr = ipaddress.ip_address(raw)
    except ValueError:
        return False
    return _ip_in_networks(addr, _trusted_proxy_networks())


def _forwarded_client_ip_from_trusted_proxy(handler):
    """Resolve the real client IP from a chain fronted by a trusted proxy.

    Precondition: the caller has verified the raw socket peer is a trusted proxy.
    Consumes ALL X-Forwarded-For values (across repeated headers), preserves wire
    order, walks RIGHT-TO-LEFT skipping hops that are themselves trusted-proxy
    addresses, and returns the first non-trusted (i.e. real-client) hop. Falls
    back to X-Real-IP, then the raw socket peer. Returns None when the chain is
    present-but-empty / malformed so the caller fails closed.
    """
    import ipaddress

    try:
        xff_values = handler.headers.get_all("X-Forwarded-For") or []
    except AttributeError:
        single = handler.headers.get("X-Forwarded-For", "")
        xff_values = [single] if single else []

    hops: list[str] = []
    for header_value in xff_values:
        for token in str(header_value or "").split(","):
            hops.append(token.strip())

    if xff_values:
        # A present-but-empty / all-blank XFF is malformed → fail closed.
        if not any(hops):
            return None
        trusted_nets = _trusted_proxy_networks()

        def _is_trusted_hop(ip_str: str) -> bool:
            try:
                addr = ipaddress.ip_address(ip_str)
            except ValueError:
                return False
            return _ip_in_networks(addr, trusted_nets)

        for hop in reversed(hops):
            if not hop:
                # An empty hop inside the chain is malformed → fail closed
                # rather than skip past it (an attacker could inject blanks).
                return None
            try:
                ipaddress.ip_address(hop)
            except ValueError:
                # Non-IP token in the chain → malformed → fail closed.
                return None
            if _is_trusted_hop(hop):
                continue
            return hop
        # Every hop was a trusted proxy → no distinct client; treat as the proxy
        # tier itself (loopback/private), i.e. resolve to the raw peer below.
        return _request_client_ip(handler)

    real_ip = handler.headers.get("X-Real-IP", "").strip()
    if real_ip:
        return real_ip
    # No forwarded header at all → the trusted proxy is speaking for itself.
    return _request_client_ip(handler)


def _onboarding_request_is_local(handler) -> bool:
    """Return True when an unauthenticated onboarding request is local/private.

    Trust model (single, symmetric — see the full truth table in
    tests/test_cvd3_terminal_local_origin_gate.py):

    * Forwarded client-IP headers are honored ONLY when the RAW socket peer is a
      trusted proxy (loopback, or an address in HERMES_WEBUI_TRUSTED_PROXY_CIDRS).
      This is checked on the un-spoofable socket address, so a direct client
      cannot promote itself to "local" by sending X-Forwarded-For: 127.0.0.1.
    * When the peer is NOT a trusted proxy, forwarded headers are ignored and the
      request is classified by the raw socket peer directly. A direct loopback or
      private/LAN client (no proxy) is therefore still correctly local — so
      onboarding, first-password/passkey setup, and passwordless embedded-terminal
      access keep working on the common direct-LAN deployment.
    * HERMES_WEBUI_TRUST_FORWARDED_FOR=1 is the opt-in that makes us CONSULT the
      forwarded chain at all; without it the raw peer is authoritative. Either
      way the classification fails closed on malformed/empty chains.
    """
    trust_forwarded = _truthy_env("AGY_WEBUI_TRUST_FORWARDED_FOR", "HERMES_WEBUI_TRUST_FORWARDED_FOR")
    peer_is_trusted_proxy = _raw_peer_is_trusted_proxy(handler)

    if trust_forwarded and peer_is_trusted_proxy:
        client_ip = _forwarded_client_ip_from_trusted_proxy(handler)
        if client_ip is None:
            # Malformed/empty forwarded chain from a trusted proxy → fail closed.
            return False
        parsed_ok, is_local = _ip_is_loopback_or_private(client_ip)
        return parsed_ok and is_local

    # Not consulting the forwarded chain (either the opt-in is off, or the raw
    # peer is not a trusted proxy). Classify by the raw socket peer — it cannot
    # be spoofed by a header. A public peer sending X-Forwarded-For: 127.0.0.1 is
    # therefore correctly rejected (its raw peer is public).
    raw = _request_client_ip(handler)
    parsed_ok, is_local = _ip_is_loopback_or_private(raw)
    if not parsed_ok:
        return False

    import ipaddress

    addr = ipaddress.ip_address(raw.strip())
    if addr.is_loopback:
        # A loopback TCP source is genuinely same-host and unspoofable → local
        # even if a (ignored) forwarded header is present.
        return True

    # Non-loopback raw peer. A forwarded header being PRESENT here means the
    # request most likely arrived through a proxy we have NOT been told to trust
    # (no trusted-proxy env, or the peer isn't in the allowlist) — so a
    # private/LAN raw peer could be an untrusted proxy relaying an arbitrary
    # (public) client we can't see. Deny in that case; require the operator to
    # opt in via HERMES_WEBUI_TRUST_FORWARDED_FOR (+ HERMES_WEBUI_TRUSTED_PROXY_CIDRS
    # for a non-loopback proxy). With NO forwarded header, a direct private/LAN
    # client (the common direct-LAN deployment) stays local so onboarding,
    # first-password/passkey setup, and passwordless terminal keep working.
    forwarded_present = bool(
        (handler.headers.get("X-Forwarded-For", "") or "").strip()
        or (handler.headers.get("X-Real-IP", "") or "").strip()
    )
    if forwarded_present:
        return False
    return bool(is_local)


def _onboarding_gate_allows(handler, auth_enabled: bool | None = None) -> bool:
    from api.auth import is_auth_enabled

    auth_enabled = is_auth_enabled() if auth_enabled is None else auth_enabled
    if auth_enabled or _truthy_env("AGY_WEBUI_ONBOARDING_OPEN", "HERMES_WEBUI_ONBOARDING_OPEN"):
        return True
    return _onboarding_request_is_local(handler)


# Operator-facing copy reused by every embedded-terminal endpoint refusal.
_EMBEDDED_TERMINAL_GATE_DENIED_MESSAGE = (
    "Embedded terminal is only available from local networks when authentication "
    "is not configured. Configure a password/passkey, or set "
    "HERMES_WEBUI_ONBOARDING_OPEN=1 to allow it on a deliberately-exposed server."
)


def _embedded_terminal_gate_allows(handler) -> bool:
    """Local-origin gate for the embedded-terminal endpoints.

    The embedded terminal spawns a PTY shell that runs arbitrary commands as the
    server-process user, so admitting an unauthenticated remote caller is remote
    code execution. When auth is enabled, ``check_auth()`` has already verified
    the session cookie before the request reaches these handlers, so this returns
    True. When auth is DISABLED (the default out-of-the-box state) ``check_auth()``
    admits every caller unconditionally, so restrict the terminal to local/private
    origins — the same trust model the onboarding/bootstrap endpoints use, ignoring
    spoofable forwarded headers unless an operator has opted into trusting them.
    A deliberately-exposed passwordless server (access secured at another layer)
    opts out with ``HERMES_WEBUI_ONBOARDING_OPEN=1``.
    """
    return _onboarding_gate_allows(handler)


# Above this many distinct client keys, sweep out entries whose timestamps have
# all aged past the window on the next update. Behind a reverse proxy the map
# holds a single key (the proxy IP) and never trips this; a directly-exposed
# deployment would otherwise keep one entry forever for every IP that ever hit
# the endpoint, since a key is only revisited when that same IP calls again.
_RATE_LIMIT_MAP_SWEEP_THRESHOLD = 4096


def _prune_stale_rate_limit_keys(mapping: dict, cutoff: float) -> None:
    """Drop keys whose newest timestamp has aged out of the window. Caller holds
    the map's lock. Size-gated so the common (few-key) path stays O(1)."""
    if len(mapping) <= _RATE_LIMIT_MAP_SWEEP_THRESHOLD:
        return
    stale = [k for k, ts in mapping.items() if not ts or ts[-1] < cutoff]
    for k in stale:
        del mapping[k]


def _csp_report_rate_limited(handler, *, now: float | None = None) -> bool:
    now = time.time() if now is None else now
    key = _client_ip_for_rate_limit(handler)
    cutoff = now - _CSP_REPORT_RATE_LIMIT_WINDOW_SECONDS
    with _CSP_REPORT_RATE_LIMIT_LOCK:
        _prune_stale_rate_limit_keys(_CSP_REPORT_RATE_LIMIT, cutoff)
        timestamps = [ts for ts in _CSP_REPORT_RATE_LIMIT.get(key, []) if ts >= cutoff]
        if len(timestamps) >= _CSP_REPORT_RATE_LIMIT_MAX:
            _CSP_REPORT_RATE_LIMIT[key] = timestamps
            return True
        timestamps.append(now)
        _CSP_REPORT_RATE_LIMIT[key] = timestamps
    return False


def _client_event_rate_limited(handler, *, now: float | None = None) -> bool:
    now = time.time() if now is None else now
    key = _client_ip_for_rate_limit(handler)
    cutoff = now - _CLIENT_EVENT_RATE_LIMIT_WINDOW_SECONDS
    with _CLIENT_EVENT_RATE_LIMIT_LOCK:
        _prune_stale_rate_limit_keys(_CLIENT_EVENT_RATE_LIMIT, cutoff)
        timestamps = [ts for ts in _CLIENT_EVENT_RATE_LIMIT.get(key, []) if ts >= cutoff]
        if len(timestamps) >= _CLIENT_EVENT_RATE_LIMIT_MAX:
            _CLIENT_EVENT_RATE_LIMIT[key] = timestamps
            return True
        timestamps.append(now)
        _CLIENT_EVENT_RATE_LIMIT[key] = timestamps
    return False


def _send_no_content(handler, status: int = 204) -> bool:
    handler.send_response(status)
    handler.send_header("Content-Length", "0")
    handler.end_headers()
    return True


def _safe_content_length(handler, max_bytes: int) -> int:
    raw_length = handler.headers.get("Content-Length", 0)
    try:
        length = int(raw_length)
    except (TypeError, ValueError):
        try:
            handler.close_connection = True
        except Exception:
            logger.debug("Silent exception in _safe_content_length", exc_info=True)
            pass
        raise ValueError(f"Invalid Content-Length: {raw_length!r}") from None
    if length < 0:
        try:
            handler.close_connection = True
        except Exception:
            logger.debug("Silent exception in _safe_content_length", exc_info=True)
            pass
        raise ValueError(f"Invalid Content-Length: {length}")
    if length > max_bytes:
        try:
            handler.close_connection = True
        except Exception:
            logger.debug("Silent exception in _safe_content_length", exc_info=True)
            pass
        raise OverflowError(f"Request body too large ({length} bytes, max {max_bytes})")
    return length


def _read_csp_report_payload(handler):
    try:
        length = _safe_content_length(handler, _CSP_REPORT_MAX_BODY_BYTES)
    except OverflowError as exc:
        try:
            handler.rfile.read(_CSP_REPORT_MAX_BODY_BYTES)
        except Exception:
            logger.debug("Silent exception in _read_csp_report_payload", exc_info=True)
            pass
        return {"discarded": "body_too_large", "error": str(exc)}
    except ValueError as exc:
        return {"discarded": "invalid_content_length", "error": str(exc)}
    raw = handler.rfile.read(length) if length else b"{}"
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        logger.debug("Silent exception in _read_csp_report_payload", exc_info=True)
        return {"invalid": True, "bytes": len(raw)}


def _handle_csp_report(handler) -> bool:
    """Collect browser CSP report-only violations without requiring auth."""
    if _csp_report_rate_limited(handler):
        _CSP_REPORT_LOGGER.warning(
            "Dropped CSP report from %s: rate limit exceeded",
            _client_ip_for_rate_limit(handler),
        )
        return _send_no_content(handler)

    payload = _read_csp_report_payload(handler)
    _CSP_REPORT_LOGGER.info("CSP report from %s: %s", _client_ip_for_rate_limit(handler), payload)
    return _send_no_content(handler)


def _bounded_client_event_string(value, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:limit]


def _sanitize_client_event_url_path(value) -> str | None:
    text = _bounded_client_event_string(value, 1024)
    if not text:
        return None
    try:
        parsed = urlsplit(text)
        path = parsed.path or "/"
    except Exception:
        logger.debug("Silent exception in _sanitize_client_event_url_path", exc_info=True)
        path = text.split("?", 1)[0] or "/"
    if not path.startswith("/"):
        path = "/" + path.lstrip("/")
    return path[: _CLIENT_EVENT_ALLOWED_FIELDS["url_path"]]


def _sanitize_client_event_payload(payload: dict | None) -> dict:
    """Whitelist tiny browser diagnostic events and discard sensitive content.

    Client-side SSE diagnostics should explain transport failures without
    persisting prompts, cookies, query strings, headers, or arbitrary browser
    payloads. This helper intentionally keeps only bounded scalar metadata.
    """
    if not isinstance(payload, dict):
        return {"event": "unknown"}
    sanitized: dict[str, object] = {}
    for field, limit in _CLIENT_EVENT_ALLOWED_FIELDS.items():
        if field == "url_path":
            value = _sanitize_client_event_url_path(payload.get(field))
        else:
            value = _bounded_client_event_string(payload.get(field), limit)
        if value is not None:
            sanitized[field] = value
    ready_state = payload.get("ready_state")
    if isinstance(ready_state, bool):
        pass
    elif isinstance(ready_state, int) and 0 <= ready_state <= 3:
        sanitized["ready_state"] = ready_state
    online = payload.get("online")
    if isinstance(online, bool):
        sanitized["online"] = online
    elif isinstance(online, str):
        lowered = online.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            sanitized["online"] = True
        elif lowered in {"false", "0", "no", "off"}:
            sanitized["online"] = False
    if "event" not in sanitized:
        sanitized["event"] = "unknown"
    return sanitized


def _read_client_event_payload(handler) -> dict:
    try:
        length = _safe_content_length(handler, _CLIENT_EVENT_MAX_BODY_BYTES)
    except OverflowError:
        try:
            handler.rfile.read(_CLIENT_EVENT_MAX_BODY_BYTES)
        except Exception:
            logger.debug("Silent exception in _read_client_event_payload", exc_info=True)
            pass
        return {"event": "discarded", "reason": "body_too_large"}
    except ValueError:
        return {"event": "invalid", "reason": "invalid_content_length"}
    raw = handler.rfile.read(length) if length else b"{}"
    try:
        decoded = raw.decode("utf-8")
        payload = json.loads(decoded)
    except Exception:
        logger.debug("Silent exception in _read_client_event_payload", exc_info=True)
        return {"event": "invalid", "reason": "invalid_json"}
    return payload if isinstance(payload, dict) else {"event": "invalid", "reason": "not_object"}


def _handle_client_event_log(handler, body: dict) -> bool:
    if _client_event_rate_limited(handler):
        _CLIENT_EVENT_LOGGER.warning(
            "Dropped client event from %s: rate limit exceeded",
            _client_ip_for_rate_limit(handler),
        )
        return j(handler, {"ok": False, "error": "rate_limited"}, status=429) or True
    payload = _sanitize_client_event_payload(body)
    _CLIENT_EVENT_LOGGER.info("Client event from %s: %s", _client_ip_for_rate_limit(handler), payload)
    return j(handler, {"ok": True, "event": payload.get("event")}) or True


from api.route_config_models import (  # noqa: F401 — re-exports for backward compat
    _PROFILE_CONFIG_CACHE,
    _PROFILE_CONFIG_CACHE_LOCK,
    _PROFILE_CONFIG_CACHE_TTL_SECONDS,
    _canonical_context_provider,
    _catalog_group_owns_exact_model,
    _catalog_has_provider,
    _catalog_model_id_matches,
    _catalog_provider_id_sets,
    _clean_session_model_provider,
    _context_length_config_api_key_for_provider,
    _context_length_lookup_inputs_for_model,
    _custom_provider_api_key_for_context,
    _custom_provider_slug_for_context,
    _load_profile_config_dict,
    _moa_fast_path_model_state,
    _model_lookup_candidates,
    _model_matches_active_provider_family,
    _model_matches_configured_default,
    _models_config_context_length,
    _normalize_provider_id,
    _normalize_session_model_in_place,
    _ordered_custom_provider_model_ids,
    _positive_context_length,
    _providers_match_for_context,
    _read_profile_config_cached,
    _read_profile_model_config,
    _repair_bare_custom_provider_model,
    _repair_foreign_session_model_provider,
    _rescale_threshold_tokens_for_context_window,
    _resolve_compatible_session_model,
    _resolve_compatible_session_model_state,
    _resolve_context_length_for_session_model,
    _resolve_effective_session_model_for_display,
    _resolve_effective_session_model_provider_for_display,
    _session_context_length_lookup_state,
    _session_model_identity_matches,
    _session_model_state_from_request,
    _should_accept_session_context_length_refresh,
    _should_attach_codex_provider_context,
    _split_provider_qualified_model,
    _starts_token,
    _worktree_default_from_config,
)

def _lookup_gateway_session_identity(session_id: str) -> dict:
    if not session_id:
        return {}
    metadata = _load_gateway_session_identity_map().get(str(session_id))
    return metadata if isinstance(metadata, dict) else {}


def _lookup_cli_session_metadata(session_id: str, *, all_profiles: bool = False) -> dict:
    if not session_id:
        return {}
    try:
        for row in get_cli_sessions(all_profiles=all_profiles):
            if row.get("session_id") == session_id:
                return row
    except Exception:
        logger.debug("Silent exception in _lookup_cli_session_metadata", exc_info=True)
        return {}
    return {}


def _session_index_marks_was_webui(sid: str) -> bool:
    """Return True iff ``sid`` is in the WebUI session index as a WebUI- or
    fork-origin row whose sidecar is now gone.

    The WebUI session index (``SESSION_INDEX_FILE``) is the canonical registry
    of sessions the WebUI ever owned. A row there tagged with ``webui`` or
    ``fork`` means the session once had a sidecar that has since been deleted
    (or never materialised on this profile). Returning 404 to the client on
    these ids is what lets the browser self-heal: strip the stale
    ``/session/<id>`` URL and clear localStorage instead of silently
    re-attaching to a now-empty session (#2782).

    Foreign-origin rows (CLI, TUI, Desktop, claude_code, gateway, telegram,
    etc.) — those with explicit non-webui source tags, OR blank sources with
    ``is_cli_session``/``read_only`` markers — are NOT treated as deleted
    WebUI sessions, even when the sidecar is absent.
    """
    if not SESSION_INDEX_FILE.exists():
        return False
    try:
        entries = json.loads(SESSION_INDEX_FILE.read_bytes())
    except Exception:
        logger.debug("Silent exception in _session_index_marks_was_webui", exc_info=True)
        return False
    for entry in entries if isinstance(entries, list) else []:
        if entry.get("session_id") != sid:
            continue
        # Classify per source field, not on a collapsed `a or b or c` — a
        # legacy CLI/imported row can carry is_cli_session:true with BLANK
        # source fields, and collapsing-then-defaulting-to-WebUI would wrongly
        # 404 it (it should keep its read-only CLI stub).
        srcs = [
            str(entry.get("source_tag") or "").strip().lower(),
            str(entry.get("raw_source") or "").strip().lower(),
            str(entry.get("session_source") or "").strip().lower(),
        ]
        explicit = [s for s in srcs if s]
        if any(s in ("webui", "fork") for s in explicit):
            # Explicit WebUI-origin (incl. forks, which /api/session/branch
            # stamps session_source="fork") — a deleted sidecar bricks
            # identically. 404.
            return True
        if explicit:
            # Explicit non-WebUI source (cli, telegram, claude_code, ...) —
            # genuine foreign session, keep the existing CLI/read-only stub.
            return False
        # All source fields blank: WebUI-origin UNLESS the row is a legacy
        # CLI/imported session marked only by is_cli_session / read_only.
        is_cli = entry.get("is_cli_session") is True
        is_read_only = bool(entry.get("read_only") or entry.get("is_read_only"))
        return not (is_cli or is_read_only)
    return False


def _session_deleted_tombstone_marks_was_webui(sid: str) -> bool:
    try:
        return sid in _load_webui_deleted_session_tombstone()
    except Exception:
        logger.warning("Silent exception in _session_deleted_tombstone_marks_was_webui", exc_info=True)
        return False


def _state_db_session_source(sid: str) -> str:
    """Return the lowercased ``sessions.source`` for ``sid`` from state.db.

    Cheap single-row lookup used to distinguish delegated ``subagent`` children
    (which have a recoverable state.db transcript) from genuinely-deleted WebUI
    sessions.  Returns "" on any error / missing row so callers fall back to
    their existing behaviour.
    """
    if not sid or not is_safe_session_id(sid):
        return ""
    try:
        from api.models import _active_state_db_path
        db_path = _active_state_db_path()
        if not db_path or not Path(db_path).exists():
            return ""
        with webui_session_db.open_db_readonly(db_path) as _conn:
            row = _conn.execute(
                "SELECT source FROM sessions WHERE id = ?", (sid,)
            ).fetchone()
    except Exception:
        logger.debug("Silent exception in _state_db_session_source", exc_info=True)
        return ""
    if not row:
        return ""
    return str(row[0] or "").strip().lower()


def _is_subagent_child_session_id(sid: str) -> bool:
    """Return True when ``sid`` is a delegated subagent child in state.db.

    Delegated ``delegate_task`` children are recorded in Hermes state.db with
    ``source='subagent'`` and a ``parent_session_id``. They frequently have no
    WebUI sidecar (they ran server-side), so opening one from the sidebar must
    recover the transcript from state.db rather than 404 as a deleted WebUI
    session (#5307).
    """
    return _state_db_session_source(sid) == "subagent"


def _session_is_subagent_view_only(sid: str) -> bool:
    """Return True when ``sid`` is a delegated subagent child by ANY signal —
    state.db source OR a persisted WebUI sidecar tagged subagent.

    Delegated children are view-only and owned by the delegate runner. Direct
    transcript/metadata mutation routes (delete / truncate / clear / pin /
    rename / move) must refuse them so a stray WebUI action can't delete or
    fork the child's state.db transcript (#5307). This is the shared
    defense-in-depth guard for routes that bypass
    ``_get_or_materialize_session()``.
    """
    if _is_subagent_child_session_id(sid):
        return True
    try:
        s = get_session(sid)
    except Exception:
        logger.debug("Silent exception in _session_is_subagent_view_only", exc_info=True)
        return False
    src = (
        str(getattr(s, "source_tag", "") or getattr(s, "raw_source", "")
            or getattr(s, "session_source", "") or "").strip().lower()
    )
    return src == "subagent"


def _is_claimable_cli_source(cli_meta: dict, state_db_source: str = "") -> tuple[bool, str]:
    """Decide whether a foreign-origin session is safe to claim writeable
    in WebUI. Returns ``(claimable, reason_if_not)``.

    Policy mirrors ``/api/session/import_cli``:
    sessions explicitly marked ``read_only`` in their foreign store are
    surfaced as read-only stubs but never materialised as writable
    WebUI sidecars. We extend that with a denylist of foreign-source
    families whose ownership belongs to a non-WebUI process
    (messaging channels, external agents, Claude Code, scheduled
    cron runs, and platformless gateway fallbacks), so a WebUI POST
    cannot accidentally turn them into writable sidecars and violate
    their ownership boundary (#4911 review + the residual
    gateway/unknown gap flagged in the follow-up + the cron-claim
    policy flagged in the Greptile 4/5 review).

    The check is denylist-based: if a source is in any of the
    refused families below, it is non-claimable. Everything else
    (CLI, TUI, Desktop, plus future local agent sources) is allowed.
    TUI/Desktop sessions whose cli_meta is empty (they don't appear
    in ``get_cli_sessions()`` due to the CLI cap) fall through to
    ``state_db_source``; state.db has a ``source`` column with values
    like ``tui``, ``desktop``, ``cli``, ``cron``, ``claude_code``,
    ``messaging``, ``external_agent``, ``gateway`` (platform-tagged
    gateways land in ``_MESSAGING_RAW_SOURCES`` and are caught by
    the messaging check above; bare ``"gateway"`` / ``"unknown"``
    literals are caught here).
    """
    cm = cli_meta or {}
    if bool(cm.get("read_only")):
        return False, "explicit_readonly"
    session_source = (cm.get("session_source") or "").strip().lower()
    if session_source in {"messaging", "external_agent"}:
        return False, f"session_source={session_source}"
    # Track cli_meta-sourced and state.db-sourced source_tag values
    # separately so the diagnostic reason string never mislabels a
    # cli_meta-sourced denial as a state.db-sourced one (Greptile P2,
    # #4911 follow-up).  The reason is currently discarded by the
    # caller, but it is exported in the return tuple and may surface
    # in a future log / user-visible diagnostic.
    cli_meta_source_tag = (cm.get("source_tag") or cm.get("raw_source") or "").strip().lower()
    if cli_meta_source_tag in {"claude_code", "cron", "external_agent",
                                "gateway", "messaging", "subagent", "unknown"}:
        # gateway/unknown are the platformless gateway fallbacks
        # (gateway/run.py, gateway/slash_commands.py) — they own the
        # conversation in the gateway, not in WebUI.
        # cron sessions are scheduled and owned by the cron runner
        # process; claiming them into a writable WebUI sidecar would
        # let a stray POST break the next scheduled run.
        # messaging / external_agent can be supplied by the foreign
        # store directly in source_tag (in addition to the
        # session_source / messaging-record checks above), and they
        # need the same provenance-correct refusal.
        return False, f"cli_meta_source={cli_meta_source_tag}"
    if _is_messaging_session_record(cm):
        return False, "messaging_record"
    # Empty cli_meta is the common case for TUI/Desktop; fall through
    # to state.db's source column.  Refuse known-foreign state.db sources.
    if not cli_meta_source_tag and state_db_source:
        state_db_source_tag = state_db_source.strip().lower()
        if state_db_source_tag in {"claude_code", "cron", "messaging",
                                    "external_agent", "gateway", "subagent", "unknown"}:
            return False, f"state_db_source={state_db_source_tag}"
    return True, ""


def _claim_or_synthesize_cli_session(sid: str, cli_meta: dict = None):
    """Resolve a session_id that has no WebUI sidecar.

    Returns ``(session_or_None, reason)``. Reasons:

      ``'materialized'``
        A state.db row with messages exists AND the foreign source is
        claimable per :func:`_is_claimable_cli_source` (CLI / TUI /
        Desktop, no explicit read_only, not a messaging / claude_code
        session). ``session`` is a fully populated
        :class:`api.models.Session` ready for writeable use; the caller
        MUST call ``session.save()`` to persist a WebUI-owned sidecar
        before the first write.  The Session carries the source-tag
        metadata from the CLI/state.db lookup (``is_cli_session=True``,
        ``read_only=False``) so the sidebar still renders the original
        source badge.

      ``'not_claimable'``
        The sid has recoverable state.db messages but the foreign
        source is owned by a non-WebUI process (messaging channel,
        claude_code, external_agent, or explicit read_only). ``session``
        is still returned, but with ``read_only=True`` preserved and
        the foreign source tag intact, so the GET stub continues to
        render the original badge and the read-only banner. The POST
        path must return 403 (not 404) so the user sees a clear
        refusal instead of the empty-state self-heal that the 404
        handler triggers (#4911 review).

      ``'was_webui'``
        The sid is in the WebUI session index as a webui/fork origin row but
        its sidecar is gone.  Callers MUST return 404 so the browser clears
        its stale ``/session/<id>`` URL and localStorage instead of silently
        re-attaching to a now-empty session (#2782).

      ``'no_foreign_state'``
        The sid has no WebUI sidecar AND no recoverable messages in
        state.db.  Callers MUST return 404.

      ``'invalid_sid'``
        ``sid`` failed :func:`is_safe_session_id`.  Callers MUST return 404.

    ``cli_meta`` is an optional pass-through.  Callers that already
    computed ``_lookup_cli_session_metadata(sid)`` (e.g. the GET path
    building a sidebar dict) can pass it in to avoid the redundant
    lookup; callers without it (POST path, tests) pass nothing and the
    helper does the lookup itself.

    Closing the GET-vs-POST asymmetry for foreign-origin sessions: GET
    ``/api/session`` and POST ``/api/chat/start`` both call this helper
    on a missing-sidecar KeyError, so a TUI/Desktop/CLI session can be
    loaded read-only AND continued writeable from the WebUI.
    """
    def build_workspace(sid, cli_meta):
        """Coalesce workspace with sane fallbacks so _start_run doesn't
        trip on a missing field. state.db's cwd is the canonical workspace for
        agent sessions; CLI metadata is the fallback (handles Telegram/etc).
        """
        workspace = (cli_meta or {}).get("workspace") or (cli_meta or {}).get("cwd")
        if not workspace:
            try:
                from api.workspace import get_last_workspace
                workspace = get_last_workspace()
            except Exception:
                logger.warning("Silent exception in build_workspace", exc_info=True)
                workspace = None
        if not workspace:
            try:
                from api.models import DEFAULT_WORKSPACE
                workspace = DEFAULT_WORKSPACE
            except Exception:
                logger.warning("Silent exception in build_workspace", exc_info=True)
                workspace = "/"
        return workspace

    def build_session(sid, cli_meta, msgs, read_only_flag, is_cli_flag=True):
        return Session(
            session_id=sid,
            title=(cli_meta or {}).get("title") or "CLI Session",
            workspace=build_workspace(sid, cli_meta),
            model=(cli_meta or {}).get("model") or "unknown",
            model_provider=(cli_meta or {}).get("model_provider"),
            messages=msgs,
            created_at=(cli_meta or {}).get("created_at") or 0,
            updated_at=(cli_meta or {}).get("updated_at") or 0,
            profile=(cli_meta or {}).get("profile"),
            # ``is_cli_flag`` is True for genuine CLI/TUI/Desktop sessions so the
            # sidebar renders the source badge and the client's external-session
            # gating applies. It is False for delegated subagent children (#5307):
            # they are recovered read-only and must NOT be CLI-classified, or they
            # would pass the frontend ``_isExternalSession`` poll-skip /
            # active-refresh gates that #3603 keeps narrow.
            is_cli_session=is_cli_flag,
            source_tag=(cli_meta or {}).get("source_tag"),
            raw_source=(cli_meta or {}).get("raw_source"),
            session_source=(cli_meta or {}).get("session_source"),
            source_label=(cli_meta or {}).get("source_label"),
            # ``read_only_flag`` is True for not_claimable sources (foreign
            # store marked them read-only / messaging / claude_code) and
            # False for genuine CLI / TUI / Desktop sessions.  Only the
            # POST claim path with a verified-claimable source can
            # actually write; the GET stub always reflects whatever the
            # helper returns so the read-only banner stays accurate.
            read_only=read_only_flag,
        )

    if not is_safe_session_id(sid):
        return None, "invalid_sid"
    if (
        (
            _session_index_marks_was_webui(sid)
            or (
                _session_deleted_tombstone_marks_was_webui(sid)
                and _state_db_session_source(sid) in ("", "webui", "fork")
            )
        )
        and not _is_subagent_child_session_id(sid)
    ):
        # A delegated subagent child (source='subagent' in state.db) can be
        # registered in the WebUI index as a webui/fork/blank-source row (it
        # shares the parent's lineage) yet have no WebUI sidecar of its own.
        # Those must recover their transcript from state.db below rather than
        # 404 as a genuinely-deleted WebUI session (#5307). Every other
        # index-marked-WebUI id keeps the #2782 self-heal 404 contract.
        #
        # The durable delete tombstone only 404s a row that is WebUI-owned
        # (source webui/fork, or blank = a stale URL with no surviving row).
        # A foreign-source row (messaging/cli/tui/desktop) that happens to
        # carry a tombstone — e.g. a WebUI delete of an imported session whose
        # state.db row the external writer later re-created — must still
        # materialize its transcript, never be self-healed to a 404 (#5504).
        return None, "was_webui"
    if cli_meta is None:
        cli_meta = _lookup_cli_session_metadata(sid) or {}
    msgs = get_cli_session_messages(sid)
    if not msgs:
        return None, "no_foreign_state"
    # TUI/Desktop sessions often have empty cli_meta (they don't appear in
    # get_cli_sessions() because of the cap).  Fall back to the state.db
    # ``source`` column to make the claim-eligibility check robust and to
    # populate the Session's source-tag metadata so the sidebar still
    # renders the correct badge for these sessions.
    state_db_source = ""
    state_db_row = None
    try:
        from api.models import _active_state_db_path
        db_path = _active_state_db_path()
        if db_path and Path(db_path).exists():
            with webui_session_db.open_db_readonly(db_path) as _conn:
                _row = _conn.execute(
                    "SELECT source, title, model, cwd, started_at, ended_at "
                    "FROM sessions WHERE id = ?", (sid,)
                ).fetchone()
                if _row is not None:
                    state_db_row = dict(_row)
                    state_db_source = str(_row["source"] or "").strip().lower()
    except Exception:
        logger.debug("Silent exception in _claim_or_synthesize_cli_session", exc_info=True)
        state_db_source = ""
    # Populate source metadata from state.db when cli_meta is empty so the
    # synthesized Session carries the right source_tag/source_label.  Only
    # fill fields that are actually missing from cli_meta; the foreign store
    # always wins when both are present.
    #
    # No-mutation contract (Greptile #4911 follow-up): the GET path passes
    # a pre-computed cli_meta dict and expects it to be unchanged after
    # this helper returns.  We use a single copy-on-write rebind at the
    # top of the block and then plain subscript assignment so the
    # caller's dict is never touched in place.
    if state_db_row:
        cli_meta = dict(cli_meta or {})
        if not cli_meta.get("source_tag") and state_db_source:
            cli_meta["source_tag"] = state_db_source
        if not cli_meta.get("raw_source") and state_db_source:
            cli_meta["raw_source"] = state_db_source
        if not cli_meta.get("title") and state_db_row.get("title"):
            cli_meta["title"] = state_db_row["title"]
        if not cli_meta.get("model") and state_db_row.get("model"):
            cli_meta["model"] = state_db_row["model"]
        if not cli_meta.get("workspace") and state_db_row.get("cwd"):
            cli_meta["workspace"] = state_db_row["cwd"]
        # Map state.db timestamps to created_at/updated_at on the
        # synthesized Session.  Without this, the first POST writes
        # epoch (0) timestamps into the permanent sidecar and the
        # sidebar sorts/dates the session as "Jan 1 1970" (Greptile
        # #4911 follow-up, P1).  created_at always comes from
        # started_at; Session.save() never touches created_at (it's
        # not in the metadata touch list), so the mapping is
        # load-bearing on both the GET stub and the POST claim path.
        # updated_at prefers ended_at (last activity) and falls back
        # to started_at — note that on the POST claim path
        # Session.save() defaults to touch_updated_at=True and stamps
        # updated_at to wall-clock now, so this value is only the
        # GET-stub display value; the claimed sidecar's updated_at
        # reflects the moment of claim (the desired "just now" UX).
        if not cli_meta.get("created_at") and state_db_row.get("started_at"):
            cli_meta["created_at"] = state_db_row["started_at"]
        if not cli_meta.get("updated_at"):
            _ended = state_db_row.get("ended_at")
            _started = state_db_row.get("started_at")
            if _ended or _started:
                cli_meta["updated_at"] = _ended or _started
    claimable, _reason = _is_claimable_cli_source(cli_meta, state_db_source)
    if not claimable:
        # The session is real and viewable, but the foreign source forbids
        # the WebUI from taking write ownership.  Build the Session with
        # readonly=True so the GET stub keeps rendering the original
        # read-only badge, and return 'not_claimable' so the POST path
        # 403s instead of bare-404ing.
        #
        # Delegated subagent children (#5307) additionally must NOT be
        # CLI-classified: they are recovered read-only for viewing, but
        # is_cli_session=True would let them pass the frontend
        # _isExternalSession poll-skip / active-refresh gates that #3603
        # keeps narrow. Every other non-claimable foreign source keeps the
        # CLI classification so its source badge renders.
        _sa_child = _is_subagent_child_session_id(sid)
        return (
            build_session(sid, cli_meta, msgs, read_only_flag=True,
                          is_cli_flag=not _sa_child),
            "not_claimable",
        )
    return build_session(sid, cli_meta, msgs, read_only_flag=False), "materialized"


def _request_wants_all_profiles_import(body) -> bool:
    if not isinstance(body, dict):
        return False
    value = body.get("all_profiles")
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _normalize_import_profile_value(value):
    profile = str(value or "").strip()
    if not profile:
        return None
    try:
        from api.profiles import _PROFILE_ID_RE
        if profile != "default" and not _PROFILE_ID_RE.fullmatch(profile):
            return ""
    except Exception:
        logger.debug("Silent exception in _normalize_import_profile_value", exc_info=True)
        pass
    return profile


def _load_branch_source_or_refuse(handler, sid: str):
    if _session_is_subagent_view_only(sid):
        bad(handler, "Subagent sessions are view-only and cannot be branched from WebUI", 400)
        return None
    try:
        source = get_session(sid)
    except KeyError:
        _foreign_session, _reason = _claim_or_synthesize_cli_session(sid)
        _source_kind = str((getattr(_foreign_session, "source_tag", None) or getattr(_foreign_session, "raw_source", None) or getattr(_foreign_session, "source", None) or "")).strip().lower() if _foreign_session is not None else ""
        if _reason == "not_claimable" and _foreign_session is not None and _source_kind == "cron":
            _foreign_session._branch_source_readonly = True; return _foreign_session
        if _reason == "not_claimable": bad(handler, "Read-only sessions cannot be branched from WebUI", 403); return None
        bad(handler, "Session not found", 404)
        return None
    # A PERSISTED (stored) session can also be read-only (e.g. a cron-owned or
    # messaging-sourced sidecar). Apply the SAME read-only branch gate as the
    # synthesized path: allow forking only a canonical-cron read-only source
    # (server-authoritative source kind, not the id prefix), marking it so the fork
    # never .save()s the read-only source; refuse every other read-only source.
    if bool(getattr(source, "read_only", False)):
        _source_kind = str((getattr(source, "source_tag", None) or getattr(source, "raw_source", None) or getattr(source, "source", None) or "")).strip().lower()
        if _source_kind == "cron":
            source._branch_source_readonly = True
            return source
        bad(handler, "Read-only sessions cannot be branched from WebUI", 403)
        return None
    return source


def _resolve_cli_import_metadata(session_id: str, *, requested_profile=None, allow_all_profiles: bool = False) -> dict:
    cli_meta = _lookup_cli_session_metadata(session_id)
    if cli_meta and (not requested_profile or _profiles_match(cli_meta.get("profile"), requested_profile)):
        return cli_meta
    if not allow_all_profiles:
        return {}
    cli_meta = _lookup_cli_session_metadata(session_id, all_profiles=True)
    if cli_meta and requested_profile and not _profiles_match(cli_meta.get("profile"), requested_profile):
        return {}
    return cli_meta or {}


def _messaging_session_identity(session: dict, raw_source: str) -> str:
    sid = _safe_first(session.get("session_id"))
    if sid and _is_pre_compression_continuation_row(session):
        return f"{raw_source}|session_id:{sid}"

    metadata = _lookup_gateway_session_identity(session.get("session_id"))
    session_key = _safe_first(
        metadata.get("session_key"),
        session.get("session_key"),
        session.get("gateway_session_key"),
    )
    if session_key:
        return f"{raw_source}|session_key:{session_key}"

    chat_id = _safe_first(
        metadata.get("chat_id"),
        session.get("chat_id"),
        session.get("origin_chat_id"),
    )
    thread_id = _safe_first(metadata.get("thread_id"), session.get("thread_id"))
    chat_type = _safe_first(metadata.get("chat_type"), session.get("chat_type"))
    user_id = _safe_first(
        metadata.get("user_id"),
        session.get("user_id"),
        session.get("origin_user_id"),
    )

    identity_parts = []
    if chat_type:
        identity_parts.append(f"chat_type:{chat_type}")
    if chat_id:
        identity_parts.append(f"chat_id:{chat_id}")
    if thread_id:
        identity_parts.append(f"thread_id:{thread_id}")
    if user_id:
        identity_parts.append(f"user_id:{user_id}")

    if identity_parts:
        return f"{raw_source}|" + "|".join(identity_parts)

    return raw_source


def _is_pre_compression_snapshot_id(session_id: str) -> bool:
    sid = _safe_first(session_id)
    if not sid or not all(c in "0123456789abcdefghijklmnopqrstuvwxyz_" for c in sid):
        return False
    try:
        path = SESSION_DIR / f"{sid}.json"
        if not path.exists():
            return False
        data = json.loads(path.read_text(encoding="utf-8"))
        return bool(data.get("pre_compression_snapshot"))
    except Exception:
        logger.debug("Silent exception in _is_pre_compression_snapshot_id", exc_info=True)
        return False


def _is_pre_compression_continuation_row(session: dict) -> bool:
    parent_sid = _safe_first(session.get("parent_session_id"))
    return bool(parent_sid and _is_pre_compression_snapshot_id(parent_sid))


def _session_messaging_raw_source(session: dict) -> str:
    raw = _safe_first(
        session.get("raw_source"),
        session.get("source_tag"),
        session.get("source"),
        session.get("platform"),
    )
    if not raw:
        raw = session.get("source_label") or "messaging"
    return _normalize_messaging_source(raw)


def _has_durable_messaging_identity(session: dict) -> bool:
    metadata = _lookup_gateway_session_identity(session.get("session_id"))
    return bool(_safe_first(
        metadata.get("session_key"),
        session.get("session_key"),
        session.get("gateway_session_key"),
        metadata.get("chat_id"),
        session.get("chat_id"),
        session.get("origin_chat_id"),
        metadata.get("thread_id"),
        session.get("thread_id"),
    ))


def _numeric_count(value) -> int:
    try:
        return int(float(_safe_first(value, 0) or 0))
    except (TypeError, ValueError):
        return 0


def _should_hide_stale_messaging_session(
    session: dict,
    active_gateway_session_ids: set[str],
    active_gateway_sources: set[str],
) -> bool:
    """Hide stale Gateway-owned internal rows after an external chat moved on.

    Hermes Gateway keeps the external conversation identity in sessions.json.
    Compression/session-reset can leave old Agent state.db rows behind; those
    rows are implementation segments, not distinct conversations users chose.
    Only apply this aggressive hiding when Gateway is currently advertising an
    active session for the same messaging source. Without that source-of-truth
    file we keep the old fallback behavior.
    """
    raw_source = _session_messaging_raw_source(session)
    if not _is_known_messaging_source(raw_source):
        return False
    if not active_gateway_session_ids or raw_source not in active_gateway_sources:
        return False

    sid = _safe_first(session.get("session_id"))
    if sid and sid in active_gateway_session_ids:
        return False

    if _safe_first(session.get("end_reason")) in _STALE_MESSAGING_END_REASONS:
        return True

    if not _has_durable_messaging_identity(session):
        if _is_pre_compression_continuation_row(session):
            return False
        parent_sid = _safe_first(session.get("parent_session_id"))
        if parent_sid and parent_sid in active_gateway_session_ids:
            return True
        return True

    if session.get("parent_session_id") and not _is_pre_compression_continuation_row(session):
        return True

    message_count = _numeric_count(session.get("message_count"))
    actual_count = _numeric_count(session.get("actual_message_count"))
    if message_count <= 0 and actual_count <= 0:
        return True

    return False


def _is_messaging_session_record(session) -> bool:
    """Return true for sessions backed by external messaging channels."""
    if not session:
        return False
    if (
        (getattr(session, "session_source", None) if not isinstance(session, dict) else session.get("session_source")) == "messaging"
    ):
        return True
    raw = _safe_first(
        getattr(session, "raw_source", None) if not isinstance(session, dict) else session.get("raw_source"),
        getattr(session, "source_tag", None) if not isinstance(session, dict) else session.get("source_tag"),
        getattr(session, "source", None) if not isinstance(session, dict) else session.get("source"),
        session.get("source_label") if isinstance(session, dict) else None,
    )
    return _is_known_messaging_source(raw)


def _messages_include_tool_metadata(messages) -> bool:
    """Return true when returned messages can reconstruct their own tool cards."""
    if not isinstance(messages, list):
        return False
    for msg in messages:
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        if isinstance(msg.get("tool_calls"), list) and msg.get("tool_calls"):
            return True
        content = msg.get("content")
        if isinstance(content, list) and any(
            isinstance(part, dict) and part.get("type") == "tool_use"
            for part in content
        ):
            return True
    return False


def _tool_calls_for_message_window(tool_calls, start_idx: int, message_count: int) -> list:
    """Keep session-level tool calls that point into a returned message window.

    ``assistant_msg_idx`` is stored in the full transcript coordinate space, but
    the frontend renders the returned ``messages`` array from index 0. Rebase the
    index into the returned window so legacy session-level tool cards still
    anchor to their visible assistant turn after paginated loads.
    """
    if not isinstance(tool_calls, list) or message_count <= 0:
        return []
    end_idx = start_idx + message_count
    filtered = []
    for tool_call in tool_calls:
        if not isinstance(tool_call, dict):
            continue
        assistant_idx = tool_call.get("assistant_msg_idx")
        if isinstance(assistant_idx, bool) or not isinstance(assistant_idx, int):
            continue
        if start_idx <= assistant_idx < end_idx:
            rebased = dict(tool_call)
            rebased["assistant_msg_idx"] = assistant_idx - start_idx
            filtered.append(rebased)
    return filtered


def _message_counts_as_renderable_for_window(message) -> bool:
    """Return true when a paginated window should include this transcript row.

    Tool result rows are rendered through their assistant anchor or hidden as raw
    tool output. Empty partial activity rows can be preserved after cancellation
    to keep thinking/tool details inspectable, but they are not reply text. A
    tail page containing only transient metadata makes the frontend open to
    collapsed activity while newer real replies sit behind "load older messages".
    """
    if not isinstance(message, dict):
        return False
    if _is_empty_partial_activity_message(message):
        return False
    role = str(message.get("role") or "").strip().lower()
    return bool(role and role != "tool")


def _tool_call_ids_in_messages(messages) -> set:
    """Collect tool-call IDs declared on renderable rows (assistant tool_calls /
    partial tool_calls / Anthropic tool_use content blocks) so trailing
    tool-result rows can be matched back to a call present in the window."""
    ids = set()
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        for key in ("tool_calls", "_partial_tool_calls"):
            for call in msg.get(key) or []:
                if isinstance(call, dict):
                    cid = call.get("id") or call.get("tool_call_id")
                    if cid:
                        ids.add(str(cid))
        content = msg.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "tool_use":
                    cid = part.get("id")
                    if cid:
                        ids.add(str(cid))
    return ids


def _tool_result_matches_call_ids(message, call_ids) -> bool:
    """Return True if a role:tool row's tool_call_id/tool_use_id is in ``call_ids``."""
    if not call_ids or not isinstance(message, dict):
        return False
    if str(message.get("role") or "").lower() != "tool":
        return False
    tid = message.get("tool_call_id") or message.get("tool_use_id") or ""
    return bool(tid) and str(tid) in call_ids


def _message_window_for_display(messages, msg_limit=None, msg_before=None, expand_renderable=False) -> tuple[list, int]:
    """Return a paginated message window plus its offset in ``messages``.

    ``msg_limit`` is a visible transcript limit, not a raw storage-row cap.
    Tool result rows are hidden or folded into assistant tool cards, so they
    should not consume the user's "load N messages" budget. Return the smallest
    suffix containing the last ``msg_limit`` renderable user/assistant rows, plus
    any intervening tool rows needed for card snippets.

    ``expand_renderable`` is accepted for compatibility with older frontend
    callers. Visible-row expansion is now the default for every limited window.
    """
    _ = expand_renderable
    messages = list(messages or [])
    if msg_before is not None:
        before_idx = max(0, min(int(msg_before), len(messages)))
    else:
        before_idx = len(messages)
    source = messages[:before_idx]
    if not source:
        return [], 0
    if not msg_limit:
        return source, 0
    limit = max(1, int(msg_limit))
    end_idx = len(source)
    last_renderable_idx = None
    for idx in range(end_idx - 1, -1, -1):
        if _message_counts_as_renderable_for_window(source[idx]):
            last_renderable_idx = idx
            break
    if last_renderable_idx is None:
        start_idx = max(0, end_idx - limit)
        return source[start_idx:end_idx], start_idx
    # Keep the last renderable row, plus any immediately-following tool-result
    # rows whose tool_call_id matches a tool-call on a renderable row already in
    # the window. The renderer rebuilds tool cards (CLI-origin / empty
    # S.toolCalls path) from role:"tool" rows indexed by tool_call_id
    # (static/ui.js resultsByTid), so dropping the result row that follows the
    # newest assistant tool-call would leave that card without its snippet.
    # Orphan trailing tool-only rows (no matching call in the window) are still
    # skipped, preserving the visible-row budget. (#4070 ship-review)
    end_idx = last_renderable_idx + 1
    window_tool_call_ids = _tool_call_ids_in_messages(source[: last_renderable_idx + 1])
    while end_idx < len(source) and not _message_counts_as_renderable_for_window(
        source[end_idx]
    ):
        if _tool_result_matches_call_ids(source[end_idx], window_tool_call_ids):
            end_idx += 1
        else:
            break
    start_idx = 0
    renderable_count = 0
    for idx in range(last_renderable_idx, -1, -1):
        if not _message_counts_as_renderable_for_window(source[idx]):
            continue
        renderable_count += 1
        if renderable_count >= limit:
            start_idx = idx
            break
    window = source[start_idx:end_idx]
    return window, start_idx


_LIMITED_TOOL_CONTENT_MAX_CHARS = 4096
# Server-side ceiling on the ?msg_limit= tail-window size. A client could
# otherwise request msg_limit=1000000 and force the server to assemble and
# serialize an unbounded message payload (the frontend's own pagination grows
# by ~30 at a time, with one outline-jump path asking for 9999). The ceiling is
# generous — far above any legitimate visible-row window — so real pagination is
# unaffected; it only caps the pathological/oversized request. When the request
# exceeds the ceiling the response is silently clamped and _messages_truncated
# is set (the existing truncation signal already covers "more rows exist").
_MAX_MSG_LIMIT = 500


def _parse_msg_limit(raw):
    """Parse and clamp the ``?msg_limit=`` query value.

    Returns a positive int clamped to ``[1, _MAX_MSG_LIMIT]``, or ``None`` when
    the value is absent/empty/malformed (the bare no-``msg_limit`` path, which
    intentionally returns the full transcript for callers that need it).
    Extracted from the handler so the clamp expression has direct test coverage.
    """
    if not raw:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return max(1, min(value, _MAX_MSG_LIMIT))


# If a sidecar JSON file exceeds this threshold, the display-path tail
# optimization fires regardless of message count.  Sessions with few messages
# but large tool outputs (multi-MB JSON) should not force a full-scan merge.
_SIDECAR_BYTE_TAIL_THRESHOLD = 500_000  # 500 KB
# Defensive row backstop for the GET /api/session display path's state.db read.
# This is NOT a semantic window (the display window counts visible rows
# post-reconciliation via _message_window_for_display); it is a safety net so a
# pathological/huge state.db cannot materialize unbounded rows into memory on the
# display path. Legitimate sessions stay far below this; the compressed-session
# case where _state_db_since_timestamp_for_limited_display bails (and would
# otherwise full-scan) is the main beneficiary. Generous on purpose: no real
# conversation approaches it, and the existing since_timestamp optimization
# already handles the common tail-load case. The full-history model-context
# callers (reconciliation, new-turn context) do NOT use this cap.
_STATE_DB_DISPLAY_ROW_BACKSTOP = 50000


def _state_db_backstop_limit_for_display(session, msg_before) -> int | None:
    """Return the row backstop to apply to the display path's state.db read, or
    ``None`` for an uncapped (full-history) read.

    The backstop is a defensive net against a pathological/huge state.db, NOT a
    semantic window. It is applied ONLY on provably-safe reads where no
    ``truncation_boundary`` prefix is required for the merge:
    ``merge_session_messages_append_only`` needs the rows at/around the session's
    ``truncation_boundary`` to reconcile correctly, and a newest-N-only SQL cap
    would drop those boundary rows for a >N-row session and corrupt the merge
    (silently losing the preserved prefix). So this mirrors the same conditions
    ``_state_db_since_timestamp_for_limited_display`` uses to decide a read is
    boundary-free: not ``msg_before`` paging, and no ``truncation_watermark`` /
    ``truncation_boundary``. Extracted for direct test coverage.
    """
    has_boundary_prefix = (
        msg_before is not None
        or getattr(session, "truncation_watermark", None) not in (None, "")
        or getattr(session, "truncation_boundary", None) not in (None, "")
    )
    return None if has_boundary_prefix else _STATE_DB_DISPLAY_ROW_BACKSTOP


_LIMITED_TOOL_CONTENT_NOTICE = (
    "\n\n[Tool output truncated in paginated session response; "
    "load the full transcript to inspect the complete result.]"
)


def _tool_message_for_limited_payload(message):
    """Return a bounded copy of large hidden tool-result rows for paginated loads."""
    if not isinstance(message, dict) or str(message.get("role") or "").lower() != "tool":
        return message
    content = message.get("content")
    if content in (None, ""):
        return message
    if isinstance(content, str):
        text = content
    else:
        try:
            text = json.dumps(content, ensure_ascii=False, default=str)
        except Exception:
            logger.debug("Silent exception in _tool_message_for_limited_payload", exc_info=True)
            text = str(content)
    if len(text) <= _LIMITED_TOOL_CONTENT_MAX_CHARS:
        return message
    clipped = dict(message)
    preview = text[:_LIMITED_TOOL_CONTENT_MAX_CHARS] + _LIMITED_TOOL_CONTENT_NOTICE
    if isinstance(content, str):
        clipped["content"] = preview
    elif isinstance(content, list):
        clipped["content"] = [{"type": "text", "text": preview}]
    elif isinstance(content, dict):
        clipped["content"] = {"_truncated": True, "preview": preview}
    else:
        clipped["content"] = preview
    clipped["_content_truncated"] = True
    clipped["_content_original_chars"] = len(text)
    return clipped


def _messages_for_limited_payload(messages) -> list:
    """Bound hidden tool-result payloads before sending a msg_limit response."""
    return [_tool_message_for_limited_payload(msg) for msg in list(messages or [])]


def _limited_webui_messages_for_display(session, state_db_messages) -> list:
    """Return the display sidecar plus only necessary state.db rows for msg_limit.

    Paginated session loads are latency-sensitive and should not stitch every
    lineage segment before slicing the tail. Keep the lightweight
    pre-compression snapshot stitch so continuation sessions can still reveal
    archived history, then merge only newer state.db rows that have not reached
    the sidecar yet.
    """
    sidecar_messages = _webui_sidecar_lineage_messages_for_display(session)
    return _limited_webui_messages_for_display_with_sidecar(
        session,
        sidecar_messages,
        state_db_messages,
    )


def _display_merge_session_is_active(session) -> bool:
    """Return whether any canonical in-memory projection is active/pending."""
    if getattr(session, "active_stream_id", None) or getattr(
        session, "pending_user_message", None
    ):
        return True
    sid = str(getattr(session, "session_id", "") or "")
    if not sid:
        return True
    with LOCK:
        live = SESSIONS.get(sid)
    if live is None or live is session:
        return False
    if str(getattr(live, "session_id", "") or "") != sid:
        return True
    return bool(
        getattr(live, "active_stream_id", None)
        or getattr(live, "pending_user_message", None)
    )


def _display_merge_cached_messages(session, sidecar_messages, *, msg_before=None):
    """Return the memoized merged transcript, or None when it can't be reused.

    Lets GET /api/session skip loading the state.db rows entirely on a hit. That
    is only sound because the cache key can be built from the existing
    commit-reliable DB/WAL/SHM signature (`_state_db_session_signature`) rather
    than a fingerprint computed FROM the loaded rows -- otherwise the key could
    not be built without paying exactly the cost we are trying to avoid.

    Fail-closed by construction: returns None whenever the session is active,
    the key cannot be built, or the cached entry does not match, and the caller
    then performs the normal full load + merge.
    """
    if msg_before is not None or _display_merge_session_is_active(session):
        return None
    sid = str(getattr(session, "session_id", "") or "")
    if not sid:
        return None
    with _display_merge_cache_lock:
        entry = _display_merge_cache.get(sid)
        if entry is None:
            return None
    # Resolve the sidecar exactly like the merge helper does: it treats None as
    # "load the lineage myself", and the cache entry was keyed on that RESOLVED
    # list. Probing with a bare None would key on an empty sidecar and miss
    # every time -- silently reverting this optimisation.
    if sidecar_messages is None:
        sidecar_messages = _webui_sidecar_lineage_messages_for_display(session)
    else:
        sidecar_messages = list(sidecar_messages or [])
    # Building the key requires the sidecar rows (cheap: already in memory or
    # served from the lineage cache) but not the state.db rows -- that
    # asymmetry is the whole point.
    cache_key = _display_merge_cache_key(session, sidecar_messages, None)
    if cache_key is None:
        return None
    with _display_merge_cache_lock:
        entry = _display_merge_cache.get(sid)
        if not _display_merge_cache_entry_usable(entry, cache_key):
            return None
        _display_merge_cache.move_to_end(sid, last=True)
        return [dict(m) if isinstance(m, dict) else m for m in entry["messages"]]


_DISPLAY_STATE_SIGNATURE_UNSET = object()


def _limited_webui_messages_for_display_with_sidecar(
    session,
    sidecar_messages,
    state_db_messages,
    *,
    state_db_signature=_DISPLAY_STATE_SIGNATURE_UNSET,
    msg_before=None,
) -> list:
    if sidecar_messages is None:
        sidecar_messages = _webui_sidecar_lineage_messages_for_display(session)
    else:
        sidecar_messages = list(sidecar_messages or [])
    state_db_messages = list(state_db_messages or [])
    if not state_db_messages:
        return sidecar_messages
    # NOTE: do not short-circuit to the sidecar when state.db has no strictly
    # newer rows. A state.db row whose timestamp is at-or-before the sidecar's
    # newest (recovery / edited-in-place / missing-timestamp cases) is still
    # absent from the sidecar and must be reconciled — dropping it would render
    # a tail that differs from the full merge path (silent wrong-transcript on
    # the paginated load). The append-only merge is O(n) over already-bounded
    # in-memory lists; the real latency win here is skipping the lineage-parent
    # DISK load above, which we still skip. (#4070 ship-review)
    #
    # perf: the merge itself is still expensive for multi-thousand-message
    # historical transcripts (~2-3s per request: json.dumps merge keys +
    # loose-content probes per row), and GET /api/session re-runs it on every
    # open/poll. Memoize per session id. Validity is fail-closed:
    #   - only INACTIVE sessions (no active stream, no pending user message):
    #     an active session's in-memory tail can be ahead of its disk
    #     signature, so it always recomputes;
    #   - the child sidecar's exact stat signature plus every lineage parent
    #     signature recorded by _webui_sidecar_lineage_messages_for_display;
    #   - the sidecar row count and last timestamp (guards unsaved in-memory
    #     appends that have not reached disk yet);
    #   - a content fingerprint of the (bounded) state.db rows.
    # Any uncertainty (missing signature, fingerprint failure) skips caching.
    cache_key = None
    # A msg_before request deliberately reads a different (uncapped) state.db
    # scope than the initial tail request.  It must bypass both cache layers:
    # skipping only the pre-load probe still let this inner lookup reuse the
    # initial 50k-row backstop merge and made the oldest row unreachable.
    if msg_before is None and not _display_merge_session_is_active(session):
        if state_db_signature is _DISPLAY_STATE_SIGNATURE_UNSET:
            _state_key = _state_db_rows_fingerprint(state_db_messages)
        else:
            _state_key = state_db_signature
            if _state_key is not None:
                _current_key = _state_db_session_signature(
                    getattr(session, "session_id", None),
                    getattr(session, "profile", None) or None,
                )
                if _current_key != _state_key:
                    _state_key = None
        if _state_key is not None:
            cache_key = _display_merge_cache_key(
                session,
                sidecar_messages,
                state_db_messages,
                state_db_signature=_state_key,
            )
    if cache_key is not None:
        sid = str(getattr(session, "session_id", "") or "")
        with _display_merge_cache_lock:
            entry = _display_merge_cache.get(sid)
            if _display_merge_cache_entry_usable(entry, cache_key):
                _display_merge_cache.move_to_end(sid, last=True)
                return [dict(m) if isinstance(m, dict) else m for m in entry["messages"]]
    merged = merge_session_messages_append_only(
        sidecar_messages,
        state_db_messages,
        truncation_watermark=getattr(session, "truncation_watermark", None),
        truncation_boundary=getattr(session, "truncation_boundary", None),
    )
    if cache_key is not None:
        _state_key = cache_key[4]
        _streaming_key = (
            isinstance(_state_key, (list, tuple))
            and bool(_state_key)
            and _state_key[0] == "streaming"
        )
        if (
            state_db_signature is not _DISPLAY_STATE_SIGNATURE_UNSET
            and not _streaming_key
            and _state_db_session_signature(
                getattr(session, "session_id", None),
                getattr(session, "profile", None) or None,
            )
            != state_db_signature
        ):
            cache_key = None
    if cache_key is not None:
        sid = str(getattr(session, "session_id", "") or "")
        with _display_merge_cache_lock:
            _display_merge_cache[sid] = {
                "key": cache_key,
                "messages": merged,
                "stored_at": time.monotonic(),
            }
            _display_merge_cache.move_to_end(sid, last=True)
            while len(_display_merge_cache) > _DISPLAY_MERGE_CACHE_MAX:
                _display_merge_cache.popitem(last=False)
        # Same shallow-copy contract as the cache-hit path (and as the lineage
        # cache): callers may attach display metadata to the returned rows.
        return [dict(m) if isinstance(m, dict) else m for m in merged]
    return merged


# perf: memoized sidecar↔state.db display merges for GET /api/session.
# See _limited_webui_messages_for_display_with_sidecar for the validity rules.
_DISPLAY_MERGE_CACHE_MAX = 16
# Legacy streaming-freeze keys are still accepted defensively and remain
# tightly bounded. Production streaming keys now carry an exact target-session
# digest, so unrelated deltas stay stable without hiding target mutations.
_DISPLAY_MERGE_STREAMING_TTL_SECONDS = 5.0
_display_merge_cache: "OrderedDict[str, dict]" = OrderedDict()
_display_merge_cache_lock = threading.Lock()


def _display_merge_cache_entry_usable(entry, cache_key) -> bool:
    if entry is None or entry.get("key") != cache_key:
        return False
    try:
        state_key = cache_key[4]
    except (IndexError, TypeError):
        return False
    is_streaming_key = (
        isinstance(state_key, (list, tuple))
        and bool(state_key)
        and state_key[0] == "streaming"
    )
    if not is_streaming_key:
        return True
    try:
        age = time.monotonic() - float(entry["stored_at"])
    except (KeyError, TypeError, ValueError):
        return False
    return 0.0 <= age <= _DISPLAY_MERGE_STREAMING_TTL_SECONDS


def _display_merge_requires_lineage_provenance(session) -> bool:
    """Return whether this sidecar view depends on a stitched snapshot parent."""
    parent_id = str(getattr(session, "parent_session_id", "") or "").strip()
    if not parent_id:
        return False
    if not is_safe_session_id(parent_id):
        return True
    try:
        parent = Session.load(parent_id)
    except Exception:
        logger.debug("Silent exception in _display_merge_requires_lineage_provenance", exc_info=True)
        return True
    if parent is None:
        return True
    if not getattr(parent, "pre_compression_snapshot", False):
        return False
    source = str(getattr(session, "session_source", "") or "").strip().lower()
    parent_source = str(
        getattr(parent, "session_source", "") or ""
    ).strip().lower()
    if source == "fork" and parent_source != "fork":
        return False
    return not _messages_start_with_visible_prefix(
        list(getattr(session, "messages", []) or []),
        list(getattr(parent, "messages", []) or []),
    )


def _evict_lineage_display_cache_entry(sid, expected_entry) -> None:
    """Evict only the lineage entry that this caller validated as stale."""
    with _lineage_display_cache_lock:
        if _lineage_display_cache.get(sid) is expected_entry:
            _lineage_display_cache.pop(sid, None)


def _display_merge_cache_key(
    session,
    sidecar_messages,
    state_db_messages,
    *,
    state_db_signature=_DISPLAY_STATE_SIGNATURE_UNSET,
):
    """Return a fail-closed validity key for the display-merge cache, or None.

    None means "do not cache": any component that cannot be resolved exactly
    (missing sidecar signature, unfingerprintable state rows) disables the
    cache for this request rather than risking a stale transcript.
    """
    from api.models import _sidecar_stat_signature

    sid = str(getattr(session, "session_id", "") or "")
    if not sid or not is_safe_session_id(sid):
        return None
    self_sig = _sidecar_stat_signature(SESSION_DIR / f"{sid}.json")
    if self_sig is None:
        return None
    # Lineage parents: reuse the signatures recorded by the (already memoized)
    # lineage stitch so a write to any parent snapshot invalidates this cache
    # too. A lineage without snapshot parents records no entry — empty tuple.
    parent_sigs = ()
    with _lineage_display_cache_lock:
        lineage_entry = _lineage_display_cache.get(sid)
    if lineage_entry is not None:
        if (
            lineage_entry.get("provenance_complete") is not True
            or lineage_entry.get("self_sig") != self_sig
        ):
            _evict_lineage_display_cache_entry(sid, lineage_entry)
            return None
        parent_sigs = tuple(
            (str(path), tuple(sig) if isinstance(sig, (list, tuple)) else sig)
            for path, sig in (lineage_entry.get("parent_sigs") or [])
        )
        for parent_path, parent_sig in parent_sigs:
            if _sidecar_stat_signature(Path(parent_path)) != parent_sig:
                _evict_lineage_display_cache_entry(sid, lineage_entry)
                return None
        with _lineage_display_cache_lock:
            if _lineage_display_cache.get(sid) is not lineage_entry:
                return None
    if not parent_sigs and _display_merge_requires_lineage_provenance(session):
        return None
    last_ts = None
    if sidecar_messages:
        last = sidecar_messages[-1]
        if isinstance(last, dict):
            last_ts = last.get("timestamp")
    # Prefer the existing commit-reliable DB/WAL/SHM signature outside streams.
    # While another turn streams, use an exact digest scoped to this target
    # session so unrelated per-delta commits do not churn the key. Fail closed
    # onto the exact row fingerprint whenever the signature cannot be read.
    if state_db_signature is _DISPLAY_STATE_SIGNATURE_UNSET:
        state_fp = _state_db_session_signature(
            sid, getattr(session, "profile", None) or None
        )
    else:
        state_fp = state_db_signature
    if state_fp is None:
        # state_db_messages is None on the cache-probe path, where the rows were
        # deliberately not loaded. Fingerprinting None would key on the empty
        # row set and could match an entry built from real rows, so fail closed.
        if state_db_messages is None:
            return None
        state_fp = _state_db_rows_fingerprint(state_db_messages)
    if state_fp is None:
        return None
    return (
        self_sig,
        parent_sigs,
        len(sidecar_messages),
        last_ts,
        state_fp,
        getattr(session, "truncation_watermark", None),
        getattr(session, "truncation_boundary", None),
    )


def _state_db_target_session_signature(db_path, session_id):
    """Hash every target-session row without materialising display dictionaries."""
    try:
        with webui_session_db.open_db_readonly(db_path, timeout=5.0, row_factory=False) as conn:
            columns = [str(row[1]) for row in conn.execute("PRAGMA table_info(messages)")]
            if "session_id" not in columns or "id" not in columns:
                return None
            quoted_columns = ", ".join(
                '"' + column.replace('"', '""') + '"' for column in columns
            )
            conn.text_factory = lambda raw: ("text", raw)
            rows = conn.execute(
                f'SELECT {quoted_columns} FROM messages '
                'WHERE session_id = ? ORDER BY id',
                (str(session_id),),
            )
            digest = hashlib.blake2b(digest_size=32)
            digest.update("\x1f".join(columns).encode("utf-8"))
            row_count = 0
            for row in rows:
                row_count += 1
                for value in row:
                    if value is None:
                        tag, payload = b"n", b""
                    elif isinstance(value, tuple) and value[:1] == ("text",):
                        tag, payload = b"t", value[1]
                    elif isinstance(value, bytes):
                        tag, payload = b"b", value
                    elif isinstance(value, int):
                        tag, payload = b"i", str(value).encode("ascii")
                    elif isinstance(value, float):
                        tag, payload = b"f", value.hex().encode("ascii")
                    else:
                        return None
                    digest.update(tag)
                    digest.update(len(payload).to_bytes(8, "big"))
                    digest.update(payload)
            return ("streaming-target", row_count, digest.hexdigest())
    except (OSError, sqlite3.Error, ValueError):
        return None


def _state_db_target_session_revision(db_path, session_id):
    """Return a bounded cross-process revision for one session's display rows.

    Supported writers update ``sessions.last_activity_at``/``message_count``.
    The indexed tail revision additionally catches direct appends, tail deletes,
    and raw changes to timestamp, row flags, or byte lengths on the newest row.
    Legacy stores without a sessions row use full numeric/length aggregates. A
    same-length raw SQL rewrite of an older row that bypasses session metadata
    is outside the state-store writer contract; detecting it exactly would
    require scanning and hashing every message payload (148 MB on the production
    long session), which would make the cache slower than the uncached path.
    """
    message_length_columns = (
        "role",
        "content",
        "tool_call_id",
        "tool_calls",
        "tool_name",
        "finish_reason",
        "reasoning",
        "reasoning_content",
        "reasoning_details",
        "codex_reasoning_items",
        "codex_message_items",
        "effect_disposition",
        "api_content",
        "display_kind",
        "display_metadata",
    )
    message_sum_columns = ("observed", "active", "compacted")
    session_revision_columns = (
        "message_count",
        "last_activity_at",
        "ended_at",
        "end_reason",
        "rewind_count",
        "archived",
    )
    try:
        with webui_session_db.open_db_readonly(
            db_path,
            timeout=0.25,
            busy_timeout_ms=250,
            row_factory=False,
        ) as conn:
            message_columns = {
                str(row[1]) for row in conn.execute("PRAGMA table_info(messages)")
            }
            if "session_id" not in message_columns or "id" not in message_columns:
                return None
            session_columns = {
                str(row[1]) for row in conn.execute("PRAGMA table_info(sessions)")
            }
            available_session_columns = [
                column
                for column in session_revision_columns
                if column in session_columns
            ]
            session_revision = None
            if "id" in session_columns and available_session_columns:
                session_revision = conn.execute(
                    "SELECT "
                    + ", ".join(
                        f'"{column}"' for column in available_session_columns
                    )
                    + " FROM sessions WHERE id = ?",
                    (str(session_id),),
                ).fetchone()
            if session_revision is not None:
                latest_parts = ["id"]
                latest_parts.append(
                    "timestamp" if "timestamp" in message_columns else "NULL"
                )
                latest_parts.extend(
                    f'LENGTH(COALESCE("{column}", \'\'))'
                    for column in message_length_columns
                    if column in message_columns
                )
                latest_parts.extend(
                    f'COALESCE("{column}", 0)'
                    for column in message_sum_columns
                    if column in message_columns
                )
                latest_revision = conn.execute(
                    f"SELECT {', '.join(latest_parts)} FROM messages "
                    "WHERE session_id = ? ORDER BY id DESC LIMIT 1",
                    (str(session_id),),
                ).fetchone()
                return (
                    "target-session-revision-v2",
                    tuple(available_session_columns),
                    tuple(session_revision),
                    tuple(latest_revision) if latest_revision is not None else None,
                )

            # Legacy state stores without a sessions row have no supported
            # O(1) activity revision. Keep them conservative by scanning compact
            # numeric/length aggregates instead of trusting a global DB stamp.
            aggregate_parts = ["COUNT(*)", "MAX(id)"]
            aggregate_parts.append(
                "MAX(timestamp)" if "timestamp" in message_columns else "NULL"
            )
            aggregate_parts.extend(
                f'SUM(LENGTH(COALESCE("{column}", \'\')))'
                for column in message_length_columns
                if column in message_columns
            )
            aggregate_parts.extend(
                f'SUM(COALESCE("{column}", 0))'
                for column in message_sum_columns
                if column in message_columns
            )
            message_revision = conn.execute(
                f"SELECT {', '.join(aggregate_parts)} FROM messages "
                "WHERE session_id = ?",
                (str(session_id),),
            ).fetchone()
            return (
                "target-session-revision-v1-legacy",
                (),
                None,
                tuple(message_revision) if message_revision is not None else None,
            )
    except (OSError, sqlite3.Error, ValueError):
        return None


def _state_db_session_signature(session_id, profile=None):
    """Return a cross-process target-session cache revision, fail-closed.

    The session-scoped revision avoids DB/WAL false invalidations caused by a
    different conversation streaming in another WebUI process. If the schema
    cannot provide that revision, fall back to the existing global file key.
    """
    from api.models import _agent_state_db_path, _sqlite_file_stat_cache_key

    sid = str(session_id or "")
    if not sid or not is_safe_session_id(sid):
        return None
    try:
        db_path = _agent_state_db_path(profile=profile)
        if not db_path or not Path(db_path).exists():
            return None
    except Exception:
        logger.debug("Silent exception in _state_db_session_signature", exc_info=True)
        return None
    target_revision = _state_db_target_session_revision(db_path, sid)
    if target_revision is not None:
        return target_revision
    try:
        signature = _sqlite_file_stat_cache_key(Path(db_path))
    except Exception:
        logger.debug("Silent exception in _state_db_session_signature", exc_info=True)
        return None
    if signature is None:
        return None
    # ``_sqlite_file_stat_cache_key`` is a tuple of the content fingerprint and
    # DB/WAL/SHM stat stamps. A completely empty result is not a valid key.
    try:
        if not any(component is not None for component in signature):
            return None
    except TypeError:
        return None
    return signature


def _load_state_db_messages_with_stable_signature(session_id, profile, reader_kwargs):
    """Load rows and return the database signature that brackets that read."""
    before = _state_db_session_signature(session_id, profile)
    rows = get_state_db_session_messages(session_id, **dict(reader_kwargs or {}))
    after = _state_db_session_signature(session_id, profile)
    stable = before if before is not None and before == after else None
    return rows, stable


def _state_db_rows_fingerprint(rows) -> str | None:
    """Content fingerprint of the state.db display rows, or None on failure."""
    try:
        h = hashlib.sha256()
        h.update(str(len(rows)).encode("utf-8"))
        for row in rows:
            if isinstance(row, dict):
                h.update(json.dumps(row, sort_keys=True, default=str).encode("utf-8", "replace"))
            else:
                h.update(repr(row).encode("utf-8", "replace"))
        return h.hexdigest()
    except Exception:
        logger.debug("Silent exception in _state_db_rows_fingerprint", exc_info=True)
        return None


def _sidecar_file_exceeds_threshold(session_id, threshold_bytes) -> bool:
    """Check if the sidecar JSON file for ``session_id`` exceeds ``threshold_bytes``."""
    from api.config import SESSION_DIR
    try:
        p = SESSION_DIR / f"{session_id}.json"
        return os.path.isfile(p) and os.path.getsize(p) > threshold_bytes
    except Exception:
        logger.debug("Silent exception in _sidecar_file_exceeds_threshold", exc_info=True)
        return False


def _state_db_since_timestamp_for_limited_display(session, msg_limit, msg_before=None):
    """Return (timestamp floor, sidecar messages) for bounded state.db tail reads.

    The display window limit counts visible transcript rows after WebUI sidecar
    and state.db reconciliation, so this deliberately does not SQL ``LIMIT`` raw
    rows.  Instead, for the common initial tail load, keep the full sidecar
    coordinate space and read a conservative recent state.db superset.  Older
    page loads and edit/truncation recovery shapes stay on the full state.db
    path because their correctness depends on older reconciliation rows.
    """
    if msg_limit is None or msg_before is not None:
        return None, None
    if getattr(session, "truncation_watermark", None) not in (None, ""):
        return None, None
    if getattr(session, "truncation_boundary", None) not in (None, ""):
        return None, None

    sidecar_messages = _webui_sidecar_lineage_messages_for_display(session)
    if not sidecar_messages:
        return None, sidecar_messages
    sidecar_timestamps = [_message_timestamp_as_float(msg) for msg in sidecar_messages]
    if any(ts is None for ts in sidecar_timestamps):
        return None, sidecar_messages

    try:
        limit = max(1, int(msg_limit))
    except (TypeError, ValueError):
        return None, sidecar_messages
    raw_budget = max(300, limit * 10)
    if len(sidecar_messages) <= raw_budget:
        _sid = getattr(session, "session_id", "") or ""
        if not _sid or not _sidecar_file_exceeds_threshold(_sid, _SIDECAR_BYTE_TAIL_THRESHOLD):
            return None, sidecar_messages

    floor = min(sidecar_timestamps[-raw_budget:])
    sidecar_before_count = sum(1 for ts in sidecar_timestamps if ts < floor)
    prefix_summary = get_state_db_session_message_prefix_summary(
        getattr(session, "session_id", None),
        floor,
        profile=getattr(session, "profile", None) or None,
    )
    if prefix_summary is None:
        return None, sidecar_messages
    try:
        state_before_count = int(prefix_summary["count"])
        null_timestamp_count = int(prefix_summary["null_timestamp_count"])
    except (KeyError, TypeError, ValueError):
        return None, sidecar_messages
    if null_timestamp_count or state_before_count != sidecar_before_count:
        return None, sidecar_messages
    if sidecar_before_count == 0:
        return floor, sidecar_messages

    sidecar_before_keys = [
        _session_message_visible_key(msg)
        for msg, ts in zip(sidecar_messages, sidecar_timestamps, strict=True)
        if ts < floor
    ]
    state_before_keys = get_state_db_session_message_keys_before_timestamp(
        getattr(session, "session_id", None),
        floor,
        profile=getattr(session, "profile", None) or None,
    )
    if state_before_keys is None or state_before_keys != sidecar_before_keys:
        return None, sidecar_messages
    return floor, sidecar_messages


def _messages_start_with_visible_prefix(messages, prefix) -> bool:
    """Return True when ``messages`` already replays ``prefix`` in display order."""
    messages = list(messages or [])
    prefix = list(prefix or [])
    if not prefix:
        return True
    if len(messages) < len(prefix):
        return False
    try:
        return all(
            _session_message_visible_key(messages[idx]) == _session_message_visible_key(prefix_msg)
            for idx, prefix_msg in enumerate(prefix)
        )
    except Exception:
        logger.debug("Silent exception in _messages_start_with_visible_prefix", exc_info=True)
        return False


# perf: memoized lineage-stitch results for GET /api/session. Keyed by session
# id; validity = exact stat signature of the child sidecar AND every snapshot
# parent involved in the stitch. Any write to any involved sidecar changes its
# signature and invalidates the entry. Bounded LRU — historical lineages are
# few but their merges cost seconds each.
_LINEAGE_DISPLAY_CACHE_MAX = 16
_lineage_display_cache: "OrderedDict[str, dict]" = OrderedDict()
_lineage_display_cache_lock = threading.Lock()


def _webui_sidecar_lineage_messages_for_display(session, *, max_hops: int = 20) -> list:
    """Return WebUI sidecar messages stitched across compression snapshots.

    WebUI compression continuations persist the archived transcript in a parent
    sidecar marked ``pre_compression_snapshot`` and keep subsequent turns in the
    child sidecar. Opening the child alone makes older turns look lost. Stitch
    only those snapshot parents for display; ordinary forks also carry
    ``parent_session_id`` but must remain independent conversations.

    perf: the stitched merge is O(total messages) with expensive per-row keys
    (json.dumps of tool_calls, loose-content regex). For multi-thousand-message
    lineages it costs seconds per request, and GET /api/session re-runs it on
    every open/poll. The result is cached per session id, keyed by the stat
    signature of every sidecar involved (child + each snapshot parent), so an
    idle historical lineage merges once and any write to any involved sidecar
    invalidates naturally. Cache hits return shallow-copied rows so callers can
    attach display metadata without corrupting the cache.
    """
    from api.models import _sidecar_stat_signature

    cache_allowed = not _display_merge_session_is_active(session)
    sid = str(getattr(session, "session_id", "") or "")
    self_sig = None
    if sid and is_safe_session_id(sid):
        self_sig = _sidecar_stat_signature(SESSION_DIR / f"{sid}.json")
    if cache_allowed and self_sig is not None:
        with _lineage_display_cache_lock:
            entry = _lineage_display_cache.get(sid)
        if (
            entry is not None
            and entry.get("provenance_complete") is True
            and entry.get("self_sig") == self_sig
        ):
            stale = False
            for parent_path, parent_sig in entry.get("parent_sigs") or []:
                if _sidecar_stat_signature(Path(parent_path)) != parent_sig:
                    stale = True
                    break
            if not stale:
                with _lineage_display_cache_lock:
                    current_entry = _lineage_display_cache.get(sid)
                    if current_entry is entry:
                        _lineage_display_cache.move_to_end(sid, last=True)
                        return [
                            dict(m) if isinstance(m, dict) else m
                            for m in entry["messages"]
                        ]
            else:
                _evict_lineage_display_cache_entry(sid, entry)

    segments = []
    current = session
    session_messages = list(getattr(session, "messages", []) or [])
    source = str(getattr(session, "session_source", "") or "").strip().lower()
    root_is_fork = source == "fork"
    seen = {str(getattr(session, "session_id", "") or "")}
    parent_sigs: list[tuple[str, tuple]] = []
    parent_signatures_complete = True
    for _ in range(max(0, int(max_hops))):
        parent_id = str(getattr(current, "parent_session_id", "") or "").strip()
        if not parent_id:
            break
        if parent_id in seen or not is_safe_session_id(parent_id):
            parent_signatures_complete = False
            break
        parent_path = SESSION_DIR / f"{parent_id}.json"
        parent_sig_before = _sidecar_stat_signature(parent_path)
        parent = Session.load(parent_id)
        if not parent:
            parent_signatures_complete = False
            break
        if not getattr(parent, "pre_compression_snapshot", False):
            break
        parent_sig = _sidecar_stat_signature(parent_path)
        if (
            parent_sig_before is None
            or parent_sig is None
            or parent_sig_before != parent_sig
        ):
            parent_signatures_complete = False
        else:
            parent_sigs.append((str(parent_path), parent_sig))
        parent_source = str(getattr(parent, "session_source", "") or "").strip().lower()
        if root_is_fork and parent_source != "fork":
            break
        if not segments and _messages_start_with_visible_prefix(
            session_messages,
            getattr(parent, "messages", []) or [],
        ):
            return session_messages
        segments.append(parent)
        seen.add(parent_id)
        current = parent
    else:
        # Exhausting max_hops means the declared ancestry may continue beyond
        # the signatures captured above. Never publish partial provenance.
        parent_signatures_complete = False

    if not segments:
        return list(getattr(session, "messages", []) or [])

    merged = []
    for segment in reversed(segments):
        merged = merge_session_messages_append_only(
            merged,
            getattr(segment, "messages", []) or [],
            truncation_watermark=getattr(segment, "truncation_watermark", None),
            truncation_boundary=getattr(segment, "truncation_boundary", None),
        )
    merged = merge_session_messages_append_only(
        merged,
        getattr(session, "messages", []) or [],
        truncation_watermark=None,
    )
    if (
        cache_allowed
        and self_sig is not None
        and parent_sigs
        and parent_signatures_complete
    ):
        with _lineage_display_cache_lock:
            _lineage_display_cache[sid] = {
                "self_sig": self_sig,
                "parent_sigs": parent_sigs,
                "provenance_complete": True,
                "messages": merged,
            }
            _lineage_display_cache.move_to_end(sid, last=True)
            while len(_lineage_display_cache) > _LINEAGE_DISPLAY_CACHE_MAX:
                _lineage_display_cache.popitem(last=False)
        # Hand out copies so caller-side metadata mutation cannot corrupt
        # the cached rows (same contract as the cache-hit path).
        return [dict(m) if isinstance(m, dict) else m for m in merged]
    return merged


def _merged_session_messages_for_display(session, cli_messages=None) -> list:
    """Return the message coordinate space exposed by ``GET /api/session``.

    Messaging sessions can have a WebUI sidecar transcript plus messages from
    the Agent/CLI store. WebUI compression continuations can have an archived
    snapshot parent plus a child continuation sidecar. The frontend computes
    fork keep-counts against this merged display list, so branch/fork must slice
    the same list rather than the sidecar-only ``session.messages`` array.
    """
    cli_messages = list(cli_messages or [])
    sidecar_messages = _webui_sidecar_lineage_messages_for_display(session)
    if cli_messages:
        if sidecar_messages and sidecar_messages != cli_messages:
            if len(sidecar_messages) >= len(cli_messages):
                return merge_session_messages_append_only(
                    sidecar_messages,
                    cli_messages,
                    truncation_watermark=getattr(session, "truncation_watermark", None),
                    truncation_boundary=getattr(session, "truncation_boundary", None),
                )
            merged_messages = []
            seen_message_keys = set()
            for msg in sorted(list(cli_messages) + list(sidecar_messages), key=lambda m: (
                float(m.get("timestamp") or 0),
                str(m.get("role") or ""),
                str(m.get("content") or ""),
            )):
                key = _session_message_merge_key(msg)
                if key in seen_message_keys:
                    continue
                seen_message_keys.add(key)
                merged_messages.append(msg)
            return merged_messages
        return sidecar_messages if len(sidecar_messages) > len(cli_messages) else cli_messages
    return sidecar_messages



def _merged_webui_lineage_messages_for_display(session, messages=None) -> list:
    """Include immediate parent-only rows when a WebUI continuation sidecar is partial.

    Compression/continuation sessions should render as one conversation. Most
    child sidecars are cumulative, so this is usually a cheap no-op. If a child
    sidecar accidentally omits rows that still exist in the immediate parent,
    merge those parent-only rows into the display transcript. Explicit forks and
    generic child-session rows remain isolated; they intentionally start from a
    subset of their parent.
    """
    primary_messages = list(messages if messages is not None else (getattr(session, "messages", []) or []))
    parent_id = str(getattr(session, "parent_session_id", "") or "").strip()
    if not parent_id:
        return primary_messages
    if (
        str(getattr(session, "compression_recovery_source_session_id", "") or "").strip()
        and str(getattr(session, "compression_recovery_action", "") or "").strip()
    ):
        return primary_messages
    source = str(getattr(session, "session_source", "") or "").strip().lower()
    relationship = str(getattr(session, "relationship_type", "") or "").strip().lower()
    if source == "fork" or relationship == "child_session":
        return primary_messages
    try:
        parent = get_session(parent_id, metadata_only=False)
    except Exception:
        logger.debug("Silent exception in _merged_webui_lineage_messages_for_display", exc_info=True)
        return primary_messages
    parent_messages = list(getattr(parent, "messages", []) or [])
    if not parent_messages:
        return primary_messages
    if _messages_start_with_visible_prefix(primary_messages, parent_messages):
        return primary_messages
    merged_messages = []
    seen_message_keys = set()
    seen_messages_by_key = {}
    for msg in sorted(list(parent_messages) + list(primary_messages), key=lambda m: (
        float(m.get("timestamp") or 0),
        str(m.get("role") or ""),
        str(m.get("content") or ""),
    )):
        key = _session_message_merge_key(msg)
        if key in seen_message_keys:
            _merge_session_display_metadata(seen_messages_by_key.get(key), msg)
            continue
        seen_message_keys.add(key)
        seen_messages_by_key[key] = msg
        merged_messages.append(msg)
    return merged_messages


def _message_summary(messages) -> dict:
    messages = list(messages or [])
    last_message_at = 0.0
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        try:
            last_message_at = max(last_message_at, float(msg.get("timestamp") or 0))
        except (TypeError, ValueError):
            pass
    return {"message_count": len(messages), "last_message_at": last_message_at}


def _metadata_only_message_summary(sid: str, profile: str | None = None) -> dict:
    """Return the cheap message summary used by metadata-only session loads.

    Threads ``profile=`` through to ``get_state_db_session_summary`` so
    background-thread reads land on the correct profile's state.db (per the
    cookie-bound profile selector — fixes the same TLS-vs-thread race the
    #2762 fix addressed for write paths).

    This intentionally does not full-read or merge transcripts.  If state.db has
    grown beyond the sidecar count, report that growth so active-session polling
    can refresh.  If state.db only contains restamped replay rows at or below the
    sidecar count, keep the sidecar metadata so polling does not loop forever on
    a false "newer transcript" signal.
    """
    sidecar_session = Session.load_metadata_only(sid)
    sidecar_count = 0
    sidecar_last_message_at = 0.0
    if sidecar_session:
        sidecar_count = _numeric_count(getattr(sidecar_session, "_metadata_message_count", None))
        if sidecar_count <= 0:
            sidecar_count = _numeric_count(sidecar_session.compact().get("message_count"))
        try:
            sidecar_last_message_at = float(getattr(sidecar_session, "updated_at", 0) or 0)
        except (TypeError, ValueError):
            sidecar_last_message_at = 0.0
        if getattr(sidecar_session, "truncation_watermark", None) is not None:
            # Intentional: once the user has truncated this sidecar, metadata
            # polling must keep the sidecar as authoritative.  A full message
            # load can still apply the watermark-aware merge, but the cheap
            # metadata path should not treat later state.db rows as external
            # growth and resurrect turns the user deliberately cut away.
            return {
                "message_count": sidecar_count,
                "last_message_at": sidecar_last_message_at,
            }
    state_summary = get_state_db_session_summary(sid, profile=profile)
    state_count = _numeric_count(state_summary.get("message_count"))
    try:
        state_last_message_at = float(state_summary.get("last_message_at") or 0)
    except (TypeError, ValueError):
        state_last_message_at = 0.0
    if state_count > sidecar_count and state_last_message_at > sidecar_last_message_at:
        return {
            "message_count": state_count,
            "last_message_at": state_last_message_at,
        }
    return {
        "message_count": sidecar_count,
        "last_message_at": sidecar_last_message_at,
    }


def _session_requires_cli_metadata_lookup(session) -> bool:
    """Return True when a sidecar/session row still needs CLI metadata.

    Legacy imported sidecars may predate the ``read_only`` field and therefore
    load with ``read_only=False``. They still persist ``is_cli_session`` and/or
    source metadata from import time, so those markers intentionally keep them
    on the CLI lookup path while ordinary WebUI-native sessions take the fast
    path.

    Supersedes the simpler is-cli-or-messaging gate from PR #1822 — the new
    gate is strictly more inclusive (also covers ``read_only=True`` sidecars,
    ``session_source`` markers, and source_tag/raw_source/platform metadata)
    so all sessions that previously took the slow path still do, plus a few
    more legacy shapes.
    """
    if not session:
        return False

    def _field(name):
        return session.get(name) if isinstance(session, dict) else getattr(session, name, None)

    if _is_messaging_session_record(session):
        return True
    if bool(_field("is_cli_session")) or bool(_field("read_only")):
        return True
    session_source = _normalize_messaging_source(_safe_first(_field("session_source")))
    if session_source in {"messaging", "external_agent", "external-agent"}:
        return True
    return bool(_safe_first(
        _field("source_tag"),
        _field("raw_source"),
        _field("source"),
        _field("source_label"),
        _field("platform"),
    ))


def _is_messaging_session_id(sid: str) -> bool:
    """Detect messaging-backed sessions from WebUI metadata or Agent rows."""
    try:
        session = Session.load(sid)
        if _is_messaging_session_record(session):
            return True
    except Exception:
        logger.debug("Silent exception in _is_messaging_session_id", exc_info=True)
        pass
    return _is_messaging_session_record(_lookup_cli_session_metadata(sid))


def _session_sort_timestamp(session: dict) -> float:
    return float(
        _safe_first(
            session.get("last_message_at"),
            session.get("updated_at"),
            session.get("created_at"),
            session.get("started_at"),
            0,
        ) or 0
    ) or 0.0


def _is_cli_session_for_settings(session: dict) -> bool:
    """Return True for importable CLI sessions that are safe to classify for settings."""
    if not isinstance(session, dict):
        return False
    if is_cli_session_row(session):
        return True

    # Fallback for legacy local copies that had weak/empty metadata:
    # keep this conservative so messaging sessions do not collapse incorrectly.
    if not session.get("is_cli_session"):
        return False
    source = str(session.get("source") or "").strip().lower()
    if source in MESSAGING_SOURCES:
        return False
    title = str(session.get("title") or "").strip().lower()
    return title in ("", "untitled", "cli", "cli session") or title.endswith(" session") and (
        not source or source == "cli"
    )


def _normalize_sidebar_source_flags(session: dict) -> dict:
    """Return a sidebar row with the frontend CLI flag matching source metadata."""
    if not isinstance(session, dict):
        return session
    normalized = dict(session)
    normalized["is_cli_session"] = is_cli_session_row(normalized)
    return normalized


def _reconcile_session_detail_source_flags(session: dict, state_meta: dict) -> dict:
    """Return a /api/session payload whose source flags match state.db truth.

    WebUI-origin sidecars can carry stale CLI/import flags after older repair or
    import paths touched the JSON file. The sidebar projection already trusts the
    state.db source row for those sessions; the detail endpoint must do the same
    or the frontend opens a WebUI-native transcript as an external session and
    starts the destructive active-refresh reload loop.
    """
    if not isinstance(session, dict):
        return session
    if not _session_source_is_webui(state_meta):
        return dict(session)

    reconciled = dict(session)
    reconciled["is_cli_session"] = False
    reconciled["read_only"] = False
    reconciled["source_tag"] = _safe_first(state_meta.get("source_tag"), "webui")
    reconciled["raw_source"] = _safe_first(state_meta.get("raw_source"), "webui")
    reconciled["session_source"] = _safe_first(state_meta.get("session_source"), "webui")
    reconciled["source_label"] = _safe_first(state_meta.get("source_label"), "WebUI")
    if state_meta.get("source"):
        reconciled["source"] = state_meta["source"]

    for key in ("message_count", "actual_message_count"):
        if state_meta.get(key) is not None:
            reconciled[key] = max(
                _numeric_count(reconciled.get(key)),
                _numeric_count(state_meta.get(key)),
            )
    for key in ("created_at", "updated_at", "last_message_at"):
        if state_meta.get(key) is not None:
            current = reconciled.get(key)
            try:
                reconciled[key] = max(float(current or 0), float(state_meta.get(key) or 0))
            except (TypeError, ValueError):
                reconciled[key] = state_meta[key]
    return reconciled


def _session_source_is_webui(session: dict) -> bool:
    """Return True for state.db/sidebar rows that describe WebUI-origin sessions."""
    if not isinstance(session, dict):
        return False
    for key in ("source_tag", "raw_source", "session_source", "source"):
        if str(session.get(key) or "").strip().lower() == "webui":
            return True
    return False


def _normalized_source_marker(value) -> str:
    marker = str(value or "").strip().lower()
    if marker.endswith(" session"):
        marker = marker[:-len(" session")].strip()
    return marker.replace("-", "_").replace(" ", "_")


def _is_api_server_sidecar_row(session: dict) -> bool:
    """Return True for API-server imported sidecars that need orphan pruning."""
    if not isinstance(session, dict) or _session_source_is_webui(session):
        return False
    markers = {
        _normalized_source_marker(session.get(key))
        for key in ("source", "source_tag", "raw_source", "session_source", "source_label")
    }
    return bool(markers & {"api", "api_server"})


def _session_lineage_ids(session: dict) -> set[str]:
    """Return known ids that identify one logical sidebar lineage."""
    if not isinstance(session, dict):
        return set()
    ids: set[str] = set()
    for key in ("session_id", "_lineage_root_id", "_lineage_tip_id"):
        value = session.get(key)
        if value:
            ids.add(str(value))
    return ids


def _is_duplicate_webui_state_projection(session: dict, represented_webui_ids: set[str]) -> bool:
    """Return True when a state.db row is only a duplicate WebUI-origin projection.

    The "Show non-WebUI sessions" toggle should add external/agent-owned
    conversations, not make WebUI compression continuations appear only when the
    external-session bridge is enabled. WebUI-origin state.db rows are still
    useful metadata sidecars, but if any id in their compression lineage is
    already represented by WebUI session JSON, they should not be injected as an
    additive external row.
    """
    if not _session_source_is_webui(session):
        return False
    return bool(_session_lineage_ids(session) & represented_webui_ids)


def _dedupe_cli_sidebar_sessions_for_api(
    cli: list[dict],
    represented_webui_ids: set[str],
    *,
    show_cron_sessions: bool = False,
    show_webhook_sessions: bool = False,
    show_kanban_sessions: bool = False,
    source_filter: str | None = None,
) -> list[dict]:
    """Return state sidebar rows while preserving project-hidden background rows.

    Agent-side cron and webhook sessions come from state.db rather than the WebUI
    session store. They should stay hidden from the default sidebar, but
    project-assigned messageful rows must remain in the `/api/sessions` payload
    with `default_hidden` so the matching project chip can reveal them (#3134).

    An explicit ``source_filter`` for a background source (cron/webhook/kanban)
    is a deliberate request to view those rows, so it overrides the default
    hide for that source only — the user asked for them.
    """
    from api.models import (
        _hide_from_default_sidebar as _hide_background,
        _include_project_hidden_background_sidebar_sessions,
    )

    # An explicit background source filter reveals that source (override the hide).
    # Normalize to match how the loader canonicalizes source_filter (strip+lower).
    _sf = str(source_filter or '').strip().lower()
    if _sf == 'cron':
        show_cron_sessions = True
    elif _sf == 'webhook':
        show_webhook_sessions = True
    elif _sf == 'kanban':
        show_kanban_sessions = True

    candidates = [
        s for s in cli
        if s["session_id"] not in represented_webui_ids
        and not _is_duplicate_webui_state_projection(s, represented_webui_ids)
        and is_cli_session_row_visible(s)
    ]
    visible = [
        s for s in candidates
        if not _hide_background(
            s,
            show_cron=show_cron_sessions,
            show_webhook=show_webhook_sessions,
            show_kanban=show_kanban_sessions,
        )
    ]
    return _include_project_hidden_background_sidebar_sessions(candidates, visible)


CLI_VISIBLE_SESSION_CAP = 20


def _cap_recent_cli_sessions(sessions: list[dict], cli_cap: int = CLI_VISIBLE_SESSION_CAP) -> list[dict]:
    """Keep only the most recent CLI-visible sessions after filtering."""
    if cli_cap <= 0:
        return sessions
    kept = []
    cli_seen = 0
    for session in sessions:
        if _is_cli_session_for_settings(session):
            cli_seen += 1
            if cli_seen > cli_cap:
                continue
        kept.append(session)
    return kept


def _merge_cli_sidebar_metadata(ui_session: dict, cli_meta: dict) -> dict:
    """Merge source-of-truth CLI metadata into a sidebar session row.

    Preserve UI-owned state (archived/pinned) while replacing metadata that can
    legitimately drift in WebUI snapshots.
    """
    if not ui_session:
        return ui_session
    if not cli_meta:
        return dict(ui_session)
    merged = dict(ui_session)
    # Only preserve the CLI flag when the imported metadata is actually a CLI
    # row. WebUI sessions are also mirrored into state.db; treating every
    # matching state row as CLI hides long WebUI continuations from the default
    # sidebar source tab.
    merged["is_cli_session"] = is_cli_session_row(cli_meta)
    for key in (
        "source_tag",
        "raw_source",
        "session_source",
        "source_label",
        "user_id",
        "chat_id",
        "chat_type",
        "thread_id",
        "session_key",
        "platform",
        "parent_session_id",
        "end_reason",
        "actual_message_count",
        "_lineage_root_id",
        "_lineage_tip_id",
        "_compression_segment_count",
    ):
        value = _safe_first(cli_meta.get(key))
        if value:
            merged[key] = value

    if cli_meta.get("created_at") is not None:
        merged["created_at"] = cli_meta["created_at"]
    if cli_meta.get("updated_at") is not None:
        merged["updated_at"] = cli_meta["updated_at"]
    if cli_meta.get("last_message_at") is not None:
        merged["last_message_at"] = cli_meta["last_message_at"]
    if cli_meta.get("message_count") is not None:
        merged["message_count"] = max(
            _numeric_count(merged.get("message_count")),
            _numeric_count(cli_meta.get("message_count")),
        )
    elif cli_meta.get("actual_message_count") is not None:
        merged["message_count"] = max(
            _numeric_count(merged.get("message_count")),
            _numeric_count(cli_meta.get("actual_message_count")),
        )

    if cli_meta.get("title"):
        current_title = merged.get("title")
        if not current_title or current_title == "Untitled":
            merged["title"] = cli_meta["title"]

    if cli_meta.get("model"):
        if not merged.get("model") or merged.get("model") == "unknown":
            merged["model"] = cli_meta["model"]
    return merged


def _messaging_source_key(session: dict) -> str | None:
    raw = _session_messaging_raw_source(session)
    if not _is_known_messaging_source(raw):
        return None
    return _messaging_session_identity(session, raw)


def _keep_latest_messaging_session_per_source(
    sessions: list[dict],
    *,
    show_previous_messaging_sessions: bool = False,
) -> list[dict]:
    """Keep only the newest sidebar row per messaging session identity."""
    if show_previous_messaging_sessions:
        return sorted(sessions, key=_session_sort_timestamp, reverse=True)

    gateway_metadata = _load_gateway_session_identity_map()
    active_gateway_session_ids = {str(sid) for sid in gateway_metadata.keys() if sid}
    session_ids = {
        _safe_first(session.get("session_id"))
        for session in sessions
        if isinstance(session, dict)
    }
    visible_active_gateway_session_ids = active_gateway_session_ids & session_ids
    active_gateway_sources = {
        _normalize_messaging_source(_safe_first(meta.get("raw_source"), meta.get("platform")))
        for sid, meta in gateway_metadata.items()
        if sid in visible_active_gateway_session_ids and isinstance(meta, dict)
    }
    active_gateway_sources = {source for source in active_gateway_sources if _is_known_messaging_source(source)}

    kept_sources: set[str] = set()
    best_by_source: dict[str, dict] = {}
    kept: list[dict] = []
    for session in sessions:
        key = _messaging_source_key(session)
        if not key:
            kept.append(session)
            continue
        if _should_hide_stale_messaging_session(session, visible_active_gateway_session_ids, active_gateway_sources):
            continue
        if key in kept_sources:
            kept_sources.add(key)
            current = best_by_source.get(key)
            if current is None or _session_sort_timestamp(session) > _session_sort_timestamp(current):
                best_by_source[key] = session
            continue
        kept_sources.add(key)
        best_by_source[key] = session

    kept.extend(best_by_source.values())
    kept.sort(key=_session_sort_timestamp, reverse=True)
    return kept


from api.models import (
    Session,
    get_session,
    get_session_for_scan,
    find_compression_recovery_session,
    get_session_for_file_ops,
    persist_recovered_workspace_binding,
    WorkspaceBindingPersistenceError,
    new_session,
    all_sessions,
    title_from,
    _write_session_index,
    SESSION_INDEX_FILE,
    _active_state_db_path,
    load_projects,
    save_projects,
    import_cli_session,
    CLAUDE_CODE_SOURCE,
    get_cli_sessions,
    get_cli_session_messages,
    get_state_db_session_messages,
    get_state_db_session_message_prefix_summary,
    get_state_db_session_message_keys_before_timestamp,
    get_state_db_session_summary,
    merge_session_messages_append_only,
    _reconcile_api_content_sidecars,
    _enrich_sidebar_lineage_metadata,
    _active_stream_ids,
    _evict_sessions_over_cap,
    _merge_session_display_metadata,
    _session_message_merge_key,
    _session_messages_have_prefix,
    _session_message_visible_key,
    _message_timestamp_as_float,
    _is_empty_partial_activity_message,
    _hide_from_default_sidebar,
    prune_session_from_index,
    agent_session_rows_existing,
    agent_session_zero_message_sids,
    _load_webui_zero_message_orphan_tombstone,
    _record_webui_zero_message_orphan_tombstone,
    _clear_webui_zero_message_orphan_tombstone,
    _load_webui_deleted_session_tombstone,
    _record_webui_deleted_session_tombstone,
    ensure_cron_project,
    _profile_has_user_projects,
    is_cron_session,
    is_safe_session_id,
    PROCESS_WAKEUP_PAUSE_ERROR,
    clear_process_wakeup_pause,
    clear_process_wakeup_pause_if_model_changed,
    process_wakeup_pause_matches,
    process_wakeup_credential_state_fingerprint,
    process_wakeup_pause_credential_state_changed,
    suppress_process_wakeup_for_provider_pause,
)


from api.route_chat import _COMPRESSION_RECOVERY_START_LOCK


def _pre_compression_continuation_session_id(session) -> str | None:
    """Return the newest visible descendant for a hidden compression snapshot.

    Mobile browsers can miss the final SSE `done` handoff while backgrounded.
    On reload they may request the archived pre-compression session id from the
    stale URL/localStorage. The old snapshot is intentionally hidden from the
    sidebar, so expose a lightweight recovery hint when a child continuation
    exists either in memory or on disk. Follow bounded snapshot-to-snapshot hops
    so repeated compression still lands on the latest visible continuation.
    """
    if not getattr(session, "pre_compression_snapshot", False):
        return None
    sid = _safe_first(getattr(session, "session_id", None))
    if not sid:
        return None
    # #2980 hardening: the resolved continuation is written to the client's
    # URL/localStorage, so it must stay within the requested snapshot's own
    # profile. Children are matched only by parent_session_id below; a
    # crafted/corrupt foreign-profile sidecar whose parent_session_id collided
    # with this snapshot's id would otherwise leak cross-profile. Pin the
    # snapshot's profile and reject any child that isn't profile-matched.
    snapshot_profile = getattr(session, "profile", None)

    def _child_rows_from_memory(seen_ids: set[str]) -> list:
        rows = []
        try:
            with LOCK:
                memory_sessions = list(SESSIONS.values())
            for child in memory_sessions:
                child_sid = _safe_first(getattr(child, "session_id", None))
                if not child_sid or child_sid in seen_ids:
                    continue
                seen_ids.add(child_sid)
                rows.append(child)
        except Exception:
            logger.debug("Silent exception in _child_rows_from_memory", exc_info=True)
            pass
        return rows

    def _child_rows_from_index(seen_ids: set[str]) -> list | None:
        if not SESSION_INDEX_FILE.exists():
            return None
        try:
            entries = json.loads(SESSION_INDEX_FILE.read_bytes())
        except Exception:
            logger.debug("Silent exception in _child_rows_from_index", exc_info=True)
            return None
        if not isinstance(entries, list):
            return None
        try:
            persisted_sidecar_ids = {
                path.stem
                for path in SESSION_DIR.glob("*.json")
                if not path.name.startswith("_") and is_safe_session_id(path.stem)
            }
        except Exception:
            logger.debug("Silent exception in _child_rows_from_index", exc_info=True)
            return None
        indexed_ids: set[str] = set()
        row_seen_ids = set(seen_ids)
        rows = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            child_sid = _safe_first(entry.get("session_id"))
            if not child_sid or not is_safe_session_id(child_sid):
                continue
            indexed_ids.add(child_sid)
            if child_sid in row_seen_ids or not _safe_first(entry.get("parent_session_id")):
                continue
            row_seen_ids.add(child_sid)
            rows.append(entry)
        # Guarantee here is index MEMBERSHIP-completeness, not per-entry content
        # freshness: if any persisted continuation sidecar is absent from the index
        # we bail to the full scan. A sidecar that IS in the index but whose entry is
        # content-stale (mid-write) still yields a valid continuation of the same
        # snapshot; proving freshness would require reading every sidecar, defeating
        # the optimization, so membership-completeness is the intended bar.
        if persisted_sidecar_ids - indexed_ids - seen_ids:
            return None
        return rows

    def _child_rows_from_sidecars(seen_ids: set[str]) -> list:
        rows = []
        try:
            for path in SESSION_DIR.glob("*.json"):
                if path.name.startswith("_"):
                    continue
                child_sid = path.stem
                if not child_sid or child_sid in seen_ids:
                    continue
                child = Session.load_metadata_only(child_sid)
                if child:
                    seen_ids.add(child_sid)
                    rows.append(child)
        except Exception:
            logger.debug("Silent exception in _child_rows_from_sidecars", exc_info=True)
            pass
        return rows

    def _row_value(row, key, default=None):
        return row.get(key, default) if isinstance(row, dict) else getattr(row, key, default)

    def _row_has_backing_state(row) -> bool:
        child_sid = _safe_first(_row_value(row, "session_id"))
        if not child_sid or not is_safe_session_id(child_sid):
            return False
        if not isinstance(row, dict):
            return True
        return (SESSION_DIR / f"{child_sid}.json").exists()

    def _resolve_from_rows(rows: list) -> str | None:
        children_by_parent: dict[str, list] = {}
        for child in rows:
            parent_sid = _safe_first(_row_value(child, "parent_session_id"))
            child_sid = _safe_first(_row_value(child, "session_id"))
            if not parent_sid or not child_sid or child_sid == sid:
                continue
            # Cross-profile guard: only follow continuations within the snapshot's profile.
            if not _profiles_match(_row_value(child, "profile"), snapshot_profile):
                continue
            children_by_parent.setdefault(parent_sid, []).append(child)

        candidates = []
        frontier = [sid]
        seen = {sid}
        for _ in range(20):
            if not frontier:
                break
            parent_sid = frontier.pop(0)
            for child in children_by_parent.get(parent_sid, []):
                child_sid = _safe_first(_row_value(child, "session_id"))
                if not child_sid or child_sid in seen or not _row_has_backing_state(child):
                    continue
                seen.add(child_sid)
                if _row_value(child, "pre_compression_snapshot", False):
                    frontier.append(child_sid)
                else:
                    candidates.append(child)

        if not candidates:
            return None
        latest = max(
            candidates,
            key=lambda child: (
                float(
                    _safe_first(
                        _row_value(child, "updated_at"),
                        _row_value(child, "created_at"),
                        0,
                    ) or 0
                ),
                # Secondary tiebreaker so the index-fast-path and the sidecar-scan
                # path resolve byte-identically on an updated_at/created_at tie
                # (otherwise the chosen sid could differ by iteration order).
                str(_safe_first(_row_value(child, "session_id"), "") or ""),
            ),
        )
        latest_sid = _safe_first(_row_value(latest, "session_id", None)) or None
        # Only hand the client a well-formed session id (it gets written to URL/localStorage).
        if latest_sid and not is_safe_session_id(latest_sid):
            return None
        return latest_sid

    memory_seen_ids: set[str] = set()
    rows = _child_rows_from_memory(memory_seen_ids)
    index_rows = _child_rows_from_index(memory_seen_ids)
    if index_rows is not None:
        return _resolve_from_rows(rows + index_rows)

    rows.extend(_child_rows_from_sidecars(memory_seen_ids))
    return _resolve_from_rows(rows)

from api.workspace import (
    load_workspaces,
    save_workspaces,
    get_last_workspace,
    get_profile_default_workspace,
    set_last_workspace,
    git_info_for_workspace,
    authorize_escape_target,
    EscapeAuthorizationExpiredError,
    list_dir,
    list_authorized_escape_dir,
    serialize_workspace_entries_for_browser,
    dir_signature,
    list_workspace_suggestions,
    read_file_content,
    read_authorized_escape_file_content,
    safe_resolve_ws,
    raw_authorized_escape_target,
    resolve_trusted_workspace,
    resolve_implicit_workspace_with_recovery,
    open_anchored_fd,
    open_anchored_create_fd,
    open_anchored_write_fd,
    unlink_anchored,
    rmtree_anchored,
    rename_anchored,
    make_anchored_dir,
    validate_workspace_to_add,
    _is_blocked_system_path,
    _home_path,
    _is_within,
    _strip_surrounding_quotes,
    _is_remote_terminal_backend,
    _workspace_blocked_roots,
)
from api.upload import (
    handle_upload,
    handle_upload_extract,
    handle_transcribe,
    handle_transcribe_capability,
    handle_workspace_upload,
)
from api.streaming import (
    _sse,
    _sse_set_write_deadline,
    _run_agent_streaming,
    cancel_stream,
    _materialize_pending_user_turn_before_error,
    generate_session_title_for_session,
    _compact_for_echo_compare,
    _strip_compact_echo_suffix,
)
from api.gateway_chat import _run_gateway_chat_streaming, webui_gateway_chat_enabled
from api.run_journal import (
    _parse_run_journal_event_id as _shared_parse_run_journal_event_id,
    bound_run_journal_snapshot_args,
    find_run_summary,
    read_run_events,
    read_session_run_events,
    session_journal_fingerprint,
    stale_interrupted_event,
    SSE_RELAY_CLOSE_EVENTS,
)
from api.todo_state import attach_todo_state
from api.providers import (
    get_providers,
    get_provider_quota,
    get_provider_cost_history,
    provider_has_process_wakeup_recovery_credential,
    set_provider_key,
    remove_provider_key,
)
from api.onboarding import (
    apply_onboarding_setup,
    get_onboarding_status,
    complete_onboarding,
    probe_provider_endpoint,
)
from api.oauth import (
    cancel_onboarding_oauth_flow,
    poll_onboarding_oauth_flow,
    start_onboarding_oauth_flow,
)

# Approval system -- state and helpers live in api.route_approvals; imported
# here for backward compatibility so existing call sites continue to resolve.
from api.route_approvals import (  # noqa: F401 — re-exports for backward compat
    _submit_pending_raw,
    approve_session,
    approve_permanent,
    save_permanent_allowlist,
    is_approved,
    _pending,
    _lock,
    _permanent_approved,
    _gateway_queues,
    resolve_gateway_approval,
    enable_session_yolo,
    disable_session_yolo,
    is_session_yolo_enabled,
    _approval_sse_subscribers,
    _approval_sse_subscribe,
    _approval_sse_unsubscribe,
    _approval_sse_notify_locked,
    _approval_sse_notify,
    _GATEWAY_AGENT_IDENTITY_V1,
    _GATEWAY_MIRROR_FLAG,
    _GATEWAY_MIRROR_TOKEN,
    _gateway_mirror_entry_token,
    gateway_yolo_handoff,
    begin_session_yolo_transition,
    claim_gateway_approval_relay_owner,
    finish_session_yolo_transition,
    gateway_pending_mirror,
    gateway_pending_mirrors,
    release_gateway_approval_relay_owner,
    retire_gateway_pending_mirror,
    reconcile_gateway_pending_mirror_locked,
    resolve_gateway_pending_local,
    resolve_gateway_pending_local_all,
    resolve_gateway_pending_local_no_run_mirror,
    set_session_yolo_enabled,
    submit_gateway_pending_mirror,
    submit_pending,
)

# Clarify prompts and session attention (extracted to api.route_approvals)
from api.route_approvals import (
    clarify_sse_subscribe,
    clarify_sse_unsubscribe,
    get_clarify_pending,
    get_clarify_pending_count,
    resolve_clarify,
    resolve_clarify_by_id,
    submit_clarify_pending,
    _session_attention_summary,
)


_SIDEBAR_SESSION_RESPONSE_FIELDS = {
    "session_id",
    "title",
    "display_title",
    "_state_db_title",
    "workspace",
    "model",
    "model_provider",
    "message_count",
    "user_message_count",
    "created_at",
    "updated_at",
    "last_message_at",
    "pinned",
    "archived",
    "project_id",
    "profile",
    "input_tokens",
    "output_tokens",
    "estimated_cost",
    "cache_read_tokens",
    "cache_write_tokens",
    "cache_hit_percent",
    "personality",
    "context_length",
    "config_context_length",
    "window_usage_percent",
    "source_tag",
    "raw_source",
    "session_source",
    "source_label",
    "is_cli_session",
    "is_messaging_session",
    "is_streaming",
    "cron_running",
    "active_stream_id",
    "has_pending_user_message",
    "pending_started_at",
    "default_hidden",
    "worktree_path",
    "worktree_branch",
    "parent_session_id",
    "parent_title",
    "parent_source",
    "relationship_type",
    "pre_compression_snapshot",
    "_lineage_root_id",
    "_lineage_tip_id",
    "_compression_segment_count",
    "_lineage_collapsed_count",
    "_parent_lineage_root_id",
    "_parent_lineage_tip_id",
    "_cross_surface_child_session",
    "match_type",
    "match_preview",
    # Preserved so the sidebar can suppress rename / action-menu / swipe on
    # read-only (imported CLI + Claude Code) sessions, and render the detailed
    # gateway model label. Dropping these silently regressed both surfaces.
    # Only the latest `gateway_routing` is included (the sidebar label reader
    # prefers it); the unbounded `gateway_routing_history` is intentionally NOT
    # sent in the list payload to avoid per-row bloat.
    "read_only",
    "is_read_only",
    "gateway_routing",
}


def _sidebar_session_response_item(session: dict, *, redact_enabled: bool | None = None) -> dict:
    """Return the bounded /api/sessions row shape used by the sidebar.

    Full session/detail fields such as messages, tool calls, compression
    summaries, context-engine state, gateway routing history, drafts, and
    pending user text are intentionally excluded from the list endpoint. Large
    installs should not ship tens of KB of per-row detail just to render a
    conversation title.
    """
    item = {
        key: value
        for key, value in dict(session).items()
        if key in _SIDEBAR_SESSION_RESPONSE_FIELDS
    }
    if isinstance(item.get("title"), str):
        item["title"] = _redact_text(item["title"], _enabled=redact_enabled)
    _redact_sidebar_title_fields(item, redact_enabled)
    item["attention"] = _session_attention_summary(str(item.get("session_id") or ""))
    return item


def _redact_sidebar_title_fields(item: dict, redact_enabled: bool | None = None) -> None:
    """Redact every user-content-derived title field on a sidebar/search row in place.

    `title` is redacted by the callers directly (they special-case it), but
    `display_title`, `_state_db_title`, and `parent_title` can ALSO carry raw
    user-message-derived text — e.g. #6056 derives a delegated subagent's
    `display_title` from its first user message, and `parent_title` copies a
    parent session's (possibly derived) title. Without this a credential-shaped
    value in a delegated goal would surface in the sidebar / search results even
    with `api_redact_enabled=True`. Shared by `_sidebar_session_response_item`
    (`/api/sessions`) and every `/api/sessions/search` response branch so the two
    endpoints can never drift on which fields get redacted.
    """
    for field in ("display_title", "_state_db_title", "parent_title"):
        value = item.get(field)
        if isinstance(value, str):
            item[field] = _redact_text(value, _enabled=redact_enabled)


# ── Login page locale strings ─────────────────────────────────────────────────
# Add entries here to support more languages on the login page.
# The key must match the 'language' setting value (from static/i18n.js LOCALES).
from api.route_auth import (  # noqa: E402
    _LOGIN_LOCALE,
    _LOGIN_PAGE_HTML,
    _oidc_login_html,
    _request_base_url,
    _resolve_login_locale_key,
    _safe_login_redirect_path,
)


# ── Logs & Insights (delegated to api.route_system) ───────────────────────────
from api.route_system import (  # noqa: E402
    _LOG_DEFAULT_TAIL,
    _LOG_FILE_WHITELIST,
    _LOG_MAX_BYTES,
    _LOG_TAIL_VALUES,
    _handle_insights,
    _handle_llm_wiki_status,
    _handle_logs,
    _normalize_logs_tail,
)


def _handle_project_os_dashboard(handler, parsed) -> bool:
    j(handler, {"workspace": None, "repo_root": None, "docs": {}})
    return True


# ── GET routes ────────────────────────────────────────────────────────────────


# ── Health checks (delegated to api.route_system) ─────────────────────────────
from api.route_system import (  # noqa: E402
    _accept_loop_health,
    _deep_health_checks,
    _handle_health,
    _run_lifecycle_health,
    _stream_runtime_diagnostics,
    _streams_lock_health,
)


from api.route_config_models import (  # noqa: E402
    _PLUGIN_VISIBILITY_HOOKS,
    _PLUGIN_VISIBILITY_HOOK_SET,
    _dashboard_plugin_enabled,
    _handle_plugins,
    _plugin_visibility_payload,
    _webui_plugin_payload,
)


from api.route_static import (  # noqa: E402
    _SHELL_ERROR_HTML,
    _serve_shell_unavailable,
)


_SHUTDOWN_LOG_VALUE_RE = re.compile(r"[\x00-\x1f\x7f]+")


def _shutdown_log_value(value, *, default: str = "unknown", max_len: int = 160) -> str:
    """Return a bounded single-line value safe for shutdown diagnostics."""
    if value is None:
        return default
    try:
        text = str(value)
    except Exception:
        logger.debug("Silent exception in _shutdown_log_value", exc_info=True)
        return default
    text = _SHUTDOWN_LOG_VALUE_RE.sub("?", text).strip()
    if not text:
        return default
    if len(text) > max_len:
        text = f"{text[:max_len]}…"
    return text


def _handle_shutdown(handler) -> bool:
    """Shut down the WebUI server process."""
    headers = getattr(handler, "headers", {})
    ua = headers.get("User-Agent", "no-ua") if hasattr(headers, "get") else "no-ua"
    remote = "unknown"
    if getattr(handler, "client_address", None):
        remote = getattr(handler, "client_address", ("unknown",))[0]
    logger.info(
        "[shutdown-request] remote=%s method=%s path=%s ua=%s",
        _shutdown_log_value(remote),
        _shutdown_log_value(getattr(handler, "command", None)),
        _shutdown_log_value(getattr(handler, "path", None), max_len=240),
        _shutdown_log_value(ua, default="no-ua", max_len=240),
    )
    j(handler, {"status": "shutting_down"})
    import signal
    import threading

    def _do_shutdown():
        import time
        time.sleep(0.3)
        os.kill(os.getpid(), signal.SIGINT)

    threading.Thread(target=_do_shutdown, daemon=True).start()
    return True


def _handle_health_restart(handler) -> bool:
    """Restart the Hermes messaging gateway service."""
    outcome = restart_active_profile_gateway()

    if outcome.get("status") == "completed":
        return j(handler, {"ok": True, "message": "Gateway service restarted successfully"})

    if outcome.get("status") == "in_progress":
        return j(handler, {"ok": True, "message": "Gateway service restart initiated (in progress)"})

    if outcome.get("status") == "busy":
        return j(
            handler,
            {"ok": False, "error": outcome.get("message", "Restart already in progress. Please wait a moment and try again.")},
            status=429,
        )

    return j(
        handler,
        {"ok": False, "error": outcome.get("message", "Internal error running restart")},
        status=500,
    )


from api.route_static import _serve_manifest  # noqa: E402


from api.route_config_models import (  # noqa: E402
    _saved_prompts_path,
    _load_saved_prompts,
    _save_saved_prompts,
)


# In-process cache for the app-shell template. The `/`, `/index.html`, and
# `/session/<id>` routes are the hottest navigations and each re-read the
from api.route_static import (  # noqa: E402
    _INDEX_SHELL_CACHE,
    _INDEX_SHELL_CACHE_LOCK,
    _render_index_shell_base,
)


from api.route_config_models import (  # noqa: E402
    _AGY_MODELS_CACHE,
    _AGY_MODELS_CACHE_TIME,
    _get_agy_models_payload,
)


from api.route_static import _handle_get_static_and_shell  # noqa: E402
from api.route_auth import (  # noqa: E402
    _handle_get_auth,
    _handle_post_auth,
    _require_passkey_registration_auth,
)
from api.route_system import _handle_get_system  # noqa: E402
from api.route_config_models import (  # noqa: E402
    _handle_get_config_and_models,
    _handle_post_config_and_settings,
)
from api.route_session import (  # noqa: E402
    _handle_get_session,
    _handle_post_session,
    _resolve_new_session_workspace,
    _validate_session_toolsets_shape,
)
from api.route_workspace_git import (  # noqa: E402
    _handle_get_workspace_and_git,
    _handle_post_workspace_and_git,
)
from api.route_chat import (  # noqa: E402
    _handle_get_chat_and_stream,
    _handle_post_chat_and_stream,
)
from api.route_tools_mcp import (  # noqa: E402
    _handle_get_tools_and_mcp,
    _handle_post_tools_and_mcp,
)


def handle_get(handler, parsed) -> bool:
    """Handle all GET routes. Returns True if handled, False for 404."""
    proxy_result = _handle_extension_sidecar_proxy(handler, parsed, "GET")
    if proxy_result is not False:
        return proxy_result

    res = _handle_get_static_and_shell(handler, parsed)
    if res is not None or getattr(handler, "_response_sent", False):
        return True if getattr(handler, "_response_sent", False) else res

    res = _handle_get_auth(handler, parsed)
    if res is not None or getattr(handler, "_response_sent", False):
        return True if getattr(handler, "_response_sent", False) else res

    if parsed.path.startswith("/api/") and not _guard_request_session_visibility(handler, parsed, method="GET"):
        return True

    res = _handle_get_system(handler, parsed)
    if res is not None or getattr(handler, "_response_sent", False):
        return True if getattr(handler, "_response_sent", False) else res

    res = _handle_get_config_and_models(handler, parsed)
    if res is not None or getattr(handler, "_response_sent", False):
        return True if getattr(handler, "_response_sent", False) else res

    res = _handle_get_session(handler, parsed)
    if res is not None or getattr(handler, "_response_sent", False):
        return True if getattr(handler, "_response_sent", False) else res

    res = _handle_get_workspace_and_git(handler, parsed)
    if res is not None or getattr(handler, "_response_sent", False):
        return True if getattr(handler, "_response_sent", False) else res

    res = _handle_get_chat_and_stream(handler, parsed)
    if res is not None or getattr(handler, "_response_sent", False):
        return True if getattr(handler, "_response_sent", False) else res

    res = _handle_get_tools_and_mcp(handler, parsed)
    if res is not None or getattr(handler, "_response_sent", False):
        return True if getattr(handler, "_response_sent", False) else res

    return False  # 404


def _read_json_body(handler) -> dict:
    try:
        length = int(handler.headers.get("Content-Length", 0))
        if length <= 0:
            return {}
        raw = handler.rfile.read(length).decode("utf-8")
        return json.loads(raw) if raw else {}
    except Exception:
        logger.debug("Silent exception in _read_json_body", exc_info=True)
        return {}



def _handle_post_pre_body(handler, parsed, diag=None):
    """Handle POST routes that read their own body or stream raw data.
    Returns True/response if handled, None if unhandled.
    """
    if parsed.path == "/api/mcp/hub/add":
        from api.mcp_hub import add_or_update_mcp_server
        body = _read_json_body(handler) or {}
        name = str(body.get("name", "")).strip()
        if not name:
            return bad(handler, "Server name is required", status=400)
        return j(handler, add_or_update_mcp_server(name, body))

    if parsed.path == "/api/mcp/hub/toggle":
        from api.mcp_hub import toggle_mcp_server
        body = _read_json_body(handler) or {}
        name = str(body.get("name", "")).strip()
        enabled = bool(body.get("enabled", True))
        return j(handler, toggle_mcp_server(name, enabled))

    if parsed.path == "/api/mcp/hub/delete":
        from api.mcp_hub import delete_mcp_server
        body = _read_json_body(handler) or {}
        name = str(body.get("name", "")).strip()
        return j(handler, delete_mcp_server(name))

    if parsed.path == "/api/mcp/hub/test":
        from api.mcp_hub import test_mcp_server
        body = _read_json_body(handler) or {}
        return j(handler, test_mcp_server(body))

    if parsed.path == "/api/diff/compute":
        from api.diff_viewer import compute_structured_diff
        body = _read_json_body(handler) or {}
        original = body.get("original", "")
        modified = body.get("modified", "")
        filename = body.get("filename", "")
        return j(handler, compute_structured_diff(original, modified, filename=filename))

    # ── Knowledge Vault & Graph Memory (POST) ──
    if parsed.path == "/api/vault/note":
        from api.vault import save_note, get_vault_dir
        body = _read_json_body(handler) or {}
        path = body.get("path", "")
        if path.startswith("knowledge/"):
            path = path[len("knowledge/"):]
        content = body.get("content", "")
        return j(handler, save_note(get_vault_dir(), path, content))

    if parsed.path == "/api/vault/delete":
        from api.vault import delete_note, get_vault_dir
        body = _read_json_body(handler) or {}
        path = body.get("path", "")
        if path.startswith("knowledge/"):
            path = path[len("knowledge/"):]
        return j(handler, delete_note(get_vault_dir(), path))

    if parsed.path == "/api/vault/sync":
        from api.vault import sync_vault_to_rules, get_vault_dir, infer_space_from_workspace
        body = _read_json_body(handler) or {}
        qs = parse_qs(parsed.query) if getattr(parsed, "query", None) else {}
        ws_param = body.get("workspace") or qs.get("workspace", [None])[0]
        space_param = body.get("space") or qs.get("space", [None])[0]
        ws_root = Path(ws_param) if ws_param and Path(ws_param).exists() else None
        if not ws_root:
            for var in ("AGY_WORKSPACE_ROOT", "WORKSPACE_DIR", "AGY_WORKSPACE_DIR", "HERMES_WORKSPACE_ROOT"):
                val = os.environ.get(var)
                if val and Path(val).exists():
                    ws_root = Path(val)
                    break
        if not ws_root:
            ws_root = Path("/workspace") if Path("/workspace").exists() else Path.cwd()
        if not space_param:
            space_param = infer_space_from_workspace(ws_root)
        return j(handler, sync_vault_to_rules(get_vault_dir(ws_root), ws_root, space=space_param))

    if parsed.path == "/api/vault/memorize":
        from api.vault import memorize_insight, get_vault_dir, infer_space_from_workspace
        body = _read_json_body(handler) or {}
        text = str(body.get("text", "")).strip()
        category = body.get("category")
        title = body.get("title")
        space_param = body.get("space")
        ws_param = body.get("workspace")
        ws_root = Path(ws_param) if ws_param and Path(ws_param).exists() else None
        if not ws_root:
            for var in ("AGY_WORKSPACE_ROOT", "WORKSPACE_DIR", "AGY_WORKSPACE_DIR", "HERMES_WORKSPACE_ROOT"):
                val = os.environ.get(var)
                if val and Path(val).exists():
                    ws_root = Path(val)
                    break
        if not ws_root:
            ws_root = Path("/workspace") if Path("/workspace").exists() else Path.cwd()
        if not space_param:
            space_param = infer_space_from_workspace(ws_root)
        return j(handler, memorize_insight(get_vault_dir(ws_root), text, category=category, title=title, workspace_path=ws_root, space=space_param))

    if parsed.path == "/api/vault/rename":
        from api.vault import rename_note, get_vault_dir
        body = _read_json_body(handler) or {}
        old_path = body.get("old_path", "")
        new_path = body.get("new_path", "")
        if old_path.startswith("knowledge/"):
            old_path = old_path[len("knowledge/"):]
        if new_path.startswith("knowledge/"):
            new_path = new_path[len("knowledge/"):]
        ws_param = body.get("workspace")
        ws_root = Path(ws_param) if ws_param and Path(ws_param).exists() else None
        if not ws_root:
            for var in ("AGY_WORKSPACE_ROOT", "WORKSPACE_DIR", "AGY_WORKSPACE_DIR", "HERMES_WORKSPACE_ROOT"):
                val = os.environ.get(var)
                if val and Path(val).exists():
                    ws_root = Path(val)
                    break
        if not ws_root:
            ws_root = Path("/workspace") if Path("/workspace").exists() else Path.cwd()
        return j(handler, rename_note(get_vault_dir(ws_root), old_path, new_path, workspace_path=ws_root))

    if parsed.path == "/api/vault/heal":
        from api.vault import heal_vault, get_vault_dir, infer_space_from_workspace
        body = _read_json_body(handler) or {}
        space_param = body.get("space")
        ws_param = body.get("workspace")
        ws_root = Path(ws_param) if ws_param and Path(ws_param).exists() else None
        if not ws_root:
            for var in ("AGY_WORKSPACE_ROOT", "WORKSPACE_DIR", "AGY_WORKSPACE_DIR", "HERMES_WORKSPACE_ROOT"):
                val = os.environ.get(var)
                if val and Path(val).exists():
                    ws_root = Path(val)
                    break
        if not ws_root:
            ws_root = Path("/workspace") if Path("/workspace").exists() else Path.cwd()
        if not space_param:
            space_param = infer_space_from_workspace(ws_root)
        return j(handler, heal_vault(get_vault_dir(ws_root), workspace_path=ws_root, space=space_param))

    if parsed.path == "/api/vault/weave":
        from api.vault import weave_wikilinks, get_vault_dir, infer_space_from_workspace, get_note, save_note
        body = _read_json_body(handler) or {}
        content = body.get("content", "")
        note_path = body.get("path", "")
        if note_path.startswith("knowledge/"):
            note_path = note_path[len("knowledge/"):]
        space_param = body.get("space")
        ws_param = body.get("workspace")
        ws_root = Path(ws_param) if ws_param and Path(ws_param).exists() else None
        if not ws_root:
            for var in ("AGY_WORKSPACE_ROOT", "WORKSPACE_DIR", "AGY_WORKSPACE_DIR", "HERMES_WORKSPACE_ROOT"):
                val = os.environ.get(var)
                if val and Path(val).exists():
                    ws_root = Path(val)
                    break
        if not ws_root:
            ws_root = Path("/workspace") if Path("/workspace").exists() else Path.cwd()
        if not space_param:
            space_param = infer_space_from_workspace(ws_root)
        vdir = get_vault_dir(ws_root)
        if note_path:
            existing = get_note(vdir, note_path)
            if existing.get("ok"):
                content = existing.get("content", "")
                weaved = weave_wikilinks(vdir, content, space=space_param, exclude_id=existing.get("id"))
                if weaved.get("links_added", 0) > 0:
                    save_note(vdir, note_path, weaved["content"], workspace_path=ws_root)
                return j(handler, weaved)
        return j(handler, weave_wikilinks(vdir, content, space=space_param))

    if parsed.path == "/api/vault/digest":
        from api.vault import digest_note, get_vault_dir, infer_space_from_workspace
        body = _read_json_body(handler) or {}
        text = str(body.get("text", "")).strip()
        title = body.get("title")
        category = body.get("category", "notes")
        space_param = body.get("space")
        ws_param = body.get("workspace")
        ws_root = Path(ws_param) if ws_param and Path(ws_param).exists() else None
        if not ws_root:
            for var in ("AGY_WORKSPACE_ROOT", "WORKSPACE_DIR", "AGY_WORKSPACE_DIR", "HERMES_WORKSPACE_ROOT"):
                val = os.environ.get(var)
                if val and Path(val).exists():
                    ws_root = Path(val)
                    break
        if not ws_root:
            ws_root = Path("/workspace") if Path("/workspace").exists() else Path.cwd()
        if not space_param:
            space_param = infer_space_from_workspace(ws_root)
        return j(handler, digest_note(get_vault_dir(ws_root), text, space=space_param, title=title, category=category, workspace_path=ws_root))

    if parsed.path == "/api/skills/scaffold":
        from api.skills_wizard import scaffold_skill_or_rule
        body = _read_json_body(handler) or {}
        return j(handler, scaffold_skill_or_rule(body))

    if parsed.path == "/api/shutdown":
        return _handle_shutdown(handler)

    if parsed.path == "/api/health/restart":
        return _handle_health_restart(handler)

    if parsed.path == "/api/upload":
        return handle_upload(handler)
    if parsed.path == "/api/upload/extract":
        return handle_upload_extract(handler)
    if parsed.path == "/api/workspace/upload":
        return handle_workspace_upload(handler)

    if parsed.path == "/api/transcribe":
        return handle_transcribe(handler)

    if parsed.path == "/api/client-events/log":
        if diag:
            diag.stage("read_client_event_body")
        return _handle_client_event_log(handler, _read_client_event_payload(handler))

    return None


def _handle_process_complete_ack_deprecated(handler):
    """Handle deprecated /api/process-complete-ack endpoint (410 Gone)."""
    return j(
        handler,
        {
            "error": (
                "gone: /api/process-complete-ack was replaced by "
                "/api/bg-task-complete-ack as part of the "
                "process_complete -> bg_task_complete event rename"
            ),
            "replaced_by": "/api/bg-task-complete-ack",
        },
        status=410,
        extra_headers={"X-Replaced-By": "/api/bg-task-complete-ack"},
    )


def handle_post(handler, parsed) -> bool:
    """Handle all POST routes. Returns True if handled, False for 404."""
    diag = RequestDiagnostics.maybe_start("POST", parsed.path, logger=logger, print_fn=getattr(handler, '_safe_webui_print', None))
    if parsed.path == "/api/csp-report":
        if diag:
            diag.stage("csp_report")
        try:
            return _handle_csp_report(handler)
        finally:
            if diag:
                diag.finish()
    if parsed.path == "/api/process-complete-ack":
        if diag:
            diag.stage("process_complete_ack_deprecated")
        try:
            return _handle_process_complete_ack_deprecated(handler)
        finally:
            if diag:
                diag.finish()
    # CSRF: reject cross-origin or tokenless authenticated browser requests.
    # /api/auth/login has no authenticated session token yet, and /api/csp-report
    # is intentionally unauthenticated for browser-generated violation reports.
    if diag:
        diag.stage("csrf")
    if not _csrf_exempt_path(parsed.path) and not _check_csrf(handler):
        try:
            return j(handler, {"error": _csrf_rejection_error(handler)}, status=403)
        finally:
            if diag:
                diag.finish()
    proxy_result = _handle_extension_sidecar_proxy(
        handler,
        parsed,
        "POST",
        read_request_body=True,
    )
    if proxy_result is not False:
        if diag:
            diag.finish()
        return proxy_result

    res = _handle_post_pre_body(handler, parsed, diag=diag)
    if res is not None or getattr(handler, "_response_sent", False):
        return True if getattr(handler, "_response_sent", False) else res

    if diag:
        diag.stage("read_body")
    try:
        body = read_body(handler)
    except ValueError as exc:
        if diag:
            diag.finish()
        status = 413 if "too large" in str(exc).lower() else 400
        return bad(handler, str(exc), status=status)
    except Exception:
        logger.warning("Silent exception in handle_post", exc_info=True)
        if diag:
            diag.finish()
        raise
    if not _guard_request_session_visibility(handler, parsed, body=body, method="POST"):
        if diag:
            diag.finish()
        return True

    res = _handle_post_auth(handler, parsed, body)
    if res is not None or getattr(handler, "_response_sent", False):
        return True if getattr(handler, "_response_sent", False) else res

    res = _handle_post_session(handler, parsed, body)
    if res is not None or getattr(handler, "_response_sent", False):
        return True if getattr(handler, "_response_sent", False) else res

    res = _handle_post_chat_and_stream(handler, parsed, body, diag=diag)
    if res is not None or getattr(handler, "_response_sent", False):
        return True if getattr(handler, "_response_sent", False) else res

    res = _handle_post_workspace_and_git(handler, parsed, body)
    if res is not None or getattr(handler, "_response_sent", False):
        return True if getattr(handler, "_response_sent", False) else res

    res = _handle_post_config_and_settings(handler, parsed, body)
    if res is not None or getattr(handler, "_response_sent", False):
        return True if getattr(handler, "_response_sent", False) else res

    res = _handle_post_tools_and_mcp(handler, parsed, body, diag=diag)
    if res is not None or getattr(handler, "_response_sent", False):
        return True if getattr(handler, "_response_sent", False) else res

    return False  # 404


def handle_patch(handler, parsed) -> bool:
    """Handle all PATCH routes. Returns True if handled, False for 404."""
    if not _check_csrf(handler):
        return j(handler, {"error": _csrf_rejection_error(handler)}, status=403)
    proxy_result = _handle_extension_sidecar_proxy(
        handler,
        parsed,
        "PATCH",
        read_request_body=True,
    )
    if proxy_result is not False:
        return proxy_result
    body = read_body(handler)
    if not _guard_request_session_visibility(handler, parsed, body=body, method="PATCH"):
        return True
    if parsed.path.startswith("/api/mcp/servers/"):
        name = parsed.path[len("/api/mcp/servers/"):]
        return _handle_mcp_server_toggle(handler, name, body)
    return False


def handle_delete(handler, parsed) -> bool:
    """Handle all DELETE routes. Returns True if handled, False for 404."""
    if not _check_csrf(handler):
        return j(handler, {"error": _csrf_rejection_error(handler)}, status=403)
    proxy_result = _handle_extension_sidecar_proxy(
        handler,
        parsed,
        "DELETE",
        read_request_body=True,
    )
    if proxy_result is not False:
        return proxy_result
    body = read_body(handler)
    if not _guard_request_session_visibility(handler, parsed, body=body, method="DELETE"):
        return True
    if parsed.path.startswith("/api/mcp/servers/"):
        name = parsed.path[len("/api/mcp/servers/"):]
        return _handle_mcp_server_delete(handler, name)
    if parsed.path == "/api/prompts":
        pid = str(body.get("id") or "").strip()
        if not pid:
            return bad(handler, "id is required")
        prompts = [p for p in _load_saved_prompts() if p.get("id") != pid]
        _save_saved_prompts(prompts)
        return j(handler, {"ok": True})
    return False


def handle_put(handler, parsed) -> bool:
    """Handle all PUT routes. Returns True if handled, False for 404."""
    if not _check_csrf(handler):
        return j(handler, {"error": "Cross-origin request rejected"}, status=403)
    proxy_result = _handle_extension_sidecar_proxy(
        handler,
        parsed,
        "PUT",
        read_request_body=True,
    )
    if proxy_result is not False:
        return proxy_result
    body = read_body(handler)
    if not _guard_request_session_visibility(handler, parsed, body=body, method="PUT"):
        return True
    if parsed.path.startswith("/api/mcp/servers/"):
        name = parsed.path[len("/api/mcp/servers/"):]
        return _handle_mcp_server_update(handler, name, body)
    return False

# ── GET route helpers ─────────────────────────────────────────────────────────

# MIME types for static file serving. Hoisted to module scope to avoid
# rebuilding the dict on every request.
from api.route_static import (  # noqa: E402
    _COMPRESSIBLE_MIME,
    _STATIC_CACHE,
    _STATIC_CACHE_LOCK,
    _STATIC_MIME,
    _TEXT_MIME_TYPES,
    _serve_static,
)


from api.route_session import (  # noqa: F401 — re-exports for backward compat
    _handle_session_export,
    _handle_session_export_workspace,
    _handle_sessions_search,
    _session_search_message_text,
    _session_search_preview,
)


from api.route_workspace_git import (  # noqa: F401 — re-exports for backward compat
    _handle_escape_authorize,
    _handle_escape_file_raw,
    _handle_escape_file_read,
    _handle_escape_list_dir,
    _handle_list_dir,
    _read_json_request_body,
)


from api.route_chat import (  # noqa: F401 — re-exports for backward compat
    _EMBEDDED_TERMINAL_GATE_DENIED_MESSAGE,
    _REMOTE_TERMINAL_BACKEND_UNSUPPORTED_ERROR,
    _REMOTE_TERMINAL_BACKEND_UNSUPPORTED_MESSAGE,
    _chat_stream_resume_cursor,
    _handle_session_run_journal_stream_for_session,
    _handle_session_sse_stream_for_session,
    _handle_sse_stream,
    _handle_terminal_close,
    _handle_terminal_input,
    _handle_terminal_output,
    _handle_terminal_resize,
    _handle_terminal_start,
    _parse_run_journal_after_seq,
    _parse_run_journal_after_seq_value,
    _parse_run_journal_event_id,
    _project_runner_event_payload,
    _replay_run_journal,
    _run_journal_covers_offline_gap,
    _run_journal_same_run_seq,
    _runner_event_id,
    _runner_event_name,
    _runner_event_payload,
    _runner_stream_cursor_from_query,
    _session_events_path_session_id,
    _session_events_resume_event_id,
    _session_snapshot_payload,
    _sse_offline_gap_recovery,
    _sse_replay_run_journal_gap_checked,
    _sse_with_id,
    _stream_runner_run_events,
    _terminal_remote_backend_enabled,
    _terminal_session_lookup,
)

def _gateway_sse_probe_payload(settings, watcher):
    enabled = bool(settings.get('show_cli_sessions'))
    # Use the public is_alive() accessor where available (current GatewayWatcher);
    # fall back to the private _thread check for any older in-memory instance
    # that might still be hanging around mid-upgrade, and for test doubles that
    # don't implement the full public API.
    if watcher is None:
        watcher_alive = False
    elif hasattr(watcher, 'is_alive') and callable(getattr(watcher, 'is_alive')):
        watcher_alive = bool(watcher.is_alive())
    else:
        _t = getattr(watcher, '_thread', None)
        watcher_alive = _t is not None and _t.is_alive()
    payload = {
        'enabled': enabled,
        'fallback_poll_ms': 30000,
        'ok': enabled and watcher_alive,
        'watcher_running': watcher_alive,
        # Cross-client scope markers (hermes-webui/hermes-android#58 follow-up):
        # this probe ONLY describes the optional gateway/agent-sessions stream.
        # Persistent per-session streaming (GET /api/session/stream) is always
        # available and is NOT gated by show_cli_sessions, so a negative gateway
        # probe result must not be read as "session SSE unavailable".
        'scope': 'gateway_sessions',
        'session_stream_available': True,
        'session_stream_path': '/api/session/stream',
    }
    if not enabled:
        payload['error'] = 'agent sessions not enabled'
        return payload, 404
    if not watcher_alive:
        payload['error'] = 'watcher not started'
        return payload, 503
    return payload, 200


def _handle_gateway_sse_stream(handler, parsed):
    """SSE endpoint for real-time gateway session updates.
    Streams change events from the gateway watcher background thread.
    Only active when show_cli_sessions (show_agent_sessions) setting is enabled.

    Probe mode (``?probe=1``) reports the status of THIS optional stream only.
    Its result says nothing about the always-on persistent per-session stream
    (``/api/session/stream``) — the probe payload carries explicit
    ``scope`` / ``session_stream_available`` markers so cross-client consumers
    do not misclassify usable session streaming as unavailable.
    """
    settings = load_settings()

    from api.gateway_watcher import get_watcher
    watcher = get_watcher()

    probe = parse_qs(parsed.query).get('probe', [''])[0].lower() in {'1', 'true', 'yes'}
    if probe:
        payload, status = _gateway_sse_probe_payload(settings, watcher)
        return j(handler, payload, status=status)

    # Check if the feature is enabled
    if not settings.get('show_cli_sessions'):
        return j(handler, {'error': 'agent sessions not enabled'}, status=404)

    # Same watcher_alive semantics as the probe path — centralised via
    # the helper so both branches stay in sync.
    _probe_body, _probe_status = _gateway_sse_probe_payload(settings, watcher)
    if not _probe_body['watcher_running']:
        return j(handler, {'error': 'watcher not started'}, status=503)

    handler.send_response(200)
    handler.send_header('Content-Type', 'text/event-stream; charset=utf-8')
    handler.send_header('Cache-Control', 'no-cache')
    handler.send_header('X-Accel-Buffering', 'no')
    # #3103: do NOT emit `Connection: close` on long-lived SSE streams.
    # The python BaseHTTPServer worker only handles one request per
    # connection anyway, but browsers (Chrome/Firefox) treat the close
    # header as a hard signal that the EventSource lifecycle has ended
    # and trigger an instant reconnect, producing a tight loop of
    # connect/sessions_changed snapshot/disconnect that thrashes the
    # session list every ~1s. Letting the server close the socket
    # naturally after the stream ends is sufficient.
    end_sse_headers(handler)
    _sse_set_write_deadline(handler)  # Defect A: slow tab can't pin this thread

    q = watcher.subscribe()
    try:
        # Send initial snapshot immediately
        from api.models import get_cli_sessions
        initial = get_cli_sessions()
        _sse(handler, 'sessions_changed', {'sessions': initial})

        while True:
            try:
                event_data = q.get(timeout=_SSE_HEARTBEAT_INTERVAL_SECONDS)
            except queue.Empty:
                handler.wfile.write(b': keepalive\n\n')
                handler.wfile.flush()
                continue
            if event_data is None:
                break  # watcher is stopping
            _sse(handler, event_data.get('type', 'sessions_changed'), event_data)
    except _CLIENT_DISCONNECT_ERRORS:
        pass
    finally:
        watcher.unsubscribe(q)
    return True


def _handle_session_events_stream(handler):
    """SSE endpoint for lightweight session-list invalidation events."""
    handler.send_response(200)
    handler.send_header('Content-Type', 'text/event-stream; charset=utf-8')
    handler.send_header('Cache-Control', 'no-cache')
    handler.send_header('X-Accel-Buffering', 'no')
    # #3103: see _handle_gateway_sse_stream — `Connection: close` causes
    # EventSource reconnect storms in browsers on long-lived SSE.
    end_sse_headers(handler)
    _sse_set_write_deadline(handler)  # Defect A: slow tab can't pin this thread

    q = subscribe_session_events()
    try:
        while True:
            try:
                event_data = q.get(timeout=_SSE_HEARTBEAT_INTERVAL_SECONDS)
            except queue.Empty:
                handler.wfile.write(b': keepalive\n\n')
                handler.wfile.flush()
                continue
            _sse(handler, event_data.get('type', 'sessions_changed'), event_data)
    except _CLIENT_DISCONNECT_ERRORS:
        pass
    finally:
        unsubscribe_session_events(q)
    return True


def _content_disposition_value(disposition: str, filename: str) -> str:
    """Build a latin-1-safe Content-Disposition value with RFC 5987 filename*."""
    import urllib.parse as _up

    safe_name = Path(filename).name.replace("\r", "").replace("\n", "")
    ascii_fallback = "".join(
        ch if 32 <= ord(ch) < 127 and ch not in {'"', '\\'} else "_"
        for ch in safe_name
    ).strip(" .")
    if not ascii_fallback:
        suffix = Path(safe_name).suffix
        ascii_suffix = "".join(
            ch if 32 <= ord(ch) < 127 and ch not in {'"', '\\'} else "_"
            for ch in suffix
        )
        ascii_fallback = f"download{ascii_suffix}" if ascii_suffix else "download"
    quoted_name = _up.quote(safe_name, safe="")
    return (
        f'{disposition}; filename="{ascii_fallback}"; '
        f"filename*=UTF-8''{quoted_name}"
    )


def _parse_range_header(range_header: str, file_size: int) -> tuple[int, int] | None:
    """Parse a single HTTP bytes range into inclusive start/end offsets."""
    if not range_header or not range_header.startswith("bytes=") or file_size < 1:
        return None
    spec = range_header.split("=", 1)[1].strip()
    if "," in spec or "-" not in spec:
        return None
    start_s, end_s = spec.split("-", 1)
    try:
        if start_s == "":
            # suffix range: bytes=-500
            suffix_len = int(end_s)
            if suffix_len <= 0:
                return None
            start = max(0, file_size - suffix_len)
            end = file_size - 1
        else:
            start = int(start_s)
            end = int(end_s) if end_s else file_size - 1
            if start < 0:
                return None
            end = min(end, file_size - 1)
        if start > end or start >= file_size:
            return None
        return start, end
    except ValueError:
        return None


def _open_file_read_fd(target: Path, anchor_root: Path | None = None) -> int:
    if anchor_root is None:
        flags = os.O_RDONLY
        # On Windows, files are opened in text mode by default; O_BINARY
        # prevents CRLF translation that would corrupt binary media files.
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        return os.open(str(target), flags)
    return open_anchored_fd(anchor_root, target.resolve(), want_dir=False)


def _close_fd_quietly(fd: int | None) -> None:
    if fd is None:
        return
    try:
        os.close(fd)
    except OSError:
        pass


# Maximum size for which a content-derived ETag is computed.  Files above
# this cap (and all HTML with no-store) are served without ETag to avoid
# hashing every byte of large media / Range requests.
_ETAG_SIZE_CAP = 10 * 1024 * 1024  # 10 MB


def _bytes_etag(data: bytes) -> str:
    """Weak ETag from a content digest of the bytes that will be served.

    The bytes must be an immutable snapshot (e.g. pread or an in-memory copy)
    so the validator cannot diverge from the body under TOCTOU.
    """
    return 'W/"%s"' % hashlib.sha256(data).hexdigest()


def _etag_and_snapshot(fd, *, file_size: int) -> tuple[str | None, bytes | None, int]:
    """Return (weak ETag, snapshot bytes, actual size) for files under the size cap.

    Uses os.lseek + looped os.read to grab an immutable snapshot in a cross-platform
    and short-read-safe manner. The snapshot bytes can be sent directly so the ETag
    and the body can never diverge under TOCTOU (the file may change on disk after read).

    Returns (None, None, file_size) for files above the cap or on unexpected I/O failure.
    Returns (None, None, actual_size) if the file was truncated mid-read (short snapshot).
    The caller must use actual_size (not the original file_size) for Content-Length/Range
    calculations to avoid header/body mismatch.
    """
    if file_size > _ETAG_SIZE_CAP:
        return None, None, file_size

    # Cross-platform: os.lseek + looped os.read instead of POSIX-only os.pread
    # Short reads are possible (interrupted, EOF from truncation), so we loop.
    os.lseek(fd, 0, os.SEEK_SET)
    data = b""
    remaining = file_size
    while remaining > 0:
        chunk = os.read(fd, min(1024 * 1024, remaining))
        if not chunk:  # EOF reached (file was truncated)
            break
        data += chunk
        remaining -= len(chunk)

    actual_size = len(data)
    if actual_size == 0:
        return None, None, 0

    # If the file was truncated after fstat (short snapshot), return the actual
    # size but no ETag — caller will fall back to streaming without ETag.
    # Round-6 fix: keep the captured `data` so the body path serves the immutable
    # snapshot instead of re-reading the fd after headers are committed.
    # Content-Length derives from actual_size == len(data), so header/body match.
    if actual_size != file_size:
        return None, data, actual_size

    return _bytes_etag(data), data, actual_size


def _serve_file_bytes(handler, target: Path, mime: str, disposition: str, cache_control: str, *, csp: str | None = None, anchor_root: Path | None = None, download_name: str | None = None):
    """Serve a file with correct MIME/disposition and optional byte-range support.

    Supports conditional GET via If-None-Match (ETag) — when the ETag matches,
    the request is short-circuited with 304 so revalidating clients (e.g.
    `no-cache` responses) do not re-download unchanged files.

    ``download_name`` overrides the Content-Disposition filename (used when
    serving an immutable media snapshot whose on-disk name is a content digest).
    """
    fd = None
    try:
        fd = _open_file_read_fd(target, anchor_root)
        st = os.fstat(fd)
        file_size = st.st_size
    except PermissionError:
        _close_fd_quietly(fd)
        return bad(handler, "Permission denied", 403)
    except FileNotFoundError:
        _close_fd_quietly(fd)
        return j(handler, {"error": "not found"}, status=404)
    except ValueError as e:
        _close_fd_quietly(fd)
        return bad(handler, _sanitize_error(e), 403)
    except Exception:
        logger.debug("Silent exception in _serve_file_bytes", exc_info=True)
        _close_fd_quietly(fd)
        return bad(handler, "Could not stat file", 500)

    try:
        # Pre-commit phase: ETag/snapshot computation and response selection.
        # OSError here is still convertible into a clean 500 because no
        # status line has been written yet. After end_headers() the response
        # is committed, so body-transmission errors must never call bad()
        # again (that would attempt a second send_response on a stream the
        # client may have already closed — see the body phase below).
        try:
            no_store = "no-store" in cache_control
            if no_store or file_size > _ETAG_SIZE_CAP:
                etag = None
                snapshot = None
                # actual_size stays as file_size for over-cap/no-store cases
            else:
                etag, snapshot, actual_size = _etag_and_snapshot(fd, file_size=file_size)
                # If the file was truncated mid-read (short snapshot), use the
                # actual read size for all subsequent calculations so
                # Content-Length/Range match the body we can actually send.
                # Round-6 fix: the captured bytes are kept, so the body path
                # serves the immutable snapshot instead of re-reading the fd.
                if actual_size != file_size:
                    file_size = actual_size  # reconcile to avoid header/body mismatch
        except OSError:
            return bad(handler, "Could not serve file", 500)

        # RFC 7232 §3.2: If-None-Match uses weak comparison (W/ prefixes ignored)
        # and "*" matches any existing resource. On match, GET/HEAD is
        # short-circuited with 304 — processed before Range since a matched
        # conditional request skips the entity entirely.
        if_none_match = handler.headers.get("If-None-Match", "")
        if if_none_match and etag is not None:
            current = etag[2:] if etag.startswith("W/") else etag
            matched = if_none_match.strip() == "*" or any(
                (c.strip()[2:] if c.strip().startswith("W/") else c.strip()) == current
                for c in if_none_match.split(",")
                if c.strip()
            )
            if matched:
                handler.send_response(304)
                handler.send_header("ETag", etag)
                handler.send_header("Cache-Control", cache_control)
                _security_headers(handler)
                handler.end_headers()
                return True

        byte_range = _parse_range_header(handler.headers.get("Range", ""), file_size)
        if handler.headers.get("Range") and byte_range is None:
            handler.send_response(416)
            handler.send_header("Content-Range", f"bytes */{file_size}")
            handler.send_header("Accept-Ranges", "bytes")
            handler.send_header("Content-Length", "0")
            _security_headers(handler)
            handler.end_headers()
            return True

        start, end = byte_range if byte_range else (0, max(0, file_size - 1))
        content_length = end - start + 1 if file_size else 0
        handler.send_response(206 if byte_range else 200)
        handler.send_header("Content-Type", mime)
        handler.send_header("Content-Length", str(content_length))
        handler.send_header("Accept-Ranges", "bytes")
        if etag is not None:
            handler.send_header("ETag", etag)
        if byte_range:
            handler.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
        handler.send_header("Cache-Control", cache_control)
        handler.send_header("Content-Disposition", _content_disposition_value(disposition, download_name or target.name))
        if csp:
            # Sandboxed inline HTML must remain frameable for workspace previews;
            # X-Frame-Options: DENY would block the iframe before CSP sandbox applies.
            handler.send_header("Content-Security-Policy", csp)
            handler.send_header("X-Content-Type-Options", "nosniff")
            handler.send_header("Referrer-Policy", "same-origin")
            handler.send_header(
                "Permissions-Policy",
                "camera=(), microphone=(self), geolocation=(), clipboard-write=(self)",
            )
        else:
            _security_headers(handler)
        handler.end_headers()

        # Body transmission: the response is committed once end_headers()
        # returns, so attempting bad()/send_response again here would corrupt
        # the stream with a second status line. Client disconnects are normal
        # (tab close, network switch) — log at debug and stop, same contract
        # as _safe_write(). Never emit a 500 after headers are out.
        if content_length:
            try:
                if snapshot is not None:
                    handler.wfile.write(snapshot[start:start + content_length])
                else:
                    with os.fdopen(fd, "rb", closefd=True) as f:
                        fd = None
                        f.seek(start)
                        remaining = content_length
                        while remaining:
                            chunk = f.read(min(1024 * 1024, remaining))
                            if not chunk:
                                break
                            handler.wfile.write(chunk)
                            remaining -= len(chunk)
            except _CLIENT_DISCONNECT_ERRORS as exc:
                logging.getLogger("hermes.webui").debug(
                    "Client disconnected mid-response (%s): %s",
                    type(exc).__name__,
                    getattr(handler, "path", "?"),
                )
            except Exception as exc:
                # Post-commit fail-closed: the status line is already on the
                # wire, so ANY body-transmission error that is not a client
                # disconnect (EIO from a truncated read, a generic OSError,
                # PermissionError, ...) must still be contained here. Letting
                # it escape would reach Handler.do_GET's 500 path and emit a
                # SECOND status line after the committed 200/206, corrupting
                # the HTTP stream. Log at debug and stop — the client already
                # received its headers (and possibly a partial body).
                logging.getLogger("hermes.webui").debug(
                    "Body transmission error after commit (%s): %s",
                    type(exc).__name__,
                    getattr(handler, "path", "?"),
                )
        return True
    finally:
        _close_fd_quietly(fd)



def _handle_tts(handler, parsed):
    bad(handler, "TTS audio generation is disabled in Antigravity mode.", status=501)
    return True


def _html_preview_with_blank_base(raw: bytes) -> bytes:
    base = '<base target="_blank">'
    text = raw.decode("utf-8", errors="replace")
    if re.search(r"<head(?:\s[^>]*)?>", text, flags=re.IGNORECASE):
        text = re.sub(r"(<head\b[^>]*>)", r"\1" + base, text, count=1, flags=re.IGNORECASE)
    elif re.search(r"<!doctype[^>]*>", text, flags=re.IGNORECASE):
        text = re.sub(
            r"(<!doctype[^>]*>)",
            r"\1<head>" + base + "</head>",
            text,
            count=1,
            flags=re.IGNORECASE,
        )
    else:
        text = "<head>" + base + "</head>" + text
    return text.encode("utf-8")


def _serve_inline_html_preview(handler, target: Path, cache_control: str, *, csp: str, anchor_root: Path | None = None):
    """Serve sandboxed workspace HTML preview with links targeting a new tab."""
    fd = None
    try:
        fd = _open_file_read_fd(target, anchor_root)
        with os.fdopen(fd, "rb", closefd=True) as f:
            fd = None
            body = _html_preview_with_blank_base(f.read())
    except PermissionError:
        return bad(handler, "Permission denied", 403)
    except FileNotFoundError:
        return j(handler, {"error": "not found"}, status=404)
    except ValueError as e:
        return bad(handler, _sanitize_error(e), 403)
    except Exception:
        logger.debug("Silent exception in _serve_inline_html_preview", exc_info=True)
        return bad(handler, "Could not read file", 500)
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass

    handler.send_response(200)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Accept-Ranges", "none")
    handler.send_header("Cache-Control", cache_control)
    handler.send_header("Content-Disposition", _content_disposition_value("inline", target.name))
    handler.send_header("Content-Security-Policy", csp)
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("Referrer-Policy", "same-origin")
    handler.send_header(
        "Permissions-Policy",
        "camera=(), microphone=(self), geolocation=(), clipboard-write=(self)",
    )
    handler.end_headers()
    handler.wfile.write(body)
    return True


_MEDIA_TOKEN_RE = re.compile(r"MEDIA:([^\s\)\]]+)")


def _message_content_text(content) -> str:
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict):
                parts.append(str(part.get("text") or ""))
            else:
                parts.append(str(part or ""))
        return "\n".join(parts)
    return str(content or "")


def _session_media_token_allows_path(sid: str, target: Path, allowed_mimes: set[str]) -> bool:
    """Allow exact safe MEDIA: paths already present in the requested session."""
    sid = str(sid or "").strip()
    if not sid:
        return False
    mime = MIME_MAP.get(target.suffix.lower(), "application/octet-stream")
    if mime not in allowed_mimes:
        return False
    try:
        target_resolved = target.resolve()
    except Exception:
        logger.debug("Silent exception in _session_media_token_allows_path", exc_info=True)
        return False
    try:
        session = get_session(sid)
    except Exception:
        logger.debug("Silent exception in _session_media_token_allows_path", exc_info=True)
        return False

    for message in getattr(session, "messages", []) or []:
        if not isinstance(message, dict):
            continue
        # Only honor MEDIA: tokens that the assistant/tool emitted. User-authored
        # content cannot mint allow-list entries even if it contains a MEDIA:
        # token — keeps the implicit threat model (assistant-emitted artifacts
        # only) explicit.
        role = str(message.get("role") or "").strip().lower()
        if role == "user":
            continue
        text = _message_content_text(message.get("content"))
        if "MEDIA:" not in text:
            continue
        for ref in _MEDIA_TOKEN_RE.findall(text):
            if "://" in ref:
                continue
            try:
                if Path(ref).expanduser().resolve() == target_resolved:
                    return True
            except Exception:
                logger.debug("Silent exception in _session_media_token_allows_path", exc_info=True)
                continue
    return False


def _session_media_token_allows_image_path(sid: str, target: Path, image_mimes: set[str]) -> bool:
    """Backward-compatible image-only wrapper for existing callers/tests."""
    return _session_media_token_allows_path(sid, target, image_mimes)


def _path_is_within_root(child: Path, root: Path) -> bool:
    """Return True when ``child`` is inside ``root`` without crashing on Windows drives."""
    try:
        return os.path.commonpath([str(child), str(root)]) == str(root)
    except ValueError:
        return False


def _media_deny_reason(target: Path) -> str | None:
    """Return a reason string when ``target`` must be hard-denied, else None.

    The ``/api/media`` #3234 state/profile deny model, extracted so the
    snapshot CAPTURE side (api/media_snapshots.media_capture_allowed) shares
    the EXACT same predicate — anything the serve path refuses is never
    captured in the first place (#6979 Round 2 MUST-FIX 1 deny parity).

    Model: the ACTIVE WORKSPACE is a legitimate-media carve-out — the user is
    entitled to their own workspace files (that is also how the workspace file
    browser reaches them), even when a workspace happens to live under a
    Hermes root. The deny rules target Hermes's OWN internal state, which lives
    OUTSIDE any workspace. So: if the target is inside the active workspace, it
    is never denied here; otherwise we deny known secret/config basenames and
    the internal state subdirectories across every Hermes root the allowlist
    accepts (active-profile HERMES_HOME, base ~/.hermes, the api.profiles
    default home, and STATE_DIR — which also defends sibling profiles).
    """
    import os as _os

    _HOME = Path(_os.path.expanduser("~"))
    _HERMES_HOME = Path(_os.getenv("HERMES_HOME", str(_HOME / ".hermes"))).expanduser()

    _DENY_FILENAMES = {
        "settings.json", "state.db", "state.db-wal", "state.db-shm",
        "auth.json", "auth.lock", "config.yaml", "config.yml", ".env",
        ".signing_key", ".pbkdf2_key", ".sessions.json",
        "google_token.json", "google_client_secret.json",
        "gateway_state.json", "channel_directory.json", "jobs.json",
        "passkeys.json", ".passkey_challenges.json", ".login_attempts.json",
    }
    # Internal state subdirs that are sensitive in their entirety. NOTE:
    # `profiles` is intentionally NOT here — it is a container of profile roots,
    # each of which has its own legitimate workspace/. We instead enumerate each
    # named-profile root below and deny ITS state subdirs, so a sibling profile's
    # secrets are blocked without 403-ing a named-profile workspace. (#3234.)
    _DENY_SUBDIRS = (
        "sessions", "memories", "cron", "logs",
        "checkpoints", "backups",
        # Content-addressed media snapshots (api/media_snapshots.py) are an
        # internal store: digest bytes are only reachable through the validated
        # `snap=` parameter, never as a bare `path=` request. (#media-snapshots)
        "media_snapshots",
    )
    _state_dir = None
    try:
        from api.config import STATE_DIR as _STATE_DIR
        _state_dir = Path(_STATE_DIR).resolve()
    except Exception:
        logger.debug("Silent exception in _media_deny_reason", exc_info=True)
        _state_dir = None
    _base_hermes_home = None
    try:
        from api.profiles import _DEFAULT_HERMES_HOME as _BASE_HH
        _base_hermes_home = Path(_BASE_HH).resolve()
    except Exception:
        logger.debug("Silent exception in _media_deny_reason", exc_info=True)
        _base_hermes_home = None
    _hermes_roots = []
    for _r in (
        _HERMES_HOME.resolve(),
        (_HOME / ".hermes").resolve(),
        _base_hermes_home,
        _state_dir,
    ):
        if _r is not None and _r not in _hermes_roots:
            _hermes_roots.append(_r)
    # Enumerate named-profile roots (<root>/profiles/<name>) and treat each as a
    # Hermes root in its own right, so a sibling/other profile's sensitive subdirs
    # + secret files are denied — WITHOUT denying the whole `profiles` container
    # (which would block a legit named-profile workspace at
    # <root>/profiles/<name>/workspace/). (Codex review #3234.)
    _profile_roots = []
    for _root in list(_hermes_roots):
        _profiles_dir = (_root / "profiles")
        try:
            if _profiles_dir.is_dir():
                for _pchild in _profiles_dir.iterdir():
                    if _pchild.is_dir():
                        _pr = _pchild.resolve()
                        if _pr not in _hermes_roots and _pr not in _profile_roots:
                            _profile_roots.append(_pr)
        except OSError:
            pass
    _hermes_roots.extend(_profile_roots)

    # Case-insensitive path helpers so STATE.DB / Sessions/ casing variants
    # cannot bypass the deny on macOS/Windows filesystems (Codex review #3234).
    def _norm(p):
        return os.path.normcase(str(Path(p).resolve())).casefold()
    def _within_ci(child, root):
        try:
            c, r = _norm(child), _norm(root)
            return os.path.commonpath([c, r]) == r
        except (ValueError, OSError):
            return False
    def _equal_ci(a, b):
        try:
            return _norm(a) == _norm(b)
        except (ValueError, OSError):
            return False

    # State-subdir deny set: each DENY_SUBDIR directly under any Hermes root
    # (which includes STATE_DIR — so STATE_DIR/sessions, STATE_DIR/memories,
    # etc. are covered). These ALWAYS apply — even to a file under the active
    # workspace — so a workspace pointed at (or overlapping) a state dir cannot
    # expose sessions/memories/profiles/etc. We do NOT deny STATE_DIR itself
    # wholesale: the default workspace lives at STATE_DIR/workspace, and that is
    # legitimate user media — direct sensitive files there are still caught by
    # the filename denies below. (Codex review #3234.)
    _deny_dirs = []
    for _root in _hermes_roots:
        for _sub in _DENY_SUBDIRS:
            _deny_dirs.append((_root / _sub).resolve())
        # Per-profile WebUI state lives at <root>/webui_state (api/workspace.py),
        # so its state subdirs (<root>/webui_state/sessions, etc.) must be denied
        # too — they are NOT direct children of <root>. (Codex review #3234.)
        _ws_state = (_root / "webui_state")
        for _sub in _DENY_SUBDIRS:
            _deny_dirs.append((_ws_state / _sub).resolve())
    # The configured media-snapshot store root itself: blobs are internal and
    # only reachable through the validated `snap=` parameter on an authorized
    # path, so a bare `path=` request at or below the store is rejected
    # REGARDLESS of the store's configured name/location
    # (HERMES_WEBUI_MEDIA_SNAPSHOT_DIR may point anywhere, e.g. /tmp/custom-name;
    # the literal "media_snapshots" entry above only covers the default layout).
    # (#6979 Round 2 MUST-FIX 2.)
    try:
        from api.media_snapshots import get_snapshot_dir
        _snap_store = get_snapshot_dir().resolve()
    except Exception:
        logger.debug("Silent exception in _media_deny_reason", exc_info=True)
        _snap_store = None
    if _snap_store is not None and _within_ci(target, _snap_store):
        return "media snapshot store is internal"
    _deny_names_ci = {n.casefold() for n in _DENY_FILENAMES}

    # Active-workspace carve-out: a file inside a genuine PROJECT workspace is
    # the user's own content, so the secret/config FILENAME denies are relaxed
    # for it. The carve-out is DISABLED when the workspace is a broad/internal
    # location ($HOME, a Hermes root itself, an ANCESTOR of a Hermes root, a
    # */profiles dir, a named-profile root, or a state subdir) — honoring those
    # would re-open the disclosure. A workspace that is a proper DESCENDANT of a
    # Hermes root (e.g. STATE_DIR/workspace) is still a legit project workspace
    # and keeps the carve-out. The dir-based denies above are NOT relaxed.
    _active_workspace = None
    try:
        from api.workspace import get_last_workspace
        _aw = Path(get_last_workspace()).resolve()
        if _aw.is_dir():
            _active_workspace = _aw
    except Exception:
        logger.debug("Silent exception in _media_deny_reason", exc_info=True)
        _active_workspace = None

    def _workspace_is_safe_carveout(ws):
        if ws is None:
            return False
        if _equal_ci(ws, _HOME):
            return False
        for _root in _hermes_roots:
            # ws IS a root, or ws is an ANCESTOR of a root → unsafe. (A proper
            # descendant of a root is fine — that's a normal project workspace.)
            if _equal_ci(ws, _root) or _within_ci(_root, ws):
                return False
        if ws.name == "profiles" or ws.parent.name == "profiles":
            return False
        if ws.name in _DENY_SUBDIRS:
            return False
        return True

    _in_active_workspace = (
        _active_workspace is not None
        and _workspace_is_safe_carveout(_active_workspace)
        and _within_ci(target, _active_workspace)
    )

    # Dir-based denies always fire (even inside the active workspace).
    if any(_within_ci(target, d) for d in _deny_dirs):
        return "denied state subdir"
    # Filename-based denies fire for files under a Hermes root, UNLESS the file
    # is inside a genuine project workspace (carve-out).
    if not _in_active_workspace:
        _under_hermes_root = any(_within_ci(target, _root) for _root in _hermes_roots)
        _name_cf = target.name.casefold()
        # Exact secret/state basenames, plus atomic-write temp files for those
        # (api/auth.py and api/passkeys.py write via a `tmp*.<name>.tmp` / `tmp*.tmp`
        # sidecar then rename) — deny those suffixes too so a momentary temp file
        # cannot be fetched. (Codex review #3234.)
        _deny_tmp_suffixes = (".sessions.tmp", ".login_attempts.tmp",
                              ".passkeys.tmp", ".passkey_challenges.tmp")
        if _under_hermes_root and (
            _name_cf in _deny_names_ci
            or _name_cf.endswith(_deny_tmp_suffixes)
        ):
            return "denied state filename"
    return None


def _handle_media(handler, parsed):
    """Serve a local file by absolute path for inline display in the chat.

    Security:
    - Path must resolve to an allowed root (hermes home, /tmp, common dirs)
    - Auth-gated when auth is enabled
    - Safe preview MIME types can render inline when requested; SVG always downloads
    - SVG always served as attachment (XSS risk)
    - No path traversal: resolved path must stay within an allowed root
    - Additional roots can be added via MEDIA_ALLOWED_ROOTS env var
      (os.pathsep-separated list of absolute paths; ":" on POSIX, ";" on Windows)
    """
    import os as _os
    from api.auth import is_auth_enabled, parse_cookie, verify_session
    _HOME = Path(_os.path.expanduser("~"))
    _HERMES_HOME = Path(_os.getenv("HERMES_HOME", str(_HOME / ".hermes"))).expanduser()

    # Auth check
    if is_auth_enabled():
        cv = parse_cookie(handler)
        if not (cv and verify_session(cv)):
            body = b'{"error":"Authentication required"}'
            handler.send_response(401)
            handler.send_header("Content-Type", "application/json")
            handler.send_header("Content-Length", str(len(body)))
            handler.end_headers()
            handler.wfile.write(body)
            return

    qs = parse_qs(parsed.query)
    raw_path = qs.get("path", [""])[0].strip()
    if not raw_path:
        return bad(handler, "path parameter required", 400)

    # Resolve the path and check it is within an allowed root
    try:
        target = Path(raw_path).resolve()
    except Exception:
        logger.warning("Silent exception in _handle_media", exc_info=True)
        return bad(handler, "Invalid path", 400)

    # Allowed roots: hermes home, /tmp, and active workspace.
    # Intentionally NOT the entire home dir — that would expose ~/.ssh,
    # ~/.aws, browser profiles, etc. to any authenticated user.
    allowed_roots = [
        _HERMES_HOME.resolve(),
        Path("/tmp").resolve(),
        (_HOME / ".hermes").resolve(),
    ]
    # Also allow the active workspace directory (where screenshots land)
    try:
        from api.workspace import get_last_workspace
        ws = Path(get_last_workspace()).resolve()
        if ws.is_dir():
            allowed_roots.append(ws)
    except Exception:
        logger.warning("Silent exception in _handle_media", exc_info=True)
        pass

    # Also allow additional roots from MEDIA_ALLOWED_ROOTS env var
    # (os.pathsep-separated list; ":" on POSIX, ";" on Windows).
    extra_roots = _os.environ.get("MEDIA_ALLOWED_ROOTS", "").strip()
    if extra_roots:
        for root in extra_roots.split(_os.pathsep):
            root = root.strip()
            if root:
                try:
                    rp = Path(root).resolve()
                    if rp.is_dir():
                        allowed_roots.append(rp)
                except Exception:
                    logger.warning("Silent exception in _handle_media", exc_info=True)
                    pass

    _INLINE_IMAGE_TYPES = {
        "image/png", "image/jpeg", "image/gif", "image/webp",
        "image/x-icon", "image/bmp",
    }
    within_allowed = any(
        _path_is_within_root(target, root)
        for root in allowed_roots
        if root.exists()
    )
    _AUDIO_VIDEO_PDF_TYPES = {
        "audio/mpeg", "audio/wav", "audio/x-wav", "audio/mp4", "audio/aac",
        "audio/ogg", "audio/opus", "audio/flac",
        "video/mp4", "video/quicktime", "video/webm", "video/ogg",
        "application/pdf",
    }
    _SESSION_MEDIA_TOKEN_TYPES = _INLINE_IMAGE_TYPES | _AUDIO_VIDEO_PDF_TYPES | {"text/html"}
    session_media_allowed = _session_media_token_allows_path(
        qs.get("session_id", [""])[0],
        target,
        _SESSION_MEDIA_TOKEN_TYPES,
    )

    # ── #3234: hard-deny Hermes's own state + secret/config files ────────────
    # The allowlist above grants the whole Hermes home (and base ~/.hermes), so
    # an authenticated session rendering attacker-influenced agent output that
    # emits a file:// / MEDIA: link to a state/secret file could fetch it
    # through /api/media. This guard runs BEFORE the allow/serve decision so it
    # covers every entry path (bare file:// URLs, markdown anchors, MEDIA:
    # tokens, and session-token grants).
    #
    # The predicate is SHARED with snapshot capture
    # (api/media_snapshots.media_capture_allowed) so capture and serve can
    # never diverge on what is denied — anything denied here is never
    # snapshotted in the first place. (#6979 Round 2 MUST-FIX 1.)
    deny_reason = _media_deny_reason(target)
    if deny_reason:
        return bad(handler, "Path not in allowed location", 403)
    # ── end #3234 deny ───────────────────────────────────────────────────────

    if not within_allowed and not session_media_allowed:
        return bad(handler, "Path not in allowed location", 403)

    # Determine MIME type from the requested path's extension. Computed BEFORE
    # the existence check because the requested file may have been overwritten
    # or deleted while its message-level snapshot still exists below.
    ext = target.suffix.lower()
    mime = MIME_MAP.get(ext, "application/octet-stream")

    # Only serve safe media/PDF types inline when explicitly requested. HTML is
    # allowed inline only with a CSP sandbox so "open full page" can work without
    # granting same-origin access to the WebUI. SVG is always a download (XSS risk).
    _INLINE_PREVIEW_TYPES = _INLINE_IMAGE_TYPES | _AUDIO_VIDEO_PDF_TYPES
    _DOWNLOAD_TYPES = {"image/svg+xml"}  # SVG: XSS risk, force download
    inline_preview = qs.get("inline", [""])[0] == "1"
    html_inline_ok = inline_preview and mime == "text/html"
    disposition = "inline" if (
        mime not in _DOWNLOAD_TYPES and (
            mime in _INLINE_IMAGE_TYPES or (inline_preview and mime in _INLINE_PREVIEW_TYPES)
            or html_inline_ok
        )
    ) else "attachment"
    # _serve_file_bytes sends Content-Security-Policy when csp is set.
    csp = "sandbox allow-scripts" if html_inline_ok else None

    # ── Message-level snapshot serving (?snap=<sha256>) ─────────────────────
    # Historical chat previews carry a content-addressed snapshot digest of the
    # file as it existed when the message settled (see api/media_snapshots.py).
    # Serving the frozen bytes instead of the live file means an in-place
    # overwrite (same filename) no longer rewrites old previews — the user can
    # still compare old vs new. The allow/deny checks above still gate the
    # request: `snap` only selects WHICH bytes to serve for an already-
    # authorized path; it never grants access to a path that would be denied
    # without it. A missing/evicted snapshot falls back to the live file.
    snap_digest = qs.get("snap", [""])[0].strip().lower()
    snapshot_file = None
    snap_dir = None
    if snap_digest:
        from api.media_snapshots import (
            get_snapshot_dir,
            is_valid_digest,
            snapshot_path_for_digest,
            snapshot_servable_for_path,
        )

        snap_dir = get_snapshot_dir().resolve()
        if is_valid_digest(snap_digest):
            snapshot_file = snapshot_path_for_digest(snap_digest)
            # Server-owned source-path binding (#6979 Round 2 MUST-FIX 1): a
            # digest may only be served back for the EXACT canonical path it
            # was captured from. Replaying a digest through a different
            # (allowed) path must not leak the stored bytes — treat it as an
            # invalid snapshot and fall back to the live file (or 404 when the
            # live file is absent).
            if snapshot_file is not None and not snapshot_servable_for_path(snap_digest, target):
                snapshot_file = None
    if snapshot_file is not None:
        # Content-addressed and immutable: the digest IS the SHA-256 of the
        # exact bytes, so the browser may cache forever and never revalidate.
        # The blob is opened ANCHORED inside the store root (no-follow), and
        # the store root itself is deny-listed from bare path= fetches above
        # (#6979 Round 2 MUST-FIX 2).
        return _serve_file_bytes(
            handler,
            snapshot_file,
            mime,
            disposition,
            "private, max-age=31536000, immutable",
            csp=csp,
            download_name=target.name,
            anchor_root=snap_dir,
        )

    if not target.exists() or not target.is_file():
        return j(handler, {"error": "not found"}, status=404)

    # HTML inline previews change frequently (agent edits + re-renders).
    # Use no-store so the browser always fetches fresh content, avoiding stale
    # previews that require a manual full-page refresh to update.
    # All other media (images, audio, video, PDF) use private, no-cache + ETag
    # revalidation (see _serve_file_bytes): the browser may cache, but must
    # revalidate on every use, so a file replaced in place (same name) is
    # picked up immediately while unchanged files still short-circuit with 304.
    # The `private` directive keeps per-user/per-session media out of shared
    # intermediary caches.
    if mime == "text/html":
        cache_control = "no-store"
    else:
        cache_control = "private, no-cache"
    return _serve_file_bytes(handler, target, mime, disposition, cache_control, csp=csp)


from api.route_workspace_git import (  # noqa: F401 — re-exports for backward compat
    _file_raw_target,
    _folder_download_collect,
    _folder_zip_max_bytes,
    _folder_zip_max_files,
    _handle_file_raw,
    _handle_file_read,
    _handle_folder_download,
    _read_anchored_file_bytes,
)


from api.route_approvals import (
    _handle_approval_inject,
    _handle_approval_pending,
    _handle_approval_sse_stream,
    _handle_clarify_pending,
    _handle_clarify_sse_stream,
)


def _handle_session_sse_stream(handler, parsed):
    """SSE endpoint for the persistent per-session channel (Option X).

    Subscribes to ``api.background_process.SESSION_CHANNELS[sid]`` — a channel
    that lives across agent turns (unlike STREAMS, which is torn down at
    end-of-turn). Used to deliver ``bg_task_complete`` events that fire while
    no agent turn is active.

    Lifecycle: opened by the frontend at session mount, closed at unmount or
    on tab close. Multiple tabs share one SessionChannel (refcounted via
    subscribe/unsubscribe). 30s SSE keepalive comments keep the proxy alive.
    Reaper-driven idle TTL (default 4h) prevents zombie channels.
    """
    sid = parse_qs(parsed.query).get("session_id", [""])[0]
    if not sid:
        return bad(handler, "session_id is required")

    # The (re)subscribing tab reports its last-known message_count via
    # ?known_count=N so the on-subscribe self-heal can detect a server-initiated
    # turn that started AND finished entirely inside this tab's SSE gap (see the
    # "server-initiated turn finished during the gap" self-heal block below).
    # Absent/blank/non-numeric => None ("tab didn't report", never triggers).
    _known_count_raw = parse_qs(parsed.query).get("known_count", [""])[0]
    try:
        subscriber_known_count = int(_known_count_raw) if _known_count_raw != "" else None
    except (TypeError, ValueError):
        subscriber_known_count = None

    from api.background_process import (
        subscribe_to_session_channel,
        active_stream_id_for_session,
        persisted_message_count_for_session,
        should_emit_session_updated,
    )

    # Atomic get-or-create + subscribe under SESSION_CHANNELS_LOCK. Doing these
    # two steps separately (get_or_create_session_channel then ch.subscribe)
    # left a TOCTOU gap where the reaper — which also holds
    # SESSION_CHANNELS_LOCK and collects idle 0-subscriber channels in one
    # critical section — could collect the channel between the two calls,
    # orphaning this subscriber on a channel no longer in SESSION_CHANNELS.
    # bg_task_complete emits would then never reach this queue. See
    # subscribe_to_session_channel for the full rationale (PR #2971 Greptile P1).
    ch, q = subscribe_to_session_channel(sid, maxsize=64)

    # NOTE: ``subscribe_to_session_channel`` above acquires a subscriber slot
    # that MUST be released on every exit path. Header setup
    # (``send_response`` / ``send_header`` / ``end_headers`` /
    # ``_sse_set_write_deadline``) and the initial-frame + on-subscribe
    # recovery writes below all touch the socket and can raise a member of
    # ``_CLIENT_DISCONNECT_ERRORS`` (BrokenPipeError / ConnectionResetError) if
    # the client drops immediately after subscribing. If that happened outside
    # this try/finally the ``ch.unsubscribe(q)`` cleanup would be skipped,
    # permanently leaking a subscriber. Because
    # ``SessionChannel.reaper_should_collect()`` refuses to collect any channel
    # with ``sub_count > 0``, a single ghost subscriber blocks the reaper
    # forever and the channel zombies in SESSION_CHANNELS. So EVERYTHING from
    # the subscribe onward — header setup included — runs inside one
    # try/finally that unconditionally unsubscribes.
    try:
        handler.send_response(200)
        handler.send_header('Content-Type', 'text/event-stream; charset=utf-8')
        handler.send_header('Cache-Control', 'no-cache')
        handler.send_header('X-Accel-Buffering', 'no')
        # #3103: omit the Connection header — rely on the HTTP/1.1 keep-alive
        # default, matching the other long-lived SSE handlers (gateway/session
        # events) that fixed the reconnect-storm. An explicit value here is a
        # third, inconsistent approach (greptile flag).
        end_sse_headers(handler)
        _sse_set_write_deadline(handler)  # Defect A: slow tab can't pin this thread

        from api.streaming import _sse

        # Push an initial frame so the client has confirmation the channel is
        # live (mirrors approval/clarify which send an 'initial' frame). No
        # snapshot data is needed — this channel only carries forward-looking
        # events, not pending state.
        _sse(handler, 'initial', {"session_id": sid})

        # ── Open-tab live-view self-heal (root cause: lost server_turn_started) ──
        # The `server_turn_started` fan-out (routes.start_session_turn) is a
        # fire-and-forget SessionChannel.emit with NO replay buffer: it reaches
        # only the subscribers connected at the exact emit instant. A tab whose
        # per-session EventSource was momentarily absent at that instant — a
        # transient SSE drop, a reverse-proxy idle-timeout, or browser
        # connection-pool starvation (all common behind a corporate proxy) —
        # misses the frame permanently, so a SERVER-initiated wakeup turn never
        # renders live and the user must hard-refresh (the reported defect). The
        # server-side wakeup itself ran and persisted fine; only the live-view
        # was lost. On (re)subscribe, if the session has a live run RIGHT NOW,
        # replay a synthetic `server_turn_started` to THIS new subscriber so the
        # open tab attaches its existing chat-stream renderer (attachLiveStream)
        # and self-heals with no refresh. `recovered: True` lets the frontend
        # use the replay (reconnecting) attach so the renderer picks up the
        # in-progress stream from the run journal rather than expecting token 0.
        # Idempotent: the frontend dedupes by (session_id, stream_id) — if the
        # original frame WAS delivered this is a harmless no-op there.
        try:
            recover_stream_id = active_stream_id_for_session(sid)
            if recover_stream_id:
                pending_started_at = None
                try:
                    recover_session = get_session(sid, metadata_only=True)
                    pending_started_at = getattr(recover_session, "pending_started_at", None)
                except Exception:
                    logger.debug(
                        "session-stream recovery could not read pending_started_at for %s",
                        sid,
                        exc_info=True,
                    )
                _sse(handler, 'server_turn_started', {
                    "session_id": sid,
                    "stream_id": recover_stream_id,
                    "pending_started_at": pending_started_at,
                    "source": "subscribe_recovery",
                    "recovered": True,
                })
            else:
                # ── Server-initiated turn that FINISHED during the SSE gap ──
                # The block above only heals a turn that is live RIGHT NOW. But
                # a server-initiated turn (self-wake / cron / restart hook) can
                # start AND finish entirely inside the gap: the fire-and-forget
                # `server_turn_started` reached no subscriber, and by the time
                # this tab reconnects the run has already cleared from
                # ACTIVE_RUNS — so active_stream_id_for_session returns None and
                # nothing above replays. The turn IS persisted, but this tab's
                # transcript stays stale until a hard refresh (the reported
                # visible-tab defect). Detect it by comparing the persisted
                # message_count against what this (re)subscribing tab last knew
                # (?known_count). If the server is AHEAD, emit a lightweight
                # `session-updated` frame so the tab does an INCREMENTAL,
                # swap-in-place message sync (frontend reuses #5189's
                # keepStaleUntilLoaded loadSession path — NO clear+refetch, so
                # the #5177/#5189 blank-gap jump is not reintroduced). Carries
                # only counts (no transcript) to stay cheap. Skipped
                # entirely when the tab didn't report a count or the persisted
                # count is unknown (legacy sidecar) → never a spurious reload.
                if subscriber_known_count is not None:
                    persisted_count = persisted_message_count_for_session(sid)
                    if should_emit_session_updated(subscriber_known_count, persisted_count):
                        _sse(handler, 'session-updated', {
                            "session_id": sid,
                            "message_count": persisted_count,
                            "known_count": subscriber_known_count,
                            "source": "subscribe_recovery",
                        })
        except _CLIENT_DISCONNECT_ERRORS:
            # Client vanished mid-recovery — re-raise so the outer handler
            # treats it as a normal disconnect and the finally still cleans up.
            raise
        except Exception:
            logger.debug(
                "session-stream on-subscribe recovery failed for %s", sid,
                exc_info=True,
            )

        while True:
            try:
                payload = q.get(timeout=_SSE_HEARTBEAT_INTERVAL_SECONDS)
            except queue.Empty:
                handler.wfile.write(b': keepalive\n\n')
                handler.wfile.flush()
                continue
            if payload is None:
                break
            event_name, data = payload
            _sse(handler, event_name, data)
    except _CLIENT_DISCONNECT_ERRORS:
        pass  # client went away — normal for long-lived connections
    finally:
        ch.unsubscribe(q)


from api.route_approvals import _handle_clarify_inject


from api.route_config_models import _handle_live_models  # noqa: E402


from api.route_tools_mcp import (  # noqa: F401 — re-exports for backward compat
    _PROJECT_CONTEXT_CURSOR_RULES_GLOB,
    _PROJECT_CONTEXT_CWD_NAMES,
    _PROJECT_CONTEXT_HERMES_NAMES,
    _PROJECT_CONTEXT_MAX_BYTES,
    _cron_output_snippet,
    _cron_output_usage_metadata,
    _handle_cron_history,
    _handle_cron_output,
    _handle_cron_recent,
    _handle_cron_run_detail,
    _handle_cron_status,
    _handle_memory_read,
    _memory_project_context_workspace,
    _project_context_candidates,
    _project_context_git_root,
    _read_active_project_context,
    _strip_project_context_frontmatter,
)


# ── POST route helpers ────────────────────────────────────────────────────────


def _handle_sessions_cleanup(handler, body, zero_only=False):
    cleaned = 0
    phase1_removed_ids = set()

    # Phase 1: Clean orphan session files (existing behavior).
    for p in SESSION_DIR.glob("*.json"):
        if p.name.startswith("_"):
            continue
        try:
            s = Session.load(p.stem)
            if zero_only:
                should_delete = s and len(s.messages) == 0
            else:
                should_delete = s and s.title == "Untitled" and len(s.messages) == 0
            if should_delete:
                with LOCK:
                    SESSIONS.pop(p.stem, None)
                p.unlink(missing_ok=True)
                cleaned += 1
                phase1_removed_ids.add(p.stem)
        except Exception:
            logger.debug("Failed to clean up session file %s", p)

    phase1_touched = bool(cleaned)
    phase2_rewrote_index = False

    # Phase 2: Index-only ghost sweep (#5331).
    # Remove index entries that have no backing .json file and no
    # in-memory session.  These are orphaned rows left by a write path that
    # updated _index.json without writing the session sidecar.
    #
    # Title-agnostic (#5331): a session with no backing file has no real
    # data regardless of its title, so even a non-"Untitled" stub in the
    # index is a ghost.  A legitimate session always has a sidecar file.
    #
    # Holds _INDEX_WRITE_LOCK for the full read-modify-write cycle to
    # prevent races with concurrent Session.save() / prune_session_from_index().
    if SESSION_INDEX_FILE.exists():
        try:
            from api.models import _INDEX_WRITE_LOCK, _safe_replace

            with _INDEX_WRITE_LOCK:
                index_file_data = json.loads(
                    SESSION_INDEX_FILE.read_bytes()
                )
                if isinstance(index_file_data, list):
                    live_ids = {
                        p.stem
                        for p in SESSION_DIR.glob("*.json")
                        if not p.name.startswith("_")
                    }
                    with LOCK:
                        in_memory_ids = set(SESSIONS.keys())

                    survivors = []
                    for entry in index_file_data:
                        sid = entry.get("session_id")
                        if not sid or sid in live_ids or sid in in_memory_ids:
                            survivors.append(entry)
                            continue
                        # Phase 1 already removed the backing file for this
                        # sid, so the index entry is stale too.  Drop it
                        # from the index without double-counting.
                        if sid in phase1_removed_ids:
                            continue
                        # Index-only ghost — no backing file, not in memory.
                        cleaned += 1
                        # Ghost not added to survivors — removed from index.

                    if cleaned > 0 and len(survivors) < len(index_file_data):
                        _tmp = SESSION_INDEX_FILE.with_suffix(
                            f".tmp.{os.getpid()}.{threading.current_thread().ident}"
                        )
                        _payload = json.dumps(survivors, ensure_ascii=False, indent=2)
                        try:
                            with open(_tmp, "w", encoding="utf-8") as f:
                                f.write(_payload)
                                f.flush()
                                os.fsync(f.fileno())
                            _safe_replace(_tmp, SESSION_INDEX_FILE)
                            phase2_rewrote_index = True
                        except Exception:
                            logger.warning("Silent exception in _handle_sessions_cleanup", exc_info=True)
                            try:
                                _tmp.unlink(missing_ok=True)
                            except Exception:
                                logger.warning("Silent exception in _handle_sessions_cleanup", exc_info=True)
                                pass
                            raise
        except Exception:
            logger.debug(
                "Failed to clean up index-only session entries", exc_info=True
            )

    # Post-cleanup index invalidation.
    # When Phase 1 removed files and Phase 2 didn't already clean the
    # index, delete the index to force a fresh rebuild from disk on the
    # next sidebar poll.  When Phase 2 succeeded the index is already
    # correct, so keep it (avoids a wasteful rebuild).
    if phase1_touched and not phase2_rewrote_index and SESSION_INDEX_FILE.exists():
        SESSION_INDEX_FILE.unlink(missing_ok=True)

    return j(handler, {"ok": True, "cleaned": cleaned})


from api.route_chat import (  # noqa: F401 — re-exports for backward compat
    _RETAINED_CONTEXT_USER_UNSET,
    _active_run_stream_for_session,
    _active_stream_blocks_chat_start,
    _agent_runtime_barrier_response,
    _chat_start_response_from_run_start,
    _checkpoint_user_message_for_eager_session_save,
    _handle_background,
    _handle_bg_task_complete_ack,
    _handle_btw,
    _handle_chat_start,
    _handle_chat_sync,
    _handle_goal_command,
    _handle_session_compression_recovery_start,
    _is_default_or_empty_session_title,
    _is_hidden_empty_session,
    _is_silent_control_message,
    _normalize_chat_attachments,
    _prepare_chat_start_session_for_stream,
    _process_wakeup_provider_has_recovery_credential,
    _process_wakeup_revalidation_provider,
    _provisional_title_from_prompt,
    _refresh_process_wakeup_pause_credential_fingerprint,
    _resolve_chat_workspace_for_regeneration,
    _resolve_chat_workspace_with_recovery,
    _runtime_adapter_goal_action,
    _runtime_runner_client_factory,
    _start_chat_stream_for_session,
    _start_regeneration_stream_locked,
    _start_run,
    start_session_turn,
)

from api.route_tools_mcp import (  # noqa: F401 — re-exports for backward compat
    _handle_cron_create,
    _handle_cron_delete,
    _handle_cron_delivery_options,
    _handle_cron_pause,
    _handle_cron_resume,
    _handle_cron_run,
    _handle_cron_update,
    _selected_profile_snapshot_updates,
)


from api.route_workspace_git import (  # noqa: F401 — re-exports for backward compat
    _git_bad,
    _git_locked_by_active_stream,
    _git_paths_from_body,
    _git_reject_destructive_if_unsafe,
    _git_session,
    _git_session_and_workspace,
    _git_session_workspace,
    _handle_git_branches,
    _handle_git_checkout,
    _handle_git_commit,
    _handle_git_commit_message,
    _handle_git_commit_message_selected,
    _handle_git_commit_selected,
    _handle_git_diff,
    _handle_git_discard,
    _handle_git_remote_action,
    _handle_git_stage,
    _handle_git_stash_checkout,
    _handle_git_status,
    _handle_git_unstage,
    _llm_git_commit_message,
)


from api.route_workspace_git import (  # noqa: F401 — re-exports for backward compat
    _handle_create_dir,
    _handle_file_create,
    _handle_file_delete,
    _handle_file_move,
    _handle_file_open_vscode,
    _handle_file_path,
    _handle_file_rename,
    _handle_file_reveal,
    _handle_file_save,
    _handle_office_file_save,
    _handle_workspace_add,
    _handle_workspace_remove,
    _handle_workspace_rename,
    _handle_workspace_reorder,
)


from api.route_approvals import (
    _GATEWAY_APPROVAL_RELAY_IN_PROGRESS,
    _GATEWAY_APPROVAL_RELAY_UNAVAILABLE,
    _enable_session_yolo_and_release_pending,
    _gateway_approval_failure,
    _gateway_pending_approval_without_run_id,
    _handle_approval_respond,
    _handle_clarify_respond,
    _pending_approval_owner_state,
    _relay_gateway_run_approval,
    _resolve_approval_legacy,
    _resolve_clarify_legacy,
    _session_has_pending_approval,
)


from api.route_session import (  # noqa: F401 — re-exports for backward compat
    _ManualCompressionMemoryHandler,
    _build_handoff_summary_tool_message,
    _extract_handoff_summary_payload,
    _handle_conversation_rounds,
    _handle_handoff_summary,
    _handle_session_compress,
    _handle_session_compress_start,
    _handle_session_compress_status,
    _is_matching_handoff_summary_content,
    _is_matching_handoff_summary_message,
    _manual_compression_cleanup_locked,
    _manual_compression_status_payload,
    _persist_handoff_summary,
    _persist_handoff_summary_locally,
    _persist_handoff_summary_to_state_db,
    _run_manual_compression_job,
)


from api.route_tools_mcp import (  # noqa: F401 — re-exports for backward compat
    _handle_memory_write,
    _handle_skill_delete,
    _handle_skill_save,
    _handle_skill_toggle,
    _normalize_names_list,
    _toggle_name_in_list,
)


from api.route_session import (  # noqa: F401 — re-exports for backward compat
    _handle_session_import,
    _handle_session_import_cli,
    _handle_session_import_workspace,
    _is_cli_tool_metadata_enrichment,
    _is_messages_refresh_prefix_match,
    _message_has_cli_tool_metadata,
    _normalize_message_for_import_refresh,
    _strip_cli_tool_metadata_for_refresh,
)


from api.route_tools_mcp import (  # noqa: E402
    _MASKED_PLACEHOLDER,
    _NOTES_SOURCE_CONFIGURED_TOOL_HINTS,
    _NOTES_SOURCE_SERVER_HINTS,
    _NOTES_SOURCE_TOOL_HINTS,
    _configured_note_tool_hints,
    _external_notes_sources_enabled,
    _handle_artifact_content,
    _handle_artifacts_list,
    _handle_mcp_server_delete,
    _handle_mcp_server_toggle,
    _handle_mcp_server_update,
    _handle_mcp_servers_list,
    _handle_mcp_tools_list,
    _handle_subagent_transcript,
    _handle_subagents_list,
    _looks_like_notes_source,
    _mask_secrets,
    _mcp_runtime_status_by_name,
    _mcp_safe_display_text,
    _mcp_schema_summary,
    _mcp_schema_type,
    _mcp_tool_schema_from_payload,
    _mcp_tool_summary,
    _mcp_tools_from_registry,
    _mcp_tools_from_runtime_status,
    _note_source_label,
    _notes_sources_from_mcp_inventory,
    _parse_mcp_enabled,
    _server_summary,
    _strip_masked_values,
    _webui_truthy,
)


