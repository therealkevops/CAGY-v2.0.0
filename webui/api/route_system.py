"""System telemetry, health probes, logs, and analytics routes.

Extracted from routes.py as part of routes decomposition (Sprint R2).
"""
import collections
import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from urllib.parse import parse_qs

from api import webui_session_db
from api.agent_health import build_agent_health_payload
from api.config import (
    SERVER_START_TIME,
    SESSION_DIR,
    SESSIONS,
    STREAMS,
    STREAMS_LOCK,
)
from api.gateway_chat import gateway_chat_config_status
from api.helpers import _sanitize_error, bad, j
from api.models import _active_state_db_path, all_sessions, load_projects
from api.system_health import build_system_health_payload

logger = logging.getLogger(__name__)

# ── Logs endpoint constants ───────────────────────────────────────────────────
_LOG_FILE_WHITELIST = {
    "agent": "agent.log",
    "errors": "errors.log",
    "gateway": "gateway.log",
}
_LOG_TAIL_VALUES = {100, 200, 500, 1000}
_LOG_DEFAULT_TAIL = 200
_LOG_MAX_BYTES = 4 * 1024 * 1024


def _normalize_logs_tail(raw_tail) -> int:
    try:
        tail = int(str(raw_tail or "").strip())
    except (TypeError, ValueError):
        return _LOG_DEFAULT_TAIL
    return tail if tail in _LOG_TAIL_VALUES else _LOG_DEFAULT_TAIL


def _handle_logs(handler, parsed) -> bool:
    """Return a bounded tail window for an active-profile Hermes log file."""
    query = parse_qs(parsed.query)
    file_key = (query.get("file", ["agent"])[0] or "agent").strip().lower()
    filename = _LOG_FILE_WHITELIST.get(file_key)
    if not filename:
        return bad(handler, "Unknown log file", status=400)

    tail = _normalize_logs_tail(query.get("tail", [None])[0])
    try:
        from api.profiles import get_active_agy_home

        hermes_home = Path(get_active_agy_home()).expanduser()
    except Exception:
        logger.warning("Silent exception in _handle_logs", exc_info=True)
        hermes_home = Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes")).expanduser()

    log_dir = hermes_home / "logs"
    log_path = log_dir / filename
    try:
        # Defense in depth: the filename is hardcoded above, but keep the final
        # path anchored under the active profile's logs directory.
        if log_path.resolve(strict=False).parent != log_dir.resolve(strict=False):
            return bad(handler, "Invalid log file", status=400)
        if not log_path.exists() or not log_path.is_file():
            return j(handler, {
                "file": file_key,
                "tail": tail,
                "lines": [],
                "truncated": False,
                "total_bytes": 0,
                "mtime": None,
                "hint": f"Log file for {file_key} not found yet.",
            })
        st = log_path.stat()
        total_bytes = int(st.st_size)
        read_bytes = min(total_bytes, _LOG_MAX_BYTES)
        with log_path.open("rb") as fh:
            if total_bytes > read_bytes:
                fh.seek(total_bytes - read_bytes)
            raw = fh.read(read_bytes)
        text = raw.decode("utf-8", errors="replace")
        lines = text.splitlines()[-tail:]
        return j(handler, {
            "file": file_key,
            "tail": tail,
            "lines": lines,
            "truncated": total_bytes > read_bytes,
            "total_bytes": total_bytes,
            "mtime": st.st_mtime,
            "hint": "",
        })
    except Exception as exc:
        logger.exception("Failed to read whitelisted log file %s", file_key)
        return bad(handler, _sanitize_error(exc), status=500)


def _handle_llm_wiki_status(handler, parsed) -> bool:
    j(handler, {"configured": False, "enabled": False})
    return True


def _handle_insights(handler, parsed) -> bool:
    """Return usage analytics from local WebUI session data."""
    import time as _time

    from api.usage import prompt_cache_hit_percent

    query = parse_qs(parsed.query)
    try:
        days = min(max(int(query.get("days", ["30"])[0]), 1), 365)
    except (ValueError, TypeError):
        days = 30

    now = _time.time()
    today = _time.localtime(now)
    today_midnight = _time.mktime((today.tm_year, today.tm_mon, today.tm_mday, 0, 0, 0, today.tm_wday, today.tm_yday, today.tm_isdst))
    day_secs = 86400
    first_day_ts = today_midnight - ((days - 1) * day_secs)
    cutoff = first_day_ts

    def _safe_usage_int(value) -> int:
        try:
            return max(int(float(value or 0)), 0)
        except (TypeError, ValueError):
            return 0

    def _safe_cost_float(value) -> float:
        if value is None:
            return 0.0
        try:
            if isinstance(value, str):
                value = value.strip().replace("$", "").replace(",", "")
                if not value:
                    return 0.0
            return max(float(value), 0.0)
        except (TypeError, ValueError):
            return 0.0

    def _session_usage_ts(session: dict) -> float:
        return session.get("updated_at", session.get("created_at", 0)) or session.get("created_at", 0) or 0

    # Walk session index (fast, no full JSON parse)
    sessions_data = []
    idx_path = SESSION_DIR / "_index.json"
    if idx_path.exists():
        try:
            idx = json.loads(idx_path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("Silent exception in _handle_insights", exc_info=True)
            idx = []
    else:
        idx = []

    for entry in idx:
        created = entry.get("created_at", 0) or 0
        updated = entry.get("updated_at", 0) or 0
        # Session is relevant if it was created or updated within the calendar window.
        if max(created, updated) < cutoff:
            continue
        sessions_data.append(entry)

    # Aggregate
    total_sessions = len(sessions_data)
    total_messages = 0
    total_input_tokens = 0
    total_output_tokens = 0
    total_cache_read_tokens = 0
    total_cost = 0.0
    model_stats: dict[str, dict] = {}
    daily_tokens: dict[str, dict] = {}
    # Activity by day of week (0=Mon .. 6=Sun)
    dow_activity = collections.Counter()
    # Activity by hour of day (0-23)
    hod_activity = collections.Counter()

    for s in sessions_data:
        input_tokens = _safe_usage_int(s.get("input_tokens"))
        output_tokens = _safe_usage_int(s.get("output_tokens"))
        cache_read_tokens = _safe_usage_int(s.get("cache_read_tokens"))
        cost_value = _safe_cost_float(s.get("estimated_cost"))
        total_messages += _safe_usage_int(s.get("message_count"))
        total_input_tokens += input_tokens
        total_output_tokens += output_tokens
        total_cache_read_tokens += cache_read_tokens
        total_cost += cost_value

        model = s.get("model") or "unknown"
        bucket = model_stats.setdefault(model, {
            "sessions": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cost": 0.0,
        })
        bucket["sessions"] += 1
        bucket["input_tokens"] += input_tokens
        bucket["output_tokens"] += output_tokens
        bucket["cache_read_tokens"] += cache_read_tokens
        bucket["cost"] += cost_value

        # Activity patterns
        ts = _session_usage_ts(s)
        if ts:
            try:
                dt = _time.localtime(ts)
                day_key = _time.strftime("%Y-%m-%d", dt)
                daily_bucket = daily_tokens.setdefault(day_key, {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cache_read_tokens": 0,
                    "sessions": 0,
                    "cost": 0.0,
                })
                daily_bucket["input_tokens"] += input_tokens
                daily_bucket["output_tokens"] += output_tokens
                daily_bucket["cache_read_tokens"] += cache_read_tokens
                daily_bucket["sessions"] += 1
                daily_bucket["cost"] += cost_value
                dow_activity[dt.tm_wday] += 1
                hod_activity[dt.tm_hour] += 1
            except Exception:
                logger.warning("Silent exception in _handle_insights", exc_info=True)
                pass

    # ── Also include CLI sessions from Hermes state.db ─────────────────────
    try:
        db_path = _active_state_db_path()
        if db_path and db_path.exists():
            with webui_session_db.open_db_readonly(db_path) as conn:
                cur = conn.cursor()
                # cache_read_tokens may not exist on older agent state DBs;
                # fall back to a query without it if the column is missing.
                try:
                    cur.execute("""
                        SELECT id, model, message_count, input_tokens, output_tokens,
                               estimated_cost_usd,
                               COALESCE(cache_read_tokens, 0) AS cache_read_tokens,
                               started_at, ended_at
                        FROM sessions
                        WHERE (started_at >= ? OR ended_at >= ?)
                          AND COALESCE(source, '') != 'webui'
                    """, (cutoff, cutoff))
                except sqlite3.OperationalError:
                    cur.execute("""
                        SELECT id, model, message_count, input_tokens, output_tokens,
                               estimated_cost_usd,
                               0 AS cache_read_tokens,
                               started_at, ended_at
                        FROM sessions
                        WHERE (started_at >= ? OR ended_at >= ?)
                          AND COALESCE(source, '') != 'webui'
                    """, (cutoff, cutoff))
                for row in cur.fetchall():
                    _input = _safe_usage_int(row["input_tokens"])
                    _output = _safe_usage_int(row["output_tokens"])
                    _cache_read = _safe_usage_int(row["cache_read_tokens"])
                    _cost = _safe_cost_float(row["estimated_cost_usd"])
                    _msgs = _safe_usage_int(row["message_count"])
                    total_sessions += 1
                    total_messages += _msgs
                    total_input_tokens += _input
                    total_output_tokens += _output
                    total_cache_read_tokens += _cache_read
                    total_cost += _cost

                    _model = row["model"] or "unknown"
                    bucket = model_stats.setdefault(_model, {
                        "sessions": 0,
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "cache_read_tokens": 0,
                        "cost": 0.0,
                    })
                    bucket["sessions"] += 1
                    bucket["input_tokens"] += _input
                    bucket["output_tokens"] += _output
                    bucket["cache_read_tokens"] += _cache_read
                    bucket["cost"] += _cost

                    _ts = row["started_at"] or row["ended_at"] or 0
                    if _ts:
                        _dt = _time.localtime(_ts)
                        _day_key = _time.strftime("%Y-%m-%d", _dt)
                        _daily = daily_tokens.setdefault(_day_key, {
                            "input_tokens": 0,
                            "output_tokens": 0,
                            "cache_read_tokens": 0,
                            "sessions": 0,
                            "cost": 0.0,
                        })
                        _daily["input_tokens"] += _input
                        _daily["output_tokens"] += _output
                        _daily["cache_read_tokens"] += _cache_read
                        _daily["sessions"] += 1
                        _daily["cost"] += _cost
                        dow_activity[_dt.tm_wday] += 1
                        hod_activity[_dt.tm_hour] += 1
    except Exception:
        logger.debug("Failed to include CLI sessions in insights", exc_info=True)

    # Build model breakdown
    total_tokens = total_input_tokens + total_output_tokens
    models_breakdown = []
    for model, stats in model_stats.items():
        row_total_tokens = stats["input_tokens"] + stats["output_tokens"]
        row_cost = round(stats["cost"], 6)
        row_cache_read = stats["cache_read_tokens"]
        # Bounded prompt-cache hit rate: cached reads over the FULL prompt total
        # (ordinary input + cache reads), so it can never exceed 100%. Computing
        # cache_read / input_tokens alone would overshoot 100% on cache-heavy
        # sessions. prompt_cache_hit_percent clamps to [0,100] and returns None
        # when there is nothing meaningful to display.
        row_cache_hit_percent = prompt_cache_hit_percent(
            row_cache_read, stats["input_tokens"] + row_cache_read
        )
        models_breakdown.append({
            "model": model,
            "sessions": stats["sessions"],
            "input_tokens": stats["input_tokens"],
            "output_tokens": stats["output_tokens"],
            "cache_read_tokens": row_cache_read,
            "cache_hit_percent": row_cache_hit_percent,
            "total_tokens": row_total_tokens,
            "cost": row_cost,
            "session_share": int(round((stats["sessions"] / total_sessions) * 100)) if total_sessions else 0,
            "token_share": int(round((row_total_tokens / total_tokens) * 100)) if total_tokens else 0,
            "cost_share": int(round((row_cost / total_cost) * 100)) if total_cost else 0,
        })
    models_breakdown.sort(key=lambda r: (-r["cost"], -r["sessions"], r["model"]))

    daily_series = []
    for i in range(days):
        day_ts = first_day_ts + (i * day_secs)
        day_key = _time.strftime("%Y-%m-%d", _time.localtime(day_ts))
        bucket = daily_tokens.get(day_key, {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "sessions": 0,
            "cost": 0.0,
        })
        daily_series.append({
            "date": day_key,
            "input_tokens": bucket["input_tokens"],
            "output_tokens": bucket["output_tokens"],
            "cache_read_tokens": bucket.get("cache_read_tokens", 0),
            "sessions": bucket["sessions"],
            "cost": round(bucket["cost"], 6),
        })

    # Day-of-week labels
    dow_labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    dow_data = [{"day": dow_labels[i], "sessions": dow_activity.get(i, 0)} for i in range(7)]

    # Hour-of-day data
    hod_data = [{"hour": h, "sessions": hod_activity.get(h, 0)} for h in range(24)]

    return j(handler, {
        "period_days": days,
        "total_sessions": total_sessions,
        "total_messages": total_messages,
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "total_cache_read_tokens": total_cache_read_tokens,
        # Aggregate prompt-cache hit rate, bounded 0-100% via the shared helper
        # (cache_read over input + cache_read).
        "total_cache_hit_percent": prompt_cache_hit_percent(
            total_cache_read_tokens, total_input_tokens + total_cache_read_tokens
        ),
        "total_tokens": total_tokens,
        "total_cost": round(total_cost, 6),
        "models": models_breakdown,
        "daily_tokens": daily_series,
        "activity_by_day": dow_data,
        "activity_by_hour": hod_data,
    })


def _accept_loop_health(handler) -> dict:
    server = getattr(handler, "server", None)
    return {
        "requests_total": int(getattr(server, "accept_loop_requests_total", 0) or 0),
        "last_request_at": round(float(getattr(server, "accept_loop_last_request_at", 0.0) or 0.0), 3),
    }


def _streams_lock_health(timeout_seconds: float = 0.5) -> dict:
    t0 = time.time()
    acquired = STREAMS_LOCK.acquire(timeout=timeout_seconds)
    elapsed_ms = round((time.time() - t0) * 1000, 1)
    if not acquired:
        return {
            "status": "blocked",
            "timeout_seconds": timeout_seconds,
            "ms": elapsed_ms,
        }
    try:
        return {
            "status": "ok",
            "active_streams": len(STREAMS),
            "ms": elapsed_ms,
        }
    finally:
        STREAMS_LOCK.release()


def _stream_runtime_diagnostics() -> dict:
    """Return non-sensitive SSE stream diagnostics for health/deep status."""
    streams = []
    total_subscribers = 0
    total_offline_buffered_events = 0
    with STREAMS_LOCK:
        items = list(STREAMS.items())
    for stream_id, stream in items:
        snapshot = {}
        diagnostic_snapshot = getattr(stream, "diagnostic_snapshot", None)
        if callable(diagnostic_snapshot):
            try:
                raw_snapshot = diagnostic_snapshot()
                if isinstance(raw_snapshot, dict):
                    snapshot = raw_snapshot
            except Exception:
                logger.debug("Silent exception in _stream_runtime_diagnostics", exc_info=True)
                snapshot = {}
        subscriber_count = int(snapshot.get("subscriber_count") or 0)
        offline_buffered_events = int(snapshot.get("offline_buffered_events") or 0)
        total_subscribers += subscriber_count
        total_offline_buffered_events += offline_buffered_events
        streams.append({
            "stream_id": str(stream_id),
            "subscriber_count": subscriber_count,
            "offline_buffered_events": offline_buffered_events,
        })
    streams.sort(key=lambda item: item["stream_id"])
    return {
        "active_streams": len(streams),
        "total_subscribers": total_subscribers,
        "total_offline_buffered_events": total_offline_buffered_events,
        "streams": streams,
    }


def _run_lifecycle_health() -> dict:
    """Return active worker-run state independent of SSE stream presence."""
    from api import config as _live_config

    now = time.time()
    with _live_config.ACTIVE_RUNS_LOCK:
        runs = []
        for _stream_id, raw in (_live_config.ACTIVE_RUNS or {}).items():
            item = dict(raw or {})
            item.pop("session_id", None)
            item.pop("stream_id", None)
            item.pop("workspace", None)
            started_at = item.get("started_at")
            try:
                age = max(0.0, now - float(started_at))
            except Exception:
                logger.debug("Silent exception in _run_lifecycle_health", exc_info=True)
                age = 0.0
            item["age_seconds"] = round(age, 1)
            runs.append(item)
        last_finished = _live_config.LAST_RUN_FINISHED_AT
    runs.sort(key=lambda item: float(item.get("started_at") or 0.0))
    payload = {
        "active_runs": len(runs),
        "runs": runs,
        "last_run_finished_at": last_finished,
    }
    if runs:
        payload["oldest_run_age_seconds"] = runs[0].get("age_seconds", 0.0)
    elif last_finished:
        payload["idle_seconds_since_last_run"] = round(max(0.0, now - float(last_finished)), 1)
    return payload


def _deep_health_checks(stream_check: dict | None = None) -> tuple[dict, bool]:
    """Run cheap probes that exercise the state paths used by the UI shell."""
    checks: dict[str, dict] = {}

    checks["streams_lock"] = stream_check if stream_check is not None else _streams_lock_health()
    checks["stream_runtime"] = {
        "status": "ok",
        **_stream_runtime_diagnostics(),
    }
    if checks["streams_lock"].get("status") != "ok":
        return checks, False

    t0 = time.time()
    try:
        sessions = all_sessions()
        checks["sessions"] = {
            "status": "ok",
            "count": len(sessions),
            "ms": round((time.time() - t0) * 1000, 1),
        }
    except Exception as exc:
        logger.debug("Silent exception in _deep_health_checks", exc_info=True)
        checks["sessions"] = {
            "status": "error",
            "error": type(exc).__name__,
            "ms": round((time.time() - t0) * 1000, 1),
        }

    t0 = time.time()
    try:
        projects = load_projects(_migrate=False)
        checks["projects"] = {
            "status": "ok",
            "count": len(projects),
            "ms": round((time.time() - t0) * 1000, 1),
        }
    except Exception as exc:
        logger.debug("Silent exception in _deep_health_checks", exc_info=True)
        checks["projects"] = {
            "status": "error",
            "error": type(exc).__name__,
            "ms": round((time.time() - t0) * 1000, 1),
        }

    t0 = time.time()
    try:
        db_path = _active_state_db_path()
        checks["state_db"] = webui_session_db.check_db_healthy(db_path)
    except Exception as exc:
        logger.debug("Silent exception in _deep_health_checks", exc_info=True)
        checks["state_db"] = {
            "status": "error",
            "error": type(exc).__name__,
            "ms": round((time.time() - t0) * 1000, 1),
        }

    healthy = all(
        check.get("status") in {"ok", "missing"}
        for check in checks.values()
    )
    return checks, healthy


def _handle_health(handler, parsed) -> bool:
    deep = parse_qs(parsed.query or "").get("deep", [""])[0].lower() in {"1", "true", "yes", "on"}
    stream_check = _streams_lock_health()
    run_check = _run_lifecycle_health()
    payload = {
        "status": "ok" if stream_check.get("status") == "ok" else "degraded",
        "sessions": len(SESSIONS),
        "active_streams": int(stream_check.get("active_streams") or 0),
        "active_runs": int(run_check.get("active_runs") or 0),
        "runs": run_check.get("runs", []),
        "last_run_finished_at": run_check.get("last_run_finished_at"),
        "server_started_at": SERVER_START_TIME,
        "uptime_seconds": round(time.time() - SERVER_START_TIME, 1),
        "accept_loop": _accept_loop_health(handler),
    }
    if "oldest_run_age_seconds" in run_check:
        payload["oldest_run_age_seconds"] = run_check["oldest_run_age_seconds"]
    if "idle_seconds_since_last_run" in run_check:
        payload["idle_seconds_since_last_run"] = run_check["idle_seconds_since_last_run"]
    if deep:
        if stream_check.get("status") != "ok":
            payload["checks"] = {"streams_lock": stream_check}
            return j(handler, payload, status=503)
        checks, healthy = _deep_health_checks(stream_check=stream_check)
        payload["checks"] = checks
        if not healthy:
            payload["status"] = "degraded"
            return j(handler, payload, status=503)
    if payload["status"] != "ok":
        return j(handler, payload, status=503)
    return j(handler, payload)


def _handle_get_system(handler, parsed):
    """Handle system health, telemetry, and log endpoints.
    Returns True if handled, None if unhandled.
    """
    if parsed.path == "/api/insights":
        return _handle_insights(handler, parsed)
    if parsed.path == "/api/logs":
        return _handle_logs(handler, parsed)

    if parsed.path == "/health":
        return _handle_health(handler, parsed)

    if parsed.path == "/api/health/agent":
        payload = build_agent_health_payload()
        payload["gateway_chat"] = gateway_chat_config_status()
        j(handler, payload)
        return True

    if parsed.path == "/api/system/health":
        j(handler, build_system_health_payload())
        return True

    return None
