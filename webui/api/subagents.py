"""
Subagent Swarm Management & Discovery for Antigravity (AGY) CLI.
Parses multi-agent swarms, lifecycle states, hierarchy trees, and transcripts.
"""

import logging
import os
import json
import re
import time
from pathlib import Path
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

def _get_brain_dirs() -> List[Path]:
    """Return list of possible brain directory locations across container and host."""
    candidates = [
        Path(os.environ.get("AGY_APP_DATA_DIR", "")) / "brain" if os.environ.get("AGY_APP_DATA_DIR") else None,
        Path("/root/.gemini/antigravity-cli/brain"),
        Path("/opt/data/gemini/antigravity-cli/brain"),
        Path.home() / ".gemini" / "antigravity-cli" / "brain",
        Path(__file__).resolve().parent.parent.parent / "container_data" / "gemini" / "antigravity-cli" / "brain",
        Path("/workspace/container_data/gemini/antigravity-cli/brain"),
    ]
    valid = []
    seen = set()
    for c in candidates:
        if c and c.exists() and c.is_dir():
            resolved = str(c.resolve())
            if resolved not in seen:
                seen.add(resolved)
                valid.append(c)
    return valid

def _get_sessions_dirs() -> List[Path]:
    """Return list of possible WebUI session directory locations."""
    candidates = [
        Path(os.environ.get("AGY_WEBUI_STATE_DIR", "")) / "sessions" if os.environ.get("AGY_WEBUI_STATE_DIR") else None,
        Path("/root/.agy/webui/sessions"),
        Path("/opt/data/webui/sessions"),
        Path.home() / ".agy" / "webui" / "sessions",
        Path("/workspace/container_data/webui/sessions"),
        Path(__file__).resolve().parent.parent.parent / "container_data" / "webui" / "sessions",
        Path.home() / ".hermes" / "webui" / "sessions",
        Path("/root/.hermes/webui/sessions"),
    ]
    valid = []
    seen = set()
    for c in candidates:
        if c and c.exists() and c.is_dir():
            resolved = str(c.resolve())
            if resolved not in seen:
                seen.add(resolved)
                valid.append(c)
    return valid

def _load_session_map() -> Dict[str, str]:
    """Load unified mapping between WebUI session_id and AGY conversation_id."""
    res = {}
    for sdir in _get_sessions_dirs():
        mf = sdir / "agy_session_map.json"
        if mf.exists():
            try:
                data = json.loads(mf.read_text(encoding="utf-8"))
                for k, v in data.items():
                    if isinstance(v, str) and not k.startswith("workspace") and k not in res:
                        res[k] = v
            except Exception:
                logger.debug("Failed to parse agy_session_map.json at %s", mf, exc_info=True)
    return res

def _resolve_conv_id_for_session(session_id: str) -> Optional[str]:
    """Map Hermes/WebUI session_id to AGY conversation_id."""
    if not session_id:
        return None
    session_map = _load_session_map()
    if session_id in session_map:
        return session_map[session_id]
    return session_id

def discover_all_swarm_sessions() -> List[Dict[str, Any]]:
    """Scan and return all sessions (WebUI + standalone CLI) with subagent counts and activity."""
    session_map = _load_session_map()
    conv_to_session = {v: k for k, v in session_map.items()}

    # Gather session metadata from .json session files
    session_meta = {}
    for sdir in _get_sessions_dirs():
        for sf in sdir.glob("*.json"):
            if sf.name.startswith("_") or sf.name == "agy_session_map.json":
                continue
            try:
                d = json.loads(sf.read_text(encoding="utf-8"))
                sid = d.get("session_id")
                if sid and sid not in session_meta:
                    session_meta[sid] = {
                        "title": d.get("title", "Untitled"),
                        "updated_at": d.get("updated_at", 0) or sf.stat().st_mtime,
                        "workspace": d.get("workspace", ""),
                        "model": d.get("model", "")
                    }
            except Exception:
                logger.debug("Failed to parse session file %s", sf, exc_info=True)

    # Discover conversations and parse subagents
    sessions_found: Dict[str, Dict[str, Any]] = {}
    seen_convs = set()

    for bdir in _get_brain_dirs():
        for cdir in bdir.iterdir():
            if not cdir.is_dir() or cdir.name in seen_convs:
                continue
            seen_convs.add(cdir.name)

            t_file = cdir / ".system_generated" / "logs" / "transcript.jsonl"
            if not t_file.exists():
                t_file = cdir / ".system_generated" / "logs" / "transcript_full.jsonl"
            if not t_file.exists():
                continue

            subs = _parse_transcript_for_subagents(cdir)
            cid = cdir.name
            t_mtime = t_file.stat().st_mtime

            if subs:
                sid = conv_to_session.get(cid)
                if sid and sid in session_meta:
                    title = session_meta[sid]["title"]
                    mtime = max(t_mtime, session_meta[sid].get("updated_at", 0))
                elif sid:
                    title = f"Session {sid[:8]}"
                    mtime = t_mtime
                else:
                    sid = cid
                    title = f"CLI / Standalone ({cid[:8]})"
                    mtime = t_mtime

                sessions_found[sid] = {
                    "session_id": sid,
                    "conversation_id": cid,
                    "title": title,
                    "subagent_count": len(subs),
                    "active_count": sum(1 for s in subs if s.get("status") == "running"),
                    "updated_at": mtime
                }

    # Include WebUI sessions that have 0 subagents so user can inspect or switch to them
    for sid, meta in session_meta.items():
        if sid not in sessions_found:
            cid = session_map.get(sid)
            sessions_found[sid] = {
                "session_id": sid,
                "conversation_id": cid,
                "title": meta.get("title", "Untitled"),
                "subagent_count": 0,
                "active_count": 0,
                "updated_at": meta.get("updated_at", 0)
            }

    # Sort sessions: sessions with subagents first, then by most recently updated
    return sorted(
        sessions_found.values(),
        key=lambda s: (s["subagent_count"] > 0, s["updated_at"]),
        reverse=True
    )

def _parse_transcript_for_subagents(conv_dir: Path) -> List[Dict[str, Any]]:
    """Parse transcript.jsonl in a conversation folder to discover spawned subagents."""
    transcript_file = conv_dir / ".system_generated" / "logs" / "transcript.jsonl"
    if not transcript_file.exists():
        transcript_file = conv_dir / ".system_generated" / "logs" / "transcript_full.jsonl"
    if not transcript_file.exists():
        return []

    subagents = []
    lines = []

    try:
        with open(transcript_file, "r", encoding="utf-8", errors="replace") as f:
            for l in f:
                l = l.strip()
                if l:
                    try:
                        lines.append(json.loads(l))
                    except Exception:
                        logger.debug("Skipping malformed transcript line in %s", transcript_file)
    except Exception:
        logger.warning("Failed to read transcript file %s", transcript_file, exc_info=True)
        return []

    for i, step in enumerate(lines):
        tool_calls = step.get("tool_calls", [])
        for tc in tool_calls:
            if tc.get("name") == "invoke_subagent":
                args = tc.get("args", {})
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except Exception:
                        logger.debug("Failed to parse invoke_subagent args JSON in %s", conv_dir.name)
                        args = {}
                sub_list = args.get("Subagents", [])
                if isinstance(sub_list, str):
                    try:
                        sub_list = json.loads(sub_list)
                    except Exception:
                        logger.debug("Failed to parse Subagents list JSON in %s", conv_dir.name)
                        sub_list = []

                # Look ahead for conversationIds in the next tool execution response
                found_cids = []
                for look_idx in range(i + 1, min(i + 4, len(lines))):
                    nxt = lines[look_idx]
                    content = str(nxt.get("content", ""))
                    cids = re.findall(r'["\']?conversationId["\']?\s*[:=]\s*["\']([a-zA-Z0-9\-_\.]+)["\']', content)
                    if cids:
                        found_cids.extend(cids)
                        break

                if isinstance(sub_list, list):
                    for idx, sub in enumerate(sub_list):
                        cid = found_cids[idx] if idx < len(found_cids) else None
                        subagents.append({
                            "parent_id": conv_dir.name,
                            "role": sub.get("Role", "Subagent"),
                            "type_name": sub.get("TypeName", "research"),
                            "model": sub.get("Model", "inherit"),
                            "workspace": sub.get("Workspace", "inherit"),
                            "prompt": sub.get("Prompt", ""),
                            "created_at": step.get("created_at", ""),
                            "status": "running",
                            "tool_count": 0,
                            "tools_used": [],
                            "last_activity": "Invoked by parent agent",
                            "conversation_id": cid
                        })

    # Check incoming messages across transcript steps for completion
    for step in lines:
        content = str(step.get("content", ""))
        for sub in subagents:
            if sub.get("conversation_id") and sub["conversation_id"] in content:
                sub["status"] = "done"
                sub["last_activity"] = "Completed task & reported to orchestrator"

    return subagents

def list_subagents(session_id: Optional[str] = None, conv_id: Optional[str] = None) -> Dict[str, Any]:
    """Retrieve full subagent swarm tree, metrics, and discovered session options."""
    available_sessions = discover_all_swarm_sessions()
    brain_dirs = _get_brain_dirs()

    # Determine requested session/conversation
    is_all = (session_id or "").strip().lower() == "all"
    target_conv_id = conv_id or (None if is_all else _resolve_conv_id_for_session(session_id or ""))
    selected_sid = session_id or ""

    all_subagents = []
    root_role = "Lead Architect (Parent)"
    root_model = "Gemini 3.7 Flash/Pro (AGY)"

    if is_all:
        # Aggregate subagents across all conversations that have them
        for sess in available_sessions:
            if sess["subagent_count"] > 0 and sess.get("conversation_id"):
                cid = sess["conversation_id"]
                for bdir in brain_dirs:
                    cdir = bdir / cid
                    if cdir.exists() and cdir.is_dir():
                        subs = _parse_transcript_for_subagents(cdir)
                        all_subagents.extend(subs)
        root_role = "Swarm Orchestrator (All Sessions)"
        root_model = "Multi-Session Swarm"
        selected_sid = "all"
    elif target_conv_id:
        # Scan target conversation
        for bdir in brain_dirs:
            cdir = bdir / target_conv_id
            if cdir.exists() and cdir.is_dir():
                subs = _parse_transcript_for_subagents(cdir)
                all_subagents.extend(subs)
        matched_sess = next((s for s in available_sessions if s.get("session_id") == session_id or s.get("conversation_id") == target_conv_id), None)
        if matched_sess:
            root_role = f"Parent ({matched_sess['title']})"
            selected_sid = matched_sess["session_id"]
    elif not session_id:
        # No session specified: auto-select most recent session that has subagents, or latest session
        top_sess = next((s for s in available_sessions if s["subagent_count"] > 0), None)
        if not top_sess and available_sessions:
            top_sess = available_sessions[0]
        if top_sess and top_sess.get("conversation_id"):
            target_conv_id = top_sess["conversation_id"]
            selected_sid = top_sess["session_id"]
            root_role = f"Parent ({top_sess['title']})"
            for bdir in brain_dirs:
                cdir = bdir / target_conv_id
                if cdir.exists() and cdir.is_dir():
                    subs = _parse_transcript_for_subagents(cdir)
                    all_subagents.extend(subs)

    # Deduplicate subagents
    seen_keys = set()
    deduped = []
    for s in all_subagents:
        key = s.get("conversation_id") or (s.get("parent_id"), s.get("role"), s.get("created_at"))
        if key not in seen_keys:
            seen_keys.add(key)
            deduped.append(s)
    all_subagents = deduped

    # Enrich subagents with data from their own conversation folders if they exist
    for sub in all_subagents:
        cid = sub.get("conversation_id")
        if cid:
            for bdir in brain_dirs:
                scdir = bdir / cid
                if scdir.exists():
                    st_file = scdir / ".system_generated" / "logs" / "transcript.jsonl"
                    if not st_file.exists():
                        st_file = scdir / ".system_generated" / "logs" / "transcript_full.jsonl"
                    if st_file.exists():
                        try:
                            tools = []
                            step_cnt = 0
                            last_act = ""
                            with open(st_file, "r", encoding="utf-8", errors="replace") as sf:
                                for sline in sf:
                                    sline = sline.strip()
                                    if not sline:
                                        continue
                                    step_cnt += 1
                                    try:
                                        sdata = json.loads(sline)
                                        for tc in sdata.get("tool_calls", []):
                                            tname = tc.get("name")
                                            if tname:
                                                tools.append(tname)
                                                last_act = f"Executed {tname}"
                                    except Exception:
                                        logger.debug("Skipping malformed line in subagent transcript %s", cid)
                            sub["tool_count"] = len(tools)
                            sub["tools_used"] = list(set(tools))
                            sub["step_count"] = step_cnt
                            if last_act:
                                sub["last_activity"] = last_act
                        except Exception:
                            logger.debug("Failed to enrich subagent %s from transcript", cid, exc_info=True)

    # Build tree hierarchy
    root_info = {
        "conversation_id": target_conv_id,
        "session_id": selected_sid,
        "role": root_role,
        "model": root_model,
        "status": "active"
    }

    sub_map = {}
    for s in all_subagents:
        s["children"] = []
        cid = s.get("conversation_id")
        if cid:
            sub_map[cid] = s

    root_children = []
    for s in all_subagents:
        pid = s.get("parent_id")
        if pid and pid in sub_map and pid != s.get("conversation_id"):
            sub_map[pid]["children"].append(s)
        else:
            root_children.append(s)

    root_info["children"] = root_children

    def _compute_subtree_stats(node):
        cnt = len(node.get("children", []))
        tools = node.get("tool_count", 0)
        for ch in node.get("children", []):
            c_cnt, c_tools = _compute_subtree_stats(ch)
            cnt += c_cnt
            tools += c_tools
        node["descendant_count"] = cnt
        node["cumulative_tool_count"] = tools
        return cnt, tools

    _compute_subtree_stats(root_info)
    active_count = sum(1 for s in all_subagents if s.get("status") == "running")

    return {
        "root": root_info,
        "subagents": all_subagents,
        "tree": root_info,
        "total": len(all_subagents),
        "active_count": active_count,
        "selected_session_id": selected_sid,
        "selected_conv_id": target_conv_id,
        "available_sessions": available_sessions,
        "timestamp": time.time()
    }

def get_subagent_transcript(subagent_id: str) -> Dict[str, Any]:
    """Load transcript steps for a specific subagent."""
    if not subagent_id:
        return {"error": "subagent_id required"}

    brain_dirs = _get_brain_dirs()
    target_dir = None

    for bdir in brain_dirs:
        cdir = bdir / subagent_id
        if cdir.exists() and cdir.is_dir():
            target_dir = cdir
            break

    if not target_dir:
        return {"steps": [], "total_steps": 0, "message": "Transcript not yet persisted"}

    transcript_file = target_dir / ".system_generated" / "logs" / "transcript.jsonl"
    if not transcript_file.exists():
        transcript_file = target_dir / ".system_generated" / "logs" / "transcript_full.jsonl"
    if not transcript_file.exists():
        return {"steps": [], "total_steps": 0, "message": "Log file not found"}

    steps = []
    try:
        with open(transcript_file, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    step = json.loads(line)
                    steps.append({
                        "step_index": step.get("step_index"),
                        "source": step.get("source"),
                        "type": step.get("type"),
                        "status": step.get("status"),
                        "created_at": step.get("created_at"),
                        "thinking": step.get("thinking"),
                        "content": step.get("content"),
                        "tool_calls": step.get("tool_calls", [])
                    })
                except Exception:
                    logger.debug("Skipping malformed step in transcript %s", subagent_id)
                    continue
    except Exception as e:
        return {"error": str(e)}

    return {
        "subagent_id": subagent_id,
        "steps": steps,
        "total_steps": len(steps)
    }


def export_subagent_transcript(subagent_id: str) -> str:
    """Return raw JSONL or JSON transcript content for export."""
    data = get_subagent_transcript(subagent_id)
    return json.dumps(data, indent=2)
