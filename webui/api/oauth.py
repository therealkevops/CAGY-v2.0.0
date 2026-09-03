"""Antigravity Web UI -- OAuth device flow stub.

Antigravity operates directly through the agy CLI runner.
In-app device OAuth flows are disabled.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

AUTH_JSON_PATH = Path.home() / ".hermes" / "auth.json"


def resolve_runtime_provider_with_anthropic_env_lock(resolver: Callable, *args: Any, **kwargs: Any) -> Any:
    """Execute resolver directly without extraneous device locks."""
    return resolver(*args, **kwargs)


def read_auth_json(path: Optional[Path] = None) -> Dict[str, Any]:
    return {}


def start_onboarding_oauth_flow(provider: str, hermes_home: Optional[Path] = None) -> Dict[str, Any]:
    return {
        "status": "disabled",
        "error": "OAuth device flows are disabled in Antigravity mode.",
    }


def poll_onboarding_oauth_flow(flow_id: str) -> Dict[str, Any]:
    return {
        "status": "expired",
        "error": "OAuth device flows are disabled in Antigravity mode.",
    }


def cancel_onboarding_oauth_flow(flow_id: str) -> Dict[str, Any]:
    return {"status": "cancelled"}
