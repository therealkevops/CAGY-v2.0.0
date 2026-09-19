"""Comprehensive hermetic unit tests for WebUI authentication and security.

Covers:
- Password hashing (PBKDF2-SHA256), verification, caching, and salt migration
- Session lifecycle: generation, HMAC signing, validation, expiration, and revocation
- CSRF protection: token derivation, constant-time validation, session binding
- Cookie security: HttpOnly, SameSite=Lax, Secure flag contexts, name resolution
- Rate limiting: brute-force mitigation, IP isolation, and attempt clearing
- Profile binding: session-bound profile signatures and anti-tampering
- Request authorization (check_auth): public paths, 401 API errors, safe 302 redirects
"""
import hashlib
import hmac
import http.cookies
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
WEBUI_DIR = REPO_ROOT / "webui"
if str(WEBUI_DIR) not in sys.path:
    sys.path.insert(0, str(WEBUI_DIR))

import api.auth as auth
from api.auth import (
    COOKIE_NAME,
    LEGACY_COOKIE_NAME,
    PUBLIC_PATHS,
    SESSION_TTL,
    _auth_cookie_header,
    _check_login_rate,
    _clear_auth_cookie_header,
    _clear_login_attempts,
    _hash_password,
    _invalidate_password_hash_cache,
    _is_secure_context,
    _load_login_attempts,
    _load_sessions,
    _record_login_attempt,
    _resolve_cookie_name,
    _resolve_session_ttl,
    _safe_login_inner_next,
    _save_login_attempts,
    _save_sessions,
    check_auth,
    create_session,
    csrf_token_for_session,
    get_password_hash,
    get_session_info,
    invalidate_session,
    is_auth_enabled,
    is_password_auth_enabled,
    parse_cookie,
    sign_profile_cookie_value,
    verify_csrf_token,
    verify_password,
    verify_profile_cookie_value,
    verify_session,
)


class MockSocket:
    """Mock socket object for peer cert checks."""

    def __init__(self, cert=None):
        self._cert = cert

    def getpeercert(self):
        return self._cert


class MockHandler:
    """Mock HTTP request handler for check_auth and cookie tests."""

    def __init__(self, headers=None, client_address=("127.0.0.1", 12345), secure_socket=False):
        self.headers = headers or {}
        self.client_address = client_address
        self.status = None
        self.response_headers = {}
        self.wfile = io.BytesIO()
        self.request = MockSocket(cert={"subject": "test"} if secure_socket else None)
        self._pending_set_cookies = []

    def send_response(self, code):
        self.status = code

    def send_header(self, k, v):
        self.response_headers[k] = v

    def end_headers(self):
        pass


class BaseAuthTestCase(unittest.TestCase):
    """Base test case providing an isolated temporary state directory and clean auth caches."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.state_dir = Path(self.temp_dir) / "state"
        self.state_dir.mkdir(parents=True, exist_ok=True)

        # Patch STATE_DIR and related file targets in auth module
        self.orig_state_dir = auth.STATE_DIR
        self.orig_sessions_file = auth._SESSIONS_FILE
        self.orig_login_file = auth._LOGIN_ATTEMPTS_FILE

        auth.STATE_DIR = self.state_dir
        auth._SESSIONS_FILE = self.state_dir / ".sessions.json"
        auth._LOGIN_ATTEMPTS_FILE = self.state_dir / ".login_attempts.json"

        # Clear in-memory session table and login attempts
        with auth._SESSIONS_LOCK:
            auth._sessions.clear()
        with auth._LOGIN_ATTEMPTS_LOCK:
            auth._login_attempts.clear()

        # Reset caches
        auth._PBKDF2_KEY_CACHE = None
        auth._SIGNING_KEY_CACHE = None
        _invalidate_password_hash_cache()

        # Clean environment variables
        self.env_patcher = patch.dict(
            os.environ,
            {
                "AGY_WEBUI_STATE_DIR": str(self.state_dir),
                "HERMES_WEBUI_STATE_DIR": str(self.state_dir),
            },
            clear=False,
        )
        self.env_patcher.start()

    def tearDown(self):
        self.env_patcher.stop()

        auth.STATE_DIR = self.orig_state_dir
        auth._SESSIONS_FILE = self.orig_sessions_file
        auth._LOGIN_ATTEMPTS_FILE = self.orig_login_file

        with auth._SESSIONS_LOCK:
            auth._sessions.clear()
        with auth._LOGIN_ATTEMPTS_LOCK:
            auth._login_attempts.clear()

        auth._PBKDF2_KEY_CACHE = None
        auth._SIGNING_KEY_CACHE = None
        _invalidate_password_hash_cache()

        shutil.rmtree(self.temp_dir, ignore_errors=True)


class TestPasswordSecurity(BaseAuthTestCase):
    """Test PBKDF2 password hashing, verification, caching, and salt migration."""

    def test_hash_password_format_and_determinism(self):
        hash1 = _hash_password("SuperSecret123!")
        self.assertEqual(len(hash1), 64, "PBKDF2-SHA256 hex digest must be 64 characters")
        hash2 = _hash_password("SuperSecret123!")
        self.assertEqual(hash1, hash2, "Identical password and salt must produce identical hash")
        diff_hash = _hash_password("AnotherPassword!")
        self.assertNotEqual(hash1, diff_hash, "Different passwords must produce different hashes")

    def test_verify_password_with_env_var(self):
        with patch.dict(os.environ, {"AGY_WEBUI_PASSWORD": "CorrectHorseBatteryStaple"}):
            _invalidate_password_hash_cache()
            self.assertTrue(verify_password("CorrectHorseBatteryStaple"))
            self.assertFalse(verify_password("WrongPassword"))
            self.assertFalse(verify_password(""))

    def test_verify_password_when_auth_disabled(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("api.auth.load_settings", return_value={}):
                _invalidate_password_hash_cache()
                self.assertFalse(is_password_auth_enabled())
                self.assertFalse(verify_password("AnyPassword"))

    def test_password_hash_caching_and_invalidation(self):
        with patch.dict(os.environ, {"AGY_WEBUI_PASSWORD": "InitialPassword"}):
            _invalidate_password_hash_cache()
            h1 = get_password_hash()
            self.assertIsNotNone(h1)

            # Change env var without invalidation: cached hash should still return
            with patch.dict(os.environ, {"AGY_WEBUI_PASSWORD": "UpdatedPassword"}):
                h2 = get_password_hash()
                self.assertEqual(h1, h2, "Hash must be cached across calls")

                # After invalidation, new hash must be computed
                _invalidate_password_hash_cache()
                h3 = get_password_hash()
                self.assertNotEqual(h1, h3, "Invalidating cache must force re-computation")

    def test_legacy_salt_migration(self):
        # Generate two distinct keys: legacy signing key and current pbkdf2 key
        legacy_salt = b"legacy_salt_key_0123456789012345"
        current_salt = b"current_pbkdf2_key_0123456789012"

        pw = "MigrateMeNow123"
        legacy_hash = _hash_password(pw, salt=legacy_salt)

        auth._PBKDF2_KEY_CACHE = current_salt
        auth._SIGNING_KEY_CACHE = legacy_salt

        with patch("api.auth.get_password_hash", return_value=legacy_hash):
            with patch("api.config.save_settings") as mock_save:
                matched = verify_password(pw)
                self.assertTrue(matched, "Legacy salted hash must verify successfully")
                mock_save.assert_called_once_with({"_set_password": pw})


class TestSessionLifecycle(BaseAuthTestCase):
    """Test session creation, HMAC signature verification, expiry, and revocation."""

    def test_create_and_verify_session(self):
        cookie_val = create_session()
        self.assertIn(".", cookie_val)
        parts = cookie_val.split(".")
        self.assertEqual(len(parts), 2)
        self.assertEqual(len(parts[0]), 64, "Token should be 32 bytes hex (64 chars)")

        self.assertTrue(verify_session(cookie_val))

        info = get_session_info(cookie_val)
        self.assertIsNotNone(info)
        self.assertEqual(info["token"], parts[0])
        self.assertGreater(info["expiry"], time.time())

    def test_create_session_with_metadata(self):
        cookie_val = create_session(auth_type="oidc", username="alice", bound_profile="dev-profile")
        self.assertTrue(verify_session(cookie_val))
        info = get_session_info(cookie_val)
        self.assertEqual(info.get("auth_type"), "oidc")
        self.assertEqual(info.get("username"), "alice")
        self.assertEqual(info.get("bound_profile"), "dev-profile")

    def test_verify_session_invalid_format(self):
        self.assertFalse(verify_session(""))
        self.assertFalse(verify_session(None))
        self.assertFalse(verify_session("no_dot_in_cookie"))
        self.assertFalse(verify_session("invalid.token.multiple.dots"))

    def test_verify_session_tampering(self):
        cookie_val = create_session()
        token, sig = cookie_val.split(".")

        # Tampered token
        tampered_token = ("a" if token[0] != "a" else "b") + token[1:]
        self.assertFalse(verify_session(f"{tampered_token}.{sig}"))

        # Tampered signature
        tampered_sig = ("0" if sig[0] != "0" else "1") + sig[1:]
        self.assertFalse(verify_session(f"{token}.{tampered_sig}"))

    def test_session_expiry_and_pruning(self):
        cookie_val = create_session()
        token, _ = cookie_val.split(".")

        # Artificially expire the session in memory
        with auth._SESSIONS_LOCK:
            auth._sessions[token] = time.time() - 100

        self.assertFalse(verify_session(cookie_val), "Expired session must fail verification")
        with auth._SESSIONS_LOCK:
            self.assertNotIn(token, auth._sessions, "Expired session must be pruned")

    def test_invalidate_session(self):
        cookie_val = create_session()
        self.assertTrue(verify_session(cookie_val))

        invalidate_session(cookie_val)
        self.assertFalse(verify_session(cookie_val), "Invalidated session must fail verification")
        self.assertIsNone(get_session_info(cookie_val))

    def test_session_persistence_roundtrip(self):
        cookie_val = create_session(username="bob")
        token = cookie_val.split(".")[0]

        # Ensure sessions file exists on disk
        self.assertTrue(auth._SESSIONS_FILE.exists())

        # Load fresh from disk
        loaded = _load_sessions()
        self.assertIn(token, loaded)
        self.assertEqual(loaded[token]["username"], "bob")

    def test_resolve_session_ttl_clamping(self):
        # Default
        self.assertEqual(_resolve_session_ttl(), SESSION_TTL)

        # Valid env override
        with patch.dict(os.environ, {"AGY_WEBUI_SESSION_TTL": "7200"}):
            self.assertEqual(_resolve_session_ttl(), 7200)

        # Under minimum (< 60s) -> fallback to default
        with patch.dict(os.environ, {"AGY_WEBUI_SESSION_TTL": "30"}):
            self.assertEqual(_resolve_session_ttl(), SESSION_TTL)

        # Over maximum (> 1 year) -> fallback to default
        with patch.dict(os.environ, {"AGY_WEBUI_SESSION_TTL": str(86400 * 400)}):
            self.assertEqual(_resolve_session_ttl(), SESSION_TTL)


class TestCSRFProtection(BaseAuthTestCase):
    """Test CSRF token derivation, constant-time verification, and session binding."""

    def test_csrf_token_generation_and_verification(self):
        cookie_val = create_session()
        csrf_token = csrf_token_for_session(cookie_val)
        self.assertIsNotNone(csrf_token)
        self.assertEqual(len(csrf_token), 64, "CSRF token must be 64-char hex string")

        # Matching token verifies True
        self.assertTrue(verify_csrf_token(cookie_val, csrf_token))

    def test_csrf_token_mismatch(self):
        cookie_val = create_session()
        fake_token = "0" * 64
        self.assertFalse(verify_csrf_token(cookie_val, fake_token))
        self.assertFalse(verify_csrf_token(cookie_val, ""))
        self.assertFalse(verify_csrf_token(cookie_val, None))

    def test_csrf_token_unauthenticated_session(self):
        # Valid signature format but session does not exist in store
        fake_token = "a" * 64
        sig = hmac.new(auth._signing_key(), fake_token.encode(), hashlib.sha256).hexdigest()
        fake_cookie = f"{fake_token}.{sig}"

        csrf = csrf_token_for_session(fake_cookie)
        self.assertFalse(verify_csrf_token(fake_cookie, csrf or "dummy"))

    def test_csrf_token_whitespace_handling(self):
        cookie_val = create_session()
        csrf_token = csrf_token_for_session(cookie_val)
        # Leading/trailing whitespace should still verify
        self.assertTrue(verify_csrf_token(cookie_val, f"  {csrf_token}  \n"))

    def test_csrf_tokens_distinct_across_sessions(self):
        cookie1 = create_session()
        cookie2 = create_session()

        csrf1 = csrf_token_for_session(cookie1)
        csrf2 = csrf_token_for_session(cookie2)

        self.assertNotEqual(csrf1, csrf2)
        # Token 1 cannot authenticate session 2
        self.assertFalse(verify_csrf_token(cookie2, csrf1))
        self.assertFalse(verify_csrf_token(cookie1, csrf2))


class TestCookieSecurity(BaseAuthTestCase):
    """Test cookie header creation, security attributes, and name resolution."""

    def test_auth_cookie_header_attributes(self):
        cookie_val = create_session()
        header = _auth_cookie_header(cookie_val)

        self.assertIn("HttpOnly", header)
        self.assertIn("SameSite=Lax", header)
        self.assertIn("Path=/", header)
        self.assertIn("Max-Age=", header)
        self.assertIn(f"{COOKIE_NAME}={cookie_val}", header)

    def test_auth_cookie_secure_flag_in_secure_context(self):
        cookie_val = create_session()

        # Insecure context -> no Secure flag
        header_insecure = _auth_cookie_header(cookie_val, handler=None)
        self.assertNotIn("Secure", header_insecure)

        # Secure via env var
        with patch.dict(os.environ, {"AGY_WEBUI_SECURE": "1"}):
            self.assertTrue(_is_secure_context(None))
            header_secure = _auth_cookie_header(cookie_val, handler=None)
            self.assertIn("Secure", header_secure)

        # Secure via X-Forwarded-Proto
        with patch.dict(os.environ, {"AGY_WEBUI_TRUST_FORWARDED_PROTO": "1"}):
            handler = MockHandler(headers={"X-Forwarded-Proto": "https"})
            self.assertTrue(_is_secure_context(handler))
            header_fwd = _auth_cookie_header(cookie_val, handler=handler)
            self.assertIn("Secure", header_fwd)

    def test_clear_auth_cookie_header(self):
        clear_header = _clear_auth_cookie_header()
        self.assertIn("Max-Age=0", clear_header)
        self.assertIn("HttpOnly", clear_header)
        self.assertIn(f"{COOKIE_NAME}=", clear_header)

    def test_parse_cookie_standard_and_legacy(self):
        handler = MockHandler(headers={"Cookie": "agy_session=session_token_123; other=val"})
        self.assertEqual(parse_cookie(handler), "session_token_123")

        # Legacy fallback
        legacy_handler = MockHandler(headers={"Cookie": "hermes_session=legacy_token_456"})
        self.assertEqual(parse_cookie(legacy_handler), "legacy_token_456")

        # Empty or missing
        empty_handler = MockHandler(headers={})
        self.assertIsNone(parse_cookie(empty_handler))

    def test_resolve_cookie_name_custom_and_fallback(self):
        # Default
        self.assertEqual(_resolve_cookie_name(), COOKIE_NAME)

        # Valid custom
        with patch.dict(os.environ, {"AGY_WEBUI_COOKIE_NAME": "custom_cagy_cookie"}):
            self.assertEqual(_resolve_cookie_name(), "custom_cagy_cookie")

        # Invalid token (contains semicolon) -> fallback to default
        with patch.dict(os.environ, {"AGY_WEBUI_COOKIE_NAME": "bad;cookie=name"}):
            self.assertEqual(_resolve_cookie_name(), COOKIE_NAME)


class TestLoginRateLimiting(BaseAuthTestCase):
    """Test login attempt throttling and brute-force mitigation."""

    def test_login_rate_limiting_flow(self):
        ip = "192.168.1.100"

        # First 5 attempts allowed
        for _ in range(5):
            self.assertTrue(_check_login_rate(ip))
            _record_login_attempt(ip)

        # 6th attempt blocked
        self.assertFalse(_check_login_rate(ip))

        # Successful login clears attempts
        _clear_login_attempts(ip)
        self.assertTrue(_check_login_rate(ip))

    def test_login_rate_limiting_ip_isolation(self):
        ip1 = "10.0.0.1"
        ip2 = "10.0.0.2"

        for _ in range(5):
            _record_login_attempt(ip1)

        self.assertFalse(_check_login_rate(ip1), "IP 1 must be blocked")
        self.assertTrue(_check_login_rate(ip2), "IP 2 must not be affected by IP 1")

    def test_login_attempts_persistence(self):
        ip = "172.16.0.5"
        _record_login_attempt(ip)
        _record_login_attempt(ip)

        self.assertTrue(auth._LOGIN_ATTEMPTS_FILE.exists())
        loaded = _load_login_attempts()
        self.assertIn(ip, loaded)
        self.assertEqual(len(loaded[ip]), 2)


class TestProfileCookieBinding(BaseAuthTestCase):
    """Test session-bound profile signatures protecting against cross-profile forging."""

    def test_sign_and_verify_profile_cookie(self):
        cookie_val = create_session()
        signed_profile = sign_profile_cookie_value("default", cookie_val)
        self.assertIn(".", signed_profile)

        verified = verify_profile_cookie_value(signed_profile, cookie_val)
        self.assertEqual(verified, "default")

    def test_tampered_profile_cookie_rejected(self):
        cookie_val = create_session()
        signed_profile = sign_profile_cookie_value("default", cookie_val)

        # Tamper profile name
        tampered = "admin." + signed_profile.split(".")[1]
        self.assertIsNone(verify_profile_cookie_value(tampered, cookie_val))

    def test_cross_session_profile_cookie_rejected(self):
        session1 = create_session()
        session2 = create_session()

        signed_for_1 = sign_profile_cookie_value("default", session1)
        # Attempting to use cookie signed for session 1 against session 2
        self.assertIsNone(verify_profile_cookie_value(signed_for_1, session2))


class TestAuthDispatchAndRedirects(BaseAuthTestCase):
    """Test check_auth endpoint gating, public paths, and redirect safety."""

    def test_public_paths_accessible_without_auth(self):
        for path in ("/login", "/health", "/favicon.ico", "/sw.js", "/manifest.json"):
            self.assertIn(path, PUBLIC_PATHS)

    def test_safe_login_inner_next(self):
        # Valid relative paths
        self.assertEqual(_safe_login_inner_next("next=/workspace"), "/workspace")
        self.assertEqual(_safe_login_inner_next("next=/settings?tab=general"), "/settings?tab=general")

        # Open redirect attacks
        self.assertEqual(_safe_login_inner_next("next=//evil.com"), "")
        self.assertEqual(_safe_login_inner_next("next=/\\evil.com"), "")
        self.assertEqual(_safe_login_inner_next("next=http://evil.com"), "")

        # Control characters and nested login loops
        self.assertEqual(_safe_login_inner_next("next=/test%0aevil"), "")
        self.assertEqual(_safe_login_inner_next("next=/login"), "")
        self.assertEqual(_safe_login_inner_next("next=/session/login"), "")

    def test_check_auth_when_disabled(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("api.auth.load_settings", return_value={}):
                _invalidate_password_hash_cache()
                handler = MockHandler()
                parsed = urllib.parse.urlparse("/api/sessions")
                self.assertTrue(check_auth(handler, parsed))

    def test_check_auth_api_unauthorized_returns_401(self):
        with patch.dict(os.environ, {"AGY_WEBUI_PASSWORD": "SecretPassword123"}):
            _invalidate_password_hash_cache()
            handler = MockHandler()
            parsed = urllib.parse.urlparse("/api/sessions")

            res = check_auth(handler, parsed)
            self.assertFalse(res)
            self.assertEqual(handler.status, 401)
            self.assertEqual(handler.response_headers.get("Content-Type"), "application/json")
            body = json.loads(handler.wfile.getvalue().decode())
            self.assertEqual(body.get("error"), "Authentication required")

    def test_check_auth_page_unauthorized_returns_302(self):
        with patch.dict(os.environ, {"AGY_WEBUI_PASSWORD": "SecretPassword123"}):
            _invalidate_password_hash_cache()
            handler = MockHandler()
            parsed = urllib.parse.urlparse("/workspace")

            res = check_auth(handler, parsed)
            self.assertFalse(res)
            self.assertEqual(handler.status, 302)
            self.assertIn("login?next=", handler.response_headers.get("Location", ""))

    def test_check_auth_authorized_with_valid_session(self):
        with patch.dict(os.environ, {"AGY_WEBUI_PASSWORD": "SecretPassword123"}):
            _invalidate_password_hash_cache()
            cookie_val = create_session()
            handler = MockHandler(headers={"Cookie": f"agy_session={cookie_val}"})
            parsed = urllib.parse.urlparse("/api/sessions")

            self.assertTrue(check_auth(handler, parsed))


if __name__ == "__main__":
    unittest.main()
