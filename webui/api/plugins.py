"""Antigravity Web UI -- Plugins stub.

Antigravity operates via native skills and MCP servers.
Legacy Hermes dashboard plugins are disabled.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

PLUGIN_MANIFESTS: Dict[str, dict] = {}
_PLUGIN_STATIC_ROOTS: Dict[str, Path] = {}


def _get_plugin_base() -> Path:
    return Path("/dev/null")


def get_plugin_metadata() -> list[dict]:
    return []


def serve_plugin_static(path: str, handler: Any) -> bool:
    return False
