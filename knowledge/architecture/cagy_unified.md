# CAGY Architecture & Execution Model

The Containerized Antigravity (CAGY) platform bridges the official Google Antigravity Linux binary (`agy`) with an agentic WebUI running inside a unified Debian container.

- **Execution Namespace**: Docker container `agy-unified` running Debian Bookworm with mounted workspace at `/workspace`.
- **Process Supervisor**: `supervisord` manages both `agy-webui` (port 8989) and background CLI streams.
- **Model Context Protocol (MCP) Hub**: Pre-bundled with `uv`/`uvx` to execute Python and Node MCP servers on demand.
- **Related Notes**:
  - Engineered according to [[user/conventions]].
  - Initial foundation captured in [[decisions/adr_001_cagy_fork]].
  - Configured for the environment defined in [[user/profile]].
