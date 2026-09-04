"""Workspace-targeted export and import operations for sessions."""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from api.workspace import resolve_trusted_workspace, load_workspaces

logger = logging.getLogger("agy.session_workspace_export")

_UNSAFE_NAME_CHARS = re.compile(r'[/\\:*?"<>|]')


def sanitize_filename(name: str, fallback: str = "export") -> str:
    """Sanitize a filename candidate by stripping forbidden chars and relative components."""
    if not name or not isinstance(name, str):
        return fallback
    clean = os.path.basename(name.strip())
    clean = _UNSAFE_NAME_CHARS.sub("_", clean)
    clean = clean.strip(" .")
    return clean or fallback


def generate_session_markdown(session_data: dict) -> str:
    """Format a session projection dict into clean, structured Markdown."""
    sid = session_data.get("session_id", "")
    title = session_data.get("title", "")
    workspace = session_data.get("workspace", "")
    model = session_data.get("model", "")
    created_at = session_data.get("created_at", "")

    header_title = title or f"Session {sid}"
    lines = [
        f"# {header_title}",
        "",
        f"- **Session ID**: `{sid}`",
    ]
    if workspace:
        lines.append(f"- **Workspace**: `{workspace}`")
    if model:
        lines.append(f"- **Model**: `{model}`")
    if created_at:
        lines.append(f"- **Created**: {created_at}")
    lines.append("")

    messages = session_data.get("messages", [])
    if isinstance(messages, list):
        for m in messages:
            if not isinstance(m, dict):
                continue
            role = m.get("role", "")
            if role == "tool":
                continue
            content = m.get("content", "")
            if isinstance(content, list):
                content = "\n".join(
                    p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"
                )
            ct = str(content).strip()
            attachments = m.get("attachments") or []
            if not ct and not attachments:
                continue
            role_label = role.capitalize() if role else "Message"
            attach_str = f"\n\n_Files: {', '.join(str(a) for a in attachments)}_" if attachments else ""
            lines.extend([f"## {role_label}", "", ct + attach_str, ""])

    return "\n".join(lines).strip() + "\n"


def resolve_destination_dir(workspace_path: str | None = None, subfolder: str | None = None) -> tuple[Path, Path]:
    """Resolve and validate the destination directory within a trusted workspace.

    Returns (base_workspace_dir, target_dir).
    Raises ValueError on untrusted workspace or path traversal attempts.
    """
    base_dir = resolve_trusted_workspace(workspace_path).resolve()
    if not base_dir.is_dir():
        raise ValueError(f"Target workspace is not a valid directory: {base_dir}")

    if not subfolder:
        return base_dir, base_dir

    # Clean subfolder, strip leading/trailing slashes and resolve relative to base_dir
    cleaned_sub = os.path.normpath(str(subfolder).strip()).lstrip("/\\")
    if cleaned_sub in ("", "."):
        return base_dir, base_dir

    target_dir = (base_dir / cleaned_sub).resolve()
    try:
        target_dir.relative_to(base_dir)
    except ValueError:
        raise ValueError(f"Subfolder path '{subfolder}' escapes the workspace directory boundary")

    return base_dir, target_dir


def export_session_to_workspace(
    session_data: dict,
    workspace_path: str | None = None,
    subfolder: str | None = None,
    format: str = "md",
    filename: str | None = None,
    content: str | None = None,
    theme: str = "dark",
    palette: dict | None = None,
) -> dict:
    """Save a session export directly to the workspace filesystem.

    Args:
        session_data: Projected safe session dictionary.
        workspace_path: Root of the target workspace.
        subfolder: Optional subfolder (e.g. 'transcripts').
        format: 'md', 'json', or 'html'.
        filename: Optional filename.
        content: Optional raw content string (used for custom Markdown transcripts).
        theme: Theme for HTML reports ('dark' or 'light').
        palette: Optional skin palette dictionary for HTML rendering.

    Returns:
        dict: {
            "ok": True,
            "path": str,
            "directory": str,
            "filename": raw_name,
            "size_bytes": int,
            "format": fmt,
        }
    """
    base_dir, target_dir = resolve_destination_dir(workspace_path, subfolder)
    target_dir.mkdir(parents=True, exist_ok=True)

    sid = session_data.get("session_id", "session")
    fmt = format.lower().strip()
    if fmt in ("markdown", "text"):
        fmt = "md"
    if fmt not in ("md", "json", "html"):
        raise ValueError(f"Unsupported export format '{format}'. Supported formats: md, json, html")

    if not filename:
        raw_name = f"agy-{sid}.{fmt}"
    else:
        raw_name = sanitize_filename(filename, fallback=f"agy-{sid}")
        if not raw_name.endswith(f".{fmt}"):
            raw_name = f"{raw_name}.{fmt}"

    target_file = (target_dir / raw_name).resolve()
    try:
        target_file.relative_to(base_dir)
    except ValueError:
        raise ValueError("Target file escapes the workspace boundary")

    if fmt == "md":
        payload = content if (content and isinstance(content, str)) else generate_session_markdown(session_data)
    elif fmt == "json":
        payload = json.dumps(session_data, ensure_ascii=False, indent=2)
    elif fmt == "html":
        from api.session_export_html import render_session_html
        payload = render_session_html(session_data, theme=theme, palette=palette)
    else:
        raise ValueError(f"Unknown format {fmt}")

    # Write atomically via temp file
    tmp_file = target_dir / f".{raw_name}.tmp.{os.getpid()}"
    try:
        with open(tmp_file, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp_file, target_file)
    finally:
        if tmp_file.exists():
            try:
                tmp_file.unlink()
            except OSError:
                pass

    size_bytes = target_file.stat().st_size
    return {
        "ok": True,
        "path": str(target_file),
        "directory": str(target_dir),
        "filename": raw_name,
        "size_bytes": size_bytes,
        "format": fmt,
    }


def list_workspace_session_exports(
    workspace_path: str | None = None,
    subfolder: str | None = None,
) -> dict:
    """Scan the target directory for saved session files.

    Returns:
        dict: {
            "ok": True,
            "directory": str,
            "exports": list[dict],
        }
    """
    base_dir, target_dir = resolve_destination_dir(workspace_path, subfolder)
    if not target_dir.exists() or not target_dir.is_dir():
        return {
            "ok": True,
            "directory": str(target_dir),
            "exports": [],
        }

    exports = []
    for entry in os.scandir(target_dir):
        if not entry.is_file() or entry.name.startswith("."):
            continue
        lower = entry.name.lower()
        ext = ""
        if lower.endswith(".json"):
            ext = "json"
        elif lower.endswith(".md"):
            ext = "md"
        elif lower.endswith(".html"):
            ext = "html"
        else:
            continue

        try:
            stat = entry.stat()
            size_bytes = stat.st_size
            mtime = stat.st_mtime
        except OSError:
            continue

        item = {
            "filename": entry.name,
            "path": entry.path,
            "format": ext,
            "size_bytes": size_bytes,
            "mtime": mtime,
            "importable": ext == "json",
            "title": "",
            "session_id": "",
            "message_count": 0,
            "created_at": "",
        }

        if ext == "json" and size_bytes <= 10 * 1024 * 1024:
            try:
                with open(entry.path, "r", encoding="utf-8") as jf:
                    data = json.load(jf)
                if isinstance(data, dict):
                    item["title"] = data.get("title", "")
                    item["session_id"] = data.get("session_id", "")
                    item["created_at"] = data.get("created_at", "")
                    msgs = data.get("messages")
                    if isinstance(msgs, list):
                        item["message_count"] = len(msgs)
            except Exception:
                pass

        exports.append(item)

    # Sort newest first
    exports.sort(key=lambda x: x["mtime"], reverse=True)

    return {
        "ok": True,
        "directory": str(target_dir),
        "exports": exports,
    }


def validate_workspace_file_for_import(file_path: str) -> Path:
    """Validate that file_path points to an existing .json file within a trusted workspace.

    Raises ValueError on invalid or untrusted file.
    """
    if not file_path or not isinstance(file_path, str):
        raise ValueError("file_path is required")

    p = Path(file_path.strip()).resolve()
    if not p.is_file():
        raise ValueError(f"File not found: {p}")
    if p.suffix.lower() != ".json":
        raise ValueError(f"Expected a .json session export file, got: {p.name}")

    trusted_workspaces = load_workspaces()
    trusted_roots: list[Path] = []
    for item in trusted_workspaces:
        raw_p = item.get("path") if isinstance(item, dict) else item
        if raw_p:
            try:
                trusted_roots.append(resolve_trusted_workspace(raw_p).resolve())
            except Exception:
                pass

    try:
        trusted_roots.append(resolve_trusted_workspace().resolve())
    except Exception:
        pass

    is_trusted = False
    for root in trusted_roots:
        try:
            p.relative_to(root)
            is_trusted = True
            break
        except ValueError:
            continue

    if not is_trusted:
        try:
            parent_trusted = resolve_trusted_workspace(p.parent).resolve()
            p.relative_to(parent_trusted)
            is_trusted = True
        except Exception:
            pass

    if not is_trusted:
        raise ValueError(f"File path '{p}' does not belong to any configured trusted workspace")

    return p
