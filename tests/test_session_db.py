"""Tests for centralized SQLite connection management and WebUI Session DB adapter."""
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WEBUI_DIR = REPO_ROOT / "webui"

if str(WEBUI_DIR) not in sys.path:
    sys.path.insert(0, str(WEBUI_DIR))

from api import webui_session_db


class TestSQLiteConnectionManagement(unittest.TestCase):
    """Hermetic unit tests for create_db_connection, context managers, and health probes."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="agy_test_sqlite_")
        self.db_path = Path(self.temp_dir) / "sub" / "test.db"

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_create_db_connection_memory(self):
        """In-memory database creates properly with row_factory."""
        conn = webui_session_db.create_db_connection(":memory:")
        try:
            cur = conn.execute("SELECT 1 AS num, 'alpha' AS val")
            row = cur.fetchone()
            self.assertEqual(row["num"], 1)
            self.assertEqual(row["val"], "alpha")
        finally:
            conn.close()

    def test_create_db_connection_writable_creates_parents_and_pragmas(self):
        """Writable connections create parent dirs and set WAL, foreign_keys, and busy_timeout."""
        self.assertFalse(self.db_path.parent.exists())
        conn = webui_session_db.create_db_connection(self.db_path, busy_timeout_ms=3500)
        try:
            self.assertTrue(self.db_path.parent.exists())
            self.assertTrue(self.db_path.exists())

            # Verify Pragmas
            journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0].lower()
            self.assertEqual(journal_mode, "wal")

            foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()[0]
            self.assertEqual(foreign_keys, 1)

            busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
            self.assertEqual(busy_timeout, 3500)

            # Check row factory
            conn.execute("CREATE TABLE test_table (id INTEGER PRIMARY KEY, name TEXT)")
            conn.execute("INSERT INTO test_table (name) VALUES ('unit-test')")
            conn.commit()

            row = conn.execute("SELECT id, name FROM test_table WHERE id = 1").fetchone()
            self.assertIsInstance(row, sqlite3.Row)
            self.assertEqual(row["name"], "unit-test")
        finally:
            conn.close()

    def test_create_db_connection_without_row_factory(self):
        """row_factory=False leaves default tuple rows."""
        conn = webui_session_db.create_db_connection(":memory:", row_factory=False)
        try:
            row = conn.execute("SELECT 42 AS val").fetchone()
            self.assertIsInstance(row, tuple)
            self.assertEqual(row[0], 42)
        finally:
            conn.close()

    def test_create_db_connection_readonly_missing_file_raises(self):
        """Read-only connection raises FileNotFoundError if file is missing (no ghost DB)."""
        missing_db = Path(self.temp_dir) / "ghost.db"
        self.assertFalse(missing_db.exists())

        with self.assertRaises(FileNotFoundError):
            webui_session_db.create_db_connection(missing_db, read_only=True)

        # Assert no empty file was created
        self.assertFalse(missing_db.exists())

    def test_create_db_connection_readonly_enforces_query_only(self):
        """Read-only connection prevents any schema or data modifications."""
        # Initialize database first
        with webui_session_db.open_db_writable(self.db_path) as conn:
            conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, title TEXT)")
            conn.execute("INSERT INTO items (title) VALUES ('sample')")
            conn.commit()

        # Open in read-only mode
        conn = webui_session_db.create_db_connection(self.db_path, read_only=True)
        try:
            # Reads succeed
            row = conn.execute("SELECT title FROM items WHERE id = 1").fetchone()
            self.assertEqual(row["title"], "sample")

            # Writes fail
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("INSERT INTO items (title) VALUES ('forbidden')")
        finally:
            conn.close()

    def test_get_db_connection_context_manager_auto_close(self):
        """get_db_connection closes connection automatically even if error raised."""
        captured_conn = None
        with webui_session_db.get_db_connection(":memory:") as conn:
            captured_conn = conn
            conn.execute("SELECT 1")

        # After block, connection must be closed
        with self.assertRaises(sqlite3.ProgrammingError):
            captured_conn.execute("SELECT 1")

        # Check with exception inside block
        captured_conn2 = None
        try:
            with webui_session_db.get_db_connection(":memory:") as conn2:
                captured_conn2 = conn2
                raise ValueError("Intentional error")
        except ValueError:
            pass

        with self.assertRaises(sqlite3.ProgrammingError):
            captured_conn2.execute("SELECT 1")

    def test_open_db_readonly_and_open_db_writable(self):
        """open_db_readonly and open_db_writable work seamlessly as shorthands."""
        # Create and write table
        with webui_session_db.open_db_writable(self.db_path) as conn:
            conn.execute("CREATE TABLE metrics (key TEXT PRIMARY KEY, val REAL)")
            conn.execute("INSERT INTO metrics (key, val) VALUES (?, ?)", ("cpu", 42.5))
            conn.commit()

        # Read back with read-only shorthand
        with webui_session_db.open_db_readonly(self.db_path) as ro_conn:
            row = ro_conn.execute("SELECT val FROM metrics WHERE key = ?", ("cpu",)).fetchone()
            self.assertEqual(row["val"], 42.5)

            # Writes must fail
            with self.assertRaises(sqlite3.OperationalError):
                ro_conn.execute("UPDATE metrics SET val = 99.0 WHERE key = 'cpu'")

    def test_execute_query(self):
        """execute_query executes and fetches rows for both read and write queries."""
        # Initialize table
        webui_session_db.execute_query(
            self.db_path,
            "CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT)",
            read_only=False,
        )

        # Insert record with params
        webui_session_db.execute_query(
            self.db_path,
            "INSERT INTO users (username) VALUES (:name)",
            {"name": "alice"},
            read_only=False,
        )

        # Query records
        rows = webui_session_db.execute_query(
            self.db_path,
            "SELECT id, username FROM users WHERE username = ?",
            ("alice",),
            read_only=True,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["username"], "alice")

    def test_check_db_healthy(self):
        """check_db_healthy accurately reports memory, existing, missing, and corrupted DBs."""
        # In-memory
        mem_health = webui_session_db.check_db_healthy(":memory:")
        self.assertEqual(mem_health["status"], "ok")
        self.assertEqual(mem_health["ms"], 0.0)

        # Non-existent DB
        missing_health = webui_session_db.check_db_healthy(Path(self.temp_dir) / "does_not_exist.db")
        self.assertEqual(missing_health["status"], "missing")

        # Existing valid DB
        with webui_session_db.open_db_writable(self.db_path) as conn:
            conn.execute("CREATE TABLE ping (id INT)")
            conn.commit()

        valid_health = webui_session_db.check_db_healthy(self.db_path)
        self.assertEqual(valid_health["status"], "ok")
        self.assertIsInstance(valid_health["ms"], float)

        # Corrupted DB file
        corrupt_path = Path(self.temp_dir) / "corrupt.db"
        corrupt_path.write_bytes(b"THIS IS NOT A VALID SQLITE DATABASE FILE HEADER")
        corrupt_health = webui_session_db.check_db_healthy(corrupt_path)
        self.assertEqual(corrupt_health["status"], "error")
        self.assertIn("DatabaseError", corrupt_health.get("error", ""))

    def test_concurrent_readers_and_writers(self):
        """WAL mode supports concurrent reads and writes without locking errors."""
        with webui_session_db.open_db_writable(self.db_path) as conn:
            conn.execute("CREATE TABLE counter (id INTEGER PRIMARY KEY, count INTEGER)")
            conn.execute("INSERT INTO counter (id, count) VALUES (1, 0)")
            conn.commit()

        def reader_task():
            with webui_session_db.open_db_readonly(self.db_path) as conn:
                for _ in range(10):
                    row = conn.execute("SELECT count FROM counter WHERE id = 1").fetchone()
                    self.assertIsNotNone(row)
            return True

        def writer_task():
            with webui_session_db.open_db_writable(self.db_path) as conn:
                for _ in range(5):
                    conn.execute("UPDATE counter SET count = count + 1 WHERE id = 1")
                    conn.commit()
            return True

        with ThreadPoolExecutor(max_workers=5) as executor:
            f_readers = [executor.submit(reader_task) for _ in range(3)]
            f_writers = [executor.submit(writer_task) for _ in range(2)]

            for f in f_readers + f_writers:
                self.assertTrue(f.result())

        # Verify final count
        with webui_session_db.open_db_readonly(self.db_path) as conn:
            final_count = conn.execute("SELECT count FROM counter WHERE id = 1").fetchone()[0]
            self.assertEqual(final_count, 10)


class TestWebUIJsonSessionDBAdapter(unittest.TestCase):
    """Hermetic unit tests for WebUI JSON session adapter functions."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="agy_test_json_session_")
        self.orig_chats_dir = os.environ.get("CHATS_DIR")
        os.environ["CHATS_DIR"] = self.temp_dir

    def tearDown(self):
        if self.orig_chats_dir is not None:
            os.environ["CHATS_DIR"] = self.orig_chats_dir
        else:
            os.environ.pop("CHATS_DIR", None)
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_json_session_lifecycle(self):
        """Create, read, update_metadata, archive, write_session, and list_sessions."""
        db = webui_session_db.WebUIJsonSessionDB(session_dir=self.temp_dir)
        sid = "sess_test_12345"

        # Write session
        payload = {
            "session_id": sid,
            "title": "Sprint Planning",
            "workspace": "/workspace/project",
            "model": "gemini-flash",
            "personality": "engineer",
            "messages": [{"role": "user", "content": "hello"}],
        }
        written = db.write_session(payload)
        self.assertEqual(written["session_id"], sid)
        self.assertEqual(written["title"], "Sprint Planning")

        # Read session
        retrieved = db.read_session(sid)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved["session_id"], sid)
        self.assertEqual(retrieved["title"], "Sprint Planning")

        # Update metadata
        updated = db.update_metadata(sid, {"pinned": True, "title": "Sprint Planning v2"})
        self.assertTrue(updated["pinned"])
        self.assertEqual(updated["title"], "Sprint Planning v2")

        # Archive
        archived = db.archive(sid, True)
        self.assertTrue(archived["archived"])

        # Unarchive
        unarchived = db.archive(sid, False)
        self.assertFalse(unarchived["archived"])

        # List sessions
        sessions = db.list_sessions()
        self.assertGreaterEqual(len(sessions), 1)
        matching = [s for s in sessions if s["session_id"] == sid]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["title"], "Sprint Planning v2")
        self.assertFalse(matching[0]["archived"])


if __name__ == "__main__":
    unittest.main()
