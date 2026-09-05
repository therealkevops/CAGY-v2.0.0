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
  - `knowledge/spaces/<space_id>/`: Space containers partitioning project-specific ADRs (`decisions/adr_XXX_<name>.md`), architecture, and domain notes.
- **Interlinking & 2D Graph**: Uses standard bi-directional Obsidian wikilinks (`[[target]]` or `[[target|Alias]]`). Visualized as an interactive 2D physics force-directed graph on `/vault` with space filtering dropdown and visual space node clustering.
- **Pre-Turn Rule Compilation**: `run_agent.py` automatically synchronizes global user profile + active space notes into `.gemini/rules/knowledge_vault.md` before every conversation turn, providing permanent recall while preventing cross-space context contamination.
- **Active Learning & `/memorize`**:
  - When the user runs `/memorize [insight]` or clicks the `🧠` bookmark icon on a message, the engine (`webui/api/vault.py`) auto-classifies the note, routes decisions into the active space (`spaces/<space_id>/decisions/adr_XXX`), derives sequential ADR numbering per space, synthesizes wikilinks, saves the file, and re-compiles rules. User preferences (`category=user`) remain global.
  - When the user runs `/memorize` with no arguments, synthesize the key architectural takeaways, decisions, or conventions from recent conversation turns into vault notes.
