"""Antigravity Web UI -- Extensions stub.

Antigravity operates via native Skills (.gemini/skills), Rules (GEMINI.md),
and MCP servers. External Hermes extension galleries and sidecar processes are disabled.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class ExtensionSidecarProxyError(Exception):
    pass


class ExtensionToggleError(Exception):
    pass


class ExtensionInstallError(Exception):
    pass


def resolve_extension_sidecar_proxy_target(*args: Any, **kwargs: Any) -> Any:
    raise ExtensionSidecarProxyError("Extension sidecar proxy is disabled in Antigravity mode.")


def set_extension_sidecar_proxy_consent(extension_id: str, approved: bool) -> Dict[str, Any]:
    return {"id": extension_id, "approved": False}


def set_extension_user_enabled(extension_id: str, enabled: bool) -> Dict[str, Any]:
    return {"id": extension_id, "enabled": False}


def install_extension(extension_id: str) -> Dict[str, Any]:
    raise ExtensionInstallError("Extension installation is disabled in Antigravity mode.")


def uninstall_extension(extension_id: str) -> Dict[str, Any]:
    raise ExtensionInstallError("Extension uninstallation is disabled in Antigravity mode.")


def inject_extension_tags(html: str) -> str:
    return html


def get_extension_status() -> Dict[str, Any]:
    return {
        "extensions": [],
        "manifest_counts": 0,
    }


def get_extension_registry() -> Dict[str, Any]:
    return {
        "extensions": [],
        "total": 0,
    }


def serve_extension_static(handler: Any, path: str) -> bool:
    return False
