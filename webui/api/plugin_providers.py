"""Antigravity Web UI -- Plugin providers stub."""
from __future__ import annotations

from typing import Any, Dict, Optional


def plugin_model_provider_profiles() -> Dict[str, Any]:
    return {}


def effective_provider_env_var(provider_id: str, static_map: Optional[Dict[str, str]] = None) -> Optional[str]:
    return None if static_map is None else static_map.get(provider_id)


def effective_provider_display_name(provider_id: str, static_map: Optional[Dict[str, str]] = None) -> str:
    return provider_id if static_map is None else static_map.get(provider_id, provider_id)


def is_plugin_model_provider(provider_id: str) -> bool:
    return False


def plugin_model_provider_ids() -> frozenset[str]:
    return frozenset()


def plugin_model_provider_display_name(provider_id: str) -> Optional[str]:
    return None


def plugin_model_provider_api_key_env_var(provider_id: str) -> Optional[str]:
    return None


def invalidate_plugin_model_provider_cache() -> None:
    pass
