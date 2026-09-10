"""
Model Context Protocol (MCP) Server Hub for Antigravity (AGY) & CAGY WebUI.
Manages mcp.json and runtime server/tool catalogs.
"""

import logging
import os
import json
import time
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

def _get_mcp_json_paths() -> List[Path]:
    """Return possible locations of Antigravity mcp.json files."""
    paths = [
        Path.home() / ".gemini" / "antigravity-cli" / "mcp.json",
        Path("/root/.gemini/antigravity-cli/mcp.json"),
        Path("/workspace/container_data/gemini/antigravity-cli/mcp.json"),
        Path(__file__).resolve().parent.parent.parent / "container_data" / "gemini" / "antigravity-cli" / "mcp.json",
        Path("/workspace/.gemini/mcp.json"),
        Path("/workspace/mcp.json"),
    ]
    return [p for p in paths if p.parent.exists()]

def _load_agy_mcp_json() -> Dict[str, Any]:
    """Load mcp.json from available paths."""
    for p in _get_mcp_json_paths():
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except Exception:
                logger.debug("Failed to parse mcp.json at %s", p, exc_info=True)
    return {"mcpServers": {}}

def _save_agy_mcp_json(data: Dict[str, Any]):
    """Persist mcp.json to all applicable paths."""
    for p in _get_mcp_json_paths():
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            logger.warning("Failed to save mcp.json at %s", p, exc_info=True)

BUILT_IN_AGY_TOOLS = [
    {
        "name": "run_command",
        "category": "Built-in Core",
        "description": "Execute shell commands, system binaries, and CLI utilities inside the execution environment.",
        "parameters": {
            "CommandLine": {"type": "string", "required": True, "description": "The exact shell command line string to execute."},
            "Cwd": {"type": "string", "required": True, "description": "Current working directory for the process execution."},
            "WaitMsBeforeAsync": {"type": "integer", "required": True, "description": "Milliseconds to wait synchronously before backgrounding."}
        }
    },
    {
        "name": "view_file",
        "category": "Built-in Filesystem",
        "description": "Read and inspect content of files on disk with slice notation and byte offsets.",
        "parameters": {
            "AbsolutePath": {"type": "string", "required": True, "description": "Absolute file path to read."},
            "StartLine": {"type": "integer", "required": False, "description": "1-indexed starting line number."},
            "EndLine": {"type": "integer", "required": False, "description": "1-indexed ending line number."}
        }
    },
    {
        "name": "replace_file_content",
        "category": "Built-in Filesystem",
        "description": "Perform surgical, deterministic search-and-replace edits on contiguous chunks of existing code files.",
        "parameters": {
            "TargetFile": {"type": "string", "required": True, "description": "Absolute target file path."},
            "TargetContent": {"type": "string", "required": True, "description": "Exact text substring to match and replace."},
            "ReplacementContent": {"type": "string", "required": True, "description": "New replacement code text."}
        }
    },
    {
        "name": "write_to_file",
        "category": "Built-in Filesystem",
        "description": "Create new files or overwrite existing files with verified code content.",
        "parameters": {
            "TargetFile": {"type": "string", "required": True, "description": "Absolute target file path to create."},
            "CodeContent": {"type": "string", "required": True, "description": "Full file content to write."},
            "Overwrite": {"type": "boolean", "required": True, "description": "Whether to overwrite if file exists."}
        }
    },
    {
        "name": "grep_search",
        "category": "Built-in Search",
        "description": "Fast regex and literal text searching across the workspace using ripgrep engine.",
        "parameters": {
            "SearchPath": {"type": "string", "required": True, "description": "Directory or file to search within."},
            "Query": {"type": "string", "required": True, "description": "Search pattern or query string."},
            "MatchPerLine": {"type": "boolean", "required": False, "description": "Whether to return snippet matches per line."}
        }
    },
    {
        "name": "find_by_name",
        "category": "Built-in Search",
        "description": "Locate files and directories matching glob patterns or extensions.",
        "parameters": {
            "SearchDirectory": {"type": "string", "required": True, "description": "Starting directory path."},
            "Pattern": {"type": "string", "required": True, "description": "Glob pattern to match."}
        }
    },
    {
        "name": "list_dir",
        "category": "Built-in Filesystem",
        "description": "List directory children with file size, metadata, and subfolder counts.",
        "parameters": {
            "DirectoryPath": {"type": "string", "required": True, "description": "Absolute directory path to inspect."}
        }
    },
    {
        "name": "invoke_subagent",
        "category": "Built-in Multi-Agent",
        "description": "Spawn concurrent child subagents to execute specialized research, coding, or verification tasks in parallel.",
        "parameters": {
            "Subagents": {
                "type": "array",
                "required": True,
                "description": "List of subagents with Role, Prompt, TypeName, Model, and Workspace mode."
            }
        }
    },
    {
        "name": "schedule",
        "category": "Built-in Automation",
        "description": "Schedule one-shot timers or recurring cron jobs that send reactive high-priority notifications in the background.",
        "parameters": {
            "Prompt": {"type": "string", "required": True, "description": "Notification prompt to execute."},
            "DurationSeconds": {"type": "integer", "required": False, "description": "Seconds to wait for one-shot timer."},
            "CronExpression": {"type": "string", "required": False, "description": "Standard 5-field cron expression."}
        }
    }
]

def list_mcp_hub_data() -> Dict[str, Any]:
    """Aggregate all MCP servers, tool schemas, and built-in AGY tools."""
    mcp_data = _load_agy_mcp_json()
    servers_dict = mcp_data.get("mcpServers", {})
    
    server_list = []
    active_count = 0

    for name, s_cfg in servers_dict.items():
        if not isinstance(s_cfg, dict):
            continue
        
        transport = "http" if ("url" in s_cfg) else "stdio"
        enabled = s_cfg.get("enabled", True)
        if enabled:
            active_count += 1
            
        command_str = s_cfg.get("command", "")
        if s_cfg.get("args") and isinstance(s_cfg["args"], list):
            command_str += " " + " ".join(str(a) for a in s_cfg["args"])
            
        server_list.append({
            "name": name,
            "transport": transport,
            "enabled": enabled,
            "command": command_str if transport == "stdio" else "",
            "url": s_cfg.get("url", "") if transport == "http" else "",
            "env": s_cfg.get("env", {}),
            "headers": s_cfg.get("headers", {}),
            "tool_count": len(s_cfg.get("tools", [])),
            "tools": s_cfg.get("tools", []),
            "status": "active" if enabled else "disabled"
        })

    return {
        "servers": server_list,
        "built_in_tools": BUILT_IN_AGY_TOOLS,
        "stats": {
            "total_servers": len(server_list),
            "active_servers": active_count,
            "total_builtin_tools": len(BUILT_IN_AGY_TOOLS),
            "total_mcp_tools": sum(s["tool_count"] for s in server_list)
        }
    }

def add_or_update_mcp_server(name: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """Add or modify an MCP server configuration in mcp.json."""
    mcp_data = _load_agy_mcp_json()
    if "mcpServers" not in mcp_data:
        mcp_data["mcpServers"] = {}

    transport = body.get("transport", "stdio").lower()
    entry = {"enabled": body.get("enabled", True)}

    if transport == "stdio":
        cmd = str(body.get("command", "")).strip()
        args = body.get("args", [])
        if isinstance(args, str):
            args = [a for a in args.split(" ") if a]
        entry["command"] = cmd
        entry["args"] = args
        if body.get("env") and isinstance(body["env"], dict):
            entry["env"] = body["env"]
    else:
        entry["url"] = str(body.get("url", "")).strip()
        if body.get("headers") and isinstance(body["headers"], dict):
            entry["headers"] = body["headers"]

    mcp_data["mcpServers"][name] = entry
    _save_agy_mcp_json(mcp_data)
    return {"ok": True, "name": name, "server": entry}

def toggle_mcp_server(name: str, enabled: bool) -> Dict[str, Any]:
    """Toggle enabled state of an MCP server."""
    mcp_data = _load_agy_mcp_json()
    servers = mcp_data.get("mcpServers", {})
    if name not in servers:
        return {"ok": False, "error": f"Server '{name}' not found"}

    servers[name]["enabled"] = bool(enabled)
    mcp_data["mcpServers"] = servers
    _save_agy_mcp_json(mcp_data)
    return {"ok": True, "name": name, "enabled": enabled}

def delete_mcp_server(name: str) -> Dict[str, Any]:
    """Delete an MCP server from configuration."""
    mcp_data = _load_agy_mcp_json()
    servers = mcp_data.get("mcpServers", {})
    if name in servers:
        del servers[name]
        mcp_data["mcpServers"] = servers
        _save_agy_mcp_json(mcp_data)
        return {"ok": True, "deleted": name}
    return {"ok": False, "error": f"Server '{name}' not found"}


def get_mcp_server(name: str) -> Optional[Dict[str, Any]]:
    """Retrieve configuration for a specific server."""
    mcp_data = _load_agy_mcp_json()
    servers = mcp_data.get("mcpServers", {})
    return servers.get(name)


def test_mcp_server(body: Dict[str, Any]) -> Dict[str, Any]:
    """Test connection / probe an MCP server configuration."""
    transport = str(body.get("transport", "stdio")).lower()
    t_start = time.time()

    if transport == "http":
        url = str(body.get("url", "")).strip()
        if not url:
            return {"ok": False, "error": "URL is required for HTTP/SSE transport"}
        try:
            import urllib.request
            headers = body.get("headers") or {}
            req = urllib.request.Request(url, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                elapsed_ms = round((time.time() - t_start) * 1000, 1)
                return {
                    "ok": True,
                    "status_code": resp.status,
                    "latency_ms": elapsed_ms,
                    "message": f"Successfully connected to endpoint ({elapsed_ms}ms)"
                }
        except Exception as e:
            elapsed_ms = round((time.time() - t_start) * 1000, 1)
            return {"ok": False, "error": str(e), "latency_ms": elapsed_ms}

    # stdio transport
    cmd = str(body.get("command", "")).strip()
    if not cmd:
        return {"ok": False, "error": "Command is required for stdio transport"}

    parts = cmd.split()
    bin_name = parts[0]
    resolved_bin = shutil.which(bin_name)
    if not resolved_bin:
        return {
            "ok": False,
            "error": f"Binary '{bin_name}' not found in PATH. Ensure Node/npx, uv/uvx, or Python is installed."
        }

    try:
        probe_cmd = [resolved_bin, "--version"] if len(parts) == 1 else parts[:2]
        proc = subprocess.run(
            probe_cmd,
            capture_output=True,
            text=True,
            timeout=5,
            env={**os.environ, **(body.get("env") or {})}
        )
        elapsed_ms = round((time.time() - t_start) * 1000, 1)
        return {
            "ok": True,
            "latency_ms": elapsed_ms,
            "binary": resolved_bin,
            "message": f"Binary '{bin_name}' verified executable ({elapsed_ms}ms)"
        }
    except Exception as e:
        elapsed_ms = round((time.time() - t_start) * 1000, 1)
        return {"ok": False, "error": f"Execution test failed: {e}", "latency_ms": elapsed_ms}
