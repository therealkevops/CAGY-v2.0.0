"""
Antigravity (CAGY) Visual Diff Engine
Computes structured side-by-side line alignments for visual diffing.
"""

import difflib
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional

def compute_structured_diff(old_text: str, new_text: str, filename: str = "") -> Dict[str, Any]:
    """
    Compute structured side-by-side diff between old_text and new_text.
    Returns synchronized rows with line numbers, change types, and stats.
    """
    old_lines = (old_text or "").splitlines()
    new_lines = (new_text or "").splitlines()

    matcher = difflib.SequenceMatcher(None, old_lines, new_lines)
    rows = []
    additions = 0
    deletions = 0

    for tag, alo, ahi, blo, bhi in matcher.get_opcodes():
        if tag == "equal":
            for i in range(ahi - alo):
                rows.append({
                    "left": {"no": alo + i + 1, "text": old_lines[alo + i], "type": "equal"},
                    "right": {"no": blo + i + 1, "text": new_lines[blo + i], "type": "equal"}
                })
        elif tag == "replace":
            count_left = ahi - alo
            count_right = bhi - blo
            max_len = max(count_left, count_right)
            deletions += count_left
            additions += count_right
            for i in range(max_len):
                left = (
                    {"no": alo + i + 1, "text": old_lines[alo + i], "type": "delete"}
                    if i < count_left
                    else {"no": None, "text": "", "type": "empty"}
                )
                right = (
                    {"no": blo + i + 1, "text": new_lines[blo + i], "type": "insert"}
                    if i < count_right
                    else {"no": None, "text": "", "type": "empty"}
                )
                rows.append({"left": left, "right": right})
        elif tag == "delete":
            count_left = ahi - alo
            deletions += count_left
            for i in range(count_left):
                rows.append({
                    "left": {"no": alo + i + 1, "text": old_lines[alo + i], "type": "delete"},
                    "right": {"no": None, "text": "", "type": "empty"}
                })
        elif tag == "insert":
            count_right = bhi - blo
            additions += count_right
            for i in range(count_right):
                rows.append({
                    "left": {"no": None, "text": "", "type": "empty"},
                    "right": {"no": blo + i + 1, "text": new_lines[blo + i], "type": "insert"}
                })

    return {
        "filename": filename,
        "stats": {
            "additions": additions,
            "deletions": deletions,
            "total_changes": additions + deletions,
            "old_line_count": len(old_lines),
            "new_line_count": len(new_lines)
        },
        "rows": rows,
        "has_changes": additions > 0 or deletions > 0
    }

def get_file_diff_against_head(workspace_path: Path, rel_file_path: str) -> Dict[str, Any]:
    """
    Retrieve original content from git HEAD and compute diff against current file on disk.
    If not a git repository or file is untracked, handles gracefully.
    """
    if not rel_file_path:
        return {"error": "rel_file_path required"}

    full_path = (workspace_path / rel_file_path).resolve()
    current_content = ""
    if full_path.exists() and full_path.is_file():
        try:
            current_content = full_path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return {"error": f"Failed to read current file: {e}"}

    # Query git for HEAD version
    old_content = ""
    try:
        proc = subprocess.run(
            ["git", "-C", str(workspace_path), "show", f"HEAD:{rel_file_path}"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if proc.returncode == 0:
            old_content = proc.stdout
        else:
            # File might be untracked or newly created
            old_content = ""
    except Exception:
        old_content = ""

    return compute_structured_diff(old_content, current_content, filename=rel_file_path)
