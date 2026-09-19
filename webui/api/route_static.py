"""Static asset serving, manifest, service worker, and HTML shell rendering routes.

Extracted from routes.py as part of routes decomposition (Sprint R2).
"""
import gzip
import json
import logging
import threading
from urllib.parse import parse_qs, quote

from api import config as api_config
from api.config import MAX_UPLOAD_BYTES
from api.helpers import j, t

logger = logging.getLogger(__name__)

# MIME types for static file serving. Hoisted to module scope to avoid
# rebuilding the dict on every request.
_STATIC_MIME = {
    "css": "text/css",
    "js": "application/javascript",
    "html": "text/html",
    "svg": "image/svg+xml",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "ico": "image/x-icon",
    "gif": "image/gif",
    "webp": "image/webp",
    "woff": "font/woff",
    "woff2": "font/woff2",
}
# MIME types that are text-based and should carry charset=utf-8
_TEXT_MIME_TYPES = {"text/css", "application/javascript", "text/html", "image/svg+xml", "text/plain"}

# MIME types worth gzipping. Image and font formats (png/jpg/webp/woff2) are
# already compressed; gzip would only add CPU and a few bytes of framing.
_COMPRESSIBLE_MIME = {
    "text/css", "application/javascript", "text/html", "image/svg+xml",
    "application/json", "text/plain",
}

# In-process cache for raw bytes, compressed bytes, and ETag. The cache is keyed
# by absolute path and invalidated on (size, high-precision mtime) change, so a
# redeploy is picked up without a process restart. Missing/random paths never
# enter the cache; memory cost is bounded by the static/ tree's served files.
_STATIC_CACHE: dict = {}
_STATIC_CACHE_LOCK = threading.Lock()


def _serve_static(handler, parsed):
    """Serve static asset files from static root with gzip compression and ETag caching."""
    static_root = api_config.get_static_root().resolve()
    # Strip the leading '/static/' prefix, then resolve and sandbox
    rel = parsed.path[len("/static/") :]
    static_file = (static_root / rel).resolve()
    try:
        static_file.relative_to(static_root)
    except ValueError:
        return j(handler, {"error": "not found"}, status=404)
    if not static_file.exists() or not static_file.is_file():
        return j(handler, {"error": "not found"}, status=404)
    ext = static_file.suffix.lower()
    ct = _STATIC_MIME.get(ext.lstrip("."), "text/plain")
    ct_header = f"{ct}; charset=utf-8" if ct in _TEXT_MIME_TYPES else ct

    # Look up or populate the per-file cache (raw, optional gzip, ETag).
    # Keyed by absolute path; invalidated by (size, nanosecond mtime).
    st = static_file.stat()
    sig = (st.st_size, st.st_mtime_ns)
    cache_key = str(static_file)
    raw = gz = etag = None
    with _STATIC_CACHE_LOCK:
        cached = _STATIC_CACHE.get(cache_key)
        if cached and cached[0] == sig:
            _, raw, gz, etag = cached
    if raw is None:
        raw = static_file.read_bytes()
        # Weak ETag: equality semantics, derived from filesystem identity.
        etag = f'W/"{sig[0]:x}-{sig[1]:x}"'
        gz = (gzip.compress(raw, compresslevel=6)
              if ct in _COMPRESSIBLE_MIME and len(raw) > 1024
              else None)
        with _STATIC_CACHE_LOCK:
            _STATIC_CACHE[cache_key] = (sig, raw, gz, etag)

    # The page template substitutes __WEBUI_VERSION__ at request time (see the
    # `/`/`/index.html`/`/session/` branch above), and static/sw.js's
    # SHELL_ASSETS list relies on the same convention. So a fingerprinted URL
    # is safe to cache aggressively: any redeploy changes the URL.
    version_values = parse_qs(parsed.query, keep_blank_values=True).get("v", [""])
    has_fingerprint = bool(version_values[0])
    cache_control = (
        "public, max-age=31536000, immutable" if has_fingerprint
        else "public, max-age=300"
    )

    # 304 short-circuit on conditional GET.
    if handler.headers.get("If-None-Match") == etag:
        handler.send_response(304)
        handler.send_header("ETag", etag)
        handler.send_header("Cache-Control", cache_control)
        if gz is not None:
            handler.send_header("Vary", "Accept-Encoding")
        handler.end_headers()
        return True

    accept_enc = (handler.headers.get("Accept-Encoding") or "").lower()
    use_gzip = gz is not None and "gzip" in accept_enc
    body = gz if use_gzip else raw

    handler.send_response(200)
    handler.send_header("Content-Type", ct_header)
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("ETag", etag)
    handler.send_header("Cache-Control", cache_control)
    if gz is not None:
        handler.send_header("Vary", "Accept-Encoding")
    if use_gzip:
        handler.send_header("Content-Encoding", "gzip")
    handler.end_headers()
    handler.wfile.write(body)
    return True


_SHELL_ERROR_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Antigravity is restarting</title>
</head>
<body style="margin:0;padding:2rem;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#111827;color:#e5e7eb;">
  <main style="max-width:40rem;margin:10vh auto;line-height:1.5;">
    <h1 style="font-size:1.5rem;margin:0 0 0.75rem;">Antigravity is restarting…</h1>
    <p style="margin:0;color:#cbd5e1;">The WebUI shell could not load cleanly. Refresh in a moment if this page does not update automatically.</p>
  </main>
</body>
</html>"""


def _serve_shell_unavailable(handler, exc: Exception) -> bool:
    """Return HTML for shell-route failures so `/` never renders JSON."""
    logger.warning("Failed to serve WebUI shell route: %s", exc)
    t(
        handler,
        _SHELL_ERROR_HTML,
        status=503,
        content_type="text/html; charset=utf-8",
    )
    return True


def _serve_manifest(handler) -> bool:
    """Serve static/manifest.json with the correct PWA Content-Type.

    Shared by the root (/manifest.json, /manifest.webmanifest) and
    session-prefixed (/session/manifest.json, /session/manifest.webmanifest)
    routes so Firefox Android can fetch the manifest when installing from
    a /session/<id> page.  See #2226.
    """
    static_root = api_config.get_static_root()
    manifest_path = (static_root / "manifest.json").resolve()
    if manifest_path.exists():
        data = manifest_path.read_bytes()
        handler.send_response(200)
        handler.send_header("Content-Type", "application/manifest+json; charset=utf-8")
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)
        return True
    return j(handler, {"error": "not found"}, status=404)


# In-process cache for the app-shell template.
_INDEX_SHELL_CACHE: dict = {}
_INDEX_SHELL_CACHE_LOCK = threading.Lock()


def _render_index_shell_base() -> str:
    """Return static/index.html with the process-constant tokens substituted.

    Cached and invalidated on (size, mtime_ns) change. The CSRF token and
    extension-tag injection are intentionally NOT applied here — they vary per
    request and are applied by the caller against this base string.
    """
    from api.updates import WEBUI_VERSION

    index_path = api_config.get_index_html_path()
    st = index_path.stat()
    sig = (index_path, st.st_size, st.st_mtime_ns)
    with _INDEX_SHELL_CACHE_LOCK:
        cached = _INDEX_SHELL_CACHE.get("base")
        if cached and cached[0] == sig:
            return cached[1]

    version_token = quote(WEBUI_VERSION, safe="")
    base = (
        index_path.read_text(encoding="utf-8")
        .replace("__WEBUI_VERSION__", version_token)
        .replace("__MAX_UPLOAD_BYTES__", str(MAX_UPLOAD_BYTES))
    )
    with _INDEX_SHELL_CACHE_LOCK:
        _INDEX_SHELL_CACHE["base"] = (sig, base)
    return base


def _handle_get_static_and_shell(handler, parsed):
    """Handle static assets, manifests, and HTML shell rendering routes.
    Returns True if handled, None if unhandled.
    """
    if parsed.path.startswith("/session/static/"):
        # Strip the leading "/session" so _serve_static() sees a path that
        # starts with "/static/" (its required prefix). _serve_static enforces
        # its own path-traversal sandbox via Path.resolve()+relative_to().
        stripped = parsed._replace(path=parsed.path[len("/session"):])
        return _serve_static(handler, stripped)

    if parsed.path.startswith("/static/"):
        return _serve_static(handler, parsed)

    # Firefox Android resolves <link rel="manifest"> against the page URL
    # before the dynamic <base href> script runs when installing from
    # /session/<id>, producing requests like /session/manifest.json.
    # Without this guard the catch-all below returns index.html instead of
    # the manifest, and Firefox falls back to a generated letter icon.
    # See #2226.
    if parsed.path in ("/session/manifest.json", "/session/manifest.webmanifest"):
        return _serve_manifest(handler)

    if parsed.path in ("/", "/index.html") or parsed.path.startswith("/session/"):
        try:
            from api.extensions import inject_extension_tags

            csrf_token = ""
            try:
                from api.auth import csrf_token_for_session, is_auth_enabled, parse_cookie, verify_session

                if is_auth_enabled():
                    cookie_val = parse_cookie(handler)
                    if not cookie_val:
                        cookie_val = getattr(handler, "_trusted_auth_session_cookie_value", None)
                    if cookie_val and verify_session(cookie_val):
                        csrf_token = csrf_token_for_session(cookie_val) or ""
            except Exception:
                logger.warning("Silent exception in handle_get", exc_info=True)
                csrf_token = ""

            # The disk read + process-constant token substitutions are cached;
            # only the per-session CSRF token and per-request extension tags are
            # applied here (see _render_index_shell_base).
            html = _render_index_shell_base().replace(
                "__CSRF_TOKEN_JSON__", json.dumps(csrf_token)
            )
            return t(
                handler,
                inject_extension_tags(html),
                content_type="text/html; charset=utf-8",
            )
        except Exception as exc:
            logger.warning("Silent exception in handle_get", exc_info=True)
            return _serve_shell_unavailable(handler, exc)

    if parsed.path in ("/manifest.json", "/manifest.webmanifest"):
        return _serve_manifest(handler)

    if parsed.path == "/sw.js":
        static_root = api_config.get_static_root()
        sw_path = (static_root / "sw.js").resolve()
        if sw_path.exists():
            # Inject the current git-derived version as the cache name so the
            # service worker cache busts automatically on every new deploy.
            from api.updates import WEBUI_VERSION
            version_token = quote(WEBUI_VERSION, safe="")
            text = sw_path.read_text(encoding="utf-8").replace(
                "__WEBUI_VERSION__", version_token
            )
            data = text.encode("utf-8")
            handler.send_response(200)
            handler.send_header("Content-Type", "application/javascript; charset=utf-8")
            handler.send_header("Cache-Control", "no-store")
            handler.send_header("Service-Worker-Allowed", "/")
            handler.send_header("Content-Length", str(len(data)))
            handler.end_headers()
            handler.wfile.write(data)
            return True
        return j(handler, {"error": "not found"}, status=404)

    if parsed.path == "/favicon.ico":
        static_root = api_config.get_static_root()
        ico_path = (static_root / "favicon.ico").resolve()
        if ico_path.exists() and ico_path.is_file():
            data = ico_path.read_bytes()
            handler.send_response(200)
            handler.send_header("Content-Type", "image/x-icon")
            handler.send_header("Content-Length", str(len(data)))
            handler.send_header("Cache-Control", "public, max-age=86400")
            handler.end_headers()
            handler.wfile.write(data)
        else:
            handler.send_response(204)
            handler.end_headers()
        return True

    return None
