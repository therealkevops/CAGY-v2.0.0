import copy
import inspect
import logging
import os
import re
import threading
import time
from collections import OrderedDict, defaultdict
from pathlib import Path

from api.agent_sessions import is_cli_session_row, is_cli_session_row_visible
from api.config import LOCK, SESSION_DIR, SESSIONS, SETTINGS_FILE, load_settings
from api.models import (
    Session,
    _active_state_db_path,
    _active_stream_ids,
    _clear_webui_zero_message_orphan_tombstone,
    _enrich_sidebar_lineage_metadata,
    _load_webui_zero_message_orphan_tombstone,
    _record_webui_zero_message_orphan_tombstone,
    agent_session_rows_existing,
    agent_session_zero_message_sids,
    all_sessions,
    get_cli_sessions,
    prune_session_from_index,
)
from api.profiles import _is_isolated_profile_mode, _profiles_match
from api.route_approvals import _session_attention_summary

logger = logging.getLogger(__name__)

CLI_VISIBLE_SESSION_CAP = 20


# Cron session ids are ``cron_{job_id}_{run_timestamp}`` where the run
# timestamp is ``YYYYMMDD_HHMMSS`` (e.g. cron_job6728_20260803_100000). Used to
# validate that the text after a matched ``cron_{jid}_`` prefix is EXACTLY a run
# timestamp, so a shorter job id (backup) cannot claim a longer job id's session
# (backup_full) when the longer job is not itself running (#6728 gate fix).
_CRON_RUN_TS_RE = re.compile(r"\d{8}_\d{6}")


_SESSIONS_CACHE_TTL_SECONDS = 2.5
# #4808: while a turn is actively streaming the frontend polls /api/sessions on a
# fixed cadence (static/sessions.js `_streamingPollMs`). With the idle TTL of
# 2.5s, the entry expires between streaming polls, so each poll can find it stale
# and force a full all_sessions() rebuild on the hot path under the global store
# LOCK — pinning CPU and starving token rendering on large stores (recurrence of
# #4672). Hold the sidebar cache steady for longer than one poll interval while
# streaming; live runtime state (active stream, sort order, pending flags) is
# overlaid on every response regardless of cache, and structural/settings changes
# still invalidate immediately. Keep this strictly greater than
# `_streamingPollMs`/1000 (see tests/test_streaming_cache_ttl_vs_poll.py).
_SESSIONS_CACHE_STREAMING_TTL_SECONDS = 45.0
_SESSIONS_CACHE_MAX_ENTRIES = 64
_SESSIONS_CACHE_WAIT_SECONDS = 0.25
_SESSIONS_CACHE_STALE_WAIT_SECONDS = 0.10
_SESSIONS_CACHE: OrderedDict[tuple, tuple[float, tuple, dict]] = OrderedDict()
_SESSIONS_CACHE_LOCK = threading.RLock()
_SESSIONS_CACHE_INFLIGHT: dict[tuple, threading.Event] = {}
_SESSIONS_CACHE_GLOBAL_INVALIDATION_VERSION = 0
_SESSIONS_CACHE_ALL_PROFILES_INVALIDATION_VERSION = 0
_SESSIONS_CACHE_PROFILE_INVALIDATION_VERSION: dict[str, int] = {}


def get_session_list_cache_snapshot() -> dict[str, object]:
    """Return scalar cache occupancy without waiting or changing LRU state.

    Held-section discipline: between the nonblocking acquire and the release,
    only ``len()`` and module-constant reads are permitted. Nothing that can
    resolve config, resolve a profile, touch the filesystem, import a module, or
    wait on another lock may be added here. ``_SESSIONS_CACHE_LOCK`` is an
    ``RLock``, so a nonblocking acquire from a thread already holding it would
    report available mid-mutation; the health collector is this helper's only
    caller and never runs nested inside a cache rebuild.
    """
    result = {
        "available": False,
        "entries": 0,
        "inflight_rebuilds": 0,
        "cap": 0,
    }
    acquired = False
    try:
        acquired = _SESSIONS_CACHE_LOCK.acquire(blocking=False)
        if not acquired:
            return result
        return {
            "available": True,
            "entries": max(0, int(len(_SESSIONS_CACHE))),
            "inflight_rebuilds": max(0, int(len(_SESSIONS_CACHE_INFLIGHT))),
            "cap": max(0, int(_SESSIONS_CACHE_MAX_ENTRIES)),
        }
    except Exception:
        return result
    finally:
        if acquired:
            _SESSIONS_CACHE_LOCK.release()


def _session_list_cache_session_dir() -> Path:
    try:
        import api.routes as _routes

        value = getattr(_routes, "SESSION_DIR", SESSION_DIR)
        return Path(value)
    except Exception:
        return SESSION_DIR


def _session_list_cache_settings_file() -> Path:
    try:
        import api.routes as _routes

        value = getattr(_routes, "SETTINGS_FILE", SETTINGS_FILE)
        return Path(value)
    except Exception:
        return SETTINGS_FILE


def _session_list_cache_state_db_path():
    try:
        import api.routes as _routes

        override = getattr(_routes, "_active_state_db_path", None)
        if callable(override) and override is not _session_list_cache_state_db_path:
            return override()
    except Exception:
        pass
    return _active_state_db_path()


def _session_list_cache_gateway_session_metadata_path() -> Path:
    try:
        import api.routes as _routes

        override = getattr(_routes, "_gateway_session_metadata_path", None)
        if callable(override) and override is not _session_list_cache_gateway_session_metadata_path:
            return Path(override())
    except Exception:
        pass

    try:
        from api.profiles import get_active_hermes_home

        hermes_home = Path(get_active_hermes_home()).expanduser().resolve()
    except Exception:
        hermes_home = Path(os.getenv("HERMES_HOME", str(Path.home() / ".hermes"))).expanduser().resolve()
    return hermes_home / "sessions" / "sessions.json"


def _session_list_cache_active_stream_ids():
    try:
        import api.routes as _routes

        override = getattr(_routes, "_active_stream_ids", None)
        if callable(override) and override is not _session_list_cache_active_stream_ids:
            return override()
    except Exception:
        pass
    return _active_stream_ids()


def _session_list_cache_running_cron_jobs() -> dict[str, float]:
    """Return {job_id: start_epoch} for cron jobs currently tracked as running.

    Cron liveness lives only in the in-memory ``_RUNNING_CRON_JOBS`` dict in
    api.routes (#6728): the sidebar polls /api/sessions (not /api/crons/status),
    so without this overlay a still-running cron job's session row looks
    completed the moment it appends a message. Fail closed to an empty dict.
    """
    try:
        import api.routes as _routes

        jobs = getattr(_routes, "_RUNNING_CRON_JOBS", None)
        lock = getattr(_routes, "_RUNNING_CRON_LOCK", None)
        if jobs is None or lock is None:
            return {}
        with lock:
            return dict(jobs)
    except Exception:
        return {}


def _session_list_cache_resolved_source_stamp(key: tuple):
    try:
        import api.routes as _routes

        override = getattr(_routes, "_session_list_cache_source_stamp", None)
        if callable(override) and override is not _session_list_cache_source_stamp:
            return override(key)
    except Exception:
        pass
    return _session_list_cache_source_stamp(key)


def _session_list_cache_profile_scope(profile: str | None) -> str:
    normalized = str(profile or "").strip() or "default"
    if _profiles_match(normalized, "default"):
        return "default"
    return normalized


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
    normalized_archived_limit = None
    if archived_limit is not None:
        try:
            normalized_archived_limit = max(0, int(archived_limit))
        except (TypeError, ValueError):
            normalized_archived_limit = None
    try:
        normalized_archived_offset = max(0, int(archived_offset or 0))
    except (TypeError, ValueError):
        normalized_archived_offset = 0
    return (
        _session_list_cache_profile_scope(active_profile),
        bool(all_profiles),
        bool(show_cli_sessions),
        bool(show_previous_messaging_sessions),
        bool(show_cron_sessions),
        bool(include_archived),
        bool(exclude_hidden),
        bool(visible_only),
        bool(show_webhook_sessions),
        bool(show_kanban_sessions),
        source_filter,
        sidebar_source,
        normalized_archived_limit,
        normalized_archived_offset,
        bool(show_claude_code_sessions),
    )


def _session_list_cache_get(
    key: tuple,
    allow_stale: bool = False,
) -> tuple[dict | None, bool]:
    now = time.monotonic()
    current_stamp = _session_list_cache_resolved_source_stamp(key)
    with _SESSIONS_CACHE_LOCK:
        entry = _SESSIONS_CACHE.get(key)
        if not entry:
            return None, False
        ts, stamp, payload = entry
        if stamp != current_stamp:
            if allow_stale:
                _SESSIONS_CACHE.move_to_end(key)
                return copy.deepcopy(payload), False
            _SESSIONS_CACHE.pop(key, None)
            return None, False
        # #4808: widen the freshness window while a turn is streaming so the fixed
        # streaming poll cadence doesn't force a full rebuild on every poll.
        ttl = _SESSIONS_CACHE_TTL_SECONDS
        if _session_list_cache_streaming_freeze_marker() is not None:
            ttl = _SESSIONS_CACHE_STREAMING_TTL_SECONDS
        fresh = (now - ts) < ttl
        if fresh:
            _SESSIONS_CACHE.move_to_end(key)
            return copy.deepcopy(payload), True
        if allow_stale:
            _SESSIONS_CACHE.move_to_end(key)
            return copy.deepcopy(payload), False
        _SESSIONS_CACHE.pop(key, None)
        return None, False


def _session_list_cache_stale_reason(key: tuple) -> str | None:
    """Return why an existing cache entry is stale, if it is stale."""
    now = time.monotonic()
    current_stamp = _session_list_cache_resolved_source_stamp(key)
    with _SESSIONS_CACHE_LOCK:
        entry = _SESSIONS_CACHE.get(key)
        if not entry:
            return None
        ts, stamp, _payload = entry
        if stamp != current_stamp:
            return "source"
        ttl = _SESSIONS_CACHE_TTL_SECONDS
        if _session_list_cache_streaming_freeze_marker() is not None:
            ttl = _SESSIONS_CACHE_STREAMING_TTL_SECONDS
        if (now - ts) >= ttl:
            return "age"
        return None


def _session_list_cache_set(key: tuple, payload: dict) -> None:
    if not isinstance(payload, dict):
        return
    stamp = _session_list_cache_resolved_source_stamp(key)
    with _SESSIONS_CACHE_LOCK:
        _SESSIONS_CACHE[key] = (time.monotonic(), stamp, copy.deepcopy(payload))
        _SESSIONS_CACHE.move_to_end(key)
        while len(_SESSIONS_CACHE) > _SESSIONS_CACHE_MAX_ENTRIES:
            _SESSIONS_CACHE.popitem(last=False)


def _session_list_cache_clear(profile: str | None = None) -> None:
    normalized_profile = _session_list_cache_profile_scope(profile) if profile else None
    with _SESSIONS_CACHE_LOCK:
        global _SESSIONS_CACHE_GLOBAL_INVALIDATION_VERSION
        global _SESSIONS_CACHE_ALL_PROFILES_INVALIDATION_VERSION
        if not profile:
            _SESSIONS_CACHE_GLOBAL_INVALIDATION_VERSION += 1
            _SESSIONS_CACHE_ALL_PROFILES_INVALIDATION_VERSION += 1
            _SESSIONS_CACHE_PROFILE_INVALIDATION_VERSION.clear()
            _SESSIONS_CACHE.clear()
            return
        _SESSIONS_CACHE_ALL_PROFILES_INVALIDATION_VERSION += 1
        _SESSIONS_CACHE_PROFILE_INVALIDATION_VERSION[normalized_profile] = (
            _SESSIONS_CACHE_PROFILE_INVALIDATION_VERSION.get(normalized_profile, 0) + 1
        )
        for cache_key in list(_SESSIONS_CACHE.keys()):
            cache_profile, cache_all_profiles, *_rest = cache_key
            if cache_all_profiles:
                _SESSIONS_CACHE.pop(cache_key, None)
                continue
            if _profiles_match(cache_profile, normalized_profile):
                _SESSIONS_CACHE.pop(cache_key, None)


def _clear_session_list_cache(profile: str | None = None) -> None:
    _session_list_cache_clear(profile=profile)


def _session_list_cache_invalidation_stamp(key: tuple) -> tuple[int, int]:
    cache_profile, cache_all_profiles, *_rest = key
    with _SESSIONS_CACHE_LOCK:
        global_version = _SESSIONS_CACHE_GLOBAL_INVALIDATION_VERSION
        if cache_all_profiles:
            return (
                global_version,
                _SESSIONS_CACHE_ALL_PROFILES_INVALIDATION_VERSION,
            )
        return (
            global_version,
            _SESSIONS_CACHE_PROFILE_INVALIDATION_VERSION.get(cache_profile, 0),
        )


def _session_list_cache_path_stamp(path: Path | None) -> tuple[int, int]:
    try:
        if path is None:
            return (0, 0)
        st = Path(path).stat()
        return (int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000))), int(st.st_size))
    except Exception:
        return (0, 0)


def _session_list_cache_streaming_freeze_marker():
    """Return a hold-down marker while any session is actively streaming, else None.

    During an active chat turn the gateway/CLI writes message rows to state.db
    continuously. Each write advances the WAL stat and the content fingerprint
    (``MAX(rowid)`` of ``messages``) that ``_session_list_cache_source_stamp``
    folds in, so the source stamp changes on essentially every ``/api/sessions``
    poll — popping the cache and forcing a full ``all_sessions()`` rebuild
    mid-stream. That rebuild then contends for the global ``LOCK`` the streaming
    worker holds while writing, which is what drags token output down to
    ~2 tok/s and produces the multi-second (and occasional ~15s) ``/api/sessions``
    latencies in issue #4672.

    The marker is keyed only on the *set* of active stream ids, not on any
    per-write state, so:
      * while the same turn(s) stream, the marker is constant → the cache holds
        steady and rebuilds are bounded to the TTL cadence (one per
        ``_SESSIONS_CACHE_TTL_SECONDS``) instead of one per poll;
      * the instant a stream starts or stops, the active set changes → the
        marker changes → the cache re-validates and the just-finished turn's
        final title/message_count is picked up immediately.

    Structural sidebar mutations (new/deleted/renamed/imported sessions,
    attention, cron completion) do NOT rely on this stamp — they invalidate the
    cache directly through the ``publish_session_list_changed`` listener — so the
    only thing that can lag under the hold-down is a streaming session's own
    title/message_count, which already tolerates a <=TTL refresh delay.
    """
    try:
        active = _session_list_cache_active_stream_ids()
    except Exception:
        return None
    if not active:
        return None
    try:
        return ("streaming", tuple(sorted(str(x) for x in active)))
    except Exception:
        return ("streaming",)


def _session_list_cache_state_db_fingerprint(state_db_path: Path | None):
    try:
        import api.routes as _routes

        override = getattr(_routes, "_session_list_cache_state_db_fingerprint", None)
        if callable(override) and override is not _session_list_cache_state_db_fingerprint:
            return override(state_db_path)
    except Exception:
        pass
    return _session_list_cache_state_db_fingerprint_impl(state_db_path)


def _session_list_cache_state_db_fingerprint_impl(state_db_path: Path | None):
    if state_db_path is None:
        return None
    try:
        from api.models import _sqlite_content_fingerprint

        return _sqlite_content_fingerprint(state_db_path)
    except Exception:
        return None


def _session_list_cache_source_stamp(key: tuple) -> tuple[tuple[int, int], tuple[int, int], tuple[int, int], tuple[int, int], tuple[int, int], object, int]:
    _cache_profile, _cache_all_profiles, _cache_show_cli_sessions, *_rest = key
    try:
        swv = _session_list_cache_settings_write_version()
    except Exception:
        swv = 0
    # WebUI-origin sessions can also receive settled rows in state.db when the
    # official Hermes Desktop App continues the same agent session.  The sidebar
    # therefore watches state.db even when the CLI/external-session tab is hidden.
    #
    # Streaming hold-down (#4672): while a turn is in flight, collapse the
    # volatile state.db-derived components (db/WAL stat, gateway metadata, index
    # stat, content fingerprint) to a marker that only changes when a stream
    # starts or stops. This stops per-token message writes from busting the
    # cache and triggering LOCK-contending rebuilds on every poll. The TTL still
    # forces a periodic rebuild so the streaming session's own count/title stay
    # fresh within the TTL window, and settings_file + the settings write
    # version stay live so user-initiated sidebar/setting toggles invalidate
    # immediately. Skipping the fingerprint's SQLite connect here also makes the
    # streaming-path stamp strictly cheaper than the idle path.
    streaming_marker = _session_list_cache_streaming_freeze_marker()
    if streaming_marker is not None:
        return (
            streaming_marker,
            streaming_marker,
            streaming_marker,
            streaming_marker,
            _session_list_cache_path_stamp(_session_list_cache_settings_file()),
            streaming_marker,
            swv,
        )
    try:
        state_db_path = Path(_session_list_cache_state_db_path())
    except Exception:
        state_db_path = None
    try:
        state_db_wal_path = state_db_path.with_name(f"{state_db_path.name}-wal") if state_db_path is not None else None
    except Exception:
        state_db_wal_path = None
    try:
        gateway_metadata_path = _session_list_cache_gateway_session_metadata_path()
    except Exception:
        gateway_metadata_path = None
    try:
        session_index_path = _session_list_cache_session_dir() / "_index.json"
    except Exception:
        session_index_path = None
    return (
        _session_list_cache_path_stamp(state_db_path),
        _session_list_cache_path_stamp(state_db_wal_path),
        _session_list_cache_path_stamp(gateway_metadata_path),
        _session_list_cache_path_stamp(session_index_path),
        _session_list_cache_path_stamp(_session_list_cache_settings_file()),
        # Commit-reliable content fingerprint of state.db — the file-stat stamps
        # above can collide under WAL-mode writes (same mtime_ns bucket + WAL
        # frame size), so without this a freshly-committed CLI/gateway session
        # could be served stale for the cache TTL. Mirrors the models-layer fix.
        _session_list_cache_state_db_fingerprint(state_db_path),
        swv,
    )


def _session_list_cache_settings_write_version() -> int:
    try:
        import api.routes as _routes

        override = getattr(_routes, "_session_list_cache_settings_write_version", None)
        if callable(override) and override is not _session_list_cache_settings_write_version:
            return int(override())
    except Exception:
        pass
    try:
        from api.config import _SETTINGS_WRITE_VERSION

        return int(_SETTINGS_WRITE_VERSION)
    except Exception:
        return 0


def _session_list_cache_overlay_runtime_rows(rows: list[dict]) -> list[dict]:
    if not rows:
        return []
    try:
        active_stream_ids = _session_list_cache_active_stream_ids()
    except Exception:
        active_stream_ids = set()
    try:
        running_cron_jobs = _session_list_cache_running_cron_jobs()
    except Exception:
        running_cron_jobs = {}
    cron_job_prefixes = [(jid, f"cron_{jid}_", started_at) for jid, started_at in running_cron_jobs.items()]
    session_ids = [
        str(row.get("session_id") or "").strip()
        for row in rows
        if isinstance(row, dict) and str(row.get("session_id") or "").strip()
    ]
    live_sessions = {}
    if session_ids:
        with LOCK:
            for sid in session_ids:
                live = SESSIONS.get(sid)
                if live is not None:
                    live_sessions[sid] = live
    overlaid = []
    for row in rows:
        item = dict(row) if isinstance(row, dict) else {}
        sid = str(item.get("session_id") or "").strip()
        live = live_sessions.get(sid)
        if live is not None:
            live_stream_id = getattr(live, "active_stream_id", None)
            item["active_stream_id"] = live_stream_id or None
            item["has_pending_user_message"] = bool(
                getattr(live, "pending_user_message", None)
            )
            for key in ("pending_started_at", "updated_at", "last_message_at"):
                current = _session_list_row_numeric_value(item.get(key))
                raw_live_value = getattr(live, key, None)
                live_value = _session_list_row_numeric_value(raw_live_value)
                if live_value > current:
                    item[key] = raw_live_value
        stream_id = item.get("active_stream_id")
        item["is_streaming"] = bool(stream_id and stream_id in active_stream_ids)
        # #6728: a still-running cron job's session row must not look completed
        # in the sidebar. Cron liveness is only exposed via /api/crons/status,
        # which the sidebar never polls — stamp the flag here so the client can
        # defer its completion/unread transition until the job actually ends.
        # Session ids are cron_{job_id}_{run_timestamp}: only the run started at
        # (or after) the tracked start belongs to the live execution — older runs
        # of the same job stay completed.
        item["cron_running"] = _session_list_row_cron_running(
            sid, item, cron_job_prefixes
        )
        overlaid.append(item)
    overlaid.sort(key=_session_list_runtime_sort_key, reverse=True)
    return overlaid


def _session_list_row_cron_running(
    sid: str, row: dict, cron_job_prefixes: list[tuple[str, str, float]]
) -> bool:
    if not cron_job_prefixes or not sid:
        return False
    created_at = _session_list_row_numeric_value(row.get("created_at"))
    # Longest prefix first, no fall-through: job ids may nest (backup vs
    # backup_full), and the shorter prefix is a valid prefix of the longer one.
    # A session belongs to the longest matching job id — first-match in
    # insertion order, or falling through to a shorter prefix after a time-miss,
    # would let a running shorter-prefix job claim a completed longer-prefix
    # session. Mirrors the max(matches, key=len) convention in
    # api.routes._latest_cron_session_info_for_jobs.
    #
    # #6728 (gate fix): prefixes are built ONLY from RUNNING jobs, so if the
    # true longer owner (backup_full) is not running, longest-prefix sorting
    # never sees it and a running `backup` would otherwise swallow a
    # `cron_backup_full_YYYYMMDD_HHMMSS` session (the leftover `full_...` still
    # starts with nothing it should match). Require the text AFTER the prefix to
    # be exactly a run-timestamp (YYYYMMDD_HHMMSS) so a shorter job id cannot
    # claim a longer job id's session regardless of which jobs are running.
    for _jid, prefix, started_at in sorted(
        cron_job_prefixes, key=lambda item: len(item[1]), reverse=True
    ):
        if sid.startswith(prefix) and _CRON_RUN_TS_RE.fullmatch(sid[len(prefix):]):
            return created_at >= started_at
    return False


def _session_list_row_numeric_value(value) -> float:
    try:
        numeric = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return numeric if numeric > 0 else 0.0


def _session_list_row_timestamp(row: dict) -> float:
    if not isinstance(row, dict):
        return 0.0
    # Match the frontend `_sessionSortTimestampMs` semantics exactly (#4688 review):
    # the idle base is the FIRST truthy of last_message_at -> updated_at -> created_at
    # (NOT a flat max over all of them — a renamed/metadata-touched idle chat bumps
    # updated_at without new messages and must not outrank a newer chatted session),
    # then pending_started_at is overlaid only as the runtime promotion.
    base = 0.0
    for key in ("last_message_at", "updated_at", "created_at"):
        base = _session_list_row_numeric_value(row.get(key))
        if base > 0:
            break
    pending = _session_list_row_numeric_value(row.get("pending_started_at"))
    return max(base, pending)


def _session_list_row_is_runtime_active(row: dict) -> bool:
    if not isinstance(row, dict):
        return False
    if row.get("is_streaming"):
        return True
    return bool(row.get("active_stream_id") and row.get("has_pending_user_message"))


def _session_list_runtime_sort_key(row: dict) -> tuple[int, float]:
    return (
        1 if _session_list_row_is_runtime_active(row) else 0,
        _session_list_row_timestamp(row),
    )


def _session_list_cache_claim_rebuild(key: tuple) -> tuple[threading.Event, bool]:
    with _SESSIONS_CACHE_LOCK:
        current = _SESSIONS_CACHE_INFLIGHT.get(key)
        if current is not None:
            return current, False
        event = threading.Event()
        _SESSIONS_CACHE_INFLIGHT[key] = event
        return event, True


def _session_list_cache_done(key: tuple, event: threading.Event | None) -> None:
    with _SESSIONS_CACHE_LOCK:
        if event is None:
            return
        if _SESSIONS_CACHE_INFLIGHT.get(key) is event:
            _SESSIONS_CACHE_INFLIGHT.pop(key, None)
    if event is not None:
        event.set()


def _callable_accepts_kwarg(callable_obj, kwarg_name: str) -> bool:
    if not callable(callable_obj):
        return False
    try:
        signature = inspect.signature(callable_obj)
    except (TypeError, ValueError):
        return False
    if kwarg_name in signature.parameters:
        return True
    return any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )


def _numeric_count(value) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _session_source_is_webui(session: dict) -> bool:
    """Return True for state.db/sidebar rows that describe WebUI-origin sessions."""
    if not isinstance(session, dict):
        return False
    for key in ("source_tag", "raw_source", "session_source", "source"):
        if str(session.get(key) or "").strip().lower() == "webui":
            return True
    return False


def _reconcile_stale_stream_state_for_session_rows(*args, **kwargs):
    from api import routes as _routes
    return _routes._reconcile_stale_stream_state_for_session_rows(*args, **kwargs)


def _normalize_sidebar_source_flags(*args, **kwargs):
    from api import routes as _routes
    return _routes._normalize_sidebar_source_flags(*args, **kwargs)


def _is_api_server_sidecar_row(*args, **kwargs):
    from api import routes as _routes
    return _routes._is_api_server_sidecar_row(*args, **kwargs)


def _is_messaging_session_record(*args, **kwargs):
    from api import routes as _routes
    return _routes._is_messaging_session_record(*args, **kwargs)


def _merge_cli_sidebar_metadata(*args, **kwargs):
    from api import routes as _routes
    return _routes._merge_cli_sidebar_metadata(*args, **kwargs)


def _session_lineage_ids(*args, **kwargs):
    from api import routes as _routes
    return _routes._session_lineage_ids(*args, **kwargs)


def _dedupe_cli_sidebar_sessions_for_api(*args, **kwargs):
    from api import routes as _routes
    return _routes._dedupe_cli_sidebar_sessions_for_api(*args, **kwargs)


def _is_cli_session_for_settings(*args, **kwargs):
    from api import routes as _routes
    return _routes._is_cli_session_for_settings(*args, **kwargs)


def _keep_latest_messaging_session_per_source(*args, **kwargs):
    from api import routes as _routes
    return _routes._keep_latest_messaging_session_per_source(*args, **kwargs)


def _cap_recent_cli_sessions(*args, **kwargs):
    from api import routes as _routes
    return _routes._cap_recent_cli_sessions(*args, **kwargs)


def _sidebar_session_response_item(*args, **kwargs):
    from api import routes as _routes
    return _routes._sidebar_session_response_item(*args, **kwargs)


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
        try:
            import api.routes as _routes
            _all_sessions = getattr(_routes, "all_sessions", all_sessions)
        except Exception:
            _all_sessions = all_sessions
        if _callable_accepts_kwarg(_all_sessions, "include_lineage_metadata"):
            return _all_sessions(diag=diag, include_lineage_metadata=False)
        # Focused tests and third-party callers sometimes monkeypatch
        # routes.all_sessions with the historical diag-only signature.
        return _all_sessions(diag=diag)

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
        try:
            import api.routes as _routes
            _get_cli_sessions = getattr(_routes, "get_cli_sessions", get_cli_sessions)
        except Exception:
            _get_cli_sessions = get_cli_sessions
        if _callable_accepts_kwarg(_get_cli_sessions, "include_claude_code"):
            cli = _get_cli_sessions(
                source_filter=source_filter,
                all_profiles=all_profiles,
                include_claude_code=show_claude_code_sessions,
            )
        else:
            # Focused tests sometimes monkeypatch routes.get_cli_sessions with
            # the historical two-keyword signature.
            cli = _get_cli_sessions(
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
        # has already cleared stale stream ids above this point.
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
