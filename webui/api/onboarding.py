"""Antigravity Web UI -- Onboarding status stub.

Antigravity operates directly through the agy CLI runner.
The multi-provider setup wizard is completed automatically.
"""
from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Any, Dict, Optional

from api.auth import is_auth_enabled
from api.config import (
    DEFAULT_WORKSPACE,
    get_available_models,
    load_settings,
)
from api.workspace import get_last_workspace, load_workspaces

logger = logging.getLogger(__name__)

_AGY_CANDIDATES = [
    shutil.which("agy"),
    "/usr/local/bin/agy",
    "/usr/bin/agy",
    str(Path.home() / ".local" / "bin" / "agy"),
]
_AGY_FOUND = any(c and Path(c).is_file() and os.access(c, os.X_OK) for c in _AGY_CANDIDATES)


def _load_env_file(path: Path) -> Dict[str, str]:
    if not path.is_file():
        return {}
    res = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            res[k.strip()] = v.strip().strip("'").strip('"')
    except Exception:
        pass
    return res


def get_onboarding_status() -> Dict[str, Any]:
    settings = load_settings()
    workspaces = load_workspaces()
    last_workspace = get_last_workspace()
    available_models = get_available_models()

    return {
        "completed": True,
        "settings": {
            "onboarding_completed": True,
            "default_workspace": settings.get("default_workspace") or str(DEFAULT_WORKSPACE),
            "password_enabled": is_auth_enabled(),
            "bot_name": settings.get("bot_name") or "AGY",
        },
        "system": {
            "agy_found": bool(_AGY_FOUND),
            "hermes_found": bool(_AGY_FOUND),
            "imports_ok": True,
            "missing_modules": [],
            "import_errors": {},
            "chat_ready": True,
            "provider": "agy",
            "model": "Antigravity 2.0 (agy CLI)",
            "provider_configured": True,
            "key_present": True,
            "key_source": "system",
            "custom_base_url": None,
            "custom_model": None,
        },
        "setup": [],
        "workspaces": {
            "items": workspaces,
            "last": last_workspace,
        },
        "models": available_models,
    }


def apply_onboarding_setup(body: Any) -> Dict[str, Any]:
    return {
        "ok": True,
        "chat_ready": True,
        "provider": "agy",
        "model": "Antigravity 2.0 (agy CLI)",
    }


def complete_onboarding() -> Dict[str, Any]:
    return {"ok": True, "completed": True}


def probe_provider_endpoint(endpoint: str, api_key: Optional[str] = None, **kwargs: Any) -> Dict[str, Any]:
    return {"ok": True, "status": "reachable"}


def apply_self_hosted_provider_setup(body: Any) -> Dict[str, Any]:
    return {"ok": True}
