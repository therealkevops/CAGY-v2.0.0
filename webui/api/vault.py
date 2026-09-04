"""
Antigravity (CAGY) Knowledge Vault & Graph Memory Engine
Manages modular Markdown notes with bi-directional wikilinks, computes relationship graphs,
and compiles persistent memory into native Antigravity rules.
"""

import os
import re
import time
from pathlib import Path
from typing import Dict, Any, List, Set, Optional

WIKILINK_REGEX = re.compile(r'\[\[([^\]\|#]+)(?:#[^\]\|]+)?(?:\|([^\]]+))?\]\]')

def extract_wikilinks(content: str) -> List[Dict[str, Any]]:
    """Extract all wikilinks [[Target|Alias]] or [[Target#Section|Alias]] from text."""
    links = []
    for match in WIKILINK_REGEX.finditer(content):
        target = match.group(1).strip()
        alias = match.group(2).strip() if match.group(2) else ""
        links.append({"target": target, "alias": alias})
    return links

def get_vault_dir(workspace_path: Optional[Path] = None) -> Path:
    """Resolve knowledge vault directory."""
    if workspace_path:
        v = (workspace_path / "knowledge").resolve()
        return v
    
    for var in ("AGY_WORKSPACE_ROOT", "WORKSPACE_DIR", "AGY_WORKSPACE_DIR", "HERMES_WORKSPACE_ROOT"):
        val = os.environ.get(var)
        if val and Path(val).exists():
            return (Path(val) / "knowledge").resolve()

    cwd = Path.cwd()
    if cwd.name == "webui":
        return (cwd.parent / "knowledge").resolve()
    return (cwd / "knowledge").resolve()

def _extract_title(content: str, fallback: str) -> str:
    """Extract first H1 title from Markdown, or use fallback."""
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("# ") and not line.startswith("## "):
            return line[2:].strip()
    return fallback.replace("_", " ").replace("-", " ").title()

def _normalize_id(link: str) -> str:
    """Normalize a wikilink target to a standard note ID."""
    clean = link.strip().replace("\\", "/").rstrip("/")
    if clean.endswith(".md"):
        clean = clean[:-3]
    return clean

def scan_vault(vault_path: Path) -> Dict[str, Any]:
    """
    Recursively scan all markdown files in vault, extract wikilinks,
    compute backlinks, and build the nodes and edges for the Knowledge Graph.
    """
    if not vault_path.exists():
        vault_path.mkdir(parents=True, exist_ok=True)

    nodes = []
    outgoing_links: Dict[str, List[str]] = {}
    backlinks: Dict[str, List[str]] = {}
    id_map: Dict[str, str] = {}  # lowercase -> actual note ID
    note_details: Dict[str, Dict[str, Any]] = {}

    md_files = list(vault_path.rglob("*.md"))

    # First pass: Index all note IDs
    for p in md_files:
        rel = p.relative_to(vault_path).as_posix()
        note_id = rel[:-3] if rel.endswith(".md") else rel
        id_map[note_id.lower()] = note_id
        # Also index by basename alone for shorthand [[profile]] links
        basename = p.stem.lower()
        if basename not in id_map:
            id_map[basename] = note_id

    # Second pass: Extract content, titles, and wikilinks
    for p in md_files:
        rel = p.relative_to(vault_path).as_posix()
        note_id = rel[:-3] if rel.endswith(".md") else rel
        folder = p.parent.relative_to(vault_path).as_posix()
        if folder == ".":
            folder = "root"

        try:
            content = p.read_text(encoding="utf-8", errors="replace")
            stat = p.stat()
            size = stat.st_size
            mtime = stat.st_mtime
        except Exception:
            content = ""
            size = 0
            mtime = 0

        title = _extract_title(content, p.stem)

        # Extract all wikilinks
        raw_links = WIKILINK_REGEX.findall(content)
        resolved_links: List[str] = []
        for raw_target, _display in raw_links:
            norm_target = _normalize_id(raw_target).lower()
            if norm_target in id_map:
                actual_id = id_map[norm_target]
                if actual_id != note_id and actual_id not in resolved_links:
                    resolved_links.append(actual_id)
            else:
                # Uncreated target note
                clean_target = _normalize_id(raw_target)
                if clean_target != note_id and clean_target not in resolved_links:
                    resolved_links.append(clean_target)

        outgoing_links[note_id] = resolved_links

        note_details[note_id] = {
            "id": note_id,
            "title": title,
            "path": rel,
            "folder": folder,
            "size_bytes": size,
            "mtime": mtime,
            "links": resolved_links,
        }

    # Third pass: Invert outgoing links into backlinks
    for source_id, targets in outgoing_links.items():
        for target_id in targets:
            if target_id not in backlinks:
                backlinks[target_id] = []
            if source_id not in backlinks[target_id]:
                backlinks[target_id].append(source_id)

    # Fourth pass: Assemble graph nodes and edges
    edges: List[Dict[str, Any]] = []
    seen_edges: Set[str] = set()

    for note_id, info in note_details.items():
        bl = backlinks.get(note_id, [])
        links_count = len(info["links"])
        backlinks_count = len(bl)

        nodes.append({
            "id": note_id,
            "title": info["title"],
            "path": info["path"],
            "folder": info["folder"],
            "size_bytes": info["size_bytes"],
            "mtime": info["mtime"],
            "links_count": links_count,
            "backlinks_count": backlinks_count,
            "total_connections": links_count + backlinks_count,
            "outgoing_links": info["links"],
            "backlinks": bl,
        })

        for target_id in info["links"]:
            edge_key = f"{note_id}->{target_id}"
            if edge_key not in seen_edges:
                seen_edges.add(edge_key)
                edges.append({
                    "source": note_id,
                    "target": target_id,
                    "exists": target_id in note_details
                })

    # Sort nodes by total connections descending
    nodes.sort(key=lambda n: n["total_connections"], reverse=True)

    return {
        "nodes": nodes,
        "edges": edges,
        "stats": {
            "total_notes": len(nodes),
            "total_edges": len(edges),
            "vault_path": str(vault_path),
            "timestamp": time.time()
        }
    }

def get_note(vault_path: Path, rel_path: str) -> Dict[str, Any]:
    """Retrieve full content and backlink context for a specific note."""
    if not rel_path:
        return {"error": "rel_path is required", "exists": False, "ok": False}

    clean_rel = str(rel_path).strip().lstrip("/")
    if not clean_rel.endswith(".md"):
        clean_rel += ".md"

    full_path = (vault_path / clean_rel).resolve()
    try:
        full_path.relative_to(vault_path.resolve())
    except ValueError:
        return {"error": "Path traversal not allowed", "exists": False, "ok": False}

    if not full_path.exists() or not full_path.is_file():
        return {"error": f"Note not found: {clean_rel}", "exists": False, "path": clean_rel}

    try:
        content = full_path.read_text(encoding="utf-8", errors="replace")
        stat = full_path.stat()
    except Exception as e:
        return {"error": str(e), "exists": False, "ok": False}

    note_id = clean_rel[:-3]
    title = _extract_title(content, full_path.stem)

    # Fast scan for backlinks
    scan = scan_vault(vault_path)
    outgoing = []
    incoming = []
    for edge in scan.get("edges", []):
        if edge["source"] == note_id:
            outgoing.append(edge["target"])
        elif edge["target"] == note_id:
            incoming.append(edge["source"])

    return {
        "id": note_id,
        "title": title,
        "path": clean_rel,
        "content": content,
        "size_bytes": stat.st_size,
        "mtime": stat.st_mtime,
        "outgoing_links": outgoing,
        "backlinks": incoming,
        "exists": True,
        "ok": True
    }

def save_note(vault_path: Path, rel_path: str, content: str) -> Dict[str, Any]:
    """Write markdown note to disk, ensuring directory structure."""
    if not rel_path:
        return {"error": "rel_path is required", "ok": False}

    clean_rel = str(rel_path).strip().lstrip("/")
    if not clean_rel.endswith(".md"):
        clean_rel += ".md"

    full_path = (vault_path / clean_rel).resolve()
    try:
        full_path.relative_to(vault_path.resolve())
    except ValueError:
        return {"error": "Path traversal not allowed", "ok": False}

    try:
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content or "", encoding="utf-8")
        # Auto-sync rules when saving core profile/conventions notes
        workspace_path = vault_path.parent
        sync_vault_to_rules(vault_path, workspace_path)
        return {
            "ok": True,
            "path": clean_rel,
            "size_bytes": len(content.encode("utf-8")),
            "message": f"Successfully saved note {clean_rel}"
        }
    except Exception as e:
        return {"error": str(e), "ok": False}

def delete_note(vault_path: Path, rel_path: str) -> Dict[str, Any]:
    """Delete a note from the vault."""
    if not rel_path:
        return {"error": "rel_path is required", "ok": False}

    clean_rel = str(rel_path).strip().lstrip("/")
    if not clean_rel.endswith(".md"):
        clean_rel += ".md"

    full_path = (vault_path / clean_rel).resolve()
    try:
        full_path.relative_to(vault_path.resolve())
    except ValueError:
        return {"error": "Path traversal not allowed", "ok": False}

    if not full_path.exists():
        return {"error": f"Note {clean_rel} not found", "ok": False}

    try:
        full_path.unlink()
        return {"ok": True, "message": f"Deleted {clean_rel}"}
    except Exception as e:
        return {"error": str(e), "ok": False}

def sync_vault_to_rules(vault_path: Path, workspace_path: Optional[Path] = None) -> Dict[str, Any]:
    """
    Compile core knowledge from vault (User profile, conventions, architectural decisions)
    into native Antigravity rules (.gemini/rules/knowledge_vault.md) so Gemini learns
    and retains context across all turns.
    """
    if not vault_path.exists():
        return {"error": "Vault does not exist", "ok": False}

    if workspace_path is None:
        workspace_path = vault_path.parent

    rules_dir = workspace_path / ".gemini" / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    target_rule = rules_dir / "knowledge_vault.md"

    scan = scan_vault(vault_path)
    nodes = scan.get("nodes", [])

    compiled_sections = [
        "# Antigravity Knowledge Vault & Long-Term Memory",
        "",
        "> [!IMPORTANT]",
        "> This context is automatically compiled from the workspace Knowledge Vault (`/workspace/knowledge/`).",
        "> Retain these principles, user preferences, and architectural decisions across all turns.",
        ""
    ]

    # Priority 1: User Profile
    user_notes = [n for n in nodes if n["folder"] == "user"]
    if user_notes:
        compiled_sections.append("## User Preferences & Profile")
        for n in user_notes:
            note_data = get_note(vault_path, n["path"])
            if note_data.get("content"):
                compiled_sections.append(f"### {note_data.get('title', n['id'])}")
                compiled_sections.append(note_data["content"].strip())
                compiled_sections.append("")

    # Priority 2: Architectural Principles
    arch_notes = [n for n in nodes if n["folder"] == "architecture"]
    if arch_notes:
        compiled_sections.append("## Architectural Principles")
        for n in arch_notes:
            note_data = get_note(vault_path, n["path"])
            if note_data.get("content"):
                compiled_sections.append(f"### {note_data.get('title', n['id'])}")
                compiled_sections.append(note_data["content"].strip())
                compiled_sections.append("")

    # Priority 3: Architecture Decision Records (ADRs)
    decisions = [n for n in nodes if n["folder"] == "decisions"]
    if decisions:
        compiled_sections.append("## Key Architectural Decisions")
        for n in decisions:
            note_data = get_note(vault_path, n["path"])
            if note_data.get("content"):
                compiled_sections.append(f"### {note_data.get('title', n['id'])}")
                compiled_sections.append(note_data["content"].strip())
                compiled_sections.append("")

    rule_content = "\n".join(compiled_sections).strip() + "\n"
    target_rule.write_text(rule_content, encoding="utf-8")

    return {
        "ok": True,
        "rule_file": str(target_rule),
        "total_compiled_notes": len(user_notes) + len(arch_notes) + len(decisions),
        "bytes_written": len(rule_content.encode("utf-8")),
        "timestamp": time.time()
    }
