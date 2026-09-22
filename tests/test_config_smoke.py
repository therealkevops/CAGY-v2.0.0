"""Smoke tests for api.config module import, registries, paths, and settings defaults."""
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WEBUI_DIR = REPO_ROOT / "webui"

if str(WEBUI_DIR) not in sys.path:
    sys.path.insert(0, str(WEBUI_DIR))

from api import config


class TestConfigSmoke(unittest.TestCase):
    """Ensure core configuration structures, paths, and registries are intact."""

    def test_config_imports_cleanly(self):
        """api.config imports cleanly and exposes expected module metadata."""
        import types
        self.assertIsInstance(config, types.ModuleType)
        self.assertTrue(hasattr(config, "load_settings"))

    def test_registries_initialized(self):
        """STREAMS, CANCEL_FLAGS, and lock registries are dictionary-like structures."""
        self.assertIsInstance(config.STREAMS, dict)
        self.assertIsInstance(config.CANCEL_FLAGS, dict)
        self.assertTrue(hasattr(config, "STREAMS_LOCK"))

    def test_default_model_and_workspace(self):
        """DEFAULT_MODEL is a string and DEFAULT_WORKSPACE resolves to a valid Path."""
        self.assertIsInstance(config.DEFAULT_MODEL, str)
        self.assertIsInstance(config.DEFAULT_WORKSPACE, (str, Path))
        self.assertTrue(len(str(config.DEFAULT_WORKSPACE)) > 0)

    def test_storage_paths_resolve(self):
        """SESSION_DIR, STATE_DIR, and SETTINGS_FILE resolve to valid Path instances."""
        self.assertIsInstance(config.SESSION_DIR, Path)
        self.assertIsInstance(config.STATE_DIR, Path)
        self.assertIsInstance(config.SETTINGS_FILE, Path)
        self.assertTrue(str(config.SESSION_DIR).endswith("sessions"))

    def test_feature_flags_are_bool(self):
        """Key boolean feature flags in settings defaults are strictly booleans."""
        settings = config.load_settings()
        self.assertIsInstance(settings, dict)
        for flag in [
            "workspace_todos_tab",
            "notifications_enabled",
            "auto_scroll_follow",
            "show_thinking",
            "simplified_tool_calling",
        ]:
            with self.subTest(flag=flag):
                self.assertIn(flag, settings)
                self.assertIsInstance(settings[flag], bool, f"{flag} must be a boolean")


if __name__ == "__main__":
    unittest.main()
