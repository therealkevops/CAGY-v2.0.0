# Antigravity Workspace Configuration

## Environment & Architecture
- **Environment**: Containerized Linux (Debian) execution namespace (`agy-unified`).
- **Workspace Root**: `/workspace` (mounted from host project root).
- **Interface**: Antigravity Web UI running on `http://localhost:8989` (and CLI bridge).
- **Tooling**: All shell commands, Python/Node.js executions, and file operations execute directly inside the Linux container.
- **Path Guidelines**: Always resolve paths within `/workspace` or relative to the current workspace root. Do not reference macOS host paths (`/Users/...`).

## Second Brain & Knowledge Vault Architecture
- **Knowledge Vault**: Stored at `/workspace/knowledge/` (mounted from host `./knowledge/`). Pure UTF-8 Markdown, 100% interoperable with the desktop Obsidian application.
- **Directory Taxonomy**:
  - `knowledge/user/`: Developer profile, tone preferences, coding standards, and workflow conventions.
  - `knowledge/architecture/`: System topology, container execution models, cloud infrastructure, and network design.
  - `knowledge/decisions/`: Architecture Decision Records (`adr_XXX_<name>.md`) tracking structural choices and trade-offs.
  - `knowledge/notes/`: Domain guides, research topics, and reference documentation.
- **Interlinking & 2D Graph**: Uses standard bi-directional Obsidian wikilinks (`[[target]]` or `[[target|Alias]]`). Visualized as an interactive 2D physics force-directed graph on `/vault`.
- **Pre-Turn Rule Compilation**: `run_agent.py` automatically synchronizes all vault notes into `.gemini/rules/knowledge_vault.md` before every conversation turn, providing permanent cross-session recall.
- **Active Learning & `/memorize`**:
  - When the user runs `/memorize [insight]` or clicks the `🧠` bookmark icon on a message, the engine (`webui/api/vault.py`) auto-classifies the note, derives sequential ADR numbering for decisions, synthesizes wikilinks to matching entities, saves the file, and re-compiles rules.
  - When the user runs `/memorize` with no arguments, synthesize the key architectural takeaways, decisions, or conventions from recent conversation turns into vault notes.
