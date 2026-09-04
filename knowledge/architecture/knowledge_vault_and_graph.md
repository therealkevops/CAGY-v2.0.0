# Knowledge Vault & 2D Force-Directed Graph Architecture

- **Date**: 2026-09-04
- **Category**: Architecture & Infrastructure
- **Tags**: #memory #knowledge-vault #obsidian #graph #architecture

## Overview & Second Brain Philosophy
The Knowledge Vault serves as the persistent Second Brain for the CAGY agentic platform. Located at `/workspace/knowledge/` (mounted from host `./knowledge/`), it replaces monolithic memory files with a modular, atomic, and interconnected web of standard UTF-8 Markdown documents.

The vault is engineered for 100% interoperability with the desktop **Obsidian** application, allowing human operators to open, browse, and edit the vault on macOS, Windows, or Linux with zero translation layers.

---

## Directory Taxonomy
```text
knowledge/
├── user/          # Developer profile, conventions, tone preferences, workflows
├── architecture/  # System design, container execution models, MCP topology
├── decisions/     # Architecture Decision Records (adr_XXX_<name>.md)
└── notes/         # Domain guides, research topics, and technical references
```

---

## Core Capabilities & Mechanisms

### 1. Bi-Directional Wikilinking & Graph Topology
- Connects notes using standard Obsidian syntax: `[[target]]`, `[[folder/note]]`, or `[[target|Alias]]`.
- The backend parser (`webui/api/vault.py`) resolves outgoing links, inverts them into backlinks, and extracts `#tags` while ignoring Markdown headers.
- Powers the interactive **2D Force-Directed Physics Graph** on `/vault`.
- Supports **Ego Network depth filtering** (`All`, `1-Hop`, `2-Hop`) to inspect local clusters around an active note without visual clutter.

### 2. Composer & Editor Wikilink Autocomplete
- Typing `[[` inside the chat composer or note editor triggers an inline fuzzy-search popover.
- Selecting a note auto-inserts the resolved wikilink, ensuring effortless interlinking during conversation and authoring.

### 3. Pre-Turn Rule Compilation (`_sync_agy_memory`)
- Prior to every conversation turn (`webui/run_agent.py`), the engine scans the vault and compiles prioritized notes into `.gemini/rules/knowledge_vault.md`.
- Because Antigravity automatically loads all `.gemini/rules/*.md` files into the agent's context, knowledge from the vault is permanently recalled at Turn 0 across all sessions.

### 4. Active Learning & `/memorize`
- When the user runs `/memorize [insight]` or clicks the `🧠` bookmark icon on a chat message:
  1. Auto-classifies category (`user`, `architecture`, `decisions`, or `notes`).
  2. Synthesizes sequential ADR numbers for architectural choices.
  3. Scans existing nodes to insert relevant `[[wikilinks]]`.
  4. Writes the note and immediately recompiles rules for zero-downtime recall.
- When `/memorize` is run without arguments, the agent reviews recent turns and synthesizes key takeaways into new vault notes.

---

## Related Notes & References
- Built on the core environment in [[architecture/cagy_unified]].
- Governed by standards in [[user/conventions]] and [[user/profile]].
- Contextualized in [[decisions/adr_002_knowledge_vault_second_brain]].
- Evaluated by the metrics in [[architecture/token_economics_and_analytics]].
