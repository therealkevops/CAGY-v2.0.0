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
TAG_REGEX = re.compile(r'(?:^|\s)#([a-zA-Z][a-zA-Z0-9_\-/]*)')
CODE_BLOCK_REGEX = re.compile(r'```[\s\S]*?```|`[^`\n]+`')

def extract_wikilinks(content: str) -> List[Dict[str, Any]]:
    """Extract all wikilinks [[Target|Alias]] or [[Target#Section|Alias]] from text, ignoring code spans."""
    clean_content = CODE_BLOCK_REGEX.sub('', content)
    links = []
    for match in WIKILINK_REGEX.finditer(clean_content):
        target = match.group(1).strip()
        alias = match.group(2).strip() if match.group(2) else ""
        links.append({"target": target, "alias": alias})
    return links

def extract_tags(content: str) -> List[str]:
    """Extract all #tags from markdown content (ignoring markdown headings # Heading)."""
    tags = set()
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        # Strip heading markers at start of line
        if line.startswith("#"):
            parts = line.split(maxsplit=1)
            if parts and all(c == "#" for c in parts[0]):
                line = parts[1] if len(parts) > 1 else ""
        for m in TAG_REGEX.finditer(line):
            tag = m.group(1).strip().lower()
            if len(tag) >= 2:
                tags.add(tag)
    return sorted(tags)

def extract_space_from_rel_path(rel_path: str) -> str:
    """
    Determine the space container of a note based on its relative path.
    - spaces/<space_id>/... -> <space_id>
    - user/... -> user (global user profile)
    - architecture/..., decisions/..., notes/... -> global
    """
    clean = str(rel_path).replace("\\", "/").strip().lstrip("/")
    parts = clean.split("/")
    if len(parts) > 1 and parts[0] == "spaces":
        return parts[1]
    if parts[0] == "user":
        return "user"
    return "global"

def infer_space_from_workspace(workspace_path: Optional[Path] = None) -> str:
    """
    Infer space identifier from workspace path.
    e.g. /workspace/projects/cka-kb -> cka-kb
         /workspace -> global
    """
    if not workspace_path:
        return "global"
    p = Path(workspace_path).resolve()
    for parent in [p, *p.parents]:
        if parent.parent and parent.parent.name in ("projects", "spaces", "workspaces"):
            return parent.name
    return "global"

def get_vault_dir(workspace_path: Optional[Path] = None) -> Path:
    """Resolve knowledge vault directory."""
    if workspace_path:
        local_v = (Path(workspace_path) / "knowledge").resolve()
        if local_v.exists():
            return local_v

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

def scan_vault(vault_path: Path, space_filter: Optional[str] = None) -> Dict[str, Any]:
    """
    Recursively scan all markdown files in vault, extract wikilinks,
    compute backlinks, tag spaces, and build the nodes and edges for the Knowledge Graph.
    Supports filtering by space (e.g. space_filter="cka-kb" or "global").
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
        space = extract_space_from_rel_path(rel)

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
        tags = extract_tags(content)

        # Extract all wikilinks (ignoring code spans)
        wikilinks_data = extract_wikilinks(content)
        resolved_links: List[str] = []
        for item in wikilinks_data:
            raw_target = item["target"]
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
            "space": space,
            "size_bytes": size,
            "mtime": mtime,
            "links": resolved_links,
            "tags": tags,
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
            "space": info.get("space", "global"),
            "tags": info.get("tags", []),
            "size_bytes": info["size_bytes"],
            "mtime": info["mtime"],
            "links_count": links_count,
            "backlinks_count": backlinks_count,
            "in_degree": backlinks_count,
            "out_degree": links_count,
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

    # Discover all distinct spaces
    discovered_spaces = {n.get("space", "global") for n in nodes if n.get("space") not in ("user",)}
    all_spaces = ["global", *sorted(s for s in discovered_spaces if s != "global")]

    # Optional Space filtering
    if space_filter and space_filter not in ("all", ""):
        clean_sf = space_filter.strip().lower()
        if clean_sf == "global":
            allowed_spaces = {"global", "user"}
        else:
            allowed_spaces = {clean_sf, "user"}

        filtered_nodes = [n for n in nodes if n.get("space") in allowed_spaces]
        filtered_ids = {n["id"] for n in filtered_nodes}
        filtered_edges = [
            e for e in edges
            if e["source"] in filtered_ids and (e["target"] in filtered_ids or not e.get("exists", False))
        ]
    else:
        filtered_nodes = nodes
        filtered_edges = edges

    # Health analysis: orphans & unresolved links on the filtered graph
    node_ids = {n["id"] for n in filtered_nodes}
    orphans = [n["id"] for n in filtered_nodes if n["total_connections"] == 0]
    unresolved_map: Dict[str, List[str]] = {}
    for edge in filtered_edges:
        if not edge.get("exists", False) or edge["target"] not in node_ids:
            unresolved_map.setdefault(edge["target"], []).append(edge["source"])
    unresolved_links = [
        {"target": target, "sources": sorted(sources), "occurrences": len(sources)}
        for target, sources in sorted(unresolved_map.items(), key=lambda x: len(x[1]), reverse=True)
    ]

    all_tags = sorted({t for n in filtered_nodes for t in n.get("tags", [])})

    return {
        "nodes": filtered_nodes,
        "edges": filtered_edges,
        "spaces": all_spaces,
        "active_space": space_filter or "all",
        "stats": {
            "total_notes": len(filtered_nodes),
            "total_edges": len(filtered_edges),
            "spaces_count": len(all_spaces),
            "spaces": all_spaces,
            "orphan_count": len(orphans),
            "orphans": orphans,
            "unresolved_count": len(unresolved_links),
            "unresolved_links": unresolved_links,
            "all_tags": all_tags,
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
        "tags": extract_tags(content),
        "size_bytes": stat.st_size,
        "mtime": stat.st_mtime,
        "outgoing_links": outgoing,
        "backlinks": incoming,
        "exists": True,
        "ok": True
    }

def save_note(vault_path: Path, rel_path: str, content: str, workspace_path: Optional[Path] = None) -> Dict[str, Any]:
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
        ws = workspace_path if workspace_path is not None else vault_path.parent
        sync_vault_to_rules(vault_path, ws)
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

def sync_vault_to_rules(
    vault_path: Path,
    workspace_path: Optional[Path] = None,
    space: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Compile core knowledge from vault (User profile, conventions, architectural decisions)
    into native Antigravity rules (.gemini/rules/knowledge_vault.md) so Gemini learns
    and retains context across all turns.
    Supports space scoping to eliminate token bloat and context contamination across spaces.
    """
    if not vault_path.exists():
        return {"error": "Vault does not exist", "ok": False}

    if workspace_path is None:
        workspace_path = vault_path.parent

    if space is None and workspace_path:
        space = infer_space_from_workspace(workspace_path)
    active_space = (space or "global").strip().lower()

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
        f"> Active Scope: **{active_space.title()}**",
        "> Retain these principles, user preferences, and architectural decisions across all turns.",
        ""
    ]

    # Priority 1: User Profile (Global - always included across all spaces)
    user_notes = [n for n in nodes if n.get("space") == "user" or n["folder"] == "user"]
    if user_notes:
        compiled_sections.append("## User Preferences & Profile")
        for n in user_notes:
            note_data = get_note(vault_path, n["path"])
            if note_data.get("content"):
                compiled_sections.append(f"### {note_data.get('title', n['id'])}")
                compiled_sections.append(note_data["content"].strip())
                compiled_sections.append("")

    # Priority 2: Architectural Principles (Scoped to active space if set, else global)
    if active_space != "global":
        arch_notes = [n for n in nodes if n.get("space") == active_space and "architecture" in n["folder"]]
    else:
        arch_notes = [n for n in nodes if n.get("space") == "global" and n["folder"] == "architecture"]

    if arch_notes:
        compiled_sections.append("## Architectural Principles")
        for n in arch_notes:
            note_data = get_note(vault_path, n["path"])
            if note_data.get("content"):
                compiled_sections.append(f"### {note_data.get('title', n['id'])}")
                compiled_sections.append(note_data["content"].strip())
                compiled_sections.append("")

    # Priority 3: Architecture Decision Records (ADRs) (Scoped to active space if set, else global)
    if active_space != "global":
        decisions = [n for n in nodes if n.get("space") == active_space and "decisions" in n["folder"]]
    else:
        decisions = [n for n in nodes if n.get("space") == "global" and n["folder"] == "decisions"]

    if decisions:
        compiled_sections.append("## Key Architectural Decisions")
        for n in decisions:
            note_data = get_note(vault_path, n["path"])
            if note_data.get("content"):
                compiled_sections.append(f"### {note_data.get('title', n['id'])}")
                compiled_sections.append(note_data["content"].strip())
                compiled_sections.append("")

    # Priority 4: Space-Specific Notes & Guides
    domain_notes = []
    if active_space != "global":
        domain_notes = [
            n for n in nodes
            if n.get("space") == active_space and ("notes" in n["folder"] or n["folder"] == f"spaces/{active_space}")
        ]
        if domain_notes:
            compiled_sections.append(f"## {active_space.title()} Domain Knowledge & Guides")
            for n in domain_notes:
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
        "space": active_space,
        "total_compiled_notes": len(user_notes) + len(arch_notes) + len(decisions) + len(domain_notes),
        "bytes_written": len(rule_content.encode("utf-8")),
        "timestamp": time.time()
    }

def memorize_insight(
    vault_path: Path,
    text: str,
    category: Optional[str] = None,
    title: Optional[str] = None,
    workspace_path: Optional[Path] = None,
    space: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Extract, auto-categorize, interlink, and save an insight, decision, or convention
    into the Knowledge Vault, and immediately re-compile rules.
    Supports space containers (e.g. space="cka-kb").
    """
    text = (text or "").strip()
    if not text:
        return {"error": "No content provided to memorize", "ok": False}

    vault_path.mkdir(parents=True, exist_ok=True)
    if workspace_path is None:
        workspace_path = vault_path.parent

    if not space:
        space = infer_space_from_workspace(workspace_path)
    active_space = (space or "global").strip().lower()

    lower_text = text.lower()

    # 1. Categorization heuristics
    cat = (category or "").strip().lower()
    if not cat:
        if any(k in lower_text for k in ["decid", "adr", "trade-off", "tradeoff", "chose", "chosen", "choice", "migrate from", "deprecated", "forked"]):
            cat = "decisions"
        elif any(k in lower_text for k in ["user profile", "preference", "i prefer", "convention", "coding standard", "style guide", "my role", "infrastructure engineer", "cloud architect"]):
            cat = "user"
        elif any(k in lower_text for k in ["architecture", "container", "infrastructure", "microservice", "cluster", "nutanix", "docker", "unified", "pipeline", "supervisor"]):
            cat = "architecture"
        else:
            cat = "user" if any(k in lower_text for k in ["rule", "guideline", "always", "never"]) else "notes"

    # 2. Title derivation
    derived_title = (title or "").strip()
    if not derived_title:
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("# ") and not line.startswith("## "):
                derived_title = line[2:].strip()
                break
        if not derived_title:
            first_line = text.splitlines()[0].strip().lstrip("#").strip()
            m = re.split(r'[.:;\n]', first_line)
            clean_chunk = m[0].strip() if m else first_line
            if len(clean_chunk) > 60:
                clean_chunk = clean_chunk[:60].rsplit(" ", 1)[0]
            derived_title = clean_chunk or "Memorized Insight"

    # 3. Slug & Filename
    raw_slug = re.sub(r'[^a-zA-Z0-9_\-]+', '_', derived_title.lower()).strip('_')
    if not raw_slug:
        raw_slug = f"insight_{int(time.time())}"

    if cat == "decisions":
        next_num = get_next_adr_number(vault_path, space=active_space if active_space != "global" else None)
        clean_slug = re.sub(r'^adr_\d+_?', '', raw_slug).strip('_')
        if not clean_slug:
            clean_slug = "decision"
        filename = f"adr_{next_num:03d}_{clean_slug}.md"
        clean_title_core = re.sub(r'^(?:adr\s*\d*[:\-]?\s*)', '', derived_title, flags=re.IGNORECASE).strip()
        note_title = f"ADR {next_num:03d}: {clean_title_core or 'Architectural Decision'}"
    else:
        filename = f"{raw_slug}.md"
        note_title = derived_title

    # Target relative path: user preferences are always global
    if cat == "user":
        target_rel = f"user/{filename}"
    elif active_space != "global":
        target_rel = f"spaces/{active_space}/{cat}/{filename}"
    else:
        target_rel = f"{cat}/{filename}"

    # 4. Wikilink Cross-Referencing
    scan = scan_vault(vault_path)
    existing_nodes = scan.get("nodes", [])
    discovered_links = []

    for n in existing_nodes:
        nid = n["id"]
        if n["path"] == target_rel:
            continue
        n_title = n.get("title", "").strip()
        n_base = nid.split("/")[-1].replace("_", " ")
        if (n_title and len(n_title) > 3 and n_title.lower() in lower_text) or \
           (n_base and len(n_base) > 4 and n_base.lower() in lower_text):
            if f"[[{nid}" not in text and f"[[{n_title}" not in text and f"[[{nid.split('/')[-1]}" not in text:
                discovered_links.append(nid)

    # 5. Format Content
    has_h1 = any(l.strip().startswith("# ") for l in text.splitlines())
    today = time.strftime("%Y-%m-%d")

    content_parts = []
    if not has_h1:
        content_parts.append(f"# {note_title}\n")
        if cat == "decisions":
            content_parts.append(f"- **Date**: {today}\n- **Status**: Accepted\n")
            if active_space != "global":
                content_parts.append(f"- **Space**: {active_space}\n")
            content_parts.append("## Context & Decision\n")
        elif cat == "user":
            content_parts.append(f"- **Date**: {today}\n- **Category**: User Preferences & Conventions\n")
        elif cat == "architecture":
            content_parts.append(f"- **Date**: {today}\n- **Category**: Architecture & Infrastructure\n")
            if active_space != "global":
                content_parts.append(f"- **Space**: {active_space}\n")
        else:
            if active_space != "global":
                content_parts.append(f"- **Date**: {today}\n- **Space**: {active_space}\n")

    content_parts.append(text)

    if discovered_links:
        content_parts.append("\n## Related References")
        for dlink in sorted(set(discovered_links)):
            content_parts.append(f"- [[{dlink}]]")

    final_content = "\n".join(content_parts).strip() + "\n"

    # 6. Save note and sync rules
    save_res = save_note(vault_path, target_rel, final_content, workspace_path=workspace_path)
    if not save_res.get("ok"):
        return save_res

    sync_res = sync_vault_to_rules(vault_path, workspace_path, space=active_space)

    return {
        "ok": True,
        "path": f"knowledge/{target_rel}",
        "rel_path": target_rel,
        "title": note_title,
        "category": cat,
        "space": active_space if cat != "user" else "user",
        "links_discovered": len(discovered_links),
        "references": discovered_links,
        "rules_synced": sync_res.get("ok", False),
        "timestamp": time.time()
    }

def get_next_adr_number(vault_path: Path, space: Optional[str] = None) -> int:
    """Derive the next sequential ADR number (e.g. 3 for adr_003_...), scoped to space if provided."""
    if not vault_path:
        vault_path = get_vault_dir()

    if space and space not in ("global", "user", "all"):
        decisions_dir = vault_path / "spaces" / space / "decisions"
    else:
        decisions_dir = vault_path / "decisions"

    if not decisions_dir.exists():
        return 1
    existing = list(decisions_dir.glob("adr_*.md"))
    max_num = 0
    for adr in existing:
        m = re.match(r'adr_(\d+)', adr.name)
        if m:
            try:
                max_num = max(max_num, int(m.group(1)))
            except ValueError:
                pass
    return max_num + 1

def get_note_template(category: str, title: str, next_adr: Optional[int] = None, space: Optional[str] = None) -> Dict[str, Any]:
    """Generate path, suggested filename, and starter template for a category."""
    clean_cat = (category or "notes").strip().lower()
    clean_title = (title or "Untitled Note").strip()
    raw_slug = re.sub(r'[^a-zA-Z0-9_\-]+', '_', clean_title.lower()).strip('_') or "note"
    today = time.strftime("%Y-%m-%d")
    active_space = (space or "global").strip().lower()

    if clean_cat == "decisions":
        num = next_adr if next_adr is not None else 1
        clean_slug = re.sub(r'^adr_\d+_?', '', raw_slug).strip('_') or "decision"
        filename = f"adr_{num:03d}_{clean_slug}.md"
        if active_space != "global":
            rel_path = f"spaces/{active_space}/decisions/{filename}"
        else:
            rel_path = f"decisions/{filename}"
        header_title = f"ADR {num:03d}: {clean_title.replace('_', ' ').title()}"
        content = f"""# {header_title}

- **Date**: {today}
- **Status**: Proposed
- **Deciders**: Antigravity Core Team
"""
        if active_space != "global":
            content += f"- **Space**: {active_space}\n"
        content += """
## Context & Problem Statement
What is the context, architectural challenge, or motivation driving this decision?

## Decision Drivers
- Need for modularity and maintainability
- Performance and resource efficiency

## Considered Options
- **Option 1**: Description
- **Option 2**: Description

## Decision Outcome
Chosen option: **Option 1**, because ...

### Positive Consequences
- Streamlined architecture

### Negative Consequences / Trade-offs
- Implementation overhead

## References
- [[architecture/knowledge_vault_and_graph]]
"""
    elif clean_cat == "architecture":
        filename = f"{raw_slug}.md"
        if active_space != "global":
            rel_path = f"spaces/{active_space}/architecture/{filename}"
        else:
            rel_path = f"architecture/{filename}"
        content = f"""# {clean_title}

- **Category**: Architecture & System Design
- **Last Updated**: {today}
- **Status**: Active
"""
        if active_space != "global":
            content += f"- **Space**: {active_space}\n"
        content += """
## System Overview
High-level overview of the component, runtime model, or subsystem.

## Architecture & Data Flow
Describe data flow, boundaries, and container execution environment.

## Key Components & Interactions
- Component A: Description
- Component B: Description

## Related Notes & Decisions
- [[user/conventions]]
"""
    elif clean_cat == "user":
        filename = f"{raw_slug}.md"
        rel_path = f"user/{filename}"
        content = f"""# {clean_title}

- **Category**: User Preferences & Conventions
- **Last Updated**: {today}

## Principles & Preferences
Document tone, workflow patterns, and toolchain defaults.

## Coding Standards
- Standard 1
- Standard 2

## References
- [[architecture/knowledge_vault_and_graph]]
"""
    else:
        clean_cat = "notes"
        filename = f"{raw_slug}.md"
        if active_space != "global":
            rel_path = f"spaces/{active_space}/notes/{filename}"
        else:
            rel_path = f"notes/{filename}"
        content = f"""# {clean_title}

- **Created**: {today}
- **Tags**: #notes
"""
        if active_space != "global":
            content += f"- **Space**: {active_space}\n"
        content += """
## Overview
Notes, domain documentation, and reference material.

## Key Takeaways
- Point 1
- Point 2
"""

    return {
        "ok": True,
        "category": clean_cat,
        "title": clean_title,
        "filename": filename,
        "rel_path": rel_path,
        "full_rel_path": f"knowledge/{rel_path}",
        "space": active_space if clean_cat != "user" else "user",
        "template": content.strip() + "\n",
        "template_content": content.strip() + "\n",
        "next_adr": next_adr
    }

def list_spaces(vault_path: Path) -> List[str]:
    """List all detected spaces in the knowledge vault."""
    scan = scan_vault(vault_path)
    return scan.get("spaces", ["global"])


def search_vault(
    vault_path: Path,
    query: str,
    folder: Optional[str] = None,
    tag: Optional[str] = None,
    limit: int = 50,
    space: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Full-text keyword and snippet search across all vault markdown notes.
    Returns matched notes with highlighted excerpt snippets, line numbers, and match scores.
    Supports space scoping (e.g. space="cka-kb" or space="global").
    """
    if not vault_path.exists():
        return {"query": query, "results": [], "total_matches": 0, "ok": True}

    q = (query or "").strip().lower()
    terms = [t for t in q.split() if t]
    filter_folder = (folder or "").strip().lower()
    filter_tag = (tag or "").strip().lower().lstrip("#")
    filter_space = (space or "").strip().lower()

    results = []
    md_files = list(vault_path.rglob("*.md"))

    for p in md_files:
        rel = p.relative_to(vault_path).as_posix()
        note_folder = p.parent.relative_to(vault_path).as_posix()
        if note_folder == ".":
            note_folder = "root"

        note_space = extract_space_from_rel_path(rel)
        if filter_space and filter_space != "all":
            if filter_space == "global" and note_space not in ("global", "user"):
                continue
            elif filter_space != "global" and note_space not in (filter_space, "user"):
                continue

        if filter_folder and filter_folder != "all" and note_folder != filter_folder:
            continue

        try:
            content = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        tags = extract_tags(content)
        if filter_tag and not any(filter_tag in t.lower() for t in tags):
            continue

        title = _extract_title(content, p.stem)
        lower_content = content.lower()
        lower_title = title.lower()

        # If query is non-empty, all terms must match somewhere in title, tags, or content
        if terms:
            matches_all = True
            for term in terms:
                if (term not in lower_title) and (not any(term in t.lower() for t in tags)) and (term not in lower_content):
                    matches_all = False
                    break
            if not matches_all:
                continue

        # Extract snippets and calculate score
        score = 0
        snippets = []
        lines = content.splitlines()

        for idx, line in enumerate(lines, start=1):
            lower_line = line.lower()
            hit = any(t in lower_line for t in terms) if terms else False
            if hit:
                score += 1
                # Format snippet with <mark> highlighting
                highlighted = line.strip()
                if len(highlighted) > 160:
                    first_idx = len(highlighted)
                    for t in terms:
                        pos = highlighted.lower().find(t)
                        if pos != -1 and pos < first_idx:
                            first_idx = pos
                    start_char = max(0, first_idx - 40)
                    end_char = min(len(highlighted), first_idx + 120)
                    prefix = "..." if start_char > 0 else ""
                    suffix = "..." if end_char < len(highlighted) else ""
                    highlighted = prefix + highlighted[start_char:end_char] + suffix

                for t in terms:
                    pattern = re.compile(re.escape(t), re.IGNORECASE)
                    highlighted = pattern.sub(lambda m: f"<mark>{m.group(0)}</mark>", highlighted)

                if len(snippets) < 4:
                    snippets.append({
                        "line": idx,
                        "text": line.strip(),
                        "highlighted": highlighted
                    })

        # Boost score for title or tag matches
        for t in terms:
            if t in lower_title:
                score += 10
            for tag_item in tags:
                if t in tag_item.lower():
                    score += 5

        if not terms:
            score = 1

        results.append({
            "id": rel[:-3] if rel.endswith(".md") else rel,
            "title": title,
            "path": rel,
            "folder": note_folder,
            "space": note_space,
            "tags": tags,
            "score": score,
            "snippets": snippets,
            "total_snippets": score if terms else 0
        })

    results.sort(key=lambda r: r["score"], reverse=True)
    if limit > 0:
        results = results[:limit]

    return {
        "ok": True,
        "query": query,
        "results": results,
        "total_matches": len(results),
        "folder_filter": filter_folder or None,
        "tag_filter": filter_tag or None,
        "timestamp": time.time()
    }


def get_vault_health(vault_path: Path) -> Dict[str, Any]:
    """Analyze vault graph health: orphan notes, unresolved links, and central hub statistics."""
    scan = scan_vault(vault_path)
    nodes = scan.get("nodes", [])
    edges = scan.get("edges", [])

    node_ids = {n["id"] for n in nodes}
    orphans = [n for n in nodes if n["total_connections"] == 0]

    unresolved_map: Dict[str, List[str]] = {}
    for edge in edges:
        if not edge.get("exists", False) or edge["target"] not in node_ids:
            unresolved_map.setdefault(edge["target"], []).append(edge["source"])

    unresolved_links = [
        {"target": target, "sources": sorted(sources), "occurrences": len(sources)}
        for target, sources in sorted(unresolved_map.items(), key=lambda x: len(x[1]), reverse=True)
    ]

    hubs = sorted(nodes, key=lambda n: n["total_connections"], reverse=True)[:5]

    return {
        "ok": True,
        "total_notes": len(nodes),
        "total_edges": len(edges),
        "orphan_count": len(orphans),
        "orphans": [{"id": o["id"], "title": o["title"], "path": o["path"], "folder": o["folder"]} for o in orphans],
        "unresolved_count": len(unresolved_links),
        "unresolved_links": unresolved_links,
        "top_hubs": [{"id": h["id"], "title": h["title"], "total_connections": h["total_connections"]} for h in hubs],
        "timestamp": time.time()
    }


