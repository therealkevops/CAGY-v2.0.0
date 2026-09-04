# ADR 002: Adopt Obsidian-Compatible Knowledge Vault & Active Learning Model

- **Date**: 2026-09-04
- **Status**: Accepted
- **Tags**: #decisions #adr #knowledge-vault #second-brain #active-learning

## Context & Problem Statement
Prior to this decision, agent memory relied on static flat files (`MEMORY.md`, `USER.md`, `SOUL.md`) or ad-hoc session journals. As projects grew in architectural complexity, this approach had several major drawbacks:
1. Flat memory files became bloated, unorganized, and difficult to maintain.
2. The agent had no automated way to interlink concepts, resulting in fragmented context.
3. Memory could not be edited or explored outside the Web UI without manual file hacking.
4. The agent could not autonomously synthesize new architectural decisions from conversation turns into structured memory.

## Decision Drivers
- Need for a clean, modular, and atomic second-brain architecture.
- 100% interoperability with the desktop Obsidian application via native Markdown and `[[wikilinks]]`.
- Turn-0 cross-session recall without blowing the context window.
- Visual discovery via an interactive 2D Force-Directed Knowledge Graph.

## Considered Options
1. **Vector Database / RAG Pipeline**: Store chunks in ChromaDB or SQLite-vec with embedding retrieval.
2. **Monolithic Extended MEMORY.md**: Continue appending summaries to a single text file.
3. **Obsidian-Compatible Knowledge Vault with Pre-Turn Rule Compilation**: Structured markdown folders (`user/`, `architecture/`, `decisions/`, `notes/`) with bi-directional wikilinks and auto-compiled `.gemini/rules/knowledge_vault.md`.

## Decision Outcome
**Option 3 was chosen**:
- All knowledge is stored as atomic Markdown files in `/workspace/knowledge/`.
- Pre-turn compilation automatically injects prioritized vault notes into `.gemini/rules/knowledge_vault.md`, granting zero-cost instant recall across turns.
- Active learning is enabled via `/memorize` and chat message bookmarks (`🧠`).
- Direct Obsidian compatibility preserves full human ownership and offline readability.

## Consequences & Linked Systems
- Detailed architecture codified in [[architecture/knowledge_vault_and_graph]].
- Evaluated and tracked by [[architecture/token_economics_and_analytics]].
- Supersedes flat-file patterns discussed in [[architecture/cagy_unified]].
