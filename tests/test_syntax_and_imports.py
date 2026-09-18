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
            "api.vault",
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


    def test_static_scripts_syntax_and_integrity(self):
        """Verify that all static JS files exist, compile via Node, and are correctly referenced in index.html."""
        import re
        import subprocess

        static_dir = WEBUI_DIR / "static"
        js_files = list(static_dir.glob("*.js"))
        self.assertGreater(len(js_files), 15, "Should find at least 15 JavaScript files in static/")

        for js_file in js_files:
            with self.subTest(file=js_file.name):
                proc = subprocess.run(["node", "-c", str(js_file)], capture_output=True, text=True)
                self.assertEqual(proc.returncode, 0, f"JS syntax error in {js_file.name}: {proc.stderr}")

        # Check index.html script tags
        index_html = (static_dir / "index.html").read_text(encoding="utf-8")
        script_srcs = re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', index_html)
        for src in script_srcs:
            if src.startswith("http://") or src.startswith("https://"):
                continue
            # Strip query params
            clean_src = src.split("?")[0]
            if clean_src.startswith("static/"):
                clean_src = clean_src.replace("static/", "", 1)
            target = static_dir / clean_src
            with self.subTest(script_src=src):
                self.assertTrue(target.exists(), f"index.html references non-existent script: {src} -> {target}")

    def test_static_scripts_no_lexical_scope_collisions(self):
        """Verify that scripts loaded in index.html have no top-level lexical declaration collisions (let/const/class)."""
        import re
        import subprocess

        static_dir = WEBUI_DIR / "static"
        index_html = (static_dir / "index.html").read_text(encoding="utf-8")
        script_srcs = re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', index_html)

        local_files = []
        for src in script_srcs:
            if src.startswith("http://") or src.startswith("https://"):
                continue
            clean_src = src.split("?")[0]
            if clean_src.startswith("static/"):
                clean_src = clean_src.replace("static/", "", 1)
            target = static_dir / clean_src
            if target.exists() and target.suffix == ".js":
                local_files.append(str(target))

        node_checker = """
        const fs = require('fs');
        const vm = require('vm');
        const files = process.argv.slice(1);
        let combined = '';
        for (const file of files) {
            combined += '\\n/* --- ' + file + ' --- */\\n' + fs.readFileSync(file, 'utf8');
        }
        try {
            new vm.Script(combined);
            process.exit(0);
        } catch (err) {
            console.error(err.message);
            process.exit(1);
        }
        """
        proc = subprocess.run(
            ["node", "-e", node_checker, *local_files],
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            proc.returncode,
            0,
            f"Lexical scope collision among static scripts in index.html: {proc.stderr}",
        )

    def test_api_js_module(self):
        """Verify api.js exports apiFetch and ApiError and behaves as expected in Node."""
        import subprocess

        test_node_script = """
        const { apiFetch, ApiError } = require('./webui/static/api.js');
        if (typeof apiFetch !== 'function') throw new Error('apiFetch is not a function');
        if (typeof ApiError !== 'function') throw new Error('ApiError is not a constructor');
        
        const err = new ApiError('Not Found', 404, 'Not Found', '/api/test', '{"error":"missing"}', { error: 'missing' });
        if (err.status !== 404) throw new Error('ApiError status mismatch: ' + err.status);
        if (err.name !== 'ApiError') throw new Error('ApiError name mismatch: ' + err.name);
        if (!err.data || err.data.error !== 'missing') throw new Error('ApiError data mismatch');
        
        console.log('API_JS_OK');
        """
        proc = subprocess.run(["node", "-e", test_node_script], cwd=str(REPO_ROOT), capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, f"Node verification failed: {proc.stderr}")
        self.assertIn("API_JS_OK", proc.stdout)


    def test_ui_theme_js_module(self):
        """Verify ui-theme.js exports expected theme functions and normalizes properly."""
        import subprocess

        test_node_script = """
        const themeMod = require('./webui/static/ui-theme.js');
        const { _THEMES, _SKINS, _normalizeAppearance, _sanitizeSkinTokens, _sanitizeSkinScheme } = themeMod;
        
        if (!Array.isArray(_THEMES) || _THEMES.length < 3) throw new Error('Invalid _THEMES');
        if (!Array.isArray(_SKINS) || _SKINS.length < 4) throw new Error('Invalid _SKINS');
        
        const norm = _normalizeAppearance('light', 'slate');
        if (norm.theme !== 'light' || norm.skin !== 'slate') throw new Error('Appearance normalization failed');
        
        const normLegacy = _normalizeAppearance('solarized', '');
        if (normLegacy.theme !== 'dark' || normLegacy.skin !== 'slate') throw new Error('Legacy appearance mapping failed');
        
        const cleanScheme = _sanitizeSkinScheme('DARK');
        if (cleanScheme !== 'dark') throw new Error('Scheme sanitization failed');
        
        const tokens = _sanitizeSkinTokens({ '--bg': '#121212', '--invalid': 'expression(bad)', '--accent': 'rgb(255, 0, 0)' });
        if (tokens['--bg'] !== '#121212' || tokens['--accent'] !== 'rgb(255, 0, 0)' || tokens['--invalid']) {
            throw new Error('Token sanitization failed: ' + JSON.stringify(tokens));
        }
        
        console.log('UI_THEME_JS_OK');
        """
        proc = subprocess.run(["node", "-e", test_node_script], cwd=str(REPO_ROOT), capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, f"Node verification failed: {proc.stderr}")
        self.assertIn("UI_THEME_JS_OK", proc.stdout)


    def test_ui_notifications_js_module(self):
        """Verify ui-notifications.js exports expected notification and dialog functions and constants."""
        import subprocess

        test_node_script = """
        const notifMod = require('./webui/static/ui-notifications.js');
        const { TOAST_DEFAULT_MS, TOAST_ERROR_DEFAULT_MS, showToast, dismissToast, showConfirmDialog, showPromptDialog, showAlertDialog } = notifMod;
        
        if (typeof TOAST_DEFAULT_MS !== 'number' || TOAST_DEFAULT_MS !== 2800) throw new Error('Invalid TOAST_DEFAULT_MS');
        if (typeof TOAST_ERROR_DEFAULT_MS !== 'number' || TOAST_ERROR_DEFAULT_MS !== 20000) throw new Error('Invalid TOAST_ERROR_DEFAULT_MS');
        if (typeof showToast !== 'function') throw new Error('showToast is not a function');
        if (typeof dismissToast !== 'function') throw new Error('dismissToast is not a function');
        if (typeof showConfirmDialog !== 'function') throw new Error('showConfirmDialog is not a function');
        if (typeof showPromptDialog !== 'function') throw new Error('showPromptDialog is not a function');
        if (typeof showAlertDialog !== 'function') throw new Error('showAlertDialog is not a function');
        
        console.log('UI_NOTIFICATIONS_JS_OK');
        """
        proc = subprocess.run(["node", "-e", test_node_script], cwd=str(REPO_ROOT), capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, f"Node verification failed: {proc.stderr}")
        self.assertIn("UI_NOTIFICATIONS_JS_OK", proc.stdout)


    def test_ui_composer_js_module(self):
        """Verify ui-composer.js exports expected composer methods, paste thresholds, and action helpers."""
        import subprocess

        test_node_script = """
        const composerMod = require('./webui/static/ui-composer.js');
        const {
            LARGE_TEXT_PASTE_CHAR_THRESHOLD,
            LARGE_TEXT_PASTE_LINE_THRESHOLD,
            setComposerStatus,
            lockComposerForClarify,
            unlockComposerForClarify,
            getComposerPrimaryAction,
            updateSendBtn,
            setBusy,
            autoResizeTextarea,
            _isImeEnter,
            _largeTextPasteLineCount,
            _shouldAttachLargePastedText,
            _largeTextPasteFileName
        } = composerMod;
        
        if (LARGE_TEXT_PASTE_CHAR_THRESHOLD !== 4000) throw new Error('Invalid LARGE_TEXT_PASTE_CHAR_THRESHOLD');
        if (LARGE_TEXT_PASTE_LINE_THRESHOLD !== 100) throw new Error('Invalid LARGE_TEXT_PASTE_LINE_THRESHOLD');
        if (typeof setComposerStatus !== 'function') throw new Error('setComposerStatus missing');
        if (typeof lockComposerForClarify !== 'function') throw new Error('lockComposerForClarify missing');
        if (typeof unlockComposerForClarify !== 'function') throw new Error('unlockComposerForClarify missing');
        if (typeof getComposerPrimaryAction !== 'function') throw new Error('getComposerPrimaryAction missing');
        if (typeof updateSendBtn !== 'function') throw new Error('updateSendBtn missing');
        if (typeof setBusy !== 'function') throw new Error('setBusy missing');
        if (typeof autoResizeTextarea !== 'function') throw new Error('autoResizeTextarea missing');
        
        if (_largeTextPasteLineCount('a\\nb\\nc') !== 3) throw new Error('_largeTextPasteLineCount mismatch');
        if (!_largeTextPasteFileName(1234567890).startsWith('pasted-text-')) throw new Error('_largeTextPasteFileName format error');
        if (_isImeEnter({ isComposing: false, keyCode: 229 }) !== true) throw new Error('_isImeEnter keyCode 229 check failed');
        if (_isImeEnter({ isComposing: true, keyCode: 13 }) !== true) throw new Error('_isImeEnter isComposing check failed');
        if (_isImeEnter({ isComposing: false, keyCode: 13 }) !== false) throw new Error('_isImeEnter plain Enter check failed');
        
        console.log('UI_COMPOSER_JS_OK');
        """
        proc = subprocess.run(["node", "-e", test_node_script], cwd=str(REPO_ROOT), capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, f"Node verification failed: {proc.stderr}")
        self.assertIn("UI_COMPOSER_JS_OK", proc.stdout)


if __name__ == "__main__":
    unittest.main()

