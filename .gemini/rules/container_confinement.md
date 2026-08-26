# Container Execution & WebUI Confinement Rule

## 1. Execution Environment
- **Runtime**: You are executing inside an isolated Linux (Debian) Docker container (`agy-unified`).
- **Primary Workspace Root**: `/workspace` (mounted from the host project root).
- **Tool Execution Context**: All bash commands, Python/Node.js scripts, ripgrep queries, and file edits execute strictly inside this Linux container.

## 2. Web UI Awareness
- **User Interface**: The user interacts with you through the integrated **Antigravity Web UI** running on `http://localhost:8989`.
- **Artifacts & Markdown**: Format code snippets and outputs clearly with GitHub-flavored markdown. File links and code symbols will be rendered interactively in the Web UI.

## 3. Host Isolation & Security
- **Path Resolution**: Never attempt to access or look for macOS host paths (e.g. `/Users/...` or `/Applications/...`). Always use relative paths or `/workspace/...`.
- **Package Management**: Use standard Linux tools (`apt-get`, `pip3`, `npm`) when installing runtime dependencies.
- **EDR Protection**: All sub-processes and terminal executions are safely contained within this container namespace.
