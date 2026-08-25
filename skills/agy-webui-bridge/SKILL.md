---
name: agy-webui-bridge
description: Maintains and verifies compatibility between the upstream Antigravity CLI (agy) and the local Hermes WebUI bridge. Activate this skill when the agy CLI is updated, when WebUI chat or tool execution encounters issues, or when verifying CLI flags, stream-json schemas, and model catalogs.
---

# Antigravity CLI <-> WebUI Bridge Guide & Maintenance

This skill maintains the integration contract between upstream Antigravity (`agy`) and the Hermes WebUI.

---

## 1. Upstream CLI Contract

The Hermes WebUI interacts with `agy` strictly as an external subprocess:

- **Command Line Execution** (`webui/run_agent.py`):
  ```bash
  agy --print "<prompt>" \
      --output-format stream-json \
      --dangerously-skip-permissions \
      --add-dir <workspace_path> \
      --model "<model_id>" \
      --conversation <conversation_id>
  ```

- **Standard Event Stream JSON** (`stdout`):
  - `{"event": "init", "conversation_id": "...", "init": {"model": "...", "tools": [...]}}`
  - `{"event": "step_update", "step_update": {"step_type": "user_input"|"agent_response"|"tool"|"thought", "text_delta": "...", ...}}`
  - `{"event": "result", "result": {"status": "SUCCESS"|"ERROR", "response": "...", "usage": {...}}}`

---

## 2. Key Bridge Components

File | Purpose | What to Check on `agy` Updates
:--- | :--- | :---
`webui/run_agent.py` | Subprocess launcher & stream parser | CLI flag names, stream-json event parsing, tool execution callback signatures.
`webui/api/routes.py` | WebUI REST / SSE endpoints (`/api/models`, `/api/skills`) | Supported model names, skills discovery directories.
`webui/static/ui.js` | Chat renderer & tool execution loop cards | Tool call cards (`on_tool_start`, `on_tool_complete`), model chip selector.

---

## 3. Automated Compatibility Verification

Run the built-in diagnostic script whenever `agy` is updated:

```bash
python3 skills/agy-webui-bridge/scripts/check_compatibility.py
```

### Diagnostic Checklist:
1. **Binary Location**: Verify `shutil.which("agy")` resolves to the updated binary.
2. **CLI Flags**: Confirm `--print`, `--output-format`, `--model`, `--conversation`, and `--dangerously-skip-permissions` remain supported.
3. **Stream-JSON Schema**: Confirm `init`, `step_update`, and `result` events are emitted on `stdout`.
4. **Tool Card Callbacks**:
   - `on_tool_start(tool_call_id, name, args)` (3 arguments)
   - `on_tool_complete(tool_call_id, name, args, output)` (4 arguments)
5. **Model Catalog**: Check if new models (e.g. Gemini, Claude, GPT) were added to `agy --model`.
