"""
Artifacts & Visual Canvas API for Antigravity (AGY) & CAGY WebUI.
Manages brain artifacts, scratch files, reports, diagrams, and generated media.
"""

import logging
import os
import json
import time
from pathlib import Path
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

def _get_brain_dirs() -> List[Path]:
    """Return list of candidate brain directories."""
    candidates = [
        Path(os.environ.get("AGY_APP_DATA_DIR", "")) / "brain" if os.environ.get("AGY_APP_DATA_DIR") else None,
        Path("/root/.gemini/antigravity-cli/brain"),
        Path("/opt/data/gemini/antigravity-cli/brain"),
        Path.home() / ".gemini" / "antigravity-cli" / "brain",
        Path(__file__).resolve().parent.parent.parent / "container_data" / "gemini" / "antigravity-cli" / "brain",
        Path("/workspace/container_data/gemini/antigravity-cli/brain"),
    ]
    valid = []
    for c in candidates:
        if c and c.exists() and c.is_dir() and c not in valid:
            valid.append(c)
    return valid

def _resolve_conv_id_for_session(session_id: str) -> Optional[str]:
    """Map WebUI session_id to AGY conversation_id."""
    if not session_id:
        return None
    map_candidates = [
        Path.home() / ".agy" / "webui" / "sessions" / "agy_session_map.json",
        Path("/root/.agy/webui/sessions/agy_session_map.json"),
        Path.home() / ".hermes" / "webui" / "sessions" / "agy_session_map.json",
        Path("/root/.hermes/webui/sessions/agy_session_map.json"),
        Path("/opt/data/webui/sessions/agy_session_map.json"),
        Path(__file__).resolve().parent.parent.parent / "container_data" / "webui" / "sessions" / "agy_session_map.json",
        Path("/workspace/container_data/webui/sessions/agy_session_map.json"),
    ]
    for mf in map_candidates:
        if mf.exists():
            try:
                data = json.loads(mf.read_text(encoding="utf-8"))
                if session_id in data:
                    return data[session_id]
            except Exception:
                logger.debug("Failed to parse session map %s", mf, exc_info=True)
    return session_id

def list_artifacts(session_id: Optional[str] = None, conv_id: Optional[str] = None) -> Dict[str, Any]:
    """Scan and list all artifacts for the active or recent conversations."""
    target_conv_id = conv_id or _resolve_conv_id_for_session(session_id or "")
    brain_dirs = _get_brain_dirs()

    artifacts = []
    seen_paths = set()

    def scan_dir(cdir: Path, cid: str):
        if not cdir.exists() or not cdir.is_dir():
            return
        for p in cdir.rglob("*"):
            if not p.is_file():
                continue
            # Skip internal system logs, pycache, git, hidden files
            parts = p.parts
            if any(part in [".system_generated", "__pycache__", ".git", "node_modules", ".venv", ".cache", "hermes-webui-src"] for part in parts):
                continue
            if p.name.startswith(".") and not p.name.endswith(".md"):
                continue
            if p.name.endswith(".pyc") or p.name == ".DS_Store":
                continue

            rel_path = str(p.relative_to(cdir))
            if rel_path in seen_paths:
                continue
            seen_paths.add(rel_path)

            ext = p.suffix.lower()
            kind = "markdown" if ext in [".md", ".markdown", ".txt"] else ("image" if ext in [".png", ".jpg", ".jpeg", ".webp", ".svg"] else ("code" if ext in [".py", ".sh", ".js", ".ts", ".json", ".yaml", ".yml", ".sql", ".csv"] else ("html" if ext in [".html", ".htm"] else "other")))
            if kind == "other":
                continue

            try:
                st = p.stat()
                artifacts.append({
                    "name": p.name,
                    "relative_path": rel_path,
                    "absolute_path": str(p.resolve()),
                    "conversation_id": cid,
                    "kind": kind,
                    "extension": ext,
                    "size_bytes": st.st_size,
                    "mtime": st.st_mtime,
                    "is_scratch": "scratch" in parts
                })
            except Exception:
                logger.debug("Failed to stat artifact file %s", p, exc_info=True)

    # 1. Scan target conversation if specified
    if target_conv_id:
        for bdir in brain_dirs:
            cdir = bdir / target_conv_id
            if cdir.exists():
                scan_dir(cdir, target_conv_id)

    # 2. Scan all recent conversations
    for bdir in brain_dirs:
        try:
            for cdir in sorted(bdir.iterdir(), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)[:10]:
                if cdir.is_dir():
                    scan_dir(cdir, cdir.name)
        except Exception:
            logger.warning("Failed to scan brain directory %s for artifacts", bdir, exc_info=True)
            continue

    # Also scan workspace reports, knowledge base, and root markdown documents
    ws_roots = [Path("/workspace"), Path(__file__).resolve().parent.parent.parent]
    for ws in ws_roots:
        if not ws.exists() or not ws.is_dir():
            continue
        # Scan kb and reports folders
        for sub_name in ["nc2-kb", "artifacts", "reports", "docs"]:
            sub_dir = ws / sub_name
            if sub_dir.exists() and sub_dir.is_dir():
                for p in sub_dir.rglob("*"):
                    if p.is_file() and p.name not in seen_paths:
                        seen_paths.add(p.name)
                        ext = p.suffix.lower()
                        kind = "markdown" if ext in [".md", ".txt"] else ("image" if ext in [".png", ".jpg", ".svg"] else ("code" if ext in [".py", ".sh", ".json", ".yaml", ".yml"] else "other"))
                        if kind == "other":
                            continue
                        try:
                            st = p.stat()
                            artifacts.append({
                                "name": p.name,
                                "relative_path": f"{sub_name}/{p.relative_to(sub_dir)}",
                                "absolute_path": str(p.resolve()),
                                "conversation_id": "workspace",
                                "kind": kind,
                                "extension": ext,
                                "size_bytes": st.st_size,
                                "mtime": st.st_mtime,
                                "is_scratch": False
                            })
                        except Exception:
                            logger.debug("Failed to stat workspace artifact %s", p, exc_info=True)
        # Scan root markdown files
        for p in ws.glob("*.md"):
            if p.is_file() and p.name not in seen_paths:
                seen_paths.add(p.name)
                try:
                    st = p.stat()
                    artifacts.append({
                        "name": p.name,
                        "relative_path": p.name,
                        "absolute_path": str(p.resolve()),
                        "conversation_id": "workspace",
                        "kind": "markdown",
                        "extension": ".md",
                        "size_bytes": st.st_size,
                        "mtime": st.st_mtime,
                        "is_scratch": False
                    })
                except Exception:
                    logger.debug("Failed to stat root markdown %s", p, exc_info=True)

    artifacts.sort(key=lambda a: a.get("mtime", 0), reverse=True)
    return {
        "artifacts": artifacts,
        "total": len(artifacts),
        "target_conv_id": target_conv_id,
        "timestamp": time.time()
    }

def get_artifact_content(file_path: str) -> Dict[str, Any]:
    """Retrieve full content of an artifact file."""
    if not file_path:
        return {"error": "file_path parameter required"}

    p = Path(file_path)
    if not p.exists() or not p.is_file():
        return {"error": f"Artifact file not found: {file_path}"}

    ext = p.suffix.lower()
    is_binary = ext in [".png", ".jpg", ".jpeg", ".webp", ".pdf", ".tar", ".gz", ".zip"]

    if is_binary:
        import base64
        try:
            b64 = base64.b64encode(p.read_bytes()).decode("utf-8")
            mime = "image/png" if ext == ".png" else ("image/jpeg" if ext in [".jpg", ".jpeg"] else "application/octet-stream")
            return {
                "name": p.name,
                "path": str(p),
                "is_binary": True,
                "mime_type": mime,
                "data_url": f"data:{mime};base64,{b64}",
                "size_bytes": p.stat().st_size
            }
        except Exception as e:
            return {"error": str(e)}

    try:
        content = p.read_text(encoding="utf-8", errors="replace")
        return {
            "name": p.name,
            "path": str(p),
            "is_binary": False,
            "content": content,
            "size_bytes": len(content.encode("utf-8")),
            "lines": len(content.splitlines()),
            "extension": ext
        }
    except Exception as e:
        return {"error": str(e)}
