"""Antigravity Web UI -- Dashboard prober stub.

Antigravity Web UI is the primary interface; probing for external
legacy Hermes dashboard instances is disabled.
"""
from __future__ import annotations

from typing import Any, Dict


def get_dashboard_status() -> dict[str, Any]:
    return {
        "reachable": False,
        "mode": "never",
        "url": None,
        "reason": "Antigravity Web UI is the primary interface",
    }


def get_dashboard_config() -> dict[str, Any]:
    return {
        "mode": "never",
        "url": None,
    }


def save_dashboard_config(body: Any) -> dict[str, Any]:
    return {
        "mode": "never",
        "url": None,
    }
