"""Antigravity Web UI -- Filesystem checkpoint (rollback) stub.

Antigravity operates directly on the project workspace using Git worktrees
and native source control, rather than Hermes shadow checkpoints.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def list_checkpoints(workspace: str) -> List[Dict[str, Any]]:
    """Return empty list: Antigravity manages files natively via git."""
    return []


def get_checkpoint_diff(workspace: str, checkpoint: str) -> Dict[str, Any]:
    return {"diff": "", "files": [], "error": "No shadow checkpoints in Antigravity mode"}


def restore_checkpoint(workspace: str, checkpoint: str) -> Dict[str, Any]:
    return {"restored": False, "error": "Shadow checkpoints not used in Antigravity mode"}
