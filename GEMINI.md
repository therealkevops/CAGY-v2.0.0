# Antigravity Workspace Configuration

## Environment & Architecture
- **Environment**: Containerized Linux (Debian) execution namespace (`agy-unified`).
- **Workspace Root**: `/workspace` (mounted from host project root).
- **Interface**: Antigravity Web UI running on `http://localhost:8989` (and CLI bridge).
- **Tooling**: All shell commands, Python/Node.js executions, and file operations execute directly inside the Linux container.
- **Path Guidelines**: Always resolve paths within `/workspace` or relative to the current workspace root. Do not reference macOS host paths (`/Users/...`).
