import os
import subprocess
from pathlib import Path
from typing import Dict, Any, List

class ToolExecutor:
    """Executes workspace tools requested by the agent."""

    def __init__(self, workspace_root: str):
        self.workspace_root = Path(workspace_root).resolve()

    def _resolve_path(self, path_str: str) -> Path:
        p = Path(path_str)
        if p.is_absolute():
            return p
        return (self.workspace_root / p).resolve()

    def run_command(self, command: str, cwd: str = None) -> Dict[str, Any]:
        """Execute a shell command in the workspace."""
        working_dir = self._resolve_path(cwd) if cwd else self.workspace_root
        if not working_dir.exists():
            working_dir = self.workspace_root

        try:
            res = subprocess.run(
                command,
                shell=True,
                cwd=str(working_dir),
                capture_output=True,
                text=True,
                timeout=120
            )
            return {
                "exit_code": res.returncode,
                "stdout": res.stdout,
                "stderr": res.stderr,
                "success": res.returncode == 0
            }
        except subprocess.TimeoutExpired:
            return {"exit_code": -1, "stdout": "", "stderr": "Command timed out after 120s", "success": False}
        except Exception as e:
            return {"exit_code": -1, "stdout": "", "stderr": str(e), "success": False}

    def read_file(self, file_path: str, start_line: int = 1, end_line: int = None) -> Dict[str, Any]:
        """Read lines from a file."""
        target = self._resolve_path(file_path)
        if not target.exists() or not target.is_file():
            return {"success": False, "error": f"File not found: {file_path}"}

        try:
            lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
            total_lines = len(lines)
            s_idx = max(1, start_line) - 1
            e_idx = min(total_lines, end_line) if end_line else total_lines
            selected = lines[s_idx:e_idx]
            
            numbered_lines = [f"{i + s_idx + 1:4d} | {line}" for i, line in enumerate(selected)]
            return {
                "success": True,
                "file_path": str(target),
                "total_lines": total_lines,
                "start_line": s_idx + 1,
                "end_line": e_idx,
                "content": "\n".join(numbered_lines),
                "raw_content": "\n".join(selected)
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def write_file(self, file_path: str, content: str) -> Dict[str, Any]:
        """Create or overwrite a file."""
        target = self._resolve_path(file_path)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return {"success": True, "file_path": str(target), "bytes_written": len(content.encode("utf-8"))}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def list_dir(self, dir_path: str = ".") -> Dict[str, Any]:
        """List files and directories."""
        target = self._resolve_path(dir_path)
        if not target.exists() or not target.is_dir():
            return {"success": False, "error": f"Directory not found: {dir_path}"}

        try:
            items = []
            for item in sorted(target.iterdir()):
                items.append({
                    "name": item.name,
                    "is_dir": item.is_dir(),
                    "size": item.stat().st_size if item.is_file() else 0,
                    "path": str(item.relative_to(self.workspace_root)) if str(item).startswith(str(self.workspace_root)) else str(item)
                })
            return {"success": True, "directory": str(target), "items": items}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def grep_search(self, pattern: str, search_path: str = ".") -> Dict[str, Any]:
        """Search for pattern using ripgrep or python fallback."""
        target = self._resolve_path(search_path)
        try:
            res = subprocess.run(
                ["rg", "--line-number", "--max-count", "50", pattern, str(target)],
                capture_output=True,
                text=True,
                timeout=30
            )
            if res.returncode in (0, 1):
                matches = res.stdout.strip().splitlines()
                return {"success": True, "pattern": pattern, "matches": matches[:50]}
        except Exception:
            pass

        # Fallback to simple python search
        matches = []
        try:
            for root, _, files in os.walk(str(target)):
                for file in files:
                    if file.startswith(".git"):
                        continue
                    fpath = Path(root) / file
                    try:
                        for idx, line in enumerate(fpath.read_text(encoding="utf-8", errors="ignore").splitlines(), start=1):
                            if pattern.lower() in line.lower():
                                rel = str(fpath.relative_to(self.workspace_root)) if str(fpath).startswith(str(self.workspace_root)) else str(fpath)
                                matches.append(f"{rel}:{idx}: {line.strip()}")
                                if len(matches) >= 50:
                                    break
                    except Exception:
                        continue
                if len(matches) >= 50:
                    break
            return {"success": True, "pattern": pattern, "matches": matches}
        except Exception as e:
            return {"success": False, "error": str(e)}
