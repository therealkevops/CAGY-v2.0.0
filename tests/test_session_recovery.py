"""Unit tests for webui/api/session_recovery.py."""
import json
import shutil
import tempfile
import unittest
import uuid
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WEBUI_DIR = REPO_ROOT / "webui"
if str(WEBUI_DIR) not in sys.path:
    sys.path.insert(0, str(WEBUI_DIR))

from api.session_recovery import (
    _is_valid_intentional_shrink_generation,
    _msg_count,
    audit_session_recovery,
    inspect_session_recovery_status,
    recover_all_sessions_on_startup,
    recover_session,
    repair_safe_session_recovery,
)


class TestSessionRecoveryUnit(unittest.TestCase):
    """Test unit-level helpers and file inspection in session_recovery."""

    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_is_valid_intentional_shrink_generation(self):
        """Verify UUID4 hex validation."""
        valid_uuid = uuid.uuid4().hex
        self.assertTrue(_is_valid_intentional_shrink_generation(valid_uuid))
        self.assertFalse(_is_valid_intentional_shrink_generation(""))
        self.assertFalse(_is_valid_intentional_shrink_generation("not-a-uuid"))
        self.assertFalse(_is_valid_intentional_shrink_generation(12345))
        self.assertFalse(_is_valid_intentional_shrink_generation(None))
        # Wrong length or non-hex
        self.assertFalse(_is_valid_intentional_shrink_generation(valid_uuid[:-1]))
        self.assertFalse(_is_valid_intentional_shrink_generation(valid_uuid + "a"))

    def test_msg_count_valid_file(self):
        """Verify _msg_count returns count for valid session JSON."""
        session_file = self.test_dir / "session_1.json"
        session_file.write_text(
            json.dumps({
                "session_id": "session_1",
                "messages": [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}],
            }),
            encoding="utf-8",
        )
        self.assertEqual(_msg_count(session_file), 2)

    def test_msg_count_missing_file(self):
        """Verify _msg_count returns -1 on missing file."""
        missing = self.test_dir / "does_not_exist.json"
        self.assertEqual(_msg_count(missing), -1)

    def test_msg_count_corrupt_json(self):
        """Verify _msg_count returns -1 on malformed JSON."""
        corrupt = self.test_dir / "corrupt.json"
        corrupt.write_text("{not valid json", encoding="utf-8")
        self.assertEqual(_msg_count(corrupt), -1)

    def test_msg_count_non_dict_payload(self):
        """Verify _msg_count returns -1 if JSON is a list (like _index.json)."""
        idx_file = self.test_dir / "_index.json"
        idx_file.write_text(json.dumps([{"session_id": "s1"}]), encoding="utf-8")
        self.assertEqual(_msg_count(idx_file), -1)

    def test_msg_count_non_list_messages(self):
        """Verify _msg_count returns -1 if messages is not a list."""
        bad_messages = self.test_dir / "bad_msgs.json"
        bad_messages.write_text(json.dumps({"session_id": "s1", "messages": "not a list"}), encoding="utf-8")
        self.assertEqual(_msg_count(bad_messages), -1)

    def test_inspect_status_no_backup(self):
        """Inspect returns recommend: 'no_backup' when .json.bak does not exist."""
        session_file = self.test_dir / "test_sid.json"
        session_file.write_text(json.dumps({"session_id": "test_sid", "messages": [{"role": "user"}]}), encoding="utf-8")
        status = inspect_session_recovery_status(session_file)
        self.assertEqual(status["recommend"], "no_backup")
        self.assertEqual(status["live_messages"], 1)
        self.assertEqual(status["bak_messages"], -1)

    def test_inspect_status_healthy_session(self):
        """Inspect returns recommend: 'no_action' when live has >= messages than .bak."""
        session_file = self.test_dir / "healthy.json"
        bak_file = self.test_dir / "healthy.json.bak"
        session_file.write_text(
            json.dumps({"session_id": "healthy", "messages": [{"role": "user"}, {"role": "assistant"}]}),
            encoding="utf-8",
        )
        bak_file.write_text(
            json.dumps({"session_id": "healthy", "messages": [{"role": "user"}]}),
            encoding="utf-8",
        )
        status = inspect_session_recovery_status(session_file)
        self.assertEqual(status["recommend"], "no_action")
        self.assertEqual(status["live_messages"], 2)
        self.assertEqual(status["bak_messages"], 1)

    def test_inspect_status_recommends_restore_on_data_loss(self):
        """Inspect recommends 'restore' when live messages dropped unexpectedly."""
        session_file = self.test_dir / "shrunken.json"
        bak_file = self.test_dir / "shrunken.json.bak"
        session_file.write_text(
            json.dumps({"session_id": "shrunken", "messages": []}),
            encoding="utf-8",
        )
        bak_file.write_text(
            json.dumps({"session_id": "shrunken", "messages": [{"role": "user"}, {"role": "assistant"}]}),
            encoding="utf-8",
        )
        status = inspect_session_recovery_status(session_file)
        self.assertEqual(status["recommend"], "restore")
        self.assertEqual(status["live_messages"], 0)
        self.assertEqual(status["bak_messages"], 2)

    def test_recover_session_noop_when_not_recommended(self):
        """recover_session returns restored: False when recovery is not recommended."""
        session_file = self.test_dir / "healthy.json"
        session_file.write_text(json.dumps({"session_id": "healthy", "messages": [{"role": "user"}]}), encoding="utf-8")
        result = recover_session(session_file)
        self.assertFalse(result["restored"])

    def test_recover_session_restores_file_from_bak(self):
        """recover_session atomically restores live session file from .bak."""
        session_file = self.test_dir / "lost.json"
        bak_file = self.test_dir / "lost.json.bak"
        session_file.write_text(json.dumps({"session_id": "lost", "messages": []}), encoding="utf-8")
        bak_data = {
            "session_id": "lost",
            "messages": [
                {"role": "user", "content": "important prompt"},
                {"role": "assistant", "content": "crucial response"},
            ],
        }
        bak_file.write_text(json.dumps(bak_data), encoding="utf-8")

        result = recover_session(session_file)
        self.assertTrue(result["restored"])
        self.assertEqual(result["live_messages"], 0)
        self.assertEqual(result["bak_messages"], 2)

        # Verify live file now contains restored data
        restored_data = json.loads(session_file.read_text(encoding="utf-8"))
        self.assertEqual(len(restored_data["messages"]), 2)
        self.assertEqual(restored_data["messages"][0]["content"], "important prompt")

    def test_recover_all_sessions_on_startup_empty(self):
        """recover_all_sessions_on_startup handles non-existent or empty dir cleanly."""
        res = recover_all_sessions_on_startup(self.test_dir / "non_existent")
        self.assertEqual(res["scanned"], 0)
        self.assertEqual(res["restored"], 0)

        empty_dir = self.test_dir / "empty"
        empty_dir.mkdir()
        res = recover_all_sessions_on_startup(empty_dir)
        self.assertEqual(res["scanned"], 0)
        self.assertEqual(res["restored"], 0)

    def test_recover_all_sessions_on_startup_restores_shrunken(self):
        """recover_all_sessions_on_startup scans dir and restores shrunken session."""
        session_dir = self.test_dir / "sessions"
        session_dir.mkdir()

        # Session 1: healthy
        s1 = session_dir / "s1.json"
        s1.write_text(json.dumps({"session_id": "s1", "messages": [{"role": "user"}]}), encoding="utf-8")

        # Session 2: shrunken with backup
        s2 = session_dir / "s2.json"
        s2_bak = session_dir / "s2.json.bak"
        s2.write_text(json.dumps({"session_id": "s2", "messages": []}), encoding="utf-8")
        s2_bak.write_text(
            json.dumps({"session_id": "s2", "messages": [{"role": "user"}, {"role": "assistant"}]}),
            encoding="utf-8",
        )

        res = recover_all_sessions_on_startup(session_dir, rebuild_index=False)
        self.assertEqual(res["restored"], 1)
        self.assertGreaterEqual(res["scanned"], 2)

        # Confirm s2 was restored
        recovered_s2 = json.loads(s2.read_text(encoding="utf-8"))
        self.assertEqual(len(recovered_s2["messages"]), 2)

    def test_audit_session_recovery_clean(self):
        """audit_session_recovery reports ok status on healthy directory."""
        session_dir = self.test_dir / "sessions"
        session_dir.mkdir()

        s1 = session_dir / "s1.json"
        s1.write_text(json.dumps({"session_id": "s1", "messages": [{"role": "user"}]}), encoding="utf-8")

        audit = audit_session_recovery(session_dir)
        self.assertEqual(audit["status"], "ok")
        self.assertEqual(audit["summary"]["repairable"], 0)
        self.assertEqual(audit["summary"]["unsafe_to_repair"], 0)

    def test_repair_safe_session_recovery(self):
        """repair_safe_session_recovery fixes repairable session data and reports clean."""
        session_dir = self.test_dir / "sessions"
        session_dir.mkdir()

        s1 = session_dir / "s1.json"
        s1_bak = session_dir / "s1.json.bak"
        s1.write_text(json.dumps({"session_id": "s1", "messages": []}), encoding="utf-8")
        s1_bak.write_text(
            json.dumps({"session_id": "s1", "messages": [{"role": "user"}]}),
            encoding="utf-8",
        )

        repair = repair_safe_session_recovery(session_dir)
        self.assertTrue(repair["clean"])
        self.assertEqual(repair["repaired"], 1)


if __name__ == "__main__":
    unittest.main()
