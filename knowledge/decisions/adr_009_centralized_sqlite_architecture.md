# ADR 009: Centralized SQLite Architecture & Connection Hardening

- **Date**: 2026-09-18
- **Status**: Accepted
- **Tags**: #decisions #adr #sqlite #database #architecture #concurrency #wal #refactoring #testing

## Context & Problem Statement
The Antigravity backend previously maintained fragmented and unstandardized SQLite access patterns across multiple core modules:
- Scattered `sqlite3.connect(...)` invocations existed across `agent_sessions.py`, `models.py`, `session_recovery.py`, `routes.py`, and `session_discoverability.py`.
- **Ghost Database Creation Risk**: Read-only operations that fell back to or used default writable paths could inadvertently trigger SQLite's default behavior of creating empty, zero-byte `.db` files when a target path was missing or misconfigured in user profiles.
- **Inconsistent Pragma Configuration**: Some call sites configured `PRAGMA busy_timeout`, while others relied on default zero-delay locks; some configured `PRAGMA foreign_keys = ON`, while others left cascades unapplied; and journal modes were inconsistently managed under heavy concurrent agent streaming.
- **Resource Management Leaks**: Several call sites relied on manual `try...finally: conn.close()` or omitted context management, risking file descriptor exhaustion during rapid UI polling or large sidebar rebuilds.

## Decision Drivers
1. **Single Source of Truth**: Consolidate all SQLite connection creation, configuration, context management, and health probes into a dedicated module: `webui/api/webui_session_db.py`.
2. **Defensive Pragma Invariants**:
   - For read-only connections (`read_only=True`): Enforce file existence checks before connect (raising `FileNotFoundError` to forbid zero-byte ghost DB creation), open via read-only URI (`file:...mode=ro`), and apply `PRAGMA query_only = ON`.
   - For writable connections (`read_only=False`): Enforce `PRAGMA journal_mode = WAL`, `PRAGMA synchronous = NORMAL`, and `PRAGMA foreign_keys = ON`.
   - For all connections: Enforce `PRAGMA busy_timeout = 5000` (configurable) to eliminate transient lock contention between streaming agent subprocesses and WebUI readers.
   - Default `row_factory = sqlite3.Row` for dict-like column access with optional tuple fallback.
3. **Ergonomic Context Managers**: Provide `get_db_connection`, `open_db_readonly`, and `open_db_writable` context managers that guarantee connection closure upon exit or exception.
4. **Hermetic Test Coverage**: Establish a comprehensive unit and concurrency test suite in `tests/test_session_db.py` verifying pragmas, ghost DB prevention, auto-closure, and thread-safe WAL concurrency.
5. **Zero Regressions**: Maintain 100% backward compatibility for existing function signatures such as `open_state_db_readonly(db_path, log)`.

## Considered Options
1. **Status Quo (Scattered `sqlite3.connect` calls)**: Keep ad-hoc connection handling across each module. (Rejected: Technical debt, lock contention risk, and ghost DB creation bugs).
2. **Heavy ORM (SQLAlchemy / Peewee / Tortoise)**: Introduce an ORM layer over SQLite. (Rejected: Adds heavy third-party dependencies, introduces schema mapping overhead for Hermes Agent's dynamically evolving schema, and risks subtle behavioral differences in containerized environments).
3. **Centralized Lightweight Hardened Helpers (`webui_session_db.py`)**: Implement standard-library-based connection factories, context managers, and probes with hardened SQLite pragmas. (Chosen).

## Decision Outcome
**Option 3 was chosen** and executed methodically across five sprints:

1. **Sprint S1 — Centralized SQLite Engine & Dedicated Test Suite**:
   - Implemented `create_db_connection`, `get_db_connection`, `open_db_readonly`, `open_db_writable`, `execute_query`, and `check_db_healthy` in `webui/api/webui_session_db.py`.
   - Created `tests/test_session_db.py` with 11 hermetic test cases testing in-memory databases, parent directory creation, WAL mode, foreign keys, busy timeouts, read-only enforcement, ghost DB prevention, auto-closing context managers, parameterized execution, and concurrent multi-threaded readers and writers.
2. **Sprint S2 — Refactoring `agent_sessions.py`**:
   - Refactored `open_state_db_readonly` to delegate to `webui_session_db.create_db_connection(db_path, read_only=True, row_factory=True)`.
   - Refactored `read_importable_agent_session_rows` to use `open_db_readonly` and defensive index self-healing to use `open_db_writable`.
3. **Sprint S3 — Refactoring `models.py` & `session_recovery.py`**:
   - Refactored `_sqlite_content_fingerprint` in `models.py` to use `open_db_readonly` with tailored fast timeouts (`timeout=0.05`, `busy_timeout_ms=50`).
   - Refactored `_sweep_orphaned_state_db_transcripts` to use `open_db_writable`.
   - Refactored `_process_stale_cleanup_manifests` to use `open_db_readonly`.
   - Refactored `_state_db_has_session` and `_scan_state_db_sessions` in `session_recovery.py` to use `open_db_readonly`.
4. **Sprint S4 — Refactoring `routes.py` & `session_discoverability.py`**:
   - Refactored `_resolve_cli_journal_targets`, `_state_db_session_source`, `_claim_or_synthesize_cli_session`, `_state_db_target_session_signature`, `_state_db_session_revision_hash`, and `_handle_insights` in `routes.py` to use `open_db_readonly`.
   - Refactored `_deep_health_checks` to delegate directly to `webui_session_db.check_db_healthy(db_path)`.
   - Refactored `_append_handoff_summary_to_state_db` to use `open_db_writable`.
   - Refactored `_read_state_db` in `session_discoverability.py` to use `open_db_readonly`.
5. **Sprint S5 — Quality Gate & Second Brain Knowledge Vault Synchronization**:
   - Verified 100% of raw `sqlite3.connect` calls outside the centralized module were eliminated.
   - Full automated test suite passed: 188/188 tests passing (11 new tests added).
   - Linter verification: Clean `npm run lint` across HTML, JavaScript, and Python (Ruff).
   - Authoring and synchronization of ADR 009 into `.gemini/rules/knowledge_vault.md`.

## Consequences & Linked Systems
- **Zero Raw Connection Sprawl**: 100% of SQLite connection creation in `webui/api/` is now routed through `webui_session_db.py`.
- **Lock Contention Resistance**: Uniform application of `PRAGMA busy_timeout = 5000` and WAL mode prevents database lock contention between streaming agent background runs and frontend API polling.
- **Ghost DB Prevention**: Read-only calls check for file presence on disk before connecting, completely preventing zero-byte database generation.
- **Standardized Health Probes**: `check_db_healthy` provides a uniform, fast, low-overhead probe for `/api/health` and status diagnostics.
- **Related ADRs**:
  - [[adr_001_cagy_fork]]
  - [[adr_006_streaming_step_decomposition]]
  - [[adr_007_routes_decomposition]]
  - [[adr_008_frontend_ui_decomposition]]
