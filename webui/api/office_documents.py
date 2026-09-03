"""Antigravity Web UI -- Office documents preview stub.

Office XML parsing is disabled in Antigravity mode.
"""
from __future__ import annotations

from typing import Any, Dict, Tuple

CLAIMED_OFFICE_EXTENSIONS = frozenset({".docx", ".xlsx", ".pptx"})
CLAIMED_OFFICE_FORMATS = frozenset({"docx", "xlsx", "pptx"})
OFFICE_PREVIEW_KIND = "office"
OFFICE_RENDER_MODE = "code"


def preview_office_document(filename: str, file_bytes: bytes) -> Dict[str, Any]:
    """Return a plain text notice indicating office file preview is not loaded."""
    return {
        "content": f"[Office document: {filename}] (Download file to view content)",
        "preview_kind": "text",
        "truncated": False,
    }


def save_office_document(rel_path: str, current_bytes: bytes, content: str) -> Tuple[Dict[str, Any], bytes]:
    raise ValueError("Office document editing is not supported in Antigravity mode.")
