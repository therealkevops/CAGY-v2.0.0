import os
import sys
import json
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Generator
from memory import MemoryManager
from skills import SkillManager
from tools import ToolExecutor

class AgyAgentBridge:
    """Bridges the WebUI directly to the Antigravity (agy) CLI engine without requiring API keys."""

    def __init__(self, workspace_root: str):
        self.workspace_root = Path(workspace_root).resolve()
        self.conversation_id = None
        self.memory = MemoryManager(str(self.workspace_root))
        self.skills = SkillManager(str(self.workspace_root))
        self.tools = ToolExecutor(str(self.workspace_root))
        self.model = "Antigravity CLI (agy)"
        self.api_key = "AGY Auth"

    def _find_agy_bin(self) -> str:
        """Find the agy binary on host or in PATH."""
        candidates = [
            "/Users/kev.gorman/.local/bin/agy",
            str(Path.home() / ".local" / "bin" / "agy"),
            shutil.which("agy"),
            "/usr/local/bin/agy",
            "/opt/homebrew/bin/agy"
        ]
        for cand in candidates:
            if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
                return cand
        return "agy"

    def _get_env(self) -> Dict[str, str]:
        """Prepare environment variables with SSL certs and PATH."""
        env = os.environ.copy()
        
        # Ensure cert bundle is set for proxy/SentinelOne
        cert_path = self.workspace_root / "container_data" / "system_certs.pem"
        if cert_path.exists():
            env["SSL_CERT_FILE"] = str(cert_path)
            env["REQUESTS_CA_BUNDLE"] = str(cert_path)
            env["CURL_CA_BUNDLE"] = str(cert_path)
            env["NODE_EXTRA_CA_CERTS"] = str(cert_path)

        local_bin = str(Path.home() / ".local" / "bin")
        if local_bin not in env.get("PATH", ""):
            env["PATH"] = f"{local_bin}:{env.get('PATH', '')}"

        return env

    def process_message_stream(self, user_message: str) -> Generator[Dict[str, Any], None, None]:
        """Stream conversational responses and tool executions from agy CLI stream-json."""
        agy_bin = self._find_agy_bin()

        cmd = [
            agy_bin,
            "--print", user_message,
            "--output-format", "stream-json",
            "--dangerously-skip-permissions",
            "--add-dir", str(self.workspace_root)
        ]

        if self.conversation_id:
            cmd.extend(["--conversation", self.conversation_id])

        yield {"type": "status", "content": "Antigravity thinking..."}

        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(self.workspace_root),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=self._get_env(),
                bufsize=1
            )

            has_streamed_deltas = False

            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue

                try:
                    event_data = json.loads(line)
                    event_type = event_data.get("event")

                    if event_type == "init":
                        self.conversation_id = event_data.get("conversation_id")
                        continue

                    elif event_type == "step_update":
                        update = event_data.get("step_update", {})
                        step_type = update.get("step_type")

                        if "text_delta" in update:
                            has_streamed_deltas = True
                            yield {"type": "text", "content": update["text_delta"]}

                        if step_type == "tool_call" or "tool_call" in update:
                            tc = update.get("tool_call", {})
                            yield {
                                "type": "tool_call",
                                "name": tc.get("name", update.get("name", "Tool")),
                                "args": tc.get("args", update.get("args", {}))
                            }

                        elif step_type == "tool_result" or "tool_result" in update:
                            res = update.get("tool_result", update.get("result", {}))
                            yield {
                                "type": "tool_result",
                                "name": update.get("name", "Tool"),
                                "result": res
                            }

                    elif event_type == "result":
                        res_obj = event_data.get("result", {})
                        if not has_streamed_deltas and "response" in res_obj:
                            yield {"type": "text", "content": res_obj["response"]}
                        yield {"type": "done"}

                except json.JSONDecodeError:
                    # Raw line output
                    yield {"type": "text", "content": line + "\n"}

            proc.wait()
            if proc.returncode != 0 and not self.conversation_id:
                err_text = proc.stderr.read()
                yield {"type": "error", "content": f"AGY CLI Error: {err_text or f'Exited with code {proc.returncode}'}"}

        except Exception as e:
            yield {"type": "error", "content": f"Failed to execute AGY CLI: {e}"}
