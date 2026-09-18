"""Dormant JSON-backed SessionDB-shaped adapter for WebUI sessions.

This module intentionally does not replace existing WebUI runtime call sites.
It provides a small compatibility surface over the current JSON sidecars so the
unified SessionDB contract can be tested without changing persistence behavior.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator, Sequence

import api.models as models

logger = logging.getLogger(__name__)


_METADATA_FIELDS = frozenset(
    {
        "title",
        "workspace",
        "model",
        "model_provider",
        "created_at",
        "updated_at",
        "pinned",
        "archived",
        "project_id",
        "profile",
        "input_tokens",
        "output_tokens",
        "estimated_cost",
        "cache_read_tokens",
        "cache_write_tokens",
        "personality",
        "active_stream_id",
        "pending_user_message",
        "pending_attachments",
        "pending_started_at",
        "compression_anchor_visible_idx",
        "compression_anchor_message_key",
        "compression_anchor_summary",
        "pre_compression_snapshot",
        "context_engine",
        "compression_anchor_engine",
        "compression_anchor_mode",
        "compression_anchor_details",
        "context_engine_state",
        "context_length",
        "threshold_tokens",
        "last_prompt_tokens",
        "compression_recovery",
        "recommended_recovery_action",
        "compression_recovery_source_session_id",
        "compression_recovery_action",
        "truncation_watermark",
        "truncation_boundary",
        "gateway_routing",
        "gateway_routing_history",
        "llm_title_generated",
        "manual_title",
        "parent_session_id",
        "worktree_path",
        "worktree_branch",
        "worktree_repo_root",
        "worktree_created_at",
        "is_cli_session",
        "source_tag",
        "raw_source",
        "session_source",
        "source_label",
        "read_only",
        "enabled_toolsets",
        "composer_draft",
    }
)

_UNSAFE_FIELDS = frozenset({"session_id", "messages", "tool_calls", "message_count"})


class WebUIJsonSessionDB:
    """Small SessionDB-like facade over existing WebUI session JSON files."""

    def __init__(self, session_dir: Path | str | None = None):
        self._session_dir = Path(session_dir).expanduser().resolve() if session_dir else None

    @property
    def session_dir(self) -> Path:
        return self._session_dir or models.SESSION_DIR

    def list_sessions(self) -> list[dict[str, Any]]:
        """Return compact metadata for persisted WebUI JSON sessions.

        Reads are direct JSON loads and never call ``Session.load()``, because
        that path may self-heal and write repaired transcripts.
        """
        rows: list[dict[str, Any]] = []
        if not self.session_dir.exists():
            return rows
        for path in self.session_dir.glob("*.json"):
            if path.name.startswith("_"):
                continue
            data = self._read_path(path)
            if not isinstance(data, dict):
                continue
            sid = str(data.get("session_id") or path.stem)
            if not models.is_safe_session_id(sid):
                continue
            rows.append(self._metadata_row(sid, data))
        rows.sort(key=lambda row: (bool(row.get("pinned")), self._sort_timestamp(row)), reverse=True)
        return rows

    def read_session(self, sid: str) -> dict[str, Any] | None:
        """Return the full JSON session payload for ``sid`` without mutation."""
        path = self._path_for_sid(sid)
        if path is None or not path.exists():
            return None
        data = self._read_path(path)
        if not isinstance(data, dict):
            return None
        return copy.deepcopy(data)

    def update_metadata(self, sid: str, fields: dict[str, Any]) -> dict[str, Any]:
        """Persist allowlisted metadata fields while preserving messages.

        This dormant adapter method is for migration experiments and tests only.
        Runtime wiring must add Session lock/cache/index parity before using it
        from live WebUI routes.
        """
        if not isinstance(fields, dict):
            raise TypeError("fields must be a dict")
        unsafe = sorted((set(fields) & _UNSAFE_FIELDS) | (set(fields) - _METADATA_FIELDS))
        if unsafe:
            raise ValueError(f"Unsafe session metadata fields: {', '.join(unsafe)}")

        path = self._existing_path_for_sid(sid)
        data = self._read_writable_session(path)
        data.update(copy.deepcopy(fields))
        data["message_count"] = len(data["messages"])
        self._atomic_write(path, data)
        return self._metadata_row(str(data.get("session_id") or sid), data)

    def archive(self, sid: str, archived: bool = True) -> dict[str, Any]:
        """Set the archived metadata flag without touching transcript messages."""
        return self.update_metadata(sid, {"archived": bool(archived)})

    def write_session(self, session: dict[str, Any]) -> dict[str, Any]:
        """Write a full session payload for tests and migration experiments."""
        if not isinstance(session, dict):
            raise TypeError("session must be a dict")
        sid = session.get("session_id")
        path = self._path_for_sid(sid)
        if path is None:
            raise ValueError(f"Unsafe session_id {sid!r}")
        messages = session.get("messages")
        if not isinstance(messages, list):
            raise ValueError("session payload must include a messages list")
        payload = copy.deepcopy(session)
        payload["message_count"] = len(messages)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write(path, payload)
        return copy.deepcopy(payload)

    def _path_for_sid(self, sid: str) -> Path | None:
        if not models.is_safe_session_id(sid):
            return None
        return self.session_dir / f"{sid}.json"

    def _existing_path_for_sid(self, sid: str) -> Path:
        path = self._path_for_sid(sid)
        if path is None:
            raise ValueError(f"Unsafe session_id {sid!r}")
        if not path.exists():
            raise KeyError(sid)
        return path

    def _read_writable_session(self, path: Path) -> dict[str, Any]:
        data = self._read_path(path)
        if not isinstance(data, dict):
            raise ValueError(f"Malformed session JSON: {path.name}")
        sid = data.get("session_id")
        if not models.is_safe_session_id(sid):
            raise ValueError(f"Unsafe session_id {sid!r}")
        if not isinstance(data.get("messages"), list):
            raise ValueError(f"Refusing to write metadata-only session stub: {sid!r}")
        return data

    @staticmethod
    def _read_path(path: Path) -> dict[str, Any] | None:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return None

    @staticmethod
    def _metadata_row(sid: str, data: dict[str, Any]) -> dict[str, Any]:
        messages = data.get("messages")
        message_count = data.get("message_count")
        if not isinstance(message_count, int):
            message_count = len(messages) if isinstance(messages, list) else 0
        row = {field: copy.deepcopy(data.get(field)) for field in _METADATA_FIELDS if field in data}
        row["session_id"] = sid
        row["message_count"] = message_count
        row["last_message_at"] = data.get("last_message_at") or data.get("updated_at") or data.get("created_at")
        return row

    @staticmethod
    def _sort_timestamp(row: dict[str, Any]) -> float:
        for key in ("last_message_at", "updated_at", "created_at"):
            value = row.get(key)
            if value is None or value == "":
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
        return 0.0

    @staticmethod
    def _atomic_write(path: Path, data: dict[str, Any]) -> None:
        payload = json.dumps(data, ensure_ascii=False, indent=2)
        tmp = path.with_suffix(f".tmp.{os.getpid()}.{threading.current_thread().ident}")
        try:
            with open(tmp, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass


def list_sessions() -> list[dict[str, Any]]:
    return WebUIJsonSessionDB().list_sessions()


def read_session(sid: str) -> dict[str, Any] | None:
    return WebUIJsonSessionDB().read_session(sid)


def update_metadata(sid: str, fields: dict[str, Any]) -> dict[str, Any]:
    return WebUIJsonSessionDB().update_metadata(sid, fields)


def archive(sid: str, archived: bool = True) -> dict[str, Any]:
    return WebUIJsonSessionDB().archive(sid, archived)


def write_session(session: dict[str, Any]) -> dict[str, Any]:
    return WebUIJsonSessionDB().write_session(session)


# ── Centralized SQLite Connection Management & Context Managers ───────────────

def create_db_connection(
    db_path: Path | str,
    read_only: bool = False,
    timeout: float = 5.0,
    row_factory: bool = True,
    busy_timeout_ms: int = 5000,
    query_only: bool = False,
    log: logging.Logger | None = None,
) -> sqlite3.Connection:
    """Create and configure a hardened SQLite connection.

    - If `read_only` is True:
        Requires the database file to exist on disk (raises ``FileNotFoundError``),
        preventing SQLite from creating empty zero-byte ghost databases.
        Opens via read-only URI: ``file:...uri?mode=ro``.
        Applies ``PRAGMA query_only = ON``.
        Falls back to a standard connection if the filesystem does not support URI mode=ro.
    - If `read_only` is False:
        Configures WAL journal mode (``PRAGMA journal_mode = WAL``).
        Configures normal synchronous mode (``PRAGMA synchronous = NORMAL``).
        Enforces foreign keys (``PRAGMA foreign_keys = ON``).
    - On all connections:
        Applies ``PRAGMA busy_timeout = {busy_timeout_ms}`` to prevent transient lock contention.
        Optionally configures ``conn.row_factory = sqlite3.Row``.
    """
    _log = log or logger
    str_path = str(db_path).strip()
    is_memory = str_path in (":memory:", "") or "mode=memory" in str_path

    if is_memory:
        conn = sqlite3.connect(":memory:", timeout=timeout)
    elif read_only:
        p = Path(db_path).resolve()
        if not p.exists():
            raise FileNotFoundError(f"SQLite database not found: {db_path}")
        read_only_uri = f"{p.as_uri()}?mode=ro"
        try:
            conn = sqlite3.connect(read_only_uri, uri=True, timeout=timeout)
        except sqlite3.Error as exc:
            _log.warning(
                "SQLite read-only open failed for %s; falling back to writable connection: %s",
                db_path,
                exc,
            )
            conn = sqlite3.connect(str(p), timeout=timeout)
    else:
        p = Path(db_path).resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(p), timeout=timeout)

    # Configure pragmas defensively
    if read_only or query_only:
        try:
            conn.execute("PRAGMA query_only = ON")
        except sqlite3.Error:
            pass
    elif not is_memory:
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.execute("PRAGMA foreign_keys = ON")
        except sqlite3.Error as pragma_err:
            _log.debug("Notice: pragma configuration warning: %s", pragma_err)

    try:
        conn.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
    except sqlite3.Error:
        pass

    if row_factory:
        conn.row_factory = sqlite3.Row

    return conn


@contextmanager
def get_db_connection(
    db_path: Path | str,
    read_only: bool = False,
    timeout: float = 5.0,
    row_factory: bool = True,
    busy_timeout_ms: int = 5000,
    query_only: bool = False,
    log: logging.Logger | None = None,
) -> Generator[sqlite3.Connection, None, None]:
    """Context manager providing a managed SQLite connection that closes on exit."""
    conn = create_db_connection(
        db_path=db_path,
        read_only=read_only,
        timeout=timeout,
        row_factory=row_factory,
        busy_timeout_ms=busy_timeout_ms,
        query_only=query_only,
        log=log,
    )
    try:
        yield conn
    finally:
        try:
            conn.close()
        except Exception:
            pass


def open_db_readonly(
    db_path: Path | str,
    timeout: float = 5.0,
    row_factory: bool = True,
    busy_timeout_ms: int = 5000,
    log: logging.Logger | None = None,
) -> Generator[sqlite3.Connection, None, None]:
    """Shorthand context manager for read-only database access."""
    return get_db_connection(
        db_path=db_path,
        read_only=True,
        timeout=timeout,
        row_factory=row_factory,
        busy_timeout_ms=busy_timeout_ms,
        query_only=True,
        log=log,
    )


def open_db_writable(
    db_path: Path | str,
    timeout: float = 5.0,
    row_factory: bool = True,
    busy_timeout_ms: int = 5000,
    log: logging.Logger | None = None,
) -> Generator[sqlite3.Connection, None, None]:
    """Shorthand context manager for writable database access."""
    return get_db_connection(
        db_path=db_path,
        read_only=False,
        timeout=timeout,
        row_factory=row_factory,
        busy_timeout_ms=busy_timeout_ms,
        log=log,
    )


def execute_query(
    db_path: Path | str,
    sql: str,
    params: Sequence[Any] | dict[str, Any] = (),
    read_only: bool = True,
    timeout: float = 5.0,
) -> list[sqlite3.Row]:
    """Execute a query within a managed connection and return all fetched rows."""
    with get_db_connection(db_path, read_only=read_only, timeout=timeout) as conn:
        cur = conn.execute(sql, params)
        rows = cur.fetchall()
        if not read_only:
            conn.commit()
        return rows


def check_db_healthy(db_path: Path | str, timeout: float = 2.0) -> dict[str, Any]:
    """Fast health probe on a SQLite database checking accessibility and schema_version."""
    str_path = str(db_path).strip()
    if str_path == ":memory:":
        return {"status": "ok", "ms": 0.0}
    p = Path(db_path).resolve()
    t0 = time.time()
    if not p.exists():
        return {"status": "missing", "ms": round((time.time() - t0) * 1000, 1)}
    try:
        with get_db_connection(p, read_only=True, timeout=timeout) as conn:
            conn.execute("PRAGMA schema_version").fetchone()
        return {"status": "ok", "ms": round((time.time() - t0) * 1000, 1)}
    except Exception as exc:
        return {
            "status": "error",
            "error": type(exc).__name__,
            "ms": round((time.time() - t0) * 1000, 1),
        }

