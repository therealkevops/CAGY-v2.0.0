# ADR 011: Authentication, CSRF & Security Hardening

- **Date**: 2026-09-19
- **Status**: Accepted
- **Tags**: #decisions #adr #security #auth #csrf #cookies #ratelimiting #testing

## Context & Problem Statement
`webui/api/auth.py` (1,250+ lines) serves as the primary security gateway for the Containerized Antigravity WebUI:
1. **Password Security**: PBKDF2-SHA256 (600k iterations) hashing, verification, in-process caching, and transparent migration from legacy salts.
2. **Session Lifecycle**: 32-byte cryptographically secure session tokens (`secrets.token_hex(32)`), HMAC-SHA256 signature verification, TTL enforcement (`_resolve_session_ttl`), lazy expired session pruning, and disk persistence (`.sessions.json`).
3. **CSRF Protection**: Session-bound CSRF token derivation (`csrf_token_for_session`) and constant-time validation (`verify_csrf_token`) paired with frontend `apiFetch` headers (`X-Agy-CSRF-Token`, `X-Hermes-CSRF-Token`).
4. **Cookie Security**: RFC 6265 cookie token validation, `HttpOnly`, `SameSite=Lax`, contextual `Secure` flag detection, and session-bound profile signatures (`sign_profile_cookie_value`, `verify_profile_cookie_value`).
5. **Brute-Force Rate Limiting**: IP-based attempt throttling (`_LOGIN_MAX_ATTEMPTS = 5`, `_LOGIN_WINDOW = 60s`).
6. **Request Authorization (`check_auth`)**: Public path bypasses, API 401 JSON responses, and open-redirect-hardened 302 login redirects.

Despite its critical importance, `auth.py` had **zero dedicated unit tests** in `tests/`, contained multiple broad `except Exception:` swallows that masked underlying IO and formatting issues, and lacked whitespace normalization on incoming CSRF tokens.

## Decision Drivers
- Build a comprehensive, hermetic test suite (`tests/test_auth.py`) that exercises all cryptographic and authorization code paths without relying on external network sockets or third-party identity servers.
- Replace bare `except Exception:` clauses with typed exceptions (`OSError`, `json.JSONDecodeError`, `UnicodeDecodeError`, `ValueError`, `TypeError`), ensuring expected transient conditions are handled cleanly while preserving diagnostic tracebacks for unexpected errors.
- Normalize incoming CSRF tokens to handle proxy/header whitespace variations safely.
- Provide clean cache invalidation hooks (`_reset_auth_caches()`) for key rotation and testing.
- Maintain 100% backward compatibility for existing settings (`settings.json`), session stores (`.sessions.json`), and cookie naming.

## Considered Options
1. **Status Quo (Rely on High-Level Route Tests)**: Continue relying only on indirect coverage from `tests/test_routes_dispatch.py`. (Rejected: Leaves security edge cases like signature tampering, legacy salt migration, brute-force IP isolation, and open redirect traps untested).
2. **External Auth Framework Migration (e.g. Authlib / FastAPI-Users)**: Replace custom auth with an external framework. (Rejected: Disproportionate blast radius; introduces heavy dependencies, breaks container portability, and alters file-based state conventions).
3. **Hermetic Test Harness & Exception Hardening**: Establish a dedicated 35-test unit suite in `tests/test_auth.py`, refine exception types, and normalize validation logic. (Chosen).

## Decision Outcome
**Option 3 was chosen** and implemented across structured sprints:

1. **Sprint A1 — Audit & Architecture Analysis**:
   - Audited all cryptographic primitives, cookie generators, rate limiters, and exception catch blocks in `auth.py`.
   - Identified edge cases: float TTL values in settings, un-trimmed CSRF tokens, un-typed exceptions in key loaders and login attempt persistence.

2. **Sprint A2 — Hermetic Test Suite Implementation (`tests/test_auth.py`)**:
   - Implemented 35 tests organized across 7 test classes:
     - `TestPasswordSecurity`: PBKDF2 format, determinism, env overrides, caching, and legacy salt migration.
     - `TestSessionLifecycle`: Generation, HMAC verification, metadata bindings, tampering rejection, expiration pruning, and persistence roundtrips.
     - `TestCSRFProtection`: Derivation, constant-time validation, mismatch handling, session isolation, and whitespace trimming.
     - `TestCookieSecurity`: Attribute compliance (`HttpOnly`, `SameSite=Lax`, `Path=/`), contextual `Secure` flag detection, and RFC 6265 name validation.
     - `TestLoginRateLimiting`: Attempt throttling, IP isolation, and post-login attempt clearing.
     - `TestProfileCookieBinding`: Session-bound profile signatures and cross-session replay prevention.
     - `TestAuthDispatchAndRedirects`: Public path allowlisting, API 401 JSON responses, and open redirect defenses (`//evil.com`, `/\evil.com`, control characters).

3. **Sprint A3 — Core Hardening & Exception Refinement**:
   - Refined `verify_csrf_token()` to strip whitespace before constant-time comparison.
   - Enhanced `_resolve_session_ttl()` to accept `int` and `float` configurations.
   - Refined `_load_sessions()`, `_save_sessions()`, `_load_login_attempts()`, and `_load_key()` with explicit exception hierarchies (`OSError`, `json.JSONDecodeError`, `ValueError`, `TypeError`).
   - Added `_reset_auth_caches()` for deterministic cache resetting.
   - Added defensive `hasattr(handler, 'headers')` guards in `_is_secure_context()`.

4. **Sprint A4 — Quality Gate & Second Brain Sync**:
   - Executed full test suite: **225/225 tests passing** in `./run-tests.sh` (35 new tests added, zero regressions).
   - Executed `npm run lint` with zero errors across HTML, JS, and Python.
   - Synchronized Knowledge Vault rules into active turn prompts.

## Consequences & Linked Systems
- **Total Test Coverage**: Test suite expanded from 190 to 225 tests, closing the largest security blind spot in the application.
- **Resilience**: Zero silent exception swallowing in session persistence or key generation.
- **Interlinks**: Complements [[decisions/adr_008_frontend_ui_decomposition]] (unified `apiFetch` CSRF token headers), [[decisions/adr_009_centralized_sqlite_architecture]], and [[decisions/adr_010_frontend_layout_modernization]].
