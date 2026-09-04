"""Antigravity Web UI -- Git-backed workspace checkpoints and rollback.

Treats Git commits as immutable workspace checkpoints. Provides:
- list_checkpoints: Recent Git commits with metadata and changed file counts.
- get_checkpoint_diff: Commit patch diff and status of changed files.
- restore_checkpoint: Reverts workspace working tree to the checkpoint commit,
  safely stashing any uncommitted changes first.
"""
from __future__ import annotations

import datetime
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Git environment scrubbing to prevent leaking subshells or external configs
_GIT_ENV_SCRUB = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_CONFIG_GLOBAL",
    "GIT_CONFIG_SYSTEM",
    "GIT_ASKPASS",
    "SSH_ASKPASS",
)

CHECKPOINT_REF_RE = re.compile(r"^[a-zA-Z0-9_\-\.\^~@{}]+$")


def _clean_git_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in _GIT_ENV_SCRUB:
        env.pop(key, None)
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _resolve_workspace(workspace: str | Path | None) -> Path:
    """Resolve workspace parameter to an absolute Path."""
    if not workspace or str(workspace).strip() in {"", "default", "."}:
        try:
            from api.config import DEFAULT_WORKSPACE
            return Path(DEFAULT_WORKSPACE).expanduser().resolve()
        except Exception:
            return Path.cwd().resolve()
    ws = Path(workspace).expanduser().resolve()
    if not ws.exists() or not ws.is_dir():
        raise ValueError(f"Workspace directory not found: {workspace}")
    return ws


def _run_git(ws: Path, args: list[str], timeout: int = 15) -> subprocess.CompletedProcess[str]:
    """Execute git in the workspace directory with timeout and scrubbed env."""
    return subprocess.run(
        ["git", *args],
        cwd=str(ws),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=_clean_git_env(),
    )


def _is_git_repo(ws: Path) -> bool:
    """Check whether ws is inside a valid git work tree."""
    try:
        res = _run_git(ws, ["rev-parse", "--is-inside-work-tree"], timeout=5)
        return res.returncode == 0 and res.stdout.strip() == "true"
    except Exception:
        return False


def _validate_checkpoint_ref(checkpoint: str) -> str:
    """Validate and sanitize git checkpoint ref."""
    cp = (checkpoint or "").strip()
    if not cp or cp.startswith("-") or not CHECKPOINT_REF_RE.match(cp):
        raise ValueError(f"Invalid checkpoint reference: {checkpoint!r}")
    return cp


def list_checkpoints(workspace: str) -> Dict[str, Any]:
    """List recent commits as checkpoints for the given workspace.

    Returns:
        Dict with 'ok' and 'checkpoints' list, compatible with the Web UI
        Checkpoints card in the Spaces panel.
    """
    ws = _resolve_workspace(workspace)
    if not _is_git_repo(ws):
        return {"ok": True, "checkpoints": []}

    cmd = [
        "log",
        "-n",
        "50",
        "--pretty=format:COMMIT:%H|%h|%an|%at|%s",
        "--shortstat",
    ]
    try:
        res = _run_git(ws, cmd)
    except subprocess.TimeoutExpired:
        logger.warning("Git log timed out for workspace %s", ws)
        return {"ok": True, "checkpoints": []}

    if res.returncode != 0:
        # e.g., fresh repo with no commits yet
        return {"ok": True, "checkpoints": []}

    checkpoints: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None

    for line in res.stdout.splitlines():
        line_s = line.strip()
        if line_s.startswith("COMMIT:"):
            if current is not None:
                checkpoints.append(current)
            parts = line_s[len("COMMIT:"):].split("|", 4)
            if len(parts) >= 5:
                full_hash, short_hash, author, ts_str, subject = parts
                try:
                    ts = int(ts_str)
                    date_display = datetime.datetime.fromtimestamp(ts).strftime("%b %d, %H:%M")
                    date_iso = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).isoformat()
                except Exception:
                    ts = 0
                    date_display = ""
                    date_iso = ""
                current = {
                    "id": short_hash,
                    "commit": full_hash,
                    "message": subject,
                    "author": author,
                    "date": date_iso,
                    "date_display": date_display,
                    "timestamp": ts,
                    "files": 0,
                }
            else:
                current = None
        elif current is not None and line_s:
            m = re.search(r"(\d+)\s+file", line_s)
            if m:
                current["files"] = int(m.group(1))

    if current is not None:
        checkpoints.append(current)

    return {"ok": True, "checkpoints": checkpoints}


def get_checkpoint_diff(workspace: str, checkpoint: str) -> Dict[str, Any]:
    """Retrieve diff and changed file list for a specific checkpoint (commit)."""
    ws = _resolve_workspace(workspace)
    cp = _validate_checkpoint_ref(checkpoint)
    if not _is_git_repo(ws):
        return {
            "ok": False,
            "error": "Workspace is not a Git repository",
            "total_changes": 0,
            "files_changed": [],
            "diff": "",
        }

    # Verify commit exists
    verify_res = _run_git(ws, ["rev-parse", "--verify", "--quiet", f"{cp}^{{commit}}"])
    if verify_res.returncode != 0:
        raise ValueError(f"Checkpoint not found: {cp}")

    # Extract changed files and status (A: added, D: deleted, M: modified)
    status_res = _run_git(ws, ["show", "--name-status", "--format=", cp])
    files_changed: List[Dict[str, str]] = []
    if status_res.returncode == 0:
        for line in status_res.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(maxsplit=1)
            if len(parts) == 2:
                status_code, filepath = parts
                status_char = status_code[0].upper()
                if status_char == "A":
                    status = "added"
                elif status_char == "D":
                    status = "deleted"
                else:
                    status = "modified"
                files_changed.append({"file": filepath.strip(), "status": status})

    # Extract patch unified diff (limited to 512KB for performance and safety)
    diff_res = _run_git(ws, ["show", "--patch", "--format=", cp])
    diff_text = diff_res.stdout if diff_res.returncode == 0 else ""
    if len(diff_text) > 512 * 1024:
        diff_text = diff_text[:512 * 1024] + "\n\n... [Diff truncated at 512KB] ..."

    return {
        "ok": True,
        "checkpoint": cp,
        "total_changes": len(files_changed),
        "files_changed": files_changed,
        "diff": diff_text,
    }


def restore_checkpoint(workspace: str, checkpoint: str) -> Dict[str, Any]:
    """Restore workspace working tree to the given checkpoint (commit).

    Safety:
    1. If the workspace has uncommitted modifications, an auto-safety stash is created.
    2. Files from target checkpoint are checked out into the working tree.
    3. Files created after the target commit are cleanly pruned.
    """
    ws = _resolve_workspace(workspace)
    cp = _validate_checkpoint_ref(checkpoint)
    if not _is_git_repo(ws):
        return {"ok": False, "error": "Workspace is not a Git repository"}

    # Verify commit exists
    verify_res = _run_git(ws, ["rev-parse", "--verify", "--quiet", f"{cp}^{{commit}}"])
    if verify_res.returncode != 0:
        raise ValueError(f"Checkpoint not found: {cp}")
    full_commit = verify_res.stdout.strip()
    short_commit = full_commit[:7]

    # 1. Safety stash if working tree is dirty
    status_res = _run_git(ws, ["status", "--porcelain"])
    stashed = False
    if status_res.returncode == 0 and status_res.stdout.strip():
        stash_msg = f"Auto-safety stash before rollback to {short_commit}"
        s_res = _run_git(ws, ["stash", "push", "-u", "-m", stash_msg])
        if s_res.returncode != 0:
            _run_git(ws, ["stash", "save", "-u", stash_msg])
        stashed = True

    # 2. Checkout files from checkpoint
    checkout_res = _run_git(ws, ["checkout", cp, "--", "."])
    if checkout_res.returncode != 0:
        logger.error("Git checkout failed during restore: %s", checkout_res.stderr)
        return {"ok": False, "error": f"Failed to checkout checkpoint: {checkout_res.stderr.strip()}"}

    # 3. Clean up any files that were introduced after checkpoint
    diff_added = _run_git(ws, ["diff", "--name-only", "--diff-filter=A", cp, "HEAD"])
    if diff_added.returncode == 0:
        for rel in diff_added.stdout.splitlines():
            rel = rel.strip()
            if not rel:
                continue
            target_path = ws / rel
            try:
                if target_path.is_file() or target_path.is_symlink():
                    target_path.unlink()
                elif target_path.is_dir():
                    shutil.rmtree(target_path, ignore_errors=True)
                _run_git(ws, ["rm", "--cached", "-f", "--ignore-unmatch", rel])
            except Exception as e:
                logger.warning("Failed to clean up post-checkpoint file %s: %s", rel, e)

    # 4. Count files restored/affected
    diff_all = _run_git(ws, ["diff", "--name-only", cp, "HEAD"])
    restored_files = [f for f in diff_all.stdout.splitlines() if f.strip()]
    restored_count = len(restored_files)

    return {
        "ok": True,
        "checkpoint": cp,
        "commit": full_commit,
        "files_restored_count": restored_count,
        "stashed_prior_changes": stashed,
        "message": f"Successfully restored to checkpoint {short_commit}",
    }

