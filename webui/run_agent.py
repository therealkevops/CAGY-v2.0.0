import os
import sys
import json
import time
import base64
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable, Union

def _get_map_file() -> Path:
    state_dir = Path(os.getenv("HERMES_WEBUI_STATE_DIR", str(Path.home() / ".hermes" / "webui"))).expanduser().resolve()
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir / "sessions" / "agy_session_map.json"

def _load_agy_conv_id(session_id: str) -> Optional[str]:
    if not session_id:
        return None
    map_file = _get_map_file()
    if map_file.exists():
        try:
            data = json.loads(map_file.read_text(encoding="utf-8"))
            return data.get(session_id)
        except Exception:
            pass
    return None

def _save_agy_conv_id(session_id: str, conv_id: str):
    if not session_id or not conv_id:
        return
    map_file = _get_map_file()
    map_file.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if map_file.exists():
        try:
            data = json.loads(map_file.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data[session_id] = conv_id
    try:
        map_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


class AIAgent:
    """Drop-in AIAgent implementation bridging the Hermes WebUI to the Antigravity (agy) CLI."""

    def __init__(
        self,
        model: Optional[str] = "Antigravity 2.0 (agy CLI)",
        workspace: Optional[str] = None,
        system_prompt: Optional[str] = None,
        session_db: Any = None,
        stream_delta_callback: Optional[Callable[[str], None]] = None,
        tool_start_callback: Optional[Callable[[str, Any], None]] = None,
        tool_complete_callback: Optional[Callable[[str, Any], None]] = None,
        status_callback: Optional[Callable[[str], None]] = None,
        reasoning_callback: Optional[Callable[[str], None]] = None,
        gateway_session_key: Optional[str] = None,
        **kwargs
    ):
        self.model = model or "Antigravity 2.0 (agy CLI)"
        
        # Resolve workspace path safely across Host and Container environments
        raw_ws = str(workspace or "").strip()
        is_container = os.environ.get("WORKSPACE_DIR") == "/workspace" or os.path.exists("/.dockerenv")
        if is_container:
            if not raw_ws or raw_ws.startswith("/Users/") or not os.path.exists(raw_ws):
                if "/workspace/" in raw_ws:
                    sub = raw_ws.split("/workspace/", 1)[1]
                    self.workspace = (Path("/workspace") / sub).resolve()
                else:
                    self.workspace = Path("/workspace")
            else:
                self.workspace = Path(raw_ws).resolve()
        else:
            self.workspace = Path(raw_ws or os.getenv("HERMES_WEBUI_DEFAULT_WORKSPACE", os.getcwd())).resolve()

        try:
            self.workspace.mkdir(parents=True, exist_ok=True)
        except Exception:
            self.workspace = Path("/workspace") if is_container else Path.cwd()

        self.system_prompt = system_prompt
        self._session_db = session_db
        
        self.stream_delta_callback = stream_delta_callback
        self.tool_start_callback = tool_start_callback
        self.tool_complete_callback = tool_complete_callback
        self.status_callback = status_callback
        self.reasoning_callback = reasoning_callback
        
        self.session_id = gateway_session_key or kwargs.get("session_id")
        self.conversation_id = _load_agy_conv_id(self.session_id) if self.session_id else None
        
        self.clarify_timeout = 3600
        self.image_input_mode = "text"
        self.reasoning_config = {"enabled": False}
        self.api_key = "AGY Auth"
        self.base_url = ""
        self._last_error = None

    def switch_model(self, model: str, **kwargs):
        self.model = model

    def handle_max_iterations(self, *args, **kwargs):
        return None

    def _find_agy_bin(self) -> str:
        candidates = [
            "/usr/local/bin/agy",
            "/usr/bin/agy",
            shutil.which("agy"),
            "/Users/kev.gorman/.local/bin/agy",
            str(Path.home() / ".local" / "bin" / "agy"),
        ]
        for cand in candidates:
            if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
                return cand
        return "agy"

    def _get_env(self) -> Dict[str, str]:
        env = os.environ.copy()
        cert_path = self.workspace / "container_data" / "system_certs.pem"
        if not cert_path.exists():
            cert_path = Path(__file__).resolve().parent.parent / "container_data" / "system_certs.pem"
        if cert_path.exists():
            env["SSL_CERT_FILE"] = str(cert_path)
            env["REQUESTS_CA_BUNDLE"] = str(cert_path)
            env["CURL_CA_BUNDLE"] = str(cert_path)
            env["NODE_EXTRA_CA_CERTS"] = str(cert_path)

        local_bin = str(Path.home() / ".local" / "bin")
        if local_bin not in env.get("PATH", ""):
            env["PATH"] = f"{local_bin}:{env.get('PATH', '')}"
        return env

    def _sync_hermes_memory(self):
        try:
            rules_dir = self.workspace / ".gemini" / "rules"
            rules_dir.mkdir(parents=True, exist_ok=True)
            
            # Sync Soul
            for sp in [Path.home() / ".hermes" / "SOUL.md", self.workspace / "SOUL.md"]:
                if sp.exists():
                    c = sp.read_text(encoding="utf-8").strip()
                    if c:
                        (rules_dir / "agent_soul.md").write_text(f"# Agent Persona & Identity (Soul)\n\n{c}\n", encoding="utf-8")
                        break

            # Sync User Profile
            for up in [Path.home() / ".hermes" / "memories" / "USER.md", self.workspace / "USER.md"]:
                if up.exists():
                    c = up.read_text(encoding="utf-8").strip()
                    if c:
                        (rules_dir / "user_profile.md").write_text(f"# User Profile & Preferences\n\n{c}\n", encoding="utf-8")
                        break

            # Sync Memory / Project Context
            for mp in [self.workspace / "MEMORY.md", Path.home() / ".hermes" / "memories" / "MEMORY.md"]:
                if mp.exists():
                    c = mp.read_text(encoding="utf-8").strip()
                    if c:
                        (rules_dir / "memory.md").write_text(f"# Persistent Memory & Project Context\n\n{c}\n", encoding="utf-8")
                        break
        except Exception:
            pass

    def run_conversation(
        self,
        user_message: Union[str, Dict[str, Any], List[Any], None] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """Execute a turn by invoking agy CLI with stream-json format."""
        self._sync_hermes_memory()
        if not self.session_id and kwargs.get("session_id"):
            self.session_id = kwargs.get("session_id")
            if not self.conversation_id:
                self.conversation_id = _load_agy_conv_id(self.session_id)

        user_prompt = ""
        attached_images = []

        def _process_part(p: Any):
            if isinstance(p, dict):
                ptype = p.get("type")
                if ptype == "text" and "text" in p:
                    return p.get("text", "")
                elif ptype == "image_url":
                    url = p.get("image_url", {}).get("url", "")
                    if url.startswith("data:image"):
                        try:
                            header, b64data = url.split(";base64,", 1)
                            ext = "png"
                            if "/" in header:
                                ext = header.split("/")[1].split(";")[0]
                            img_dir = self.workspace / ".hermes_uploads"
                            img_dir.mkdir(parents=True, exist_ok=True)
                            img_path = img_dir / f"screenshot_{int(time.time()*1000)}.{ext}"
                            img_path.write_bytes(base64.b64decode(b64data))
                            attached_images.append(str(img_path))
                        except Exception:
                            pass
                elif ptype in ("image", "file") and p.get("path"):
                    attached_images.append(str(p.get("path")))
            return ""

        if user_message is not None:
            if isinstance(user_message, str):
                user_prompt = user_message
            elif isinstance(user_message, dict):
                content = user_message.get("content", "")
                if isinstance(content, list):
                    texts = [_process_part(p) for p in content]
                    user_prompt = "\n".join([t for t in texts if t])
                else:
                    user_prompt = str(content)
            elif isinstance(user_message, list):
                texts = []
                for p in user_message:
                    t = _process_part(p)
                    if t:
                        texts.append(t)
                    elif not isinstance(p, dict):
                        texts.append(str(p))
                user_prompt = "\n".join(texts)

        if not user_prompt and messages:
            for m in reversed(messages):
                if isinstance(m, dict) and m.get("role") == "user":
                    content = m.get("content", "")
                    if isinstance(content, list):
                        texts = [_process_part(p) for p in content]
                        user_prompt = "\n".join([t for t in texts if t])
                    else:
                        user_prompt = str(content)
                    break

        if attached_images:
            img_refs = "\n".join([f"- [Attached Screenshot](file://{img_path})" for img_path in attached_images])
            if user_prompt:
                user_prompt = f"{user_prompt}\n\n[Attached Screenshot / Image Files]:\n{img_refs}\nPlease inspect the attached image file using `view_file` to review it."
            else:
                user_prompt = f"Please inspect the attached screenshot at file://{attached_images[0]} and explain what you see."

        if not user_prompt:
            user_prompt = "Hello"

        agy_bin = self._find_agy_bin()
        cmd = [
            agy_bin,
            "--print", user_prompt,
            "--output-format", "stream-json",
            "--dangerously-skip-permissions",
            "--add-dir", str(self.workspace)
        ]

        if self.conversation_id:
            cmd.extend(["--conversation", self.conversation_id])

        if self.model and self.model not in ("Antigravity 2.0 (agy CLI)", "default", "auto", ""):
            cmd.extend(["--model", self.model])

        effort = kwargs.get("effort") or os.environ.get("AGY_DEFAULT_EFFORT")
        if effort and effort in ("low", "medium", "high"):
            cmd.extend(["--effort", effort])

        mode = kwargs.get("mode") or os.environ.get("AGY_DEFAULT_MODE")
        if mode and mode in ("accept-edits", "plan"):
            cmd.extend(["--mode", mode])

        assistant_text = ""
        tool_calls = []
        input_tokens = 0
        output_tokens = 0
        t_start = time.time()
        t_first_token = None
        streamed_chars = 0

        try:
            if self.status_callback:
                try:
                    self.status_callback("Antigravity thinking...")
                except Exception:
                    pass

            proc = subprocess.Popen(
                cmd,
                cwd=str(self.workspace),
                stdin=subprocess.DEVNULL,
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
                        new_conv_id = event_data.get("conversation_id")
                        if new_conv_id:
                            self.conversation_id = new_conv_id
                            if self.session_id:
                                _save_agy_conv_id(self.session_id, new_conv_id)
                        continue

                    elif event_type == "step_update":
                        update = event_data.get("step_update", {})
                        step_type = update.get("step_type")
                        state = update.get("state")
                        tool_name = update.get("tool_name") or update.get("tool_info", {}).get("name", "tool")

                        if "text_delta" in update:
                            delta = update["text_delta"]
                            if t_first_token is None:
                                t_first_token = time.time()
                            streamed_chars += len(delta)
                            has_streamed_deltas = True
                            assistant_text += delta
                            if self.stream_delta_callback:
                                try:
                                    self.stream_delta_callback(delta)
                                except Exception:
                                    pass

                        if step_type == "tool":
                            tool_info = update.get("tool_info", {})
                            name = tool_info.get("name") or tool_name
                            params = tool_info.get("parameters", {})
                            output = tool_info.get("output", "")

                            if state == "ACTIVE":
                                tid = f"call_{len(tool_calls)}"
                                tool_calls.append({"id": tid, "name": name, "args": params})
                                if self.status_callback:
                                    try:
                                        self.status_callback(f"Executing {name}...")
                                    except Exception:
                                        pass
                                if self.tool_start_callback:
                                    try:
                                        self.tool_start_callback(tid, name, params)
                                    except TypeError:
                                        try:
                                            self.tool_start_callback(name, params)
                                        except Exception:
                                            pass
                                    except Exception:
                                        pass

                            elif state == "DONE":
                                tid = tool_calls[-1]["id"] if tool_calls else "call_0"
                                if tool_calls and tool_calls[-1]["name"] == name:
                                    tool_calls[-1]["output"] = output
                                if self.status_callback:
                                    try:
                                        self.status_callback("Analyzing results...")
                                    except Exception:
                                        pass
                                if self.tool_complete_callback:
                                    try:
                                        self.tool_complete_callback(tid, name, params, output)
                                    except TypeError:
                                        try:
                                            self.tool_complete_callback(name, output)
                                        except Exception:
                                            pass
                                    except Exception:
                                        pass

                        elif step_type == "checkpoint":
                            if self.status_callback:
                                try:
                                    self.status_callback("Thinking & planning...")
                                except Exception:
                                    pass

                        elif step_type == "agent_response" and state == "ACTIVE":
                            if self.status_callback:
                                try:
                                    self.status_callback("Responding...")
                                except Exception:
                                    pass

                    elif event_type == "result":
                        res_obj = event_data.get("result", {})
                        usage = res_obj.get("usage", {})
                        input_tokens = usage.get("input_tokens", 0)
                        output_tokens = usage.get("output_tokens", 0)

                        if not has_streamed_deltas and "response" in res_obj:
                            resp = res_obj["response"]
                            assistant_text = resp
                            if self.stream_delta_callback:
                                try:
                                    self.stream_delta_callback(resp)
                                except Exception:
                                    pass

                except json.JSONDecodeError:
                    assistant_text += line + "\n"
                    if self.stream_delta_callback:
                        try:
                            self.stream_delta_callback(line + "\n")
                        except Exception:
                            pass

            proc.wait()

            if not assistant_text and not tool_calls and proc.stderr:
                try:
                    err_output = proc.stderr.read().strip()
                    if err_output:
                        assistant_text = f"⚠️ Antigravity runtime error:\n```\n{err_output}\n```"
                        if self.stream_delta_callback:
                            self.stream_delta_callback(assistant_text)
                except Exception:
                    pass

        except Exception as e:
            assistant_text += f"\n[Error: {e}]"
            if self.stream_delta_callback:
                try:
                    self.stream_delta_callback(f"\n[Error: {e}]")
                except Exception:
                    pass

        history = list(messages) if messages else []
        if not history or history[-1].get("role") != "user":
            history.append({"role": "user", "content": user_prompt})

        if tool_calls:
            formatted_tc = []
            for i, tc in enumerate(tool_calls):
                call_id = f"call_{i}"
                formatted_tc.append({
                    "id": call_id,
                    "type": "function",
                    "function": {"name": tc["name"], "arguments": json.dumps(tc.get("args", {}))}
                })
            history.append({
                "role": "assistant",
                "content": "",
                "tool_calls": formatted_tc
            })
            for i, tc in enumerate(tool_calls):
                call_id = f"call_{i}"
                history.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": str(tc.get("output") or "")
                })

        history.append({
            "role": "assistant",
            "content": assistant_text,
            "tool_calls": []
        })

        total_duration = max(0.1, time.time() - t_start)
        thinking_sec = max(0.0, (t_first_token - t_start) if t_first_token else total_duration)
        stream_sec = max(0.1, total_duration - thinking_sec)
        tps = (output_tokens / stream_sec) if output_tokens > 0 else (streamed_chars / 4.0 / stream_sec)

        return {
            "messages": history,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "tps": round(tps, 1),
                "duration_sec": round(total_duration, 2),
                "thinking_sec": round(thinking_sec, 2)
            },
            "status": "completed"
        }
