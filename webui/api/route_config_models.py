"""Model discovery, providers, plugins, settings, and runtime configuration routes.

Extracted from routes.py as part of routes decomposition (Sprint R3).
"""
import copy
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs

import yaml

from api.config import (
    CUSTOM_MODELS_ENDPOINT_TIMEOUT_SECONDS,
    DEFAULT_MODEL,
    _PROVIDER_ALIASES,
    _custom_provider_entries,
    _custom_provider_slug_from_name,
    _lookup_custom_api_key_env,
    _parse_provider_qualified_model_id,
    _provider_is_known_or_configured,
    _resolve_provider_alias,
    get_auxiliary_models,
    get_available_models,
    get_config,
    get_config_for_profile_home,
    get_max_tokens_status,
    get_reasoning_status,
    load_settings,
    model_with_provider_context,
    persisted_speech_settings_keys,
    resolve_custom_provider_connection,
    resolve_model_provider,
)
from api.helpers import bad, j
from api.onboarding import get_onboarding_status
from api.profiles import get_agy_home_for_profile
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


# OpenAI-compatible /v1/models endpoints for live model discovery.
# Used as fallback when hermes_cli.provider_model_ids() is unavailable or
# returns [] for a provider (#871).  Kept at module level so the dict is
# built once, not reconstructed per request.
_OPENAI_COMPAT_ENDPOINTS = {
    "zai": "https://api.z.ai/v1",
    "minimax": "https://api.minimax.chat/v1",
    "mistralai": "https://api.mistral.ai/v1",
    "xai": "https://api.x.ai/v1",
    "deepseek": "https://api.deepseek.com",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    "nvidia": "https://integrate.api.nvidia.com/v1",
}
# NOTE: "openai-codex" is excluded because it maps to the same endpoint as
# the base "openai" provider (api.openai.com/v1).  When both are configured
# the openai provider is already wired through provider_model_ids(); codex-
# specific model filtering happens downstream in hermes_cli.
#
_LIVE_MODELS_CACHE_TTL = 60.0
_LIVE_MODELS_CACHE: dict[tuple[str, str], tuple[float, dict]] = {}
_LIVE_MODELS_CACHE_LOCK = threading.RLock()


def _active_profile_for_live_models_cache() -> str:
    try:
        from api.profiles import get_active_profile_name

        return get_active_profile_name() or "default"
    except Exception as _e:
        # A transient profile-resolution error mis-scopes the cache for up to
        # 60s ("default" gets the wrong payload). Log so we can detect it; the
        # blast radius stays small because the TTL caps the bad-cache window.
        logger.debug("_active_profile_for_live_models_cache fell back to 'default': %s", _e)
        return "default"


def _live_models_cache_key(provider: str) -> tuple[str, str]:
    return (_active_profile_for_live_models_cache(), provider)


def _get_cached_live_models(key: tuple[str, str]) -> dict | None:
    now = time.monotonic()
    with _LIVE_MODELS_CACHE_LOCK:
        cached = _LIVE_MODELS_CACHE.get(key)
        if not cached:
            return None
        ts, payload = cached
        if now - ts >= _LIVE_MODELS_CACHE_TTL:
            _LIVE_MODELS_CACHE.pop(key, None)
            return None
        return copy.deepcopy(payload)


def _set_cached_live_models(key: tuple[str, str], payload: dict) -> None:
    with _LIVE_MODELS_CACHE_LOCK:
        _LIVE_MODELS_CACHE[key] = (time.monotonic(), copy.deepcopy(payload))


def _clear_live_models_cache() -> None:
    with _LIVE_MODELS_CACHE_LOCK:
        _LIVE_MODELS_CACHE.clear()


def _handle_live_models(handler, parsed):
    """Return the live model list for a provider.

    Delegates to the agent's provider_model_ids() which handles:
    - OpenRouter: live fetch from /api/v1/models
    - Anthropic: live fetch from /v1/models (API key or OAuth token)
    - Copilot: live fetch from api.githubcopilot.com/models with correct headers
    - openai-codex: Codex OAuth endpoint + local ~/.codex/ cache fallback
    - Nous: live fetch from inference-api.nousresearch.com/v1/models
    - DeepSeek, kimi-coding, opencode-zen/go, custom: generic OpenAI-compat /v1/models
    - ZAI, MiniMax, Google/Gemini: fall back to static list (non-standard endpoints)
    - All others: static _PROVIDER_MODELS fallback

    The agent already maintains all provider-specific auth and endpoint logic
    in one place; the WebUI inherits it rather than duplicating it.

    Query params:
        provider  (optional) — provider ID; defaults to active profile provider
    """
    qs = parse_qs(parsed.query)
    provider = (qs.get("provider", [""])[0] or "").lower().strip()

    try:
        from api.config import get_config as _gc
        cfg = _gc()
        if not provider:
            provider = cfg.get("model", {}).get("provider") or ""
        if not provider:
            return j(handler, {"error": "no_provider", "models": []})

        if provider == "antigravity":
            return j(handler, {
                "provider": "antigravity",
                "models": []
            })

        from api.config import _resolve_provider_alias
        provider = _resolve_provider_alias(provider)

        cache_key = _live_models_cache_key(provider)
        cached = _get_cached_live_models(cache_key)
        if cached is not None:
            return j(handler, cached)

        def _finish(payload: dict):
            _set_cached_live_models(cache_key, payload)
            return j(handler, payload)

        # Delegate to the agent's live-fetch + fallback resolver.
        # provider_model_ids() tries live endpoints first and falls back to
        # the static _PROVIDER_MODELS list — it never raises.
        try:
            import sys as _sys
            import os as _os
            _agent_dir = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                                       "..", "..", ".hermes", "hermes-agent")
            _agent_dir = _os.path.normpath(_agent_dir)
            if _agent_dir not in _sys.path:
                _sys.path.insert(0, _agent_dir)
            from hermes_cli.models import provider_model_ids as _pmi
            ids = _pmi(provider)
        except Exception as _import_err:
            logger.debug("provider_model_ids import failed for %s: %s", provider, _import_err)
            ids = []

        if not ids:
            custom_provider_entry = None

            def _custom_provider_entries_for_request():
                if not (provider == "custom" or provider.startswith("custom:")):
                    return []
                try:
                    from api.config import _custom_provider_slug_from_name
                    _cp_entries = cfg.get("custom_providers", [])
                    if not isinstance(_cp_entries, list):
                        return []
                    _matches = []
                    for _cp in _cp_entries:
                        if not isinstance(_cp, dict):
                            continue
                        _slug = _custom_provider_slug_from_name(_cp.get("name", ""))
                        if provider.startswith("custom:"):
                            if _slug == provider:
                                _matches.append(_cp)
                        elif provider == "custom" and not _slug:
                            _matches.append(_cp)
                    return _matches
                except Exception:
                    logger.debug("Silent exception in _custom_provider_entries_for_request", exc_info=True)
                    return []

            def _custom_provider_model_ids(_cp):
                _ids = []

                def _append(_mid):
                    _mid = str(_mid or "").strip()
                    if _mid and _mid not in _ids:
                        _ids.append(_mid)

                _append(_cp.get("model", ""))
                _models = _cp.get("models")
                if isinstance(_models, dict):
                    for _mid in _models:
                        if isinstance(_mid, str):
                            _append(_mid)
                elif isinstance(_models, list):
                    for _item in _models:
                        if isinstance(_item, str):
                            _append(_item)
                        elif isinstance(_item, dict):
                            _append(_item.get("id") or _item.get("model") or _item.get("name"))
                return _ids

            def _custom_provider_api_key(_cp):
                _raw = _cp.get("api_key")
                if _raw is not None:
                    _key = str(_raw).strip()
                    if _key.startswith("${") and _key.endswith("}") and len(_key) > 3:
                        _key = os.getenv(_key[2:-1], "").strip()
                    if _key:
                        return _key
                _env = str(_cp.get("key_env") or "").strip()
                return os.getenv(_env, "").strip() if _env else ""

            # For 'custom' and 'custom:*' providers, provider_model_ids()
            # returns [] because they aren't real hermes_cli endpoints.
            # Fall back to the custom_providers entries from config.yaml so
            # the live-model enrichment step can add any models that weren't
            # already in the static list (issue #1619).
            # Collect config-specified model IDs separately so they don't
            # prevent the live fetch below from running (#3718).
            _config_ids = []
            if provider == "custom" or provider.startswith("custom:"):
                for _cp in _custom_provider_entries_for_request():
                    if custom_provider_entry is None:
                        custom_provider_entry = _cp
                    _config_ids.extend(_custom_provider_model_ids(_cp))

            # Always try live fetch for custom providers — config entries are a
            # fallback, not a replacement.  The live endpoint should return ALL
            # models the key has access to, not just what's listed in config.yaml.
            if provider == "custom" or provider.startswith("custom:"):
                _base_url = None
                _api_key = None
                if custom_provider_entry:
                    _base_url = custom_provider_entry.get("base_url")
                    _api_key = _custom_provider_api_key(custom_provider_entry)
                else:
                    _model_cfg = cfg.get("model", {})
                    _base_url = _model_cfg.get("base_url")
                    _api_key = _model_cfg.get("api_key")
                # Fallback: try credential pool for base_url + api_key
                if (not _base_url or not _api_key) and provider.startswith("custom:"):
                    try:
                        from api.config import _has_explicit_pool_credentials

                        if _has_explicit_pool_credentials(provider):
                            from agent.credential_pool import load_pool as _lpool
                            _resolved = _resolve_provider_alias(provider)
                            _lm_pool = _lpool(_resolved)
                            if _lm_pool:
                                _lm_entry = _lm_pool.select()
                                if _lm_entry:
                                    if not _api_key:
                                        _api_key = getattr(_lm_entry, "runtime_api_key", "") or ""
                                    if not _base_url:
                                        _base_url = str(getattr(_lm_entry, "base_url", "") or "").strip()
                    except ImportError:
                        pass
                if _base_url and _api_key:
                    try:
                        import urllib.request
                        import json

                        # Build the models endpoint URL
                        # AxonHub and similar OpenAI-compat endpoints serve /v1/models
                        _ep = _base_url.rstrip("/")
                        # If base_url already ends with /v1, use /models; otherwise add /v1/models
                        if _ep.endswith("/v1"):
                            _models_url = f"{_ep}/models"
                        else:
                            _models_url = f"{_ep}/v1/models"

                        _req = urllib.request.Request(
                            _models_url,
                            headers={"Authorization": f"Bearer {_api_key}"},
                        )

                        with urllib.request.urlopen(_req, timeout=CUSTOM_MODELS_ENDPOINT_TIMEOUT_SECONDS) as _resp:
                            _body = json.loads(_resp.read())

                        # Parse response: {"data": [{"id": "model1", ...}, ...]}
                        if isinstance(_body, dict):
                            _data = _body.get("data", [])
                            if isinstance(_data, list):
                                ids = [m.get("id", "") for m in _data if m.get("id")]
                        elif isinstance(_body, list):
                            ids = [m.get("id", m) if isinstance(m, dict) else m for m in _body]

                        if ids:
                            logger.debug("Live-fetched %d models from custom provider %s", len(ids), _base_url)
                        else:
                            logger.debug("Custom provider returned no models from %s", _base_url)

                    except Exception as _fetch_err:
                        logger.debug("Live fetch from custom provider failed: %s", _fetch_err)

                # If live fetch succeeded, merge with config entries (live takes
                # priority).  If live fetch failed, fall back to config-only list.
                if ids:
                    _live_set = set(ids)
                    for _cid in _config_ids:
                        if _cid not in _live_set:
                            ids.append(_cid)
                else:
                    ids = list(_config_ids)

        # ── OpenAI-compat live fetch fallback ──────────────────────────────────
        # When provider_model_ids() is unavailable or returns [] for a provider
        # that exposes a standard /v1/models endpoint, fetch directly.  This
        # eliminates the need to keep _PROVIDER_MODELS in sync for providers
        # that have a discoverable API (#871).
        #
        # WARNING: This uses synchronous urllib.request which blocks the worker
        # thread for up to 8 seconds on timeout. This is acceptable because:
        #  (a) the server uses threading (not async), so other requests continue;
        #  (b) the frontend shows the static list immediately and enriches in
        #      the background via _fetchLiveModels(), so the user never waits.
        if not ids:
            _ep = _OPENAI_COMPAT_ENDPOINTS.get(provider)
            if _ep:
                try:
                    import urllib.request
                    _providers_cfg = cfg.get("providers") or {}
                    _prov = _providers_cfg.get(provider, {}) if isinstance(_providers_cfg, dict) else {}
                    # Only use a provider-scoped key.  A top-level model.api_key
                    # is safe here only when it belongs to the requested provider;
                    # otherwise /api/models/live?provider=<other> could forward
                    # the active provider's credential to the wrong third party.
                    _key = _prov.get("api_key") if isinstance(_prov, dict) else None
                    if not _key:
                        _model_cfg = cfg.get("model", {})
                        if isinstance(_model_cfg, dict):
                            _active_provider = _resolve_provider_alias(
                                (_model_cfg.get("provider") or "").strip().lower()
                            )
                            if _active_provider == provider:
                                _key = _model_cfg.get("api_key")
                    if _key:
                        _req = urllib.request.Request(
                            f"{_ep}/models",
                            headers={"Authorization": f"Bearer {_key}"},
                        )
                        with urllib.request.urlopen(_req, timeout=8) as _resp:
                            _body = json.loads(_resp.read())
                        ids = [m.get("id", "") for m in _body.get("data", []) if m.get("id")]
                        logger.debug("Live-fetched %d models from %s /v1/models", len(ids), provider)
                except Exception as _fetch_err:
                    logger.debug("Live fetch from %s failed: %s", provider, _fetch_err)
                    # Fall through to static list below

        # Static fallback — only reached when live fetch also failed.
        if not ids:
            from api.config import _PROVIDER_MODELS as _pm
            ids = [m["id"] for m in _pm.get(provider, [])]
        if not ids:
            return _finish({"provider": provider, "models": [], "count": 0})

        # Match the same dropdown visibility budget that /api/models uses so
        # background enrichment via _fetchLiveModels() does not re-append an
        # uncapped catalog after the initial picker render. The full catalog
        # still comes from /api/models via extra_models for search/show-all;
        # this endpoint is only a dropdown-enrichment surface. (#1567, #3691)
        if provider == "nous":
            try:
                from api.config import _build_nous_featured_set
                _default_model = (cfg.get("model", {}) or {}).get("model") if isinstance(cfg.get("model"), dict) else None
                _featured, _ = _build_nous_featured_set(ids, selected_model_id=_default_model)
                ids = _featured
            except Exception:
                logger.debug("Failed to apply Nous featured-set cap for /api/models/live")
        else:
            from api.config import _MODEL_PICKER_OVERFLOW_THRESHOLD, _MODEL_PICKER_VISIBLE_TARGET
            if len(ids) > _MODEL_PICKER_OVERFLOW_THRESHOLD:
                ids = ids[:_MODEL_PICKER_VISIBLE_TARGET]

        # Normalise to {id, label} — provider_model_ids() returns plain string IDs.
        # For ollama-cloud use the shared Ollama formatter (handles `:variant` suffix).
        # For all other providers use a simpler hyphen-split capitaliser.
        from api.config import (
            _format_ollama_label as _fmt_ollama,
            _is_openai_family_provider as _is_fast_tier_provider,
            _model_supports_fast_tier_for_provider,
        )

        def _make_label(mid):
            """Best-effort human label from a model ID string."""
            if provider in ("ollama", "ollama-cloud"):
                return _fmt_ollama(mid)
            # Preserve slashes for router IDs like "anthropic/claude-sonnet-4.6"
            display = mid.split("/")[-1] if "/" in mid else mid
            parts = display.split("-")
            result = []
            for p in parts:
                pl = p.lower()
                if pl == "gpt":
                    result.append("GPT")
                elif pl in ("claude", "gemini", "gemma", "llama", "mistral",
                            "qwen", "deepseek", "grok", "kimi", "glm"):
                    result.append(p.capitalize())
                elif p[:1].isdigit():
                    result.append(p)  # version numbers: 5.4, 3.5, 4.6 — unchanged
                else:
                    result.append(p.capitalize())
            label = " ".join(result)
            # Restore well-known uppercase tokens that title-casing breaks
            for orig in ("GPT", "GLM", "API", "AI", "XL", "MoE"):
                label = label.replace(orig.title(), orig)
            return label

        annotate_fast_tier = _is_fast_tier_provider(provider)
        models_out = []
        for mid in ids:
            if not mid:
                continue
            entry = {"id": mid, "label": _make_label(mid)}
            if annotate_fast_tier:
                entry["supports_fast_tier"] = _model_supports_fast_tier_for_provider(mid, provider)
            models_out.append(entry)
        return _finish({"provider": provider, "models": models_out,
                        "count": len(models_out)})

    except Exception as _e:
        logger.debug("_handle_live_models failed for %s: %s", provider, _e)
        return j(handler, {"error": str(_e), "models": []})


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
            return _handle_live_models(handler, parsed)

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


def _saved_prompts_path() -> "Path":
    try:
        from api.profiles import get_active_agy_home

        return Path(get_active_agy_home()).expanduser() / "webui" / "saved_prompts.json"
    except Exception:
        logger.warning("Silent exception in _saved_prompts_path", exc_info=True)
        return Path(os.getenv("HERMES_HOME", str(Path.home() / ".hermes"))).expanduser() / "webui" / "saved_prompts.json"


def _load_saved_prompts() -> list:
    p = _saved_prompts_path()
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("Silent exception in _load_saved_prompts", exc_info=True)
        return []


def _save_saved_prompts(prompts: list) -> None:
    p = _saved_prompts_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(prompts, ensure_ascii=False, indent=2), encoding="utf-8")


def _handle_post_config_and_settings(handler, parsed, body):
    """Handle POST configuration, models, providers, profiles, settings, and onboarding routes.
    Returns True/response if handled, None if unhandled.
    """
    from api import routes as _routes

    set_hermes_default_model = _routes.set_hermes_default_model
    set_provider_key = _routes.set_provider_key
    remove_provider_key = _routes.remove_provider_key
    set_reasoning_display = _routes.set_reasoning_display
    set_reasoning_effort = _routes.set_reasoning_effort
    _sanitize_error = _routes._sanitize_error
    _onboarding_gate_allows = _routes._onboarding_gate_allows
    save_settings = _routes.save_settings
    persisted_speech_settings_keys = _routes.persisted_speech_settings_keys
    _clear_session_list_cache = _routes._clear_session_list_cache
    _security_headers = _routes._security_headers
    start_onboarding_oauth_flow = _routes.start_onboarding_oauth_flow
    cancel_onboarding_oauth_flow = _routes.cancel_onboarding_oauth_flow
    apply_onboarding_setup = _routes.apply_onboarding_setup
    complete_onboarding = _routes.complete_onboarding
    probe_provider_endpoint = _routes.probe_provider_endpoint
    if parsed.path == "/api/dashboard/config":
        from api import dashboard_probe

        try:
            j(handler, dashboard_probe.save_dashboard_config(body))
        except ValueError as exc:
            bad(handler, str(exc), status=400)
        except Exception as exc:
            logger.exception("dashboard config save failed")
            bad(handler, str(exc), status=500)
        return True

    if parsed.path == "/api/prompts":
        text = str(body.get("text") or "").strip()
        label = str(body.get("label") or "").strip()
        if not text:
            return bad(handler, "text is required")
        if len(text) > 8000:
            return bad(handler, "text too long (max 8000 chars)")
        prompts = _load_saved_prompts()
        if len(prompts) >= 200:
            return bad(handler, "saved prompts limit reached (max 200)")
        new_prompt = {"id": uuid.uuid4().hex[:12], "label": label or text[:60], "text": text, "created_at": time.time()}
        prompts.append(new_prompt)
        _save_saved_prompts(prompts)
        return j(handler, {"ok": True, "prompt": new_prompt})

    if parsed.path == "/api/default-model":
        try:
            advanced = body.get("advanced") if isinstance(body, dict) else None
            provider = body.get("provider") if isinstance(body, dict) else None
            if str(provider or "").strip().lower() == "auto":
                provider = None
            return j(handler, set_hermes_default_model(body.get("model"), provider=provider, advanced=advanced))
        except ValueError as e:
            return bad(handler, str(e))
        except RuntimeError as e:
            return bad(handler, str(e), 500)

    # ── Auxiliary model set (POST) ──
    if parsed.path == "/api/model/set":
        scope = str(body.get("scope") or "").strip()
        task = str(body.get("task") or "").strip()
        provider = str(body.get("provider") or "auto").strip()
        model = str(body.get("model") or "").strip()
        advanced = body.get("advanced") if isinstance(body, dict) else None
        if scope == "auxiliary":
            from api.config import set_auxiliary_model
            try:
                return j(handler, set_auxiliary_model(task, provider, model, advanced=advanced))
            except Exception as exc:
                logger.warning("Silent exception in handle_post", exc_info=True)
                return bad(handler, str(exc), status=400)
        if scope == "main":
            try:
                main_provider = provider if provider != "auto" else None
                return j(handler, set_hermes_default_model(model, provider=main_provider, advanced=advanced))
            except ValueError as exc:
                return bad(handler, str(exc), status=400)
        return bad(handler, f"unknown scope: {scope}", status=400)

    # ── Providers (POST) ──
    if parsed.path == "/api/providers":
        provider_id = (body.get("provider") or "").strip().lower()
        api_key = body.get("api_key")
        if not provider_id:
            return bad(handler, "provider is required")
        if api_key is not None:
            api_key = str(api_key).strip() or None
        result = set_provider_key(provider_id, api_key)
        if not result.get("ok"):
            return bad(handler, result.get("error", "Unknown error"))
        return j(handler, result)

    if parsed.path == "/api/providers/delete":
        provider_id = (body.get("provider") or "").strip().lower()
        if not provider_id:
            return bad(handler, "provider is required")
        result = remove_provider_key(provider_id)
        if not result.get("ok"):
            return bad(handler, result.get("error", "Unknown error"))
        return j(handler, result)

    if parsed.path == "/api/providers/self-hosted":
        try:
            from api.onboarding import apply_self_hosted_provider_setup
            return j(handler, apply_self_hosted_provider_setup(body))
        except ValueError as exc:
            return bad(handler, str(exc), 400)

    if parsed.path == "/api/models/refresh":
        provider_id = (body.get("provider") or "").strip().lower()
        if not provider_id:
            return bad(handler, "provider is required")
        from api.config import invalidate_provider_models_cache
        invalidate_provider_models_cache(provider_id)
        return j(handler, {"ok": True, "provider": provider_id})

    if parsed.path == "/api/reasoning":
        try:
            display = body.get("display")
            effort = body.get("effort")
            if display is not None:
                flag = str(display).strip().lower()
                if flag in ("show", "on", "true", "1"):
                    return j(handler, set_reasoning_display(True))
                if flag in ("hide", "off", "false", "0"):
                    return j(handler, set_reasoning_display(False))
                return bad(handler, f"display must be show|hide|on|off (got '{display}')")
            if effort is not None:
                model_id = str(body.get("model") or "").strip() or None
                provider_id = str(body.get("provider") or "").strip() or None
                base_url = str(body.get("base_url") or "").strip() or None
                return j(
                    handler,
                    set_reasoning_effort(
                        effort,
                        model_id=model_id,
                        provider_id=provider_id,
                        base_url=base_url,
                    ),
                )
            return bad(handler, "reasoning: must supply 'display' or 'effort'")
        except ValueError as e:
            return bad(handler, str(e))
        except RuntimeError as e:
            return bad(handler, str(e), 500)

    if parsed.path == "/api/admin/reload":
        import importlib
        from api import models as _models
        importlib.reload(_models)
        import api.routes as _routes
        _routes.get_session = _models.get_session
        _routes.Session = _models.Session
        return j(handler, {"status": "ok", "reloaded": "api.models"})

    # ── Profile API (POST) ──
    if parsed.path == "/api/profile/switch":
        name = body.get("name", "").strip()
        if not name:
            return bad(handler, "name is required")
        try:
            from api.auth import ensure_trusted_auth_session
            from api.profiles import switch_profile, _validate_profile_name
            from api.helpers import build_profile_cookie
            if name != 'default':
                _validate_profile_name(name)
            session_info = ensure_trusted_auth_session(handler)
            if getattr(handler, '_trusted_auth_session_rejected', False):
                return bad(handler, 'Authentication required', 401)
            bound_profile = str((session_info or {}).get("bound_profile") or "").strip() or None
            if bound_profile and name != bound_profile:
                return bad(handler, "Profile is bound to the current session", 403)
            result = switch_profile(name, process_wide=False)
            from api.config import invalidate_models_cache
            invalidate_models_cache()
            try:
                from api.gateway_watcher import restart_watcher_for_profile
                restart_watcher_for_profile(name)
            except Exception as exc:
                logger.warning("Failed to restart gateway watcher for profile %s: %s", name, exc)
            session_cookie_value = getattr(handler, '_trusted_auth_session_cookie_value', None)
            if session_cookie_value:
                if bound_profile and name == bound_profile:
                    return j(handler, result)
                extra_header = build_profile_cookie(name, session_cookie_value=session_cookie_value)
            else:
                extra_header = build_profile_cookie(name, handler)
            return j(handler, result, extra_headers={
                'Set-Cookie': extra_header,
            })
        except PermissionError as e:
            return bad(handler, _sanitize_error(e), 403)
        except (ValueError, FileNotFoundError) as e:
            return bad(handler, _sanitize_error(e), 404)
        except RuntimeError as e:
            return bad(handler, str(e), 409)

    if parsed.path == "/api/profile/create":
        name = body.get("name", "").strip()
        if not name:
            return bad(handler, "name is required")
        import re as _re

        if not _re.match(r"^[a-z0-9][a-z0-9_-]{0,63}$", name):
            return bad(
                handler,
                "Invalid profile name: lowercase letters, numbers, hyphens, underscores only",
            )
        clone_from = body.get("clone_from")
        if clone_from is not None:
            clone_from = str(clone_from).strip()
            if not _re.match(r"^[a-z0-9][a-z0-9_-]{0,63}$", clone_from):
                return bad(handler, "Invalid clone_from name")
        base_url = body.get("base_url", "").strip() if body.get("base_url") else None
        api_key = body.get("api_key", "").strip() if body.get("api_key") else None
        default_model = body.get("default_model", "").strip() if body.get("default_model") else None
        model_provider = body.get("model_provider", "").strip() if body.get("model_provider") else None
        if base_url and not base_url.startswith(("http://", "https://")):
            return bad(handler, "base_url must start with http:// or https://")
        try:
            from api.profiles import create_profile_api

            result = create_profile_api(
                name,
                clone_from=clone_from,
                clone_config=bool(body.get("clone_config", False)),
                base_url=base_url,
                api_key=api_key,
                default_model=default_model,
                model_provider=model_provider,
            )
            return j(handler, {"ok": True, "profile": result})
        except PermissionError as e:
            return bad(handler, _sanitize_error(e), 403)
        except (ValueError, FileExistsError, RuntimeError) as e:
            return bad(handler, str(e))

    if parsed.path == "/api/profile/delete":
        name = body.get("name", "").strip()
        if not name:
            return bad(handler, "name is required")
        try:
            from api.profiles import delete_profile_api, _validate_profile_name

            _validate_profile_name(name)
            result = delete_profile_api(name)
            return j(handler, result)
        except PermissionError as e:
            return bad(handler, _sanitize_error(e), 403)
        except (ValueError, FileNotFoundError) as e:
            return bad(handler, _sanitize_error(e))
        except RuntimeError as e:
            return bad(handler, str(e), 409)

    # ── Antigravity (AGY) Settings (POST) ──
    if parsed.path == "/api/agy/settings":
        try:
            from api.vault import is_deepmode_enabled, sync_deepmode_rule
            req_body = body if isinstance(body, dict) else {}
            if "deepmode" in req_body:
                dm_val = bool(req_body["deepmode"])
                sync_deepmode_rule(enabled=dm_val)
            if "effort" in req_body and req_body["effort"] in ("low", "medium", "high"):
                os.environ["AGY_DEFAULT_EFFORT"] = req_body["effort"]
            if "mode" in req_body and req_body["mode"] in ("accept-edits", "plan"):
                os.environ["AGY_DEFAULT_MODE"] = req_body["mode"]
            return j(handler, {
                "ok": True,
                "effort": os.environ.get("AGY_DEFAULT_EFFORT", "medium"),
                "mode": os.environ.get("AGY_DEFAULT_MODE", "accept-edits"),
                "deepmode": is_deepmode_enabled()
            })
        except Exception as exc:
            logger.warning("Silent exception in handle_post", exc_info=True)
            return bad(handler, str(exc), status=400)

    # ── Settings (POST) ──
    if parsed.path == "/api/settings":
        from api.auth import (
            create_session,
            get_password_hash,
            is_auth_enabled,
            parse_cookie,
            set_auth_cookie,
            verify_password,
            verify_session,
        )

        if "bot_name" in body:
            body["bot_name"] = (str(body["bot_name"]) or "").strip() or "AGY"

        auth_enabled_before = is_auth_enabled()
        password_auth_enabled_before = auth_enabled_before and get_password_hash() is not None
        current_cookie = parse_cookie(handler)
        logged_in_before = bool(current_cookie and verify_session(current_cookie))
        requested_password = bool(
            isinstance(body.get("_set_password"), str)
            and body.get("_set_password", "").strip()
        )
        requested_passwordless = bool(body.pop("_passwordless", False))
        requested_clear_password = bool(body.get("_clear_password") or requested_passwordless)
        if requested_passwordless:
            body["_clear_password"] = True

        current_password = body.pop("_current_password", None)

        if requested_password or requested_clear_password:
            active_env_var = "AGY_WEBUI_PASSWORD" if os.getenv("AGY_WEBUI_PASSWORD", "").strip() else ("HERMES_WEBUI_PASSWORD" if os.getenv("HERMES_WEBUI_PASSWORD", "").strip() else None)
            if active_env_var:
                return bad(
                    handler,
                    f"{active_env_var} env var is set — it overrides the settings password. "
                    "Unset the env var and restart the server before changing the password here.",
                    409,
                )

        max_tokens_provided = "max_tokens" in body
        max_tokens_status = None
        max_tokens_value = body.pop("max_tokens", None) if max_tokens_provided else None

        if requested_password and not auth_enabled_before:
            if not _onboarding_gate_allows(handler, auth_enabled_before):
                return bad(
                    handler,
                    "First password setup is only available from local networks when auth is not enabled. "
                    "To bootstrap this on a remote server, set HERMES_WEBUI_ONBOARDING_OPEN=1.",
                    403,
                )

        if auth_enabled_before and password_auth_enabled_before and (requested_password or requested_clear_password):
            if not isinstance(current_password, str) or not current_password:
                return bad(
                    handler,
                    "Current password is required to change or disable authentication.",
                    403,
                )
            if not verify_password(current_password):
                return bad(
                    handler,
                    "Current password is incorrect.",
                    403,
                )

        if requested_passwordless:
            from api.auth import _passkey_feature_flag_enabled
            from api.passkeys import registered_credentials

            if not _passkey_feature_flag_enabled():
                return bad(handler, "Passkey support is disabled. Enable AGY_WEBUI_PASSKEY before going passwordless.", 409)
            if not registered_credentials():
                return bad(handler, "Register a passkey before going passwordless.", 409)
        elif requested_clear_password:
            from api.passkeys import clear_credentials

            clear_credentials()

        ack = body.pop("_auth_disabled_acknowledged", None)
        if ack is not None and not is_auth_enabled():
            body["auth_disabled_acknowledged"] = bool(ack)
        elif is_auth_enabled() or requested_password:
            body["auth_disabled_acknowledged"] = False

        from api.config import get_max_tokens_status, set_max_tokens

        saved = save_settings(body)
        saved["persisted_speech_keys"] = persisted_speech_settings_keys()
        if max_tokens_provided:
            max_tokens_status = set_max_tokens(max_tokens_value)
        saved.pop("password_hash", None)
        saved.update(max_tokens_status if max_tokens_provided else get_max_tokens_status())

        if any(
            k in body
            for k in (
                "show_cli_sessions",
                "show_claude_code_sessions",
                "show_cron_sessions",
                "show_webhook_sessions",
                "show_kanban_sessions",
                "show_previous_messaging_sessions",
            )
        ):
            try:
                _clear_session_list_cache()
            except Exception:
                logger.warning("Silent exception in handle_post", exc_info=True)
                pass
            try:
                from api.models import clear_cli_sessions_cache
                clear_cli_sessions_cache()
            except Exception:
                logger.warning("Silent exception in handle_post", exc_info=True)
                pass

        auth_enabled_after = is_auth_enabled()
        auth_just_enabled = bool(
            requested_password and auth_enabled_after and not auth_enabled_before
        )
        logged_in_after = logged_in_before
        new_cookie = None

        if auth_just_enabled and not logged_in_before:
            new_cookie = create_session()
            logged_in_after = True

        saved["auth_enabled"] = auth_enabled_after
        saved["password_auth_enabled"] = get_password_hash() is not None
        saved["logged_in"] = logged_in_after
        saved["auth_just_enabled"] = auth_just_enabled
        try:
            from api.auth import _passkey_feature_flag_enabled as _pffe
            from api.passkeys import registered_credentials as _rc
            if _pffe():
                saved["passkeys_enabled"] = bool(_rc())
                saved["passwordless_enabled"] = bool(_rc()) and not saved["password_auth_enabled"]
            else:
                saved["passkeys_enabled"] = False
                saved["passwordless_enabled"] = False
        except Exception:
            logger.warning("Silent exception in handle_post", exc_info=True)
            pass

        if not new_cookie:
            return j(handler, saved)

        response_body = json.dumps(saved, ensure_ascii=False, indent=2).encode("utf-8")
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(response_body)))
        handler.send_header("Cache-Control", "no-store")
        set_auth_cookie(handler, new_cookie)
        _security_headers(handler)
        handler.end_headers()
        handler.wfile.write(response_body)
        return True

    if parsed.path == "/api/onboarding/oauth/start":
        if not _onboarding_gate_allows(handler):
            return bad(handler, "Onboarding OAuth is only available from local networks when auth is not enabled. To bypass this on a remote server, set HERMES_WEBUI_ONBOARDING_OPEN=1.", 403)
        try:
            return j(handler, start_onboarding_oauth_flow(body), extra_headers={"Cache-Control": "no-store"})
        except ValueError as e:
            return bad(handler, str(e))
        except RuntimeError as e:
            return bad(handler, str(e), 500)

    if parsed.path == "/api/onboarding/oauth/cancel":
        try:
            return j(handler, cancel_onboarding_oauth_flow(body), extra_headers={"Cache-Control": "no-store"})
        except ValueError as e:
            return bad(handler, str(e))

    if parsed.path == "/api/onboarding/setup":
        if not _onboarding_gate_allows(handler):
            return bad(handler, "Onboarding setup is only available from local networks when auth is not enabled. To bypass this on a remote server, set HERMES_WEBUI_ONBOARDING_OPEN=1.", 403)
        try:
            return j(handler, apply_onboarding_setup(body))
        except ValueError as e:
            return bad(handler, str(e))
        except RuntimeError as e:
            return bad(handler, str(e), 500)

    if parsed.path == "/api/onboarding/complete":
        if not _onboarding_gate_allows(handler):
            return bad(handler, "Onboarding is only available from local networks when auth is not enabled. To bypass this on a remote server, set HERMES_WEBUI_ONBOARDING_OPEN=1.", 403)
        return j(handler, complete_onboarding())

    if parsed.path == "/api/onboarding/probe":
        if not _onboarding_gate_allows(handler):
            return bad(handler, "Onboarding probe is only available from local networks when auth is not enabled. To bypass this on a remote server, set HERMES_WEBUI_ONBOARDING_OPEN=1.", 403)
        provider = str((body or {}).get("provider") or "").strip().lower()
        base_url = str((body or {}).get("base_url") or "")
        api_key = str((body or {}).get("api_key") or "").strip() or None
        try:
            return j(handler, probe_provider_endpoint(provider, base_url, api_key))
        except Exception as e:
            logger.warning("Silent exception in handle_post", exc_info=True)
            return bad(handler, f"probe failed: {e}", 500)

    return None


# ── Model Resolution and Profile Model Config (Sprint M3.1) ────────────────
def _starts_token(raw: str, prefix: str) -> bool:
    if not raw.startswith(prefix):
        return False
    rest = raw[len(prefix):]
    return rest == "" or rest[0] in ":/"


def _normalize_provider_id(value: str | None) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    if raw in _PROVIDER_ALIASES:
        return _PROVIDER_ALIASES[raw]
    for prefix, normalized in (
        ("openai-codex", "openai"),
        ("openai", "openai"),
        ("anthropic", "anthropic"),
        ("claude", "anthropic"),
        ("google", "google"),
        ("gemini", "google"),
        ("openrouter", "openrouter"),
        ("custom", "custom"),
    ):
        if _starts_token(raw, prefix):
            return normalized
    # Unknown prefix — return empty so callers treat it as "no match" and pass
    # the model through unchanged rather than incorrectly stripping it.
    return ""


def _catalog_provider_id_sets(catalog: dict) -> tuple[set[str], set[str]]:
    raw_provider_ids: set[str] = set()
    normalized_provider_ids: set[str] = set()
    for group in catalog.get("groups") or []:
        raw = str(group.get("provider_id") or "").strip().lower()
        if not raw:
            continue
        raw_provider_ids.add(raw)
        normalized = _normalize_provider_id(raw)
        if normalized:
            normalized_provider_ids.add(normalized)
    return raw_provider_ids, normalized_provider_ids


def _catalog_has_provider(
    provider_raw: str,
    provider_normalized: str,
    raw_provider_ids: set[str],
    normalized_provider_ids: set[str],
) -> bool:
    return (
        provider_raw in raw_provider_ids
        or (provider_normalized and provider_normalized in raw_provider_ids)
        or (provider_normalized and provider_normalized in normalized_provider_ids)

    )


def _model_matches_active_provider_family(
    model: str,
    active_provider: str,
) -> bool:
    model_lower = model.lower()
    for bare_prefix in ("gpt", "claude", "gemini"):
        if model_lower.startswith(bare_prefix):
            return _normalize_provider_id(bare_prefix) == active_provider
    return False


def _catalog_model_id_matches(candidate: str, model: str) -> bool:
    candidate = str(candidate or "").strip()
    if candidate.startswith("@") and ":" in candidate:
        candidate = candidate.rsplit(":", 1)[1]
    if "/" in candidate:
        candidate = candidate.split("/", 1)[1]
    return candidate.replace("-", ".").lower() == model.replace("-", ".").lower()


def _catalog_group_owns_exact_model(group: dict, model: str) -> bool:
    provider_id = str(group.get("provider_id") or "").strip()
    wrapper = f"@{provider_id}:"
    for bucket in ("models", "extra_models"):
        for entry in group.get(bucket) or []:
            if not isinstance(entry, dict):
                continue
            candidate = str(entry.get("id") or "").strip()
            if candidate.lower().startswith(wrapper.lower()):
                candidate = candidate[len(wrapper):]
            if candidate == model or _catalog_model_id_matches(candidate, model):
                return True
    return False


def _repair_foreign_session_model_provider(
    session,
    *,
    requested_model: str,
    requested_provider: str | None,
    resolved_model: str,
    resolved_provider: str | None,
    explicit_model_pick: bool,
    profile_provider: str | None,
) -> str | None:
    """Repair a stale provider only when the cached catalog names one owner."""
    stored_model = str(getattr(session, "model", "") or "").strip()
    stored_provider = _clean_session_model_provider(getattr(session, "model_provider", None))
    requested_provider = _clean_session_model_provider(requested_provider)
    resolved_provider = _clean_session_model_provider(resolved_provider)
    profile_provider = _clean_session_model_provider(profile_provider)
    _, qualified_provider = _split_provider_qualified_model(requested_model)
    if (
        explicit_model_pick
        or qualified_provider
        or not stored_model
        or not stored_provider
        or (
            str(requested_model or "").strip() != stored_model
            and not _catalog_model_id_matches(str(requested_model or "").strip(), stored_model)
        )
        or requested_provider != stored_provider
        or (
            resolved_model != stored_model
            and not _catalog_model_id_matches(resolved_model, stored_model)
        )
        or resolved_provider != stored_provider
        or not profile_provider
        or profile_provider == stored_provider
    ):
        return resolved_provider

    try:
        catalog = get_available_models(prefer_cache=True)
    except Exception:
        logger.debug("Silent exception in _repair_foreign_session_model_provider", exc_info=True)
        return resolved_provider
    groups = [group for group in catalog.get("groups") or [] if isinstance(group, dict)]
    stored_groups = [
        group
        for group in groups
        if str(group.get("provider_id") or "").strip().lower() == stored_provider
    ]
    if (
        not stored_groups
        or any(group.get("models_endpoint_error") for group in stored_groups)
        or any(_catalog_group_owns_exact_model(group, stored_model) for group in stored_groups)
    ):
        return resolved_provider
    owners = [
        group
        for group in groups
        if str(group.get("provider_id") or "").strip().lower() != stored_provider
        and _catalog_group_owns_exact_model(group, stored_model)
    ]
    if len(owners) != 1:
        return resolved_provider
    return str(owners[0].get("provider_id") or "").strip() or resolved_provider


def _clean_session_model_provider(value: str | None) -> str | None:
    """Normalize a stored/requested provider value to a bare provider ID.

    An ``@``-prefixed value is a provider-qualified *model* hint, so the
    provider is resolved with the shared
    ``config._parse_provider_qualified_model_id()`` grammar rather than a
    positional colon split — that keeps multi-segment custom provider IDs
    (``custom:<slug>``, ``custom:<host>:<port>``) whole while still dropping a
    trailing model segment (#6722). Values without the ``@`` marker are already
    plain provider IDs, whose colons belong to the ID itself, so they are
    preserved verbatim.
    """
    provider = str(value or "").strip().lower()
    if not provider or provider == "default":
        return None
    if provider.startswith("@"):
        parsed = _parse_provider_qualified_model_id(provider)
        provider = parsed[1].strip() if parsed else provider[1:]
    return provider or None


def _split_provider_qualified_model(model: str) -> tuple[str, str | None]:
    """Split an ``@provider:model`` hint into ``(bare_model, provider)``.

    Delegates the grammar to ``config._parse_provider_qualified_model_id()``,
    the shared parser that already knows how to keep a multi-segment custom
    provider ID (``custom:<slug>``, ``custom:<host>:<port>``) intact while
    still letting the model segment carry its own colons for tags such as
    ``:free``. Keeping one parser here means every caller in this module and
    the gateway request path resolve the same provider/model pair (#6722).
    """
    model = str(model or "").strip()
    parsed = _parse_provider_qualified_model_id(model)
    if parsed:
        bare_model, provider_hint = parsed
        provider = _clean_session_model_provider(provider_hint)
        bare = str(bare_model or "").strip()
        if provider and bare:
            return bare, provider
    return model, None


def _model_matches_configured_default(
    session_model: str | None,
    cfg_default: str | None,
    provider: str | None = None,
) -> bool:
    """Return True when ``session_model`` refers to the configured ``model.default``.

    The global ``model.context_length`` cap applies ONLY to the default model
    (#3256/#3263). An exact string compare is not enough because ``model.default``
    and the session model can be stored in different but equivalent shapes:
      - bare:            ``claude-opus-4.8``
      - slash-prefixed:  ``anthropic/claude-opus-4.8``  (OpenRouter-style)
      - @provider:model: ``@anthropic:claude-opus-4.8``

    Matching rule (correct in both directions):
      1. Identical strings → match.
      2. Otherwise compare BARE model ids — BUT only after a provider-compatibility
         check: if BOTH sides carry an identifiable provider (from a ``provider/``
         prefix, an ``@provider:`` qualifier, or the explicit ``provider`` arg for
         the session side) and those providers DIFFER, it is NOT a match. This
         stops a non-default model on a different provider that happens to share a
         bare name (``openai/gpt-4o`` vs default ``openrouter/gpt-4o``) from being
         treated as the default and wrongly receiving its cap.
      3. When a provider can't be identified on one side, fall through to the bare
         comparison (lenient-when-unknown — a bare default config still matches a
         bare/prefixed session model).
    Empty default → no match.
    """
    sess = str(session_model or "").strip()
    default = str(cfg_default or "").strip()
    if not sess or not default:
        return False
    if sess == default:
        return True

    def _split(value: str) -> tuple[str, str | None]:
        """Return (bare_model, provider_or_None) for any of the 3 shapes."""
        value = str(value or "").strip()
        # @provider:model
        unq, q_prov = _split_provider_qualified_model(value)
        if q_prov:
            return unq.strip(), str(q_prov).strip().lower()
        # provider/model (single leading slash segment)
        if "/" in value:
            prefix, rest = value.split("/", 1)
            return rest.strip(), prefix.strip().lower()
        return value, None

    sess_bare, sess_prov = _split(sess)
    default_bare, default_prov = _split(default)
    # The explicit provider arg is the session side's provider when the model
    # string itself didn't carry one.
    if not sess_prov and provider:
        sess_prov = str(provider).strip().lower() or None

    if not sess_bare or not default_bare or sess_bare != default_bare:
        return False
    # Bare ids match. Reject only when both sides name DIFFERENT providers.
    if sess_prov and default_prov and sess_prov != default_prov:
        return False
    return True


class _ContextLengthLookupInputs:
    __slots__ = ("config_context_length", "custom_providers", "base_url", "provider", "api_key")

    def __init__(
        self,
        *,
        config_context_length: int | None = None,
        custom_providers: list | None = None,
        base_url: str = "",
        provider: str = "",
        api_key: str = "",
    ) -> None:
        self.config_context_length = config_context_length
        self.custom_providers = custom_providers
        self.base_url = base_url
        self.provider = provider
        self.api_key = api_key


def _positive_context_length(value) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _model_lookup_candidates(model: str) -> tuple[str, ...]:
    raw = str(model or "").strip()
    candidates = []
    for candidate in (raw, _split_provider_qualified_model(raw)[0]):
        if candidate and candidate not in candidates:
            candidates.append(candidate)
        if "/" in candidate:
            bare = candidate.split("/", 1)[1].strip()
            if bare and bare not in candidates:
                candidates.append(bare)
    return tuple(candidates)


def _models_config_context_length(models_cfg, model: str) -> int | None:
    candidates = _model_lookup_candidates(model)
    if isinstance(models_cfg, dict):
        for candidate in candidates:
            entry = models_cfg.get(candidate)
            raw_ctx = entry.get("context_length") if isinstance(entry, dict) else entry
            ctx = _positive_context_length(raw_ctx)
            if ctx is not None:
                return ctx
    if isinstance(models_cfg, list):
        for entry in models_cfg:
            if not isinstance(entry, dict):
                continue
            entry_model = str(entry.get("id") or entry.get("model") or entry.get("name") or "").strip()
            if entry_model in candidates:
                ctx = _positive_context_length(entry.get("context_length"))
                if ctx is not None:
                    return ctx
    return None


def _canonical_context_provider(value: str | None) -> str:
    provider = _clean_session_model_provider(value) or ""
    if not provider:
        return ""
    try:
        from api.config import _resolve_provider_alias

        provider = _resolve_provider_alias(provider)
    except Exception:
        logger.debug("Silent exception in _canonical_context_provider", exc_info=True)
        pass
    return str(provider or "").strip().lower()


def _custom_provider_slug_for_context(name: object) -> str:
    try:
        from api.config import _custom_provider_slug_from_name

        return _custom_provider_slug_from_name(name)
    except Exception:
        logger.debug("Silent exception in _custom_provider_slug_for_context", exc_info=True)
        raw = str(name or "").strip().lower()
        if not raw:
            return ""
        if raw.startswith("custom:"):
            return raw
        slug = re.sub(r"[^a-z0-9._-]+", "-", raw).strip("-")
        slug = re.sub(r"-{2,}", "-", slug)
        return f"custom:{slug}" if slug else ""


def _providers_match_for_context(config_key: object, requested_provider: str) -> bool:
    if not requested_provider:
        return False
    raw_key = str(config_key or "").strip().lower()
    key = _canonical_context_provider(raw_key)
    requested = _canonical_context_provider(requested_provider)
    return bool(
        requested
        and (
            raw_key == requested
            or key == requested
            or raw_key == str(requested_provider or "").strip().lower()
        )
    )


def _custom_provider_api_key_for_context(entry: dict, provider: str) -> str:
    """Resolve the API key for a matched ``custom_providers`` entry.

    Static session hydration/update routes already have a per-profile config
    snapshot. Resolve from the matched entry instead of re-reading global config,
    while preserving the same literal, ``${ENV_VAR}``, ``key_env``, and
    sanitized-env shapes used by streaming/provider resolution.
    """
    raw_api_key = entry.get("api_key")
    if raw_api_key is not None:
        api_key_text = str(raw_api_key).strip()
        if api_key_text.startswith("${") and api_key_text.endswith("}") and len(api_key_text) > 3:
            env_name = api_key_text[2:-1]
            resolved = os.getenv(env_name, "").strip()
            if resolved:
                return resolved
            logger.debug(
                "Custom provider %s api_key references %s, but the environment variable is unset or empty",
                provider,
                api_key_text,
            )
        elif api_key_text:
            return api_key_text

    key_env = str(entry.get("key_env") or "").strip()
    if key_env:
        resolved = os.getenv(key_env, "").strip()
        if resolved:
            return resolved

    try:
        from api.config import _lookup_custom_api_key_env

        return _lookup_custom_api_key_env(provider) or ""
    except Exception:
        logger.debug("Silent exception in _custom_provider_api_key_for_context", exc_info=True)
        return ""


def _context_length_config_api_key_for_provider(
    provider: str | None,
    cfg: dict | None,
) -> str:
    """Return a config/env API key usable for context-window metadata lookup."""
    cfg = cfg if isinstance(cfg, dict) else {}
    provider = _canonical_context_provider(provider)

    def _resolve_key(raw_api_key, raw_key_env=None) -> str:
        api_key_text = str(raw_api_key or "").strip()
        if (
            api_key_text.startswith("${")
            and api_key_text.endswith("}")
            and len(api_key_text) > 3
        ):
            resolved = os.getenv(api_key_text[2:-1], "").strip()
            if resolved:
                return resolved
        elif api_key_text:
            return api_key_text
        key_env = str(raw_key_env or "").strip()
        if key_env:
            resolved = os.getenv(key_env, "").strip()
            if resolved:
                return resolved
        return ""

    providers_cfg = cfg.get("providers") or {}
    if isinstance(providers_cfg, dict):
        for provider_key, provider_cfg in providers_cfg.items():
            if not isinstance(provider_cfg, dict):
                continue
            if not _providers_match_for_context(provider_key, provider):
                continue
            api_key = _resolve_key(provider_cfg.get("api_key"), provider_cfg.get("key_env"))
            if api_key:
                return api_key

    model_cfg = cfg.get("model", {})
    if isinstance(model_cfg, dict):
        model_provider = _canonical_context_provider(model_cfg.get("provider"))
        if not provider or _providers_match_for_context(model_provider, provider):
            api_key = _resolve_key(model_cfg.get("api_key"), model_cfg.get("key_env"))
            if api_key:
                return api_key
    return ""


def _context_length_lookup_inputs_for_model(
    model: str | None,
    provider: str | None = None,
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    cfg: dict | None = None,
) -> _ContextLengthLookupInputs:
    """Return the effective metadata resolver inputs for a WebUI model.

    ``agent.model_metadata.get_model_context_length`` understands global
    ``config_context_length`` and custom-provider overrides, but only when the
    matching base URL is supplied. WebUI also owns ``providers.<provider>.models``
    overrides, so normalize those here and keep route/session-save/SSE aligned.
    """
    model_for_lookup = str(model or "").strip()
    if not model_for_lookup:
        return _ContextLengthLookupInputs()

    if cfg is None:
        try:
            from api.config import get_config as _get_config_for_cl

            cfg = _get_config_for_cl()
        except Exception:
            logger.debug("Silent exception in _context_length_lookup_inputs_for_model", exc_info=True)
            cfg = {}
    cfg = cfg if isinstance(cfg, dict) else {}

    bare_model, explicit_provider = _split_provider_qualified_model(model_for_lookup)
    effective_provider = _canonical_context_provider(provider or explicit_provider)
    effective_base_url = str(base_url or "").strip()

    model_cfg = cfg.get("model", {}) if isinstance(cfg, dict) else {}
    if isinstance(model_cfg, dict):
        if not effective_provider:
            effective_provider = _canonical_context_provider(model_cfg.get("provider"))
        if not effective_base_url:
            effective_base_url = str(model_cfg.get("base_url") or "").strip()

    custom_providers = cfg.get("custom_providers") if isinstance(cfg, dict) else None
    if not isinstance(custom_providers, list):
        custom_providers = None

    provider_context_length = None
    providers_cfg = (cfg.get("providers") or {}) if isinstance(cfg, dict) else {}
    if isinstance(providers_cfg, dict):
        for provider_key, provider_cfg in providers_cfg.items():
            if not isinstance(provider_cfg, dict):
                continue
            if not _providers_match_for_context(provider_key, effective_provider):
                continue
            if not effective_base_url:
                effective_base_url = str(provider_cfg.get("base_url") or "").strip()
            provider_context_length = _models_config_context_length(
                provider_cfg.get("models"),
                bare_model or model_for_lookup,
            )
            break

    custom_context_length = None
    effective_api_key = str(api_key or "").strip()
    if custom_providers:
        target_base = effective_base_url.rstrip("/")
        model_candidates = set(_model_lookup_candidates(bare_model or model_for_lookup))
        for entry in custom_providers:
            if not isinstance(entry, dict):
                continue
            entry_name = str(entry.get("name") or "").strip()
            entry_slug = _custom_provider_slug_for_context(entry_name)
            entry_base = str(entry.get("base_url") or "").strip()
            entry_base_norm = entry_base.rstrip("/")
            provider_matches = bool(
                effective_provider
                and (
                    effective_provider == entry_slug
                    or effective_provider == entry_name.lower()
                    or (effective_provider == "custom" and len(custom_providers) == 1)
                )
            )
            base_matches = bool(target_base and entry_base_norm and target_base == entry_base_norm)
            model_matches = bool(model_candidates.intersection(set(_model_lookup_candidates(entry.get("model")))))
            models_cfg = entry.get("models")
            if isinstance(models_cfg, dict):
                model_matches = model_matches or any(candidate in models_cfg for candidate in model_candidates)
            if not (provider_matches or base_matches or (not effective_provider and model_matches)):
                continue
            if not effective_provider and entry_slug:
                effective_provider = entry_slug
            if not effective_base_url and entry_base:
                effective_base_url = entry_base
            if not effective_api_key:
                effective_api_key = _custom_provider_api_key_for_context(entry, effective_provider or entry_slug)
            custom_context_length = _models_config_context_length(models_cfg, bare_model or model_for_lookup)
            break

    global_context_length = None
    if isinstance(model_cfg, dict):
        cfg_default_model = str(model_cfg.get("default") or "").strip()
        raw_cfg_ctx = model_cfg.get("context_length")
        if raw_cfg_ctx is not None and (
            not cfg_default_model
            or _model_matches_configured_default(
                model_for_lookup,
                cfg_default_model,
                effective_provider,
            )
        ):
            global_context_length = _positive_context_length(raw_cfg_ctx)

    if not effective_api_key:
        effective_api_key = _context_length_config_api_key_for_provider(effective_provider, cfg)

    return _ContextLengthLookupInputs(
        config_context_length=provider_context_length or custom_context_length or global_context_length,
        custom_providers=custom_providers,
        base_url=effective_base_url,
        provider=effective_provider,
        api_key=effective_api_key,
    )



def _should_attach_codex_provider_context(model: str, raw_active_provider: str, catalog: dict) -> bool:
    """Return True when a bare Codex model needs separate provider context.

    OpenAI, OpenAI Codex, Copilot, and OpenRouter can all expose GPT-looking
    bare names. If a session stores only ``gpt-...`` while Codex is active, a
    later provider-list/default-model round trip can lose the user's Codex
    choice. Store the provider separately instead of converting the persisted
    model to ``@openai-codex:model``.
    """
    if raw_active_provider != "openai-codex":
        return False
    if not model.lower().startswith("gpt"):
        return False
    for group in catalog.get("groups") or []:
        if str(group.get("provider_id") or "").strip().lower() != "openai-codex":
            continue
        return any(
            _catalog_model_id_matches(entry.get("id"), model)
            for entry in group.get("models", [])
            if isinstance(entry, dict)
        )
    return False

def _read_profile_model_config(
    session,
    requested_provider: str | None,
) -> tuple[str | None, str | None, dict | None]:
    """Read model.provider, model.default, and the full profile config dict.

    Returns (profile_provider, profile_default_model, profile_config_dict).
    The first two are None when the session has no profile or the profile config
    is unreadable; profile_config_dict is None in the same cases so callers only
    pay for one YAML parse.

    When the session already has an explicit ``requested_provider``, the profile
    ``model.provider`` is not returned (first tuple element is None) so profile
    does not override the session provider. ``profile_default_model`` is still
    returned for suffix repair (#5127) only when the profile's configured
    provider matches ``requested_provider`` after normalization.

    perf(webui/session-load-latency) tier2a: the parse is wrapped in a
    per-process LRU keyed by (profile_name, config_mtime, size). The
    function fires on every chat-open for sessions under a named
    profile (resolve_model=1 path), and the YAML parse alone is
    hundreds of µs to single-digit ms on the Chromebook. Cache TTL
    60s is a backstop in case mtime resolution is poor on a given
    filesystem; under normal edits the mtime changes and invalidates
    immediately.
    """
    if not getattr(session, "profile", None):
        return None, None, None

    try:
        from api.profiles import get_agy_home_for_profile

        _profile_name = str(session.profile or "")
        _profile_home = get_agy_home_for_profile(_profile_name)
        _profile_cfg_path = os.path.join(str(_profile_home), "config.yaml")
        if not os.path.isfile(_profile_cfg_path):
            return None, None, None
        _pcfg = _read_profile_config_cached(_profile_name, _profile_cfg_path)
        if _pcfg is None:
            return None, None, None
        _model_cfg = _pcfg.get("model") or {}
        if not isinstance(_model_cfg, dict):
            return None, None, _pcfg
        _provider = (_model_cfg.get("provider") or "").strip() or None
        _default = (_model_cfg.get("default") or "").strip() or None
    except Exception:
        logger.warning(
            "profile provider read failed for %r",
            getattr(session, "profile", None),
            exc_info=True,
        )
        return None, None, None

    _requested = _clean_session_model_provider(requested_provider)
    if _requested:
        _profile_prov = _clean_session_model_provider(_provider)
        if _profile_prov != _requested:
            return None, None, _pcfg
        return None, _default, _pcfg
    return _provider, _default, _pcfg


# perf(webui/session-load-latency) tier2a: process-wide cache for parsed
# profile config.yaml. Key = (profile_name, inode, mtime, size); value = parsed
# dict. inode tracks atomic-rename edits (most editors replace files, giving a
# new inode on Linux). mtime+size auto-invalidates on in-place edits; a 60s TTL
# is the backstop in case of coarse mtime resolution (some network filesystems
# round mtime to whole seconds — the size guard catches a write of equal-length
# bytes within the same second). On cache hit, a full-content comparison catches
# any in-place rewrite that inode+mtime+size missed (Greptile P1, PR#5803
# discussion_r3548477915). Reading and comparing the full file content (~1-10KB)
# is much cheaper than yaml.safe_load(). Reads are guarded by a single Lock to
# keep the hot path simple; the underlying yaml.safe_load is the slow step, not
# the lock, so contention is bounded.
_PROFILE_CONFIG_CACHE: "dict[tuple, tuple[float, str, dict]]" = {}
_PROFILE_CONFIG_CACHE_TTL_SECONDS = 60.0
_PROFILE_CONFIG_CACHE_LOCK = threading.Lock()


def _read_profile_config_cached(profile_name: str, cfg_path: str) -> dict | None:
    """Return parsed profile config, caching by (inode, mtime, size) with
    TTL backstop and full-content verification.

    The full-content comparison reads the current file and compares it to a
    copy stored in the cache entry. This catches any in-place rewrite where
    inode+mtime+size are identical, regardless of where in the file the change
    occurs — unlike a fixed-length prefix comparison, edits to fields after the
    first N characters are always detected. Reading and comparing the full file
    content (~1-10KB for a typical config.yaml) is much cheaper than
    yaml.safe_load().

    NOTE: The cache key uses inode+mtime+size to handle the common cases
    (atomic-rename editors -> new inode; in-place editors -> mtime/size
    change). The full-content comparison is a backstop for the rare case where
    all three collide (e.g., sed -i on a filesystem with coarse mtime
    resolution, writing the same byte count).
    """
    try:
        st = os.stat(cfg_path)
    except OSError:
        return None
    mtime = float(getattr(st, "st_mtime", 0.0) or 0.0)
    size = int(getattr(st, "st_size", 0) or 0)
    inode = int(getattr(st, "st_ino", 0) or 0)
    key = (str(profile_name or ""), inode, mtime, size)
    now = time.monotonic()
    with _PROFILE_CONFIG_CACHE_LOCK:
        cached = _PROFILE_CONFIG_CACHE.get(key)
        if cached is not None:
            cached_at, cached_content, cached_dict = cached
            if (now - cached_at) <= _PROFILE_CONFIG_CACHE_TTL_SECONDS:
                # Full content comparison catches any in-place rewrite where
                # inode+mtime+size are identical but the file content changed.
                # Reading and comparing the full file (~1-10KB) is cheaper than
                # yaml.safe_load(). Unlike a fixed-length prefix, this detects
                # edits anywhere in the file. Greptile P1 (PR#5803).
                _current_content = None
                try:
                    with open(cfg_path, "r", encoding="utf-8") as _f:
                        _current_content = _f.read()
                except Exception:
                    logger.debug("Silent exception in _read_profile_config_cached", exc_info=True)
                    pass
                if _current_content == cached_content:
                    return cached_dict
                # Content changed while key collided — fall through to re-parse
    import yaml
    try:
        with open(cfg_path, encoding="utf-8") as _f:
            content = _f.read()
            parsed = yaml.safe_load(content) or {}
    except Exception:
        logger.debug("Silent exception in _read_profile_config_cached", exc_info=True)
        return None
    if not isinstance(parsed, dict):
        return None
    with _PROFILE_CONFIG_CACHE_LOCK:
        _PROFILE_CONFIG_CACHE[key] = (now, content, parsed)
        # Cap the cache at 32 entries; profiles are bounded in practice
        # and unbounded growth would be a leak.
        if len(_PROFILE_CONFIG_CACHE) > 32:
            # Drop the oldest entry by insertion order (dict is ordered).
            for old_key in list(_PROFILE_CONFIG_CACHE.keys())[:max(0, len(_PROFILE_CONFIG_CACHE) - 32)]:
                _PROFILE_CONFIG_CACHE.pop(old_key, None)
    return parsed


def _load_profile_config_dict(session) -> dict | None:
    """Load the session profile's config.yaml as a dict, or None."""
    if not getattr(session, "profile", None):
        return None
    try:
        from api.profiles import get_agy_home_for_profile

        _profile_cfg_path = os.path.join(
            str(get_agy_home_for_profile(session.profile)),
            "config.yaml",
        )
        if not os.path.isfile(_profile_cfg_path):
            return None
        import yaml

        with open(_profile_cfg_path, encoding="utf-8") as _f:
            _pcfg = yaml.safe_load(_f) or {}
        return _pcfg if isinstance(_pcfg, dict) else None
    except Exception:
        logger.warning(
            "profile config read failed for %r",
            getattr(session, "profile", None),
            exc_info=True,
        )
        return None


def _ordered_custom_provider_model_ids(entry: dict) -> list[str]:
    """Model ids from a custom_providers entry (default model + dict/list models)."""
    ordered: list[str] = []
    _cp_model = str(entry.get("model") or "").strip()
    if _cp_model:
        ordered.append(_cp_model)
    _cp_models = entry.get("models")
    if isinstance(_cp_models, dict):
        for _key in _cp_models.keys():
            if isinstance(_key, str):
                _kid = _key.strip()
                if _kid and _kid not in ordered:
                    ordered.append(_kid)
    elif isinstance(_cp_models, list):
        for _item in _cp_models:
            if isinstance(_item, str):
                _mid = _item.strip()
                if _mid and _mid not in ordered:
                    ordered.append(_mid)
            elif isinstance(_item, dict):
                _mid = str(
                    _item.get("id") or _item.get("model") or _item.get("name") or ""
                ).strip()
                if _mid and _mid not in ordered:
                    ordered.append(_mid)
    return ordered


def _repair_bare_custom_provider_model(
    bare_model: str,
    provider: str | None,
    *,
    config_obj: dict | None = None,
) -> str | None:
    """Re-qualify a bare model ID using the named custom provider's config (#5314).

    Returns the fully namespaced model id when ``bare_model`` matches the suffix
    of a registered id on ``custom_providers``; otherwise None. Model ids are
    scanned in config declaration order (default ``model`` first, then
    ``models`` dict keys or list entries) so repair is deterministic when
    suffixes collide.

    When ``config_obj`` is set (typically the session profile's config.yaml),
    only that object's ``custom_providers`` are scanned. Otherwise uses
    ``get_config()`` for the active global config (not the raw ``cfg`` alias).
    """
    try:
        model = str(bare_model or "").strip()
        prov = _clean_session_model_provider(provider)
        if not model or "/" in model or not prov:
            return None
        if prov != "custom" and not str(prov).startswith("custom:"):
            return None
        from api.config import (
            _custom_provider_entries,
            _custom_provider_slug_from_name,
            get_config,
        )

        if isinstance(config_obj, dict):
            _entries = _custom_provider_entries(config_obj)
        else:
            _cfg = get_config()
            _entries = _custom_provider_entries(
                _cfg if isinstance(_cfg, dict) else None
            )
        prov_norm = str(prov).strip().lower()
        raw_suffix = prov_norm.removeprefix("custom:")
        _matching_cp = None
        for _entry in _entries:
            entry_name = str(_entry.get("name") or "").strip().lower()
            slug = _custom_provider_slug_from_name(_entry.get("name"))
            if not slug:
                continue
            if (
                prov_norm in {entry_name, slug}
                or raw_suffix == slug.removeprefix("custom:")
            ):
                _matching_cp = _entry
                break
        if not _matching_cp:
            return None
        for _id in _ordered_custom_provider_model_ids(_matching_cp):
            if "/" in _id and _id.rsplit("/", 1)[-1] == model:
                return _id
        return None
    except Exception:
        logger.debug("Silent exception in _repair_bare_custom_provider_model", exc_info=True)
        return None


def _moa_fast_path_model_state(model: str) -> tuple[str, str, bool]:
    """Strip an optional ``@moa:``/``moa/`` prefix from an MoA-routed model.

    Split out of ``_resolve_compatible_session_model_state`` so the MoA
    fast-path stays a single-line call in that function body (see
    ``test_issue1855_resolve_model_provider_fast_path.py``, the fast-path/
    catalog-call ordering check scans a bounded window of that function's
    source, and inlining this here previously pushed the catalog call just
    past that window).
    """
    if model.startswith("@moa:"):
        return model.split(":", 1)[1].strip(), "moa", True
    if model.lower().startswith("moa/"):
        return model.split("/", 1)[1].strip(), "moa", True
    return model, "moa", False


def _resolve_compatible_session_model_state(
    model_id: str | None,
    model_provider: str | None = None,
    *,
    profile_provider: str | None = None,
    profile_default_model: str | None = None,
    profile_config: dict | None = None,
    explicit_model_pick: bool = False,
    prefer_cached_catalog: bool = False,
) -> tuple[str, str | None, bool]:
    """Return (effective_model, effective_provider, model_was_normalized).

    Sessions can outlive provider changes. When an older session still points at
    a different provider namespace (for example `gemini/...` after switching the
    agent to OpenAI Codex), reusing that stale model causes chat startup to hit
    the wrong backend and fail. Normalize only obvious cross-provider mismatches.
    When a model has an explicit provider context, keep the model string itself
    in its picker/API shape and carry the provider as separate state.

    Fast path (#1855): when the caller supplies both a model and an explicit
    ``model_provider`` AND the model is not itself ``@provider:model``-qualified,
    we can return the inputs verbatim without calling ``get_available_models()``.
    The slow path below would arrive at the same answer via
    ``if requested_provider and not explicit_provider: return model, requested_provider, False``
    after paying the full catalog-build cost. Avoiding the catalog here keeps
    ``POST /api/chat/start`` snappy even when the model catalog is cold and the
    rebuild has to make network calls (custom OpenAI-compat endpoints,
    OpenRouter ``/models``, LM Studio ``/models``, credential pool refresh),
    those used to wedge the handler for >100s and trigger 502s on default-60s
    reverse proxies, even though the WebUI itself eventually responded.

    ``prefer_cached_catalog=True`` (ours-original) makes the catalog lookup
    non-blocking: it resolves from the warm/disk cache or a network-free
    minimal catalog and NEVER triggers a live per-provider rebuild (the
    Copilot token-exchange HTTPS call that hangs a server-initiated wakeup
    turn, see rebase report §1/§3/model-resolve-hang). Human-initiated
    chat/start leaves this False to keep full live discovery; a session that
    already has a persisted model still resolves correctly because the
    persisted model wins over the catalog and the catalog is only consulted
    for the default-model backstop.
    """
    model = str(model_id or "").strip()
    requested_provider = _clean_session_model_provider(model_provider)
    if model and requested_provider == "moa":
        return _moa_fast_path_model_state(model)
    if model and requested_provider and model.startswith(f"@{requested_provider}:"):
        try:
            from api.config import cfg as _active_cfg

            providers_cfg = _active_cfg.get("providers") if isinstance(_active_cfg, dict) else {}
        except Exception:
            logger.debug("Silent exception in _resolve_compatible_session_model_state", exc_info=True)
            providers_cfg = {}
        if isinstance(providers_cfg, dict) and requested_provider in providers_cfg:
            return model, requested_provider, False
    if model and requested_provider:
        # Only safe when the model itself does not carry an ``@provider:model``
        # qualifier — qualified strings require the catalog to decide whether
        # the qualifier matches the active provider (see slow path below).
        bare_model, explicit_provider = _split_provider_qualified_model(model)
        model_prefix = model.split("/", 1)[0].strip().lower() if "/" in model else ""
        stale_codex_openai_slash_id = (
            requested_provider == "openai-codex"
            and model_prefix == "openai"
        )
        if not explicit_provider and not stale_codex_openai_slash_id:
            _profile_default = str(profile_default_model or "").strip()
            _profile_prov = _clean_session_model_provider(profile_provider)
            _providers_match_for_repair = (
                _profile_prov is None or _profile_prov == requested_provider
            )
            if (
                _profile_default
                and "/" in _profile_default
                and "/" not in model
                and _profile_default.rsplit("/", 1)[-1] == model
                and _providers_match_for_repair
                and (
                    requested_provider == "custom"
                    or str(requested_provider).startswith("custom:")
                )
            ):
                return _profile_default, requested_provider, True

            _repaired_model = _repair_bare_custom_provider_model(
                model,
                requested_provider,
                config_obj=profile_config,
            )
            if _repaired_model:
                return _repaired_model, requested_provider, True

            return model, requested_provider, False

    # Default (human chat/start) path calls get_available_models() with NO
    # kwargs so it stays signature-compatible with the many tests that stub
    # get_available_models as a zero-arg callable. Only the server-side wakeup
    # path (prefer_cached_catalog=True) opts into the cache-only mode. Some
    # tests monkeypatch get_available_models as a zero-arg callable, so probe
    # the (possibly monkeypatched) signature for ``prefer_cache`` rather than
    # catching TypeError — a blanket ``except TypeError`` would also swallow a
    # genuine TypeError raised *inside* get_available_models(prefer_cache=True)
    # and silently fall back to the slow live provider rebuild that
    # prefer_cached_catalog=True is meant to avoid.
    if prefer_cached_catalog:
        import inspect as _inspect

        try:
            _gam_accepts_prefer_cache = (
                "prefer_cache" in _inspect.signature(get_available_models).parameters
            )
        except (TypeError, ValueError):
            # Builtins / C-callables can refuse introspection; assume the
            # zero-arg stub shape in that case.
            _gam_accepts_prefer_cache = False
        if _gam_accepts_prefer_cache:
            catalog = get_available_models(prefer_cache=True)
        else:
            catalog = get_available_models()
    else:
        catalog = get_available_models()
    default_model = str(catalog.get("default_model") or DEFAULT_MODEL or "").strip()

    # Profile-aware resolution: when the caller supplies profile context
    # (not an explicit per-chat override), use the profile's provider and
    # default model as the resolution context instead of the catalog's
    # active_provider / default_model. This preserves the repair path
    # (stale models still get normalized) but normalizes to the profile's
    # default model under the profile's provider rather than the global default.
    bare_model, explicit_provider = _split_provider_qualified_model(model) if model else ("", None)
    if profile_provider and not explicit_provider:
        _profile_provider_normalized = _normalize_provider_id(profile_provider)
        _profile_default = str(profile_default_model or "").strip()
        if not model:
            _fallback = _profile_default or default_model
            return _fallback, profile_provider, bool(_fallback)

        model_prefix = model.split("/", 1)[0].strip().lower() if "/" in model else ""
        model_provider_from_name = _normalize_provider_id(model_prefix) if "/" in model else ""

        model_family = ""
        if "/" not in model:
            model_lower = model.lower()
            for bare_prefix in ("gpt", "claude", "gemini"):
                if model_lower.startswith(bare_prefix):
                    model_family = _normalize_provider_id(bare_prefix)
                    break

        if model_family and model_family != _profile_provider_normalized:
            if explicit_model_pick:
                # User explicitly chose a cross-family model; honor it (#3737)
                return model, profile_provider, False
            _target = _profile_default or default_model
            return _target, profile_provider, True

        if (
            "/" in model
            and str(profile_provider).strip().lower() == "openai-codex"
            and model_provider_from_name == "openai"
        ):
            _target = _profile_default or default_model
            return _target, profile_provider, True

        # Slash-qualified models (e.g. openai/gpt-5.4-mini) are native IDs on
        # OpenRouter and custom providers, not cross-provider artifacts. Only
        # repair when the profile provider actually requires a different family.
        if "/" in model and _profile_provider_normalized in {"openrouter", "custom", ""}:
            return model, profile_provider, False

        if "/" in model and model_provider_from_name and model_provider_from_name != _profile_provider_normalized:
            _target = _profile_default or default_model
            return _target, profile_provider, True

        # Async server-side continuations (for example delegate_task completion
        # re-entry) can arrive here with profile context but without a usable
        # requested_provider, bypassing the fast-path custom-provider repair
        # above. If the profile's configured custom-provider default is a
        # slash-qualified model whose suffix matches the bare session model,
        # repair back to the profile default before the provider call (#5225).
        if (
            "/" not in model
            and _profile_default
            and "/" in _profile_default
            and _profile_default.rsplit("/", 1)[-1] == model
            and (
                _profile_provider_normalized == "custom"
                or str(profile_provider).startswith("custom:")
            )
        ):
            return _profile_default, profile_provider, True

        _repaired_model = _repair_bare_custom_provider_model(
            model,
            profile_provider,
            config_obj=profile_config,
        )
        if _repaired_model:
            return _repaired_model, profile_provider, True

        return model, profile_provider, False

    if not model:
        return default_model, requested_provider, bool(default_model)

    active_provider = _normalize_provider_id(catalog.get("active_provider"))
    # Also keep the raw active_provider slug for cross-provider detection with
    # non-listed providers (ollama-cloud, deepseek, xai, etc.) that _normalize_provider_id
    # returns "" for. If the raw provider is set but normalization returned "", we still
    # want to detect that a session model from a known provider (e.g. openai/gpt-5.4-mini)
    # is stale relative to this unknown active provider. (#1023)
    raw_active_provider = str(catalog.get("active_provider") or "").strip().lower()
    if not active_provider and not raw_active_provider:
        bare_model, explicit_provider = _split_provider_qualified_model(model)
        return model, explicit_provider or requested_provider, False

    bare_for_context, explicit_provider = _split_provider_qualified_model(model)
    if requested_provider and not explicit_provider:
        model_prefix = model.split("/", 1)[0].strip().lower() if "/" in model else ""
        stale_codex_openai_slash_id = (
            raw_active_provider == "openai-codex"
            and requested_provider == "openai-codex"
            and model_prefix == "openai"
        )
        if not stale_codex_openai_slash_id:
            return model, requested_provider, False

    if model.startswith("@") and ":" in model:
        provider_raw = explicit_provider or ""
        provider_normalized = _normalize_provider_id(provider_raw)
        bare_model = bare_for_context.strip()
        if not provider_raw or not bare_model:
            return model, requested_provider, False

        # A fresh, explicit user pick is by definition not a stale artifact, so
        # honor the @provider:model exactly as chosen — never reroute it via the
        # active-provider family repair or the cold-catalog fallback below (a bare
        # id like "gpt-oss-120b" under an OpenAI-active agent would otherwise get
        # pulled to OpenAI by the family-match branch). If the named provider is
        # unreachable the user sees a clear run-time error rather than a silent
        # model swap. Must sit above the family-match repair (#3737 principle).
        if explicit_model_pick:
            return model, provider_raw, False

        raw_provider_ids, normalized_provider_ids = _catalog_provider_id_sets(catalog)
        hint_matches_active = (
            provider_raw == raw_active_provider
            or provider_raw == active_provider
            or (provider_normalized and provider_normalized == active_provider)
        )
        if hint_matches_active:
            # The @provider:model hint explicitly names the active provider, so this
            # selection is intentional — not a stale cross-provider artifact. Return
            # the full @provider:model string unchanged so downstream (resolve_model_provider
            # in config.py) can route through the correct provider. Stripping the prefix
            # here would collapse duplicate model IDs from different providers back to the
            # bare ID, causing the first matching provider to win on the next UI render
            # and the wrong provider to be used for the agent run. (#1253)
            return model, provider_raw, False

        if _catalog_has_provider(
            provider_raw,
            provider_normalized,
            raw_provider_ids,
            normalized_provider_ids,
        ):
            return model, provider_raw, False

        if _model_matches_active_provider_family(bare_model, active_provider):
            provider_context = (
                raw_active_provider
                if _should_attach_codex_provider_context(bare_model, raw_active_provider, catalog)
                else None
            )
            return bare_model, provider_context, True
        # On NON-explicit resolves (2nd+ turn, chat switch — explicit picks already
        # returned above), preserve the selection only when all three hold:
        #
        #   * provider_normalized == "" — a non-first-party provider hint
        #     (ollama-cloud / deepseek / xai / a named custom proxy). First-party
        #     families fall through to the stale-cross-provider repair below.
        #
        #   * the BARE model is not a first-party family id (does not start with
        #     gpt/claude/gemini), i.e. not a misrouted first-party model that a
        #     vanished provider used to host (e.g. "@copilot:claude-opus-4.6").
        #
        #   * the provider is KNOWN or CONFIGURED. This is the load-bearing
        #     distinction: catalog-absence has two causes —
        #       (a) a cold live-discovery provider (ollama-cloud is configured; its
        #           group just isn't in this cached snapshot yet) → preserve, and
        #       (b) a genuinely removed/unknown provider ("@removed:mistral-large"
        #           configured nowhere) → fall through to the default so chat/start
        #           doesn't route to an unreachable provider.
        #     _provider_is_known_or_configured() decides this from the static
        #     provider registry + config state, NOT from the cold catalog snapshot
        #     (re-deriving that live would defeat the prefer_cached_catalog win).
        #
        # DELIBERATE: the registry test treats a KNOWN built-in (deepseek, minimax,
        # ollama-cloud, …) as preservable even when the user has no key configured
        # for it. We accept this on purpose. The only fully-reliable "is this
        # provider authenticated" signal is the live auth store / catalog rebuild —
        # exactly the cost this hot path avoids — and a cheap config/env-only check
        # would mis-classify providers configured via OAuth/auth-store (ollama-cloud
        # among them), re-introducing the original silent-revert bug for them. So a
        # known-but-unconfigured pick is kept; the user gets a clear run-time auth
        # error instead of a silent swap to the default. Pinned by
        # test_at_provider_known_unconfigured_builtin_is_intentionally_preserved.
        #
        # KNOWN LIMITATION: the first-party-family test is a bare-name prefix match
        # (the same approximation _model_matches_active_provider_family uses). A
        # genuine third-party model whose name merely *starts* with gpt/claude/
        # gemini (e.g. "@ollama:gpt4all-mini") is therefore still mis-classified as
        # first-party and reverted on non-explicit paths. A name-based check cannot
        # disambiguate that; the behavior is pinned by
        # test_at_provider_first_party_named_third_party_model_known_limitation.
        _bare_is_first_party_family = any(
            bare_model.lower().startswith(_p) for _p in ("gpt", "claude", "gemini")
        )
        if (
            not provider_normalized
            and not _bare_is_first_party_family
            and _provider_is_known_or_configured(provider_raw)
        ):
            return model, provider_raw, False
        if default_model:
            provider_context = (
                raw_active_provider
                if _should_attach_codex_provider_context(default_model, raw_active_provider, catalog)
                else None
            )
            return default_model, provider_context, True
        return model, provider_raw, False

    slash = model.find("/")
    if slash < 0:
        if explicit_model_pick:
            # User explicitly chose this model; don't second-guess (#3737)
            return model, requested_provider, False
        model_lower = model.lower()
        for bare_prefix in ("gpt", "claude", "gemini"):
            if model_lower.startswith(bare_prefix):
                model_provider = _normalize_provider_id(bare_prefix)
                if model_provider and model_provider != active_provider and default_model:
                    provider_context = (
                        raw_active_provider
                        if _should_attach_codex_provider_context(default_model, raw_active_provider, catalog)
                        else None
                    )
                    return default_model, provider_context, True
                provider_context = (
                    raw_active_provider
                    if _should_attach_codex_provider_context(model, raw_active_provider, catalog)
                    else requested_provider
                )
                return model, provider_context, False
        return model, requested_provider, False

    model_provider = _normalize_provider_id(model[:slash])

    # For custom/openrouter active providers: only skip normalization when the
    # model's namespace prefix is actually routable by a group in the catalog.
    # A user who only has custom_providers configured (active_provider="custom")
    # with a stale session model like "openai/gpt-5.4-mini" would otherwise
    # never get cleaned up, causing "(unavailable)" to appear in the picker.
    if active_provider in {"custom", "openrouter"}:
        # These namespaces are always routable as-is — preserve them.
        if model_provider in {"", "custom", "openrouter"}:
            return model, requested_provider, False
        # Check if any catalog group can actually route this model's prefix.
        groups = catalog.get("groups") or []
        routable_provider_ids = {
            _normalize_provider_id(g.get("provider_id") or "") for g in groups
        }
        # openrouter group can route any provider/model namespace
        has_openrouter_group = any(
            (g.get("provider_id") or "") == "openrouter" for g in groups
        )
        if model_provider in routable_provider_ids or has_openrouter_group:
            return model, requested_provider, False
        # Model prefix is not routable — stale cross-provider reference, clear it.
        if default_model:
            return default_model, requested_provider, True
        return model, requested_provider, False

    # Skip normalization for models on custom/openrouter namespaces — these are
    # user-controlled and should never be silently replaced.
    #
    # OpenAI Codex is intentionally normalized to the OpenAI family above so bare
    # GPT IDs survive provider switches. Slash-qualified OpenAI IDs are different:
    # ``openai/gpt-...`` is the OpenRouter shape for OpenAI models, and
    # resolve_model_provider() routes that through OpenRouter when Codex is the
    # configured provider. Legacy sessions can carry that stale slash ID without
    # a saved model_provider, so repair it to the active Codex default unless the
    # session/request explicitly says it is an OpenRouter selection. (#1734)
    if (
        raw_active_provider == "openai-codex"
        and model_provider == "openai"
        and requested_provider in {None, "openai-codex"}
        and default_model
    ):
        # Persist provider_context = "openai-codex" unconditionally on this
        # repair path so the resolved shape is stable across resolutions
        # (Opus stage-303 SHOULD-FIX: avoid redundant repair-writes per
        # chat-start when the catalog-coverage check fails — e.g. if a
        # future Codex default is itself slash-prefixed). Once we've
        # decided the session belongs to Codex, persist that decision.
        return default_model, raw_active_provider, True

    # Also normalize when the model is from a known provider but the active provider
    # is an unlisted one (e.g. ollama-cloud) — active_provider is "" in that case
    # but raw_active_provider is set. If model_provider doesn't start with the raw
    # active provider name, the session model is stale. (#1023)
    _active_for_compare = active_provider or raw_active_provider
    if model_provider and model_provider not in {"", "custom", "openrouter"} and model_provider != _active_for_compare and default_model:
        return default_model, requested_provider, True
    return model, requested_provider, False


def _resolve_compatible_session_model(model_id: str | None) -> tuple[str, bool]:
    """Return (effective_model, model_was_normalized) for legacy callers."""
    effective_model, _provider, changed = _resolve_compatible_session_model_state(model_id)
    return effective_model, changed


def _normalize_session_model_in_place(session) -> str:
    original_model = getattr(session, "model", None) or ""
    original_provider = _clean_session_model_provider(
        getattr(session, "model_provider", None)
    )
    effective_model, effective_provider, changed = _resolve_compatible_session_model_state(
        original_model or None,
        original_provider,
    )
    provider_changed = effective_provider != original_provider
    # Only persist the correction if the session had an explicit model that needed changing.
    # Sessions with no model stored (empty/None) get the effective default returned without
    # a disk write — no need to rebuild the index for a fill-in-blank operation.
    if original_model and effective_model and (
        (changed and original_model != effective_model) or provider_changed
    ):
        if changed and original_model != effective_model:
            session.model = effective_model
        session.model_provider = effective_provider
        session.save(touch_updated_at=False)
    return effective_model


def _resolve_effective_session_model_for_display(session) -> str:
    """Resolve the model a session should display without mutating persisted state.

    `GET /api/session` should stay side-effect free. If a stale persisted model
    needs normalization for the current provider configuration, return the
    effective model for the response payload only and leave disk state alone.
    """
    original_model = getattr(session, "model", None) or ""
    requested_provider = getattr(session, "model_provider", None)
    _pp_provider, _pp_default, _pp_cfg = _read_profile_model_config(session, requested_provider)
    effective_model, _provider, _changed = _resolve_compatible_session_model_state(
        original_model or None,
        requested_provider,
        profile_provider=_pp_provider,
        profile_default_model=_pp_default,
        profile_config=_pp_cfg,
        # GET /api/session is a hot, side-effect-free per-tab/per-poll path.
        # It must never pay the cold live provider-catalog rebuild (a
        # botocore IMDS probe that cannot resolve on a non-AWS / WSL / corp
        # network, plus anthropic/openrouter /models). That rebuild is
        # un-cacheable here (auth.json fingerprint churn) so every cold call
        # cost ~10s and, run concurrently across browser tabs, serialized on
        # the models-cache lock and starved SSE/streaming -> BrokenPipe storm
        # (#multi-tab-streaming-interlock). The persisted session model is
        # authoritative; the catalog is only a default-model backstop, which
        # the network-free minimal catalog already provides.
        prefer_cached_catalog=True,
    )
    return effective_model or original_model

def _resolve_effective_session_model_provider_for_display(session) -> str | None:
    original_model = getattr(session, "model", None) or ""
    requested_provider = getattr(session, "model_provider", None)
    _pp_provider, _pp_default, _pp_cfg = _read_profile_model_config(session, requested_provider)
    _model, provider, _changed = _resolve_compatible_session_model_state(
        original_model or None,
        requested_provider,
        profile_provider=_pp_provider,
        profile_default_model=_pp_default,
        profile_config=_pp_cfg,
        # See _resolve_effective_session_model_for_display: same hot
        # side-effect-free GET /api/session path; must not trigger the cold
        # live rebuild. prefer_cached_catalog resolves from warm/disk cache
        # or the network-free minimal catalog.
        prefer_cached_catalog=True,
    )
    return provider


def _resolve_context_length_for_session_model(
    model: str | None,
    provider: str | None = None,
    *,
    base_url: str | None = None,
    api_key: str | None = None,
) -> int:
    """Best-effort current context window for a session model.

    Persisted session context metadata is a snapshot from a prior model call.
    During session hydration/model switching, the current model metadata should
    be allowed to replace that stale snapshot.
    """
    model_for_lookup = str(model or "").strip()
    if not model_for_lookup:
        return 0
    try:
        from agent.model_metadata import get_model_context_length as _get_cl
        from api.config import get_config as _get_config_for_cl

        _cfg_for_cl = _get_config_for_cl()
        _ctx_lookup = _context_length_lookup_inputs_for_model(
            model_for_lookup,
            provider,
            base_url=base_url,
            api_key=api_key,
            cfg=_cfg_for_cl if isinstance(_cfg_for_cl, dict) else {},
        )
        try:
            return _get_cl(
                model_for_lookup,
                _ctx_lookup.base_url,
                api_key=_ctx_lookup.api_key,
                config_context_length=_ctx_lookup.config_context_length,
                provider=_ctx_lookup.provider or provider or "",
                custom_providers=_ctx_lookup.custom_providers,
            ) or 0
        except TypeError:
            # Older hermes-agent builds: legacy 2-arg form.
            return _get_cl(model_for_lookup, _ctx_lookup.base_url) or 0
    except Exception:
        logger.debug("Silent exception in _resolve_context_length_for_session_model", exc_info=True)
        return 0


def _session_context_length_lookup_state(
    model: str | None,
    provider: str | None,
) -> tuple[str, str, str, str]:
    """Return model/provider/base_url/api_key inputs for session context lookup.

    This stays config-based and side-effect-free for GET /api/session. It avoids
    a live provider catalog rebuild while still aligning the reload path with
    the base URL / custom-provider key shape used by streaming saves. (#4248)
    """
    model_for_lookup = str(model or "").strip()
    provider_for_lookup = str(provider or "").strip()
    base_url_for_lookup = ""
    api_key_for_lookup = ""
    if not model_for_lookup:
        return "", provider_for_lookup, "", ""
    try:
        from api.config import resolve_model_provider

        model_for_resolution = model_with_provider_context(model_for_lookup, provider_for_lookup or None)
        resolved_model, resolved_provider, resolved_base_url = resolve_model_provider(model_for_resolution)
        model_for_lookup = str(resolved_model or model_for_lookup).strip()
        provider_for_lookup = str(resolved_provider or provider_for_lookup or "").strip()
        base_url_for_lookup = str(resolved_base_url or "").strip()
    except Exception:
        logger.debug("session context-length lookup state resolution failed", exc_info=True)
    if provider_for_lookup.startswith("custom:"):
        try:
            from api.config import resolve_custom_provider_connection

            custom_key, custom_base = resolve_custom_provider_connection(provider_for_lookup)
            api_key_for_lookup = str(custom_key or "").strip()
            if not base_url_for_lookup:
                base_url_for_lookup = str(custom_base or "").strip()
        except Exception:
            logger.debug("custom provider context-length connection resolution failed", exc_info=True)
    return model_for_lookup, provider_for_lookup, base_url_for_lookup, api_key_for_lookup


def _session_model_identity_matches(
    stored_model: str | None,
    stored_provider: str | None,
    resolved_model: str | None,
    resolved_provider: str | None,
) -> bool:
    stored = str(stored_model or "").strip()
    resolved = str(resolved_model or "").strip()
    if not stored or not resolved:
        return False

    def _split_model_identity(value: str) -> tuple[str, str | None]:
        # Handle BOTH provider-qualified shapes so a slash-prefixed session model
        # (e.g. ``deepseek/deepseek-v4-1m``, OpenRouter-style) compares equal to its
        # resolved bare id. ``_split_provider_qualified_model`` only handles the
        # ``@provider:model`` form; without the slash case a reload of a
        # slash-stored model is wrongly treated as a model change, bypassing the
        # #4248 256k-clobber guard (Codex regression gate, v0.51.x).
        bare, prov = _split_provider_qualified_model(value)
        if prov is None and "/" in value:
            prefix, rest = value.split("/", 1)
            prefix = prefix.strip()
            rest = rest.strip()
            if prefix and rest:
                return rest, prefix
        return bare, prov

    stored_bare, stored_explicit_provider = _split_model_identity(stored)
    resolved_bare, resolved_explicit_provider = _split_model_identity(resolved)
    stored_provider_norm = _canonical_context_provider(stored_explicit_provider or stored_provider)
    resolved_provider_norm = _canonical_context_provider(resolved_explicit_provider or resolved_provider)
    if stored == resolved and stored_provider_norm == resolved_provider_norm:
        return True
    if stored_bare != resolved_bare:
        return False
    if stored_provider_norm and resolved_provider_norm:
        return stored_provider_norm == resolved_provider_norm
    return True


def _should_accept_session_context_length_refresh(
    persisted: int,
    resolved: int,
    *,
    model_changed: bool = False,
) -> bool:
    if not resolved:
        return False
    if not persisted:
        return True
    # #4248: an anonymous reload resolver can still fall through to the agent
    # metadata default fallback. Do not let that lower-confidence 256k value
    # clobber a larger context window persisted by the streaming path. If the
    # effective model changed, though, a 256k result may be the real new model
    # window and should replace the old snapshot.
    return model_changed or not (resolved == 256_000 and persisted > resolved)


def _rescale_threshold_tokens_for_context_window(
    threshold: int,
    old_window: int,
    new_window: int,
) -> int:
    try:
        threshold = int(threshold or 0)
        old_window = int(old_window or 0)
        new_window = int(new_window or 0)
    except (TypeError, ValueError):
        return 0
    if threshold <= 0 or old_window <= 0 or new_window <= 0:
        return 0
    return max(1, int(threshold * new_window / old_window))


def _worktree_default_from_config(profile: str | None) -> bool:
    """Return the agent's config-level ``worktree:`` default for *profile*.

    The agent CLI honors ``worktree: true`` in config.yaml for every session
    it creates (``use_worktree = worktree or w or CLI_CONFIG.get("worktree",
    False)``).  /api/session/new consults this only when the request body has
    no explicit ``worktree`` key, so both entry points to the same repo agree
    on isolation (#6022).  Explicit body values always win.

    Profile-aware on purpose: the WebUI serves multiple profiles from one
    process, and a user with ``worktree: true`` in one profile but not another
    expects per-profile behavior.  ``get_config_for_profile_home`` handles the
    ambient/common case via the mtime-tracked cache and reads a diverging
    profile's config.yaml directly off disk (see #3294).
    """
    try:
        if profile:
            from api.profiles import get_agy_home_for_profile

            cfg_dict = get_config_for_profile_home(get_agy_home_for_profile(profile))
        else:
            cfg_dict = get_config_for_profile_home(None)
        # Strict boolean: only a real YAML `true` opts in.  Any other shape
        # ("true", 1, [], {}, null, ...) is malformed for this key and must
        # fall to the safe no-worktree default rather than truthiness-coerce
        # into minting worktrees.
        return (cfg_dict or {}).get("worktree", False) is True
    except Exception:
        # Config resolution must never break session creation.
        logger.warning("failed to read worktree config default", exc_info=True)
        return False


def _session_model_state_from_request(
    model: str | None,
    requested_provider: str | None,
    current_provider: str | None = None,
) -> tuple[str | None, str | None]:
    model_value = str(model).strip() if model is not None else None
    provider = (
        _clean_session_model_provider(requested_provider)
        if requested_provider is not None
        else None
    )
    if model_value:
        _bare, explicit_provider = _split_provider_qualified_model(model_value)
        if explicit_provider:
            provider = explicit_provider
        elif requested_provider is None:
            provider = _clean_session_model_provider(current_provider)
        model_value, provider, _changed = _resolve_compatible_session_model_state(
            model_value,
            provider,
        )
    return model_value, provider

