"""Antigravity Web UI -- Agent engine health status."""
from __future__ import annotations

import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict


def build_agent_health_payload() -> dict[str, Any]:
    """Return `{alive, checked_at, details}` for the Antigravity (agy) engine."""
    candidates = [
        shutil.which("agy"),
        "/usr/local/bin/agy",
        "/usr/bin/agy",
        str(Path.home() / ".local" / "bin" / "agy"),
    ]
    agy_bin = next(
        (c for c in candidates if c and Path(c).is_file() and os.access(c, os.X_OK)),
        None,
    )
    alive = bool(agy_bin)
    return {
        "alive": alive,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "details": {
            "engine": "Antigravity CLI (agy)",
            "binary": agy_bin or "not found",
            "state": "running" if alive else "unavailable",
            "reason": None if alive else "agy_binary_not_found",
        },
    }
