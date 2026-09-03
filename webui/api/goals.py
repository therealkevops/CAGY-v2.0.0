"""Antigravity Web UI -- Session goals stub.

In Antigravity mode, goal tracking and autonomous multi-turn reasoning
are handled natively by the agy agent engine.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def has_active_goal(session_id: str, *, profile_home: str | Path | None = None) -> bool:
    return False


def evaluate_goal_after_turn(
    session_id: str,
    last_response: str,
    *,
    user_initiated: bool = True,
    profile_home: str | Path | None = None,
) -> Dict[str, Any]:
    return {
        "status": None,
        "should_continue": False,
        "continuation_prompt": None,
        "verdict": "inactive",
        "reason": "goals handled natively by agy agent engine",
        "message": "",
    }


def goal_state_snapshot(session_id: str, *, profile_home: str | Path | None = None) -> Any:
    return None


def restore_goal_state(session_id: str, snapshot: Any, *, profile_home: str | Path | None = None) -> None:
    pass


def goal_command_payload(
    session_id: str,
    raw_input: str,
    *,
    profile_home: str | Path | None = None,
) -> Dict[str, Any]:
    return {"handled": False, "is_goal_command": False}
