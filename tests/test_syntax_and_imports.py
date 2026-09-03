"""Test syntax compilation and core module imports for WebUI."""
import os
import sys
import unittest
import py_compile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WEBUI_DIR = REPO_ROOT / "webui"

# Ensure webui directory is on sys.path
if str(WEBUI_DIR) not in sys.path:
    sys.path.insert(0, str(WEBUI_DIR))


class TestSyntaxAndImports(unittest.TestCase):
    """Ensure all python files compile without syntax errors and import cleanly."""

    def test_all_python_files_compile(self):
        """Walk webui and verify py_compile succeeds for every .py file."""
        py_files = list(WEBUI_DIR.glob("**/*.py"))
        self.assertGreater(len(py_files), 10, "Should find at least 10 python files")
        
        for p in py_files:
            with self.subTest(file=str(p.relative_to(REPO_ROOT))):
                try:
                    py_compile.compile(str(p), doraise=True)
                except py_compile.PyCompileError as e:
                    self.fail(f"Syntax error compiling {p}: {e}")

    def test_core_module_imports(self):
        """Verify core WebUI modules import cleanly without unhandled exceptions."""
        core_modules = [
            "api.config",
            "api.models",
            "api.agent_health",
            "api.updates",
            "api.artifacts",
            "api.subagents",
            "api.workspace",
            "api.onboarding",
            "api.plugins",
            "api.commands",
            "run_agent",
        ]
        for mod_name in core_modules:
            with self.subTest(module=mod_name):
                try:
                    __import__(mod_name)
                except Exception as e:
                    self.fail(f"Failed to import {mod_name}: {e}")


if __name__ == "__main__":
    unittest.main()
