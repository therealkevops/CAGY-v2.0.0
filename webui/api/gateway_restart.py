"""Antigravity Web UI -- Gateway restart stub."""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def restart_active_profile_gateway(
    *,
    profile: str | None = None,
    quick_timeout_seconds: float = 2.0,
    background_wait_seconds: float = 240.0,
) -> dict:
    """No-op stub: Antigravity does not use a messaging gateway daemon."""
    return {
        "status": "disabled",
        "message": "Gateway messaging daemon is not used in Antigravity mode.",
    }
