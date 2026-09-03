"""Antigravity Web UI -- Provider stub.

Antigravity uses the local agy CLI runner for model execution.
External cloud provider API key CRUD and budgeting are disabled.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def get_providers(config_data: Any = None) -> List[Dict[str, Any]]:
    return []


def get_provider_quota(provider_id: str, config_data: Any = None) -> Dict[str, Any]:
    return {"ok": False, "status": "unavailable", "quota": None}


def get_provider_cost_history(provider_id: str, config_data: Any = None) -> Dict[str, Any]:
    return {"history": []}


def provider_has_process_wakeup_recovery_credential(*args: Any, **kwargs: Any) -> bool:
    return False


def set_provider_key(provider: str, api_key: str, **kwargs: Any) -> Dict[str, Any]:
    return {"ok": True, "provider": provider}


def remove_provider_key(provider: str, **kwargs: Any) -> Dict[str, Any]:
    return {"ok": True, "provider": provider}


def _provider_has_key(provider: str, config_data: Any = None) -> bool:
    return False


def invalidate_account_usage_status_cache() -> None:
    pass


def _provider_credential_env_vars(*args: Any, **kwargs: Any) -> Tuple[str, ...]:
    return ()
