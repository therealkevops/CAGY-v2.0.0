# ADR 012: HTTP Keep-Alive Dispatch Resilience & Stream Desync Prevention

- **Date**: 2026-09-19
- **Status**: Accepted
- **Tags**: #decisions #adr #http #keepalive #routes #server #webui #session #caching

## Context & Problem Statement
Following the GET/POST route decomposition in [[decisions/adr_007_routes_decomposition]], selecting sessions from the WebUI sidebar sporadically failed or rendered a blank/error view ("Session not available in web UI") despite backend data integrity.

### Root Cause Analysis
1. **HTTP Keep-Alive Stream Desynchronization**:
   - The modular sub-dispatchers (`_handle_get_session`, `_handle_get_workspace_and_git`, etc.) invoked response helpers `j()` and `bad()`, which returned `None`.
   - The root dispatcher (`handle_get` / `handle_post`) treated `None` as "route unhandled" and fell through to the terminal `return False`.
   - In `webui/server.py`, `do_GET` and `_handle_write` inspected `if result is False:` and wrote an immediate second response (`HTTP/1.1 404 Not Found {"error": "not found"}`) on the active connection.
   - For HTTP/1.1 persistent (keep-alive) connections, this queued a trailing 404 response in the TCP receive buffer. The browser's subsequent request (e.g. `GET /api/session?session_id=...` triggered by clicking a session) immediately read the stale 404 response instead of its own payload.
   - The client-side `loadSession` in `webui/static/sessions.js` caught the 404 status and executed self-heal logic, stripping the session ID and wiping the URL.
2. **Aggressive Browser Caching of Versioned Static Shell Assets**:
   - Static assets requested with `?v=...` are served with `Cache-Control: public, max-age=31536000, immutable`.
   - `WEBUI_VERSION` was static at `"2.0-agy"`. Browsers complying with `immutable` did not invalidate cached JavaScript files even upon reload.
   - PWA Service Worker (`webui/static/sw.js`) was missing modular scripts (`ui-layout.js`, `ui-composer.js`, `events.js`, `api.js`, etc.) from `SHELL_ASSETS`.

## Decision Outcome
1. **Server Response State Tracking (`webui/server.py`)**:
   - Instrumented `Handler.send_response()` to record `self._response_sent = True`.
   - Initialized `self._response_sent = False` at the start of `do_GET` and `_handle_write`.
   - Guarded fallback 404 and 500 error writers with `not getattr(self, '_response_sent', False)`. Under no circumstances will a 404 or 500 header be written if headers have already been transmitted.
2. **Truth-Valued Response Helpers (`webui/api/helpers.py`)**:
   - Modified `j()` and `t()` to return `True` (and typed as `-> bool`), allowing `return j(...)` and `return bad(...)` to propagate affirmative dispatch status up the call chain.
3. **Short-Circuit Route Dispatching (`webui/api/routes.py`)**:
   - Updated each sub-dispatcher invocation in `handle_get` and `handle_post`:
     `if res is not None or getattr(handler, "_response_sent", False): return True if getattr(handler, "_response_sent", False) else res`
   - Guarantees immediate termination of route evaluation as soon as any handler writes to the client.
4. **Version Bump & Complete Shell Pre-Caching**:
   - Bumped `WEBUI_VERSION` to `"2.0.1-agy"` in `webui/api/updates.py`, busting browser cache and triggering automatic service worker cache rotation.
   - Added all decomposed scripts (`ui-layout.js`, `ui-composer.js`, `events.js`, `api.js`, `ui-theme.js`, `ui-notifications.js`, `diff_viewer.js`, `vault.js`, `analytics.js`) to `SHELL_ASSETS` in `webui/static/sw.js`.
5. **Persistent Connection Regression Testing (`tests/test_routes_dispatch.py`)**:
   - Added `test_http_keepalive_consecutive_requests` sending consecutive GET requests (`/api/projects`, `/api/sessions`, `/api/models`, `/api/settings`, `/api/vault/health`) over a single persistent `http.client.HTTPConnection`.

## Consequences & Linked Systems
- **Resilience**: Complete elimination of duplicate HTTP responses and request smuggling/desync across all persistent HTTP/1.1 connections.
- **Verification**: **226/226 unit tests passing** (including new keepalive test) and clean `npm run lint`.
- **Interlinks**: Extends [[decisions/adr_007_routes_decomposition]], complements [[decisions/adr_010_frontend_layout_modernization]] and [[decisions/adr_011_auth_csrf_security_hardening]].
