"""
Subagent Swarm Management & Discovery for Antigravity (AGY) CLI.
Parses multi-agent swarms, lifecycle states, hierarchy trees, and transcripts.
"""

import os
import json
import re
import time
from pathlib import Path
from typing import Dict, Any, List, Optional

def _get_brain_dirs() -> List[Path]:
    """Return list of possible brain directory locations across container and host."""
    candidates = [
        Path(os.environ.get("AGY_APP_DATA_DIR", "")) / "brain" if os.environ.get("AGY_APP_DATA_DIR") else None,
        Path("/root/.gemini/antigravity-cli/brain"),
        Path("/opt/data/gemini/antigravity-cli/brain"),
        Path.home() / ".gemini" / "antigravity-cli" / "brain",
        Path(__file__).resolve().parent.parent.parent / "container_data" / "gemini" / "antigravity-cli" / "brain",
        Path("/workspace/container_data/gemini/antigravity-cli/brain"),
        Path("/Users/kev.gorman/.gemini/antigravity-cli/brain"),
    ]
    valid = []
    for c in candidates:
        if c and c.exists() and c.is_dir() and c not in valid:
            valid.append(c)
    return valid

def _resolve_conv_id_for_session(session_id: str) -> Optional[str]:
    """Map Hermes/WebUI session_id to AGY conversation_id."""
    if not session_id:
        return None
    
    # Check session map file
    map_candidates = [
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
                pass
    return session_id

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
                        pass
    except Exception:
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
                        args = {}
                sub_list = args.get("Subagents", [])
                if isinstance(sub_list, str):
                    try:
                        sub_list = json.loads(sub_list)
                    except Exception:
                        sub_list = []

                # Look ahead for conversationIds in the next tool execution response
                found_cids = []
                for look_idx in range(i + 1, min(i + 4, len(lines))):
                    nxt = lines[look_idx]
                    content = str(nxt.get("content", ""))
                    cids = re.findall(r'"conversationId":\s*"([a-f0-9\-]+)"', content)
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
    """Retrieve full subagent swarm tree and metrics."""
    target_conv_id = conv_id or _resolve_conv_id_for_session(session_id or "")
    brain_dirs = _get_brain_dirs()
    
    all_subagents = []
    root_info = {
        "conversation_id": target_conv_id,
        "session_id": session_id,
        "role": "Lead Architect (Parent)",
        "model": "Gemini 3.7 Flash/Pro (AGY)",
        "status": "active"
    }

    scanned_convs = set()
    
    # 1. Scan target conversation if known
    if target_conv_id:
        for bdir in brain_dirs:
            cdir = bdir / target_conv_id
            if cdir.exists() and cdir.is_dir():
                scanned_convs.add(target_conv_id)
                subs = _parse_transcript_for_subagents(cdir)
                all_subagents.extend(subs)

    # 2. If no subagents found for target, scan all recent brain directories
    if not all_subagents:
        for bdir in brain_dirs:
            try:
                for cdir in sorted(bdir.iterdir(), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)[:15]:
                    if cdir.is_dir() and cdir.name not in scanned_convs:
                        scanned_convs.add(cdir.name)
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
                                        pass
                            sub["tool_count"] = len(tools)
                            sub["tools_used"] = list(set(tools))
                            sub["step_count"] = step_cnt
                            if last_act:
                                sub["last_activity"] = last_act
                        except Exception:
                            pass

    active_count = sum(1 for s in all_subagents if s.get("status") == "running")
    
    return {
        "root": root_info,
        "subagents": all_subagents,
        "total": len(all_subagents),
        "active_count": active_count,
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
                    continue
    except Exception as e:
        return {"error": str(e)}

    return {
        "subagent_id": subagent_id,
        "steps": steps,
        "total_steps": len(steps)
    }
