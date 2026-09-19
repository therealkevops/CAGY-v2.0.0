"""Authentication, login shell, OIDC code flow, and conversation share routes.

Extracted from routes.py as part of routes decomposition (Sprint R2).
"""
import html as _html
import logging
import re
from urllib.parse import parse_qs, quote, unquote

from api.config import load_settings
from api.helpers import _security_headers, bad, j, t
from api.shares import load_share

logger = logging.getLogger(__name__)

# ── Login locales ─────────────────────────────────────────────────────────────
_LOGIN_LOCALE = {
    "en": {
        "lang": "en",
        "title": "Sign in",
        "subtitle": "Enter your password to continue",
        "placeholder": "Password",
        "btn": "Sign in",
        "invalid_pw": "Invalid password",
        "conn_failed": "Connection failed",
    },
    "fr": {
        "lang": "fr-FR",
        "title": "Se connecter",
        "subtitle": "Entrez votre mot de passe pour continuer",
        "placeholder": "Mot de passe",
        "btn": "Se connecter",
        "invalid_pw": "Mot de passe invalide",
        "conn_failed": "Échec de la connexion",
    },
    "es": {
        "lang": "es-ES",
        "title": "Iniciar sesión",
        "subtitle": "Introduce tu contraseña para continuar",
        "placeholder": "Contraseña",
        "btn": "Entrar",
        "invalid_pw": "Contraseña inválida",
        "conn_failed": "Error de conexión",
    },
    "de": {
        "lang": "de-DE",
        "title": "Anmelden",
        "subtitle": "Geben Sie Ihr Passwort ein, um fortzufahren",
        "placeholder": "Passwort",
        "btn": "Anmelden",
        "invalid_pw": "Ungültiges Passwort",
        "conn_failed": "Verbindung fehlgeschlagen",
    },
    "ru": {
        "lang": "ru-RU",
        "title": "Войти",
        "subtitle": "Введите пароль, чтобы продолжить",
        "placeholder": "Пароль",
        "btn": "Войти",
        "invalid_pw": "Неверный пароль",
        "conn_failed": "Не удалось подключиться",
    },
    "zh": {
        "lang": "zh-CN",
        "title": "登录",
        "subtitle": "输入密码继续使用",
        "placeholder": "密码",
        "btn": "登录",
        "invalid_pw": "密码错误",
        "conn_failed": "连接失败",
    },
    "zh-Hant": {
        "lang": "zh-TW",
        "title": "登录",
        "subtitle": "輸入密碼繼續使用",
        "placeholder": "密碼",
        "btn": "登录",
        "invalid_pw": "密碼錯誤",
        "conn_failed": "連接失敗",
    },
    "it": {
        "lang": "it-IT",
        "title": "Accedi",
        "subtitle": "Inserisci la password per continuare",
        "placeholder": "Password",
        "btn": "Accedi",
        "invalid_pw": "Password non valida",
        "conn_failed": "Connessione fallita",
    },
    "ja": {
        "lang": "ja-JP",
        "title": "サインイン",
        "subtitle": "パスワードを入力して続行",
        "placeholder": "パスワード",
        "btn": "サインイン",
        "invalid_pw": "パスワードが無効です",
        "conn_failed": "接続失敗",
    },
    "pt": {
        "lang": "pt-BR",
        "title": "Entrar",
        "subtitle": "Digite sua senha para continuar",
        "placeholder": "Senha",
        "btn": "Entrar",
        "invalid_pw": "Senha inválida",
        "conn_failed": "Falha na conexão",
    },
    "ko": {
        "lang": "ko-KR",
        "title": "로그인",
        "subtitle": "계속하려면 비밀번호를 입력하세요",
        "placeholder": "비밀번호",
        "btn": "로그인",
        "invalid_pw": "비밀번호가 올바르지 않습니다",
        "conn_failed": "연결 실패",
    },
    "tr": {
        "lang": "tr-TR",
        "title": "Oturum aç",
        "subtitle": "Devam etmek için şifrenizi girin",
        "placeholder": "Şifre",
        "btn": "Oturum aç",
        "invalid_pw": "Geçersiz şifre",
        "conn_failed": "Bağlantı başarısız",
    },
    "pl": {
        "lang": "pl-PL",
        "title": "Zaloguj się",
        "subtitle": "Wpisz hasło, aby kontynuować",
        "placeholder": "Hasło",
        "btn": "Zaloguj się",
        "invalid_pw": "Nieprawidłowe hasło",
        "conn_failed": "Połączenie nie powiodło się",
    },
    "vi": {
        "lang": "vi",
        "title": "Đăng nhập",
        "subtitle": "Nhập mật khẩu của bạn để tiếp tục",
        "placeholder": "Mật khẩu",
        "btn": "Đăng nhập",
        "invalid_pw": "Mật khẩu không hợp lệ",
        "conn_failed": "Kết nối thất bại",
    },
    "cs": {
        "lang": "cs-CZ",
        "title": "Přihlásit se",
        "subtitle": "Zadejte heslo pro pokračování",
        "placeholder": "Heslo",
        "btn": "Přihlásit se",
        "invalid_pw": "Neplatné heslo",
        "conn_failed": "Připojení selhalo",
    },
}


def _resolve_login_locale_key(raw_lang: str | None) -> str:
    """Resolve settings.language to a known _LOGIN_LOCALE key."""
    if not raw_lang:
        return "en"
    lang = str(raw_lang).strip()
    if not lang:
        return "en"
    if lang in _LOGIN_LOCALE:
        return lang

    normalized = lang.replace("_", "-")
    lower = normalized.lower()

    # Case-insensitive direct key match first.
    for key in _LOGIN_LOCALE:
        if key.lower() == lower:
            return key

    # Common Chinese aliases.
    if lower == "zh" or lower.startswith("zh-cn") or lower.startswith("zh-sg") or lower.startswith("zh-hans"):
        return "zh"
    if lower.startswith("zh-tw") or lower.startswith("zh-hk") or lower.startswith("zh-mo") or lower.startswith("zh-hant"):
        return "zh-Hant" if "zh-Hant" in _LOGIN_LOCALE else "zh"

    # Fallback to base language subtag (e.g. en-US -> en).
    base = lower.split("-", 1)[0]
    for key in _LOGIN_LOCALE:
        if key.lower() == base:
            return key
    return "en"


# ── Login page (self-contained, no external deps) ────────────────────────────
_LOGIN_PAGE_HTML = """<!doctype html>
<html lang="{{LANG}}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{BOT_NAME}} — {{LOGIN_TITLE}}</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#1a1a2e;color:#e8e8f0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;
  height:100vh;display:flex;align-items:center;justify-content:center}
.card{background:#16213e;border:1px solid rgba(255,255,255,.08);border-radius:16px;padding:36px 32px;
  width:320px;text-align:center;box-shadow:0 8px 32px rgba(0,0,0,.3)}
.logo{width:48px;height:48px;border-radius:12px;background:linear-gradient(145deg,#e8a030,#e94560);
  display:flex;align-items:center;justify-content:center;font-weight:800;font-size:20px;color:#fff;
  margin:0 auto 12px;box-shadow:0 2px 12px rgba(233,69,96,.3)}
h1{font-size:18px;font-weight:600;margin-bottom:4px}
.sub{font-size:12px;color:#8888aa;margin-bottom:24px}
input{width:100%;padding:10px 14px;border-radius:10px;border:1px solid rgba(255,255,255,.1);
  background:rgba(255,255,255,.04);color:#e8e8f0;font-size:14px;outline:none;margin-bottom:14px;
  transition:border-color .15s}
input:focus{border-color:rgba(124,185,255,.5);box-shadow:0 0 0 3px rgba(124,185,255,.1)}
button{width:100%;padding:10px;border-radius:10px;border:none;background:rgba(124,185,255,.15);
  border:1px solid rgba(124,185,255,.3);color:#7cb9ff;font-size:14px;font-weight:600;cursor:pointer;
  transition:all .15s}
button:hover{background:rgba(124,185,255,.25)}
.oidc-login{display:block;margin-top:10px;padding:10px;border-radius:10px;text-decoration:none;
  background:rgba(255,255,255,.04);border:1px solid rgba(111,214,164,.35);color:#6fd6a4;
  font-size:14px;font-weight:600;cursor:pointer;transition:all .15s}
.oidc-login:hover{background:rgba(111,214,164,.12)}
.passkey-login{margin-top:10px;background:rgba(255,255,255,.04);border-color:rgba(232,160,48,.35);color:#e8a030}
.err{color:#e94560;font-size:12px;margin-top:10px;display:none}
</style></head><body>
<div class="card">
  <div class="logo">{{BOT_NAME_INITIAL}}</div>
  <h1>{{BOT_NAME}}</h1>
  <p class="sub">{{LOGIN_SUBTITLE}}</p>
  <form id="login-form" data-invalid-pw="{{LOGIN_INVALID_PW}}" data-conn-failed="{{LOGIN_CONN_FAILED}}">
    <input type="password" id="pw" placeholder="{{LOGIN_PLACEHOLDER}}" autofocus>
    <button type="submit">{{LOGIN_BTN}}</button>
    <button type="button" id="passkey-login" class="passkey-login" style="display:none">Sign in with passkey</button>
    {{OIDC_LOGIN_HTML}}
  </form>
  <div class="err" id="err"></div>
</div>
<!-- Keep login.js relative so subpath mounts load it under the current scope. -->
<script src="static/login.js?v={{WEBUI_VERSION}}"></script>
</body></html>"""


def _safe_login_redirect_path(raw_path: str | None) -> str:
    path = str(raw_path or "").strip()
    if not path:
        return "/"
    if path[0] != "/":
        return "/"
    if path[1:2] in {"/", "\\"}:
        return "/"
    if re.search(r"[\x00-\x1f\x7f\s]", path):
        return "/"
    if len(path) > 2048:
        return "/"
    _probe = path
    for _ in range(8):
        _path_only = _probe.split("?", 1)[0].split("#", 1)[0].split("&", 1)[0].rstrip("/")
        if _path_only.endswith("/login") or _path_only == "/login":
            return "/"
        _decoded = unquote(_probe)
        if _decoded == _probe:
            break
        _probe = _decoded
    else:
        _path_only = _probe.split("?", 1)[0].split("#", 1)[0].split("&", 1)[0].rstrip("/")
        if _path_only.endswith("/login") or _path_only == "/login":
            return "/"
        return "/"
    return path


def _request_base_url(handler) -> str:
    from api.auth import _is_secure_context

    scheme = "https" if _is_secure_context(handler) else "http"
    host = str(handler.headers.get("Host") or "").strip() or "127.0.0.1:8787"
    return f"{scheme}://{host}"


def _oidc_login_html(parsed) -> str:
    try:
        from api.auth_oidc import is_oidc_enabled
    except Exception:
        logger.debug("Silent exception in _oidc_login_html", exc_info=True)
        return ""
    if not is_oidc_enabled():
        return ""
    next_path = _safe_login_redirect_path(
        parse_qs(parsed.query or "").get("next", [""])[0]
    )
    href = "/api/auth/oidc/start"
    if next_path != "/":
        href += "?next=" + quote(next_path, safe="/")
    return (
        '<a id="oidc-login" class="oidc-login" '
        f'href="{_html.escape(href, quote=True)}">Continue with SSO</a>'
    )


def _handle_get_auth(handler, parsed):
    """Handle authentication, OIDC callbacks, and share routes.
    Returns True if handled, None if unhandled.
    """
    if parsed.path == "/share" or parsed.path.startswith("/share/"):
        return bad(handler, "Share links are disabled", status=404)

    if parsed.path == "/login":
        _settings = load_settings()
        _bn = _html.escape(_settings.get("bot_name") or "AGY")
        _lang = _settings.get("language", "en")
        _login_strings = _LOGIN_LOCALE[
            _resolve_login_locale_key(_lang)
        ]
        from api.updates import WEBUI_VERSION
        version_token = quote(WEBUI_VERSION, safe="")
        _page = (
            _LOGIN_PAGE_HTML.replace("{{BOT_NAME}}", _bn)
            .replace("{{BOT_NAME_INITIAL}}", _bn[0].upper())
            .replace("{{WEBUI_VERSION}}", version_token)
            .replace("{{LANG}}", _html.escape(_login_strings["lang"]))
            .replace("{{LOGIN_TITLE}}", _html.escape(_login_strings["title"]))
            .replace("{{LOGIN_SUBTITLE}}", _html.escape(_login_strings["subtitle"]))
            .replace(
                "{{LOGIN_PLACEHOLDER}}", _html.escape(_login_strings["placeholder"])
            )
            .replace("{{LOGIN_BTN}}", _html.escape(_login_strings["btn"]))
            .replace("{{LOGIN_INVALID_PW}}", _html.escape(_login_strings["invalid_pw"]))
            .replace(
                "{{LOGIN_CONN_FAILED}}", _html.escape(_login_strings["conn_failed"])
            )
            .replace("{{OIDC_LOGIN_HTML}}", _oidc_login_html(parsed))
        )
        return t(handler, _page, content_type="text/html; charset=utf-8")

    if parsed.path == "/api/auth/oidc/start":
        from api.auth_oidc import OIDCAuthError, OIDCConfigError, build_authorization_redirect

        next_path = _safe_login_redirect_path(
            parse_qs(parsed.query or "").get("next", [""])[0]
        )
        try:
            location = build_authorization_redirect(
                _request_base_url(handler), next_path
            )
        except OIDCConfigError as exc:
            return j(handler, {"error": str(exc)}, status=404)
        except OIDCAuthError as exc:
            return j(handler, {"error": str(exc)}, status=exc.status_code)
        handler.send_response(302)
        handler.send_header("Location", location)
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("Content-Length", "0")
        _security_headers(handler)
        handler.end_headers()
        return True

    if parsed.path == "/api/auth/oidc/callback":
        from api.auth import create_session, set_auth_cookie
        from api.auth_oidc import OIDCAuthError, OIDCConfigError, complete_authorization_code_flow

        query = parse_qs(parsed.query or "")
        error = str(query.get("error", [""])[0] or "").strip()
        if error:
            description = str(query.get("error_description", [""])[0] or "").strip()
            return j(handler, {"error": description or error}, status=401)
        state = str(query.get("state", [""])[0] or "").strip()
        code = str(query.get("code", [""])[0] or "").strip()
        if not state or not code:
            return j(handler, {"error": "Missing OIDC callback state or code"}, status=400)
        try:
            result = complete_authorization_code_flow(
                _request_base_url(handler), state, code
            )
        except OIDCConfigError as exc:
            return j(handler, {"error": str(exc)}, status=404)
        except OIDCAuthError as exc:
            return j(handler, {"error": str(exc)}, status=exc.status_code)
        cookie_val = create_session()
        handler.send_response(302)
        handler.send_header(
            "Location",
            _safe_login_redirect_path(result.get("next_path")),
        )
        handler.send_header("Cache-Control", "no-store")
        _security_headers(handler)
        set_auth_cookie(handler, cookie_val)
        handler.send_header("Content-Length", "0")
        handler.end_headers()
        return True

    if parsed.path == "/api/auth/status":
        from api.auth import (
            _passkey_feature_flag_enabled,
            ensure_trusted_auth_session,
            get_password_hash,
            is_auth_enabled,
            is_oidc_auth_enabled,
            is_trusted_auth_enabled,
        )
        from api.passkeys import registered_credentials

        logged_in = False
        session_info = None
        auth_enabled = is_auth_enabled()
        oidc_enabled = is_oidc_auth_enabled()
        if auth_enabled:
            session_info = ensure_trusted_auth_session(handler)
            logged_in = bool(session_info)
        passkey_flag = _passkey_feature_flag_enabled()
        passkeys = registered_credentials() if passkey_flag else []
        password_auth_enabled = get_password_hash() is not None
        payload = {
            "auth_enabled": auth_enabled,
            "logged_in": logged_in,
            "oidc_enabled": oidc_enabled,
            "password_auth_enabled": password_auth_enabled,
            "passwordless_enabled": bool(passkeys) and not password_auth_enabled,
            "passkeys_enabled": bool(passkeys),
            "passkeys_count": len(passkeys),
            "passkey_feature_flag": passkey_flag,
            "auth_disabled_acknowledged": bool(load_settings().get("auth_disabled_acknowledged")) if not auth_enabled else False,
        }
        if is_trusted_auth_enabled() or (session_info and session_info.get("auth_type") == "trusted"):
            payload["trusted_auth_enabled"] = True
        if session_info and session_info.get("auth_type") == "trusted":
            payload["auth_type"] = session_info.get("auth_type")
            payload["user"] = session_info.get("username")
            payload["bound_profile"] = session_info.get("bound_profile")
        return j(handler, payload)

    if parsed.path.startswith("/api/share/"):
        token = parsed.path[len("/api/share/"):].strip()
        share = load_share(token)
        if not share:
            return bad(handler, "Shared conversation not found", 404)
        return j(
            handler,
            {"share": share},
            extra_headers={
                "Cache-Control": "no-store",
                "X-Robots-Tag": "noindex, nofollow",
            },
        )

    return None
