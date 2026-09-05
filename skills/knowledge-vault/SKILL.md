---
name: knowledge-vault
description: Interacts with, reads, updates, and creates modular Markdown notes inside the Obsidian-compatible Knowledge Vault at /workspace/knowledge/. Activate this skill when the user asks to memorize preferences, architectural decisions (ADRs), conventions, or when the user invokes /memorize.
---

# Knowledge Vault & Second Brain Management Guide

This skill governs how the agent interacts with the workspace Knowledge Vault located at `/workspace/knowledge/`.

---

## 1. Vault Directory Taxonomy

Directory | Purpose | Examples
:--- | :--- | :---
`/workspace/knowledge/user/` | User preferences, engineering conventions, developer background, workflows. | `profile.md`, `conventions.md`, `preferences.md`
`/workspace/knowledge/architecture/` | System architecture, infrastructure designs, container execution models, network topologies. | `cagy_unified.md`, `nutanix_nc2.md`
`/workspace/knowledge/decisions/` | Architecture Decision Records (ADRs) tracking structural choices, trade-offs, and deprecations. | `adr_001_cagy_fork.md`, `adr_002_dual_az.md`
`/workspace/knowledge/notes/` | Topic research, domain guides, reference documentation. | `mcp_patterns.md`, `ci_pipelines.md`

---

## 2. Note Authoring & Formatting Standards

1. **Title & Frontmatter**:
   - Begin with a single `# Heading` matching the note title.
   - For ADRs in `decisions/`, include `- **Date**: YYYY-MM-DD` and `- **Status**: Accepted | Superseded | Deprecated`.
   - For `user/` and `architecture/`, include brief bullet metadata at the top.

2. **Obsidian Wikilink Interlinking**:
   - Always connect notes using standard Obsidian bi-directional wikilinks: `[[target]]`, `[[folder/note]]`, or `[[target|Alias]]`.
   - When introducing a concept related to existing notes, reference them (e.g. `See [[user/conventions]]` or `Aligned with [[architecture/cagy_unified]]`).
   - This populates the 2D Force-Directed Graph and enables associative discovery.

3. **Atomic & Modular**:
   - Keep notes focused on single concepts or decisions rather than creating monolithic files.
   - Divide complex topics across linked notes.

---

## 3. Handling the `/memorize` Command

When the user issues `/memorize [content]` or asks you to remember or store knowledge:

1. **Classify the Target Category**:
   - Technical choices, trade-offs, migrations -> `decisions/` (as sequential `adr_XXX_<name>.md`).
   - Personal styles, preferred tools, workflows -> `user/`.
   - Component diagrams, server setups, deployment designs -> `architecture/`.
   - General knowledge summaries -> `notes/`.

2. **Cross-Reference Existing Notes**:
   - Check existing notes in `/workspace/knowledge/`.
   - Insert relevant `[[wikilinks]]` so the new note immediately hooks into the Knowledge Graph.

3. **Confirm & Report**:
   - Output a clean confirmation specifying the created/updated file path and the linked connections.

---

## 4. Tiered Context Diet & On-Demand Recall

To prevent token bloat during agent turns, the Knowledge Vault engine automatically applies a **Tiered Context Diet**:
- **Global Profile (`knowledge/user/`)**: Always compiled in 100% full text across all turns.
- **Space Notes (`knowledge/spaces/<space_id>/`)**: When candidate space documentation exceeds 20 KB, it compiles into a high-density **Architectural Decisions Matrix** and **Executive Abstracts Map** with bidirectional wikilinks.
- **On-Demand Recall**: Whenever deep technical specifics or full text are needed for a specific topic, execute:
  ```bash
  python3 skills/knowledge-vault/scripts/recall_vault.py "<query>" [--space <space>] [--full]
  ```

---

## 5. Link Refactoring, Linting & Self-Healing

The vault provides automated integrity auditing and self-healing:
1. **Audit Vault Links**:
   ```bash
   python3 skills/knowledge-vault/scripts/lint_vault.py [--space <space>]
   ```
2. **Auto-Heal Broken Links**:
   ```bash
   python3 skills/knowledge-vault/scripts/lint_vault.py --heal [--space <space>]
   ```
   Automatically corrects broken wikilinks and broken workspace file hyperlinks when an unambiguous candidate file exists.
3. **Refactor on Rename**:
   Renaming a note via `/api/vault/rename` or `rename_note()` scans all markdown files across the vault and updates all incoming `[[wikilinks]]` in place.
