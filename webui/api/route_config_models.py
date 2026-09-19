"""Model discovery, providers, plugins, settings, and runtime configuration routes.

Extracted from routes.py as part of routes decomposition (Sprint R3).
"""
import logging
import os
import platform
import shutil
import subprocess
import sys
import time
from urllib.parse import parse_qs

from api.config import (
    get_auxiliary_models,
    get_max_tokens_status,
    get_reasoning_status,
    load_settings,
    persisted_speech_settings_keys,
)
from api.helpers import bad, j
from api.onboarding import get_onboarding_status
from api.providers import (
    get_provider_cost_history,
    get_provider_quota,
    get_providers,
)
from api.upload import handle_transcribe_capability

logger = logging.getLogger(__name__)

# ── Plugin visibility endpoint helpers (#539) ─────────────────────────────────
_PLUGIN_VISIBILITY_HOOKS = (
    "pre_tool_call",
    "post_tool_call",
    "pre_llm_call",
    "post_llm_call",
)
_PLUGIN_VISIBILITY_HOOK_SET = set(_PLUGIN_VISIBILITY_HOOKS)


def _get_plugin_manager_for_visibility():
    """Return plugin manager for read-only WebUI visibility if available."""
    try:
        from hermes_cli.plugins import get_plugin_manager
        return get_plugin_manager()
    except Exception:
        logger.debug("Silent exception in _get_plugin_manager_for_visibility", exc_info=True)
        return None


def _clean_plugin_visibility_text(value, *, limit=240) -> str:
    """Return bounded display text without path/callback-like internals."""
    if value is None:
        return ""
    text = str(value).replace("\x00", "").strip()
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def _plugin_visibility_category_from_key(key: str) -> str:
    """Return the config category prefix for nested plugin keys."""
    raw = str(key or "").strip().replace("\\", "/")
    if "/" not in raw:
        return ""
    category = raw.split("/", 1)[0].strip()
    return category if category and category not in {".", ".."} else ""


def _plugin_visibility_selected_provider(category: str) -> str:
    """Read ``<category>.provider`` without surfacing config errors."""
    if not category:
        return ""
    try:
        from api.config import get_config as _get_cfg

        cfg = _get_cfg() or {}
    except Exception:
        logger.debug("Silent exception in _plugin_visibility_selected_provider", exc_info=True)
        return ""
    category_cfg = cfg.get(category, {}) if isinstance(cfg, dict) else {}
    if not isinstance(category_cfg, dict):
        return ""
    return str(category_cfg.get("provider") or "").strip().lower()


def _plugin_visibility_payload(manager=None) -> dict:
    """Build a sanitized plugin/hook visibility payload for Settings."""
    manager = manager or _get_plugin_manager_for_visibility()
    if manager is None:
        return {
            "plugins": [],
            "empty": True,
            "supported_hooks": list(_PLUGIN_VISIBILITY_HOOKS),
            "read_only": True,
            "unavailable": True,
        }
    if not getattr(manager, "is_loaded", False):
        return {
            "plugins": [],
            "supported_hooks": list(_PLUGIN_VISIBILITY_HOOKS),
            "unavailable": True,
        }
    manager.discover_and_load(force=False)

    plugins = []
    raw_plugins = getattr(manager, "_plugins", {}) or {}
    for key, loaded in sorted(raw_plugins.items(), key=lambda item: str(item[0])):
        manifest = getattr(loaded, "manifest", None)
        if manifest is None:
            continue
        plugin_key = _clean_plugin_visibility_text(
            getattr(manifest, "key", None) or key or getattr(manifest, "name", ""),
            limit=120,
        )
        name = _clean_plugin_visibility_text(getattr(manifest, "name", "") or plugin_key, limit=120)
        version = _clean_plugin_visibility_text(getattr(manifest, "version", ""), limit=80)
        description = _clean_plugin_visibility_text(getattr(manifest, "description", ""), limit=280)
        kind = _clean_plugin_visibility_text(getattr(manifest, "kind", "") or "standalone", limit=40)
        enabled_flag = bool(getattr(loaded, "enabled", False))
        category = _plugin_visibility_category_from_key(plugin_key)
        selected_provider = _plugin_visibility_selected_provider(category)
        plugin_slug = plugin_key.rsplit("/", 1)[-1].strip().lower()
        if kind == "exclusive":
            activation = "exclusive"
        elif kind == "model-provider" and enabled_flag:
            activation = "provider"
        else:
            activation = "enabled" if enabled_flag else "disabled"
        include_active_provider = True
        if kind == "exclusive":
            if category:
                is_active_provider = bool(selected_provider) and plugin_slug == selected_provider
            else:
                include_active_provider = False
                is_active_provider = False
        else:
            is_active_provider = kind == "model-provider" and enabled_flag
        registered = []
        for hook in list(getattr(manifest, "provides_hooks", []) or []) + list(getattr(loaded, "hooks_registered", []) or []):
            hook_name = str(hook or "").strip()
            if hook_name in _PLUGIN_VISIBILITY_HOOK_SET and hook_name not in registered:
                registered.append(hook_name)
        registered.sort(key=_PLUGIN_VISIBILITY_HOOKS.index)
        plugin_payload = {
            "name": name,
            "key": plugin_key or name,
            "version": version,
            "description": description,
            "enabled": enabled_flag,
            "kind": kind,
            "activation": activation,
            "hooks": registered,
        }
        if include_active_provider:
            plugin_payload["is_active_provider"] = bool(is_active_provider)
        plugins.append(plugin_payload)

    return {
        "plugins": plugins,
        "empty": not bool(plugins),
        "supported_hooks": list(_PLUGIN_VISIBILITY_HOOKS),
        "read_only": True,
    }


def _dashboard_plugin_enabled(plugin_name: str) -> bool:
    """True if a dashboard plugin is enabled in settings."""
    try:
        prefs = (load_settings() or {}).get("dashboard_plugins", {}) or {}
        return bool(prefs.get(plugin_name, False))
    except Exception:
        logger.debug("Silent exception in _dashboard_plugin_enabled", exc_info=True)
        return False


def _webui_plugin_payload() -> list[dict]:
    try:
        from api.plugins import get_plugin_metadata
        meta = get_plugin_metadata()
        if isinstance(meta, dict):
            return list(meta.values())
        return list(meta or [])
    except Exception:
        logger.debug("Silent exception in _webui_plugin_payload", exc_info=True)
        return []


def _handle_plugins(handler, parsed) -> bool:
    try:
        hermes_plugins = _plugin_visibility_payload()
        webui = _webui_plugin_payload()
        all_plugins = hermes_plugins["plugins"] + webui
        return j(handler, {
            "plugins": all_plugins,
            "empty": not bool(all_plugins),
            "supported_hooks": hermes_plugins["supported_hooks"],
            "read_only": True,
        })
    except Exception as exc:
        logger.warning("Failed to build plugin visibility payload: %s", exc)
        return j(
            handler,
            {
                "plugins": [],
                "empty": True,
                "supported_hooks": list(_PLUGIN_VISIBILITY_HOOKS),
                "read_only": True,
                "unavailable": True,
            },
        )


# ── AGY Models Cache ──────────────────────────────────────────────────────────
_AGY_MODELS_CACHE = None
_AGY_MODELS_CACHE_TIME = 0.0


def _get_agy_models_payload(force: bool = False):
    global _AGY_MODELS_CACHE, _AGY_MODELS_CACHE_TIME
    now = time.time()
    if not force and _AGY_MODELS_CACHE and (now - _AGY_MODELS_CACHE_TIME < 300.0):
        return _AGY_MODELS_CACHE

    agy_bin = os.environ.get("AGY_CLI_PATH") or shutil.which("agy") or "/usr/local/bin/agy"
    models_raw = []
    try:
        proc = subprocess.run([agy_bin, "models"], capture_output=True, text=True, timeout=8)
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line or line.startswith("Fetching"):
                continue
            if "\t" in line:
                mid, label = line.split("\t", 1)
                models_raw.append((mid.strip(), label.strip()))
    except Exception:
        logger.debug("Silent exception in _get_agy_models_payload", exc_info=True)
        pass

    gemini_models = []
    claude_models = []
    other_models = []

    for mid, label in models_raw:
        mid_l = mid.lower()
        lbl_l = label.lower()
        if "3.8-flash-high" in mid_l:
            desc = "Flagship hybrid reasoning model (v3.8 High)"
        elif "3.8-flash-medium" in mid_l:
            desc = "Balanced latency and reasoning depth (v3.8 Medium)"
        elif "3.8-flash-low" in mid_l:
            desc = "High-throughput rapid execution (v3.8 Low)"
        elif "3.7-flash-high" in mid_l:
            desc = "Flagship hybrid reasoning model (v3.7 High)"
        elif "3.7-flash-medium" in mid_l:
            desc = "Balanced latency and reasoning depth (v3.7 Medium)"
        elif "3.7-flash-low" in mid_l:
            desc = "High-throughput rapid execution (v3.7 Low)"
        elif "3.1-pro-high" in mid_l:
            desc = "Deep frontier reasoning and complex code architecture (v3.1 High)"
        elif "3.1-pro-low" in mid_l:
            desc = "Direct pro-tier code generation (v3.1 Low)"
        elif "3.6-flash" in mid_l:
            desc = "Fast reasoning model (v3.6)"
        elif "3.5-flash" in mid_l:
            desc = "Lightweight reasoning model (v3.5)"
        elif "sonnet" in mid_l or "sonnet" in lbl_l:
            desc = "Frontier coding with extended thinking"
        elif "opus" in mid_l or "opus" in lbl_l:
            desc = "Maximum capability frontier model"
        elif "gpt-oss" in mid_l or "oss" in mid_l:
            desc = "120B open weights reasoning model"
        else:
            desc = f"Official Antigravity model: {label}"

        item = {
            "id": label,
            "cli_id": mid,
            "label": label,
            "description": desc
        }
        if "gemini" in mid_l or "gemini" in lbl_l:
            gemini_models.append(item)
        elif "claude" in mid_l or "claude" in lbl_l or "sonnet" in mid_l or "opus" in mid_l:
            claude_models.append(item)
        else:
            other_models.append(item)

    if not gemini_models:
        gemini_models = [
            {"id": "Gemini 3.8 Flash (High)", "cli_id": "gemini-3.8-flash-high", "label": "Gemini 3.8 Flash (High)", "description": "Flagship hybrid reasoning model (v3.8 High)"},
            {"id": "Gemini 3.8 Flash (Medium)", "cli_id": "gemini-3.8-flash-medium", "label": "Gemini 3.8 Flash (Medium)", "description": "Balanced latency and reasoning depth (v3.8 Medium)"},
            {"id": "Gemini 3.8 Flash (Low)", "cli_id": "gemini-3.8-flash-low", "label": "Gemini 3.8 Flash (Low)", "description": "High-throughput rapid execution (v3.8 Low)"},
            {"id": "Gemini 3.7 Flash (High)", "cli_id": "gemini-3.7-flash-high", "label": "Gemini 3.7 Flash (High)", "description": "Flagship hybrid reasoning model (v3.7 High)"},
            {"id": "Gemini 3.7 Flash (Medium)", "cli_id": "gemini-3.7-flash-medium", "label": "Gemini 3.7 Flash (Medium)", "description": "Balanced latency and reasoning depth (v3.7 Medium)"},
            {"id": "Gemini 3.7 Flash (Low)", "cli_id": "gemini-3.7-flash-low", "label": "Gemini 3.7 Flash (Low)", "description": "High-throughput rapid execution (v3.7 Low)"},
            {"id": "Gemini 3.1 Pro (High)", "cli_id": "gemini-3.1-pro-high", "label": "Gemini 3.1 Pro (High)", "description": "Deep frontier reasoning and complex code architecture (v3.1 High)"},
            {"id": "Gemini 3.1 Pro (Low)", "cli_id": "gemini-3.1-pro-low", "label": "Gemini 3.1 Pro (Low)", "description": "Direct pro-tier code generation (v3.1 Low)"},
            {"id": "Gemini 3.6 Flash (High)", "cli_id": "gemini-3.6-flash-high", "label": "Gemini 3.6 Flash (High)", "description": "Fast reasoning model (v3.6)"},
            {"id": "Gemini 3.5 Flash (High)", "cli_id": "gemini-3.5-flash-high", "label": "Gemini 3.5 Flash (High)", "description": "Lightweight reasoning model (v3.5)"},
        ]
        claude_models = [
            {"id": "Claude Sonnet 4.6 (Thinking)", "cli_id": "claude-sonnet-4-6", "label": "Claude Sonnet 4.6 (Thinking)", "description": "Frontier coding with extended thinking"},
            {"id": "Claude Opus 4.6 (Thinking)", "cli_id": "claude-opus-4-6-thinking", "label": "Claude Opus 4.6 (Thinking)", "description": "Maximum capability frontier model"},
        ]
        other_models = [
            {"id": "GPT-OSS 120B (Medium)", "cli_id": "gpt-oss-120b-medium", "label": "GPT-OSS 120B (Medium)", "description": "120B open weights reasoning model"},
        ]

    payload = {
        "active_provider": "antigravity",
        "default_model": "Gemini 3.8 Flash (High)",
        "configured_model_badges": {},
        "groups": [
            {"provider": "Google Gemini", "provider_id": "antigravity", "models": gemini_models},
            {"provider": "Anthropic Claude", "provider_id": "antigravity", "models": claude_models},
            {"provider": "Open Source & Local", "provider_id": "antigravity", "models": other_models},
        ],
        "aliases": {
            "flash": "Gemini 3.8 Flash (High)",
            "flash38": "Gemini 3.8 Flash (High)",
            "flash37": "Gemini 3.7 Flash (High)",
            "pro": "Gemini 3.1 Pro (High)",
            "sonnet": "Claude Sonnet 4.6 (Thinking)",
            "opus": "Claude Opus 4.6 (Thinking)"
        }
    }
    _AGY_MODELS_CACHE = payload
    _AGY_MODELS_CACHE_TIME = now
    return payload


def _handle_get_config_and_models(handler, parsed):
    """Handle model discovery, providers, settings, plugins, and appearance routes.
    Returns True if handled, None if unhandled.
    """
    if parsed.path == "/api/models":
        force = False
        if getattr(parsed, "query", None):
            qs = parse_qs(parsed.query)
            force = bool(qs.get("force") or qs.get("refresh") or qs.get("freshness"))
        return j(handler, _get_agy_models_payload(force=force))

    if parsed.path == "/api/models/live":
        from api.profiles import profile_env_for_active_request
        with profile_env_for_active_request("/api/models/live", logger_override=logger):
            from api import routes as _routes
            return _routes._handle_live_models(handler, parsed)

    # ── Auxiliary models (GET/POST) ──
    if parsed.path == "/api/model/auxiliary":
        return j(handler, get_auxiliary_models())

    if parsed.path == "/api/dashboard/status":
        from api import dashboard_probe

        j(handler, dashboard_probe.get_dashboard_status())
        return True

    if parsed.path == "/api/dashboard/config":
        from api import dashboard_probe

        try:
            j(handler, dashboard_probe.get_dashboard_config())
        except ValueError as exc:
            bad(handler, str(exc), status=400)
        return True

    # ── Providers (GET) ──
    if parsed.path == "/api/providers":
        from api.profiles import profile_env_for_active_request_readonly
        with profile_env_for_active_request_readonly("/api/providers", logger_override=logger):
            return j(handler, get_providers())

    # ── Plugins/hooks visibility (read-only, no callback/source internals) ──
    if parsed.path == "/api/plugins":
        return _handle_plugins(handler, parsed)
    if parsed.path == "/api/provider/quota":
        query = parse_qs(parsed.query)
        provider_id = (query.get("provider", [""])[0] or None)
        refresh = (query.get("refresh", [""])[0] or "").strip().lower() in {"1", "true", "yes", "on"}
        from api.profiles import profile_env_for_active_request_readonly
        with profile_env_for_active_request_readonly("/api/provider/quota", logger_override=logger):
            return j(handler, get_provider_quota(provider_id, refresh=refresh))

    if parsed.path == "/api/provider/cost-history":
        query = parse_qs(parsed.query)
        provider_id = (query.get("provider", [""])[0] or None)
        days_raw = (query.get("days", ["7"])[0] or "7").strip()
        try:
            days = max(1, min(int(days_raw), 365))
        except (ValueError, TypeError):
            days = 7
        return j(handler, get_provider_cost_history(provider_id, days))

    # ── Antigravity (AGY) Live Quota & Diagnostics (GET) ──
    if parsed.path == "/api/agy/quota":
        try:
            try:
                from run_agent import AIAgent
            except ImportError:
                from webui.run_agent import AIAgent
            agent = AIAgent()
            agy_bin = agent._find_agy_bin()
            env = agent._get_env()
            cmd = [agy_bin, "--print", "/quota", "--dangerously-skip-permissions"]
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=15, env=env, cwd=str(agent.workspace))
            lines = [line.strip() for line in res.stdout.split("\n") if line.strip()]
            quotas = []
            for line in lines:
                parts = [p.strip() for p in line.split("\t") if p.strip()]
                if len(parts) >= 3:
                    pct_str = parts[2].replace("%", "")
                    try:
                        pct_val = int(pct_str)
                    except ValueError:
                        pct_val = 100
                    quotas.append({
                        "family": parts[0],
                        "window": parts[1],
                        "percent_remaining": pct_val,
                        "reset_time": parts[3] if len(parts) > 3 else ""
                    })
            return j(handler, {"ok": True, "quotas": quotas, "raw": res.stdout})
        except Exception as exc:
            logger.warning("Failed to fetch AGY quota: %s", exc)
            return j(handler, {"ok": False, "error": str(exc), "quotas": []})

    if parsed.path == "/api/agy/status":
        is_container = os.path.exists("/.dockerenv") or os.environ.get("WORKSPACE_DIR") == "/workspace"
        try:
            from run_agent import AIAgent
        except ImportError:
            from webui.run_agent import AIAgent
        agent = AIAgent()
        agy_bin = agent._find_agy_bin()
        return j(handler, {
            "ok": True,
            "container": is_container,
            "sandbox_isolation": "Active (Linux Container Namespace)" if is_container else "Host Native",
            "agy_bin": agy_bin,
            "workspace": str(agent.workspace),
            "python": sys.version.split()[0],
            "system": platform.platform(),
            "effort": os.environ.get("AGY_DEFAULT_EFFORT", "medium"),
            "mode": os.environ.get("AGY_DEFAULT_MODE", "accept-edits"),
            "deepmode": (lambda: sys.modules.get("api.vault") and sys.modules["api.vault"].is_deepmode_enabled(agent.workspace) if "api.vault" in sys.modules else False)()
        })

    if parsed.path == "/api/agy/settings":
        try:
            from api.vault import is_deepmode_enabled
            dm_status = is_deepmode_enabled()
        except Exception:
            logger.warning("Silent exception in handle_get", exc_info=True)
            dm_status = False
        return j(handler, {
            "ok": True,
            "effort": os.environ.get("AGY_DEFAULT_EFFORT", "medium"),
            "mode": os.environ.get("AGY_DEFAULT_MODE", "accept-edits"),
            "deepmode": dm_status
        })

    if parsed.path == "/api/settings":
        settings = load_settings()
        settings["persisted_speech_keys"] = persisted_speech_settings_keys()
        settings.pop("password_hash", None)
        settings.setdefault("max_tokens", None)
        settings.setdefault("max_tokens_effective", None)
        settings.setdefault("max_tokens_fallback", None)
        try:
            settings.update(get_max_tokens_status())
        except Exception:
            logger.warning("Silent exception in handle_get", exc_info=True)
            settings["max_tokens"] = None
            settings["max_tokens_effective"] = None
            settings["max_tokens_fallback"] = None
        settings["password_env_var"] = bool(
            os.getenv("AGY_WEBUI_PASSWORD", "").strip()
            or os.getenv("HERMES_WEBUI_PASSWORD", "").strip()
        )
        from api.auth import get_password_hash, is_auth_enabled
        settings["auth_enabled"] = is_auth_enabled()
        settings["password_auth_enabled"] = get_password_hash() is not None
        try:
            from api.auth import _passkey_feature_flag_enabled as _pffe
            from api.passkeys import registered_credentials as _rc
            if _pffe():
                settings["passkeys_enabled"] = bool(_rc())
                settings["passwordless_enabled"] = bool(_rc()) and not settings["password_auth_enabled"]
            else:
                settings["passkeys_enabled"] = False
                settings["passwordless_enabled"] = False
        except Exception:
            logger.warning("Silent exception in handle_get", exc_info=True)
            pass
        try:
            from api.updates import AGENT_VERSION, WEBUI_VERSION
            settings["webui_version"] = WEBUI_VERSION
            settings["agent_version"] = AGENT_VERSION
        except Exception:
            logger.warning("Silent exception in handle_get", exc_info=True)
            pass
        try:
            from api.updates import _read_update_channel, channel_version_badge
            channel = _read_update_channel()
            settings["update_channel"] = channel
            settings["update_channel_version"] = channel_version_badge()
        except Exception:
            logger.warning("Silent exception in handle_get", exc_info=True)
            pass
        return j(handler, settings)

    if parsed.path == "/api/transcribe/capability":
        return handle_transcribe_capability(handler)

    if parsed.path == "/api/reasoning":
        query = parse_qs(parsed.query)
        model_id = (query.get("model", [""])[0] or "").strip() or None
        provider_id = (query.get("provider", [""])[0] or "").strip() or None
        base_url = (query.get("base_url", [""])[0] or "").strip() or None
        return j(
            handler,
            get_reasoning_status(
                model_id=model_id,
                provider_id=provider_id,
                base_url=base_url,
            ),
        )

    if parsed.path == "/api/onboarding/status":
        return j(handler, get_onboarding_status())

    if parsed.path == "/api/extensions/status":
        from api.extensions import get_extension_status

        return j(handler, get_extension_status())

    if parsed.path == "/api/extensions/registry":
        from api.extensions import get_extension_registry

        return j(handler, get_extension_registry())

    if parsed.path.startswith("/extensions/"):
        from api.extensions import serve_extension_static

        return serve_extension_static(handler, parsed)

    if parsed.path.startswith("/static/"):
        from api.route_static import _serve_static

        return _serve_static(handler, parsed)

    return None
