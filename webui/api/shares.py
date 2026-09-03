"""Antigravity Web UI -- Share stub.

Public unauthenticated chat sharing is disabled in Antigravity mode.
"""
from __future__ import annotations

from typing import Any, Dict, Optional


def create_or_refresh_share(session_id: str, origin: str) -> Dict[str, Any]:
    raise ValueError("Public chat sharing is disabled in Antigravity mode.")


def load_share(token: str) -> Optional[Dict[str, Any]]:
    return None


def revoke_share(token: str) -> bool:
    return False
