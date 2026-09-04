# Knowledge Vault Protocol & Active Learning Rule

## 1. Knowledge Vault Location & Interoperability
- The repository Knowledge Vault is located at `/workspace/knowledge/`.
- The vault uses pure standard Markdown files organized into structured subdirectories (`user/`, `architecture/`, `decisions/`, `notes/`).
- The vault is fully compatible with the desktop Obsidian application.

## 2. Active Memorization & The `/memorize` Command
- When the user types `/memorize [insight]` or requests to remember, record, or memorize a convention, design preference, or architectural decision:
  1. Determine the appropriate category:
     - `user/`: Developer profile, coding conventions, personal workflows.
     - `architecture/`: Infrastructure architecture, system components, execution environments.
     - `decisions/`: Architecture Decision Records (`adr_XXX_<name>.md`).
     - `notes/`: Domain guides and general reference notes.
  2. Maintain bi-directional `[[wikilinks]]` pointing to related notes (e.g., `[[user/profile]]`, `[[architecture/cagy_unified]]`) so the 2D Force-Directed Knowledge Graph connects all concepts.
  3. Ensure the note begins with a single `# Heading` and contains clean, high-signal Markdown.

## 3. Pre-Turn Rule Compilation
- The WebUI automatically compiles the Knowledge Vault into `.gemini/rules/knowledge_vault.md` before every turn.
- The agent has full access to the compiled vault memory across all sessions.
