# Changelog

All notable changes to the Containerized Antigravity (CAGY) platform will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.0.0] - 2026-09-05

### Added
- **Standalone Docker Execution & Hardened Packaging**:
  - Direct execution support for standalone containers (`docker run -d -p 8989:8989 cagy:latest`) without requiring host volume mounts.
  - Strict `.dockerignore` blocking credentials, temporary build artifacts, git internals, and cache files from leaking into container images.
- **Knowledge Vault & Second Brain (Obsidian Compatible)**:
  - 100% Obsidian-interoperable Markdown vault stored at `/workspace/knowledge/` (`user/`, `architecture/`, `decisions/`, `notes/`).
  - Interactive 2D Force-Directed Physics Graph (`/vault`) with node spacing controls, collision buffering, and category filters.
  - Full-text search engine with snippet matching and line numbers across vault markdown notes.
  - Interactive wikilinks (`[[target]]` or `[[target|Alias]]`) with bi-directional backlink indexing.
  - Vault Health Analytics reporting orphan notes, unresolved wikilinks, and connectivity density.
  - Note template modal with automated sequential ADR numbering (`adr_XXX_<name>.md`).
  - Active learning via `/memorize [insight]` slash command and chat message bookmark button (`🧠`) with entity wikilinking.
  - Pre-turn rule compilation automatically compiling vault notes into `.gemini/rules/knowledge_vault.md`.
- **Token Economics & Memory ROI Analytics**:
  - Real-time token consumption tracking (prompt tokens, response tokens, total cost estimation).
  - 25,000-token "Context Debt" threshold alert guiding operators when to snapshot decisions and refresh sessions.
  - Memory Leverage Ratio (MLR) and Memory Token Savings ROI tracking calculating prompt token reduction across turns.
  - Real-time generation throughput and agent status telemetry.
- **Workspace Destination Selector & Sandboxed Session Management**:
  - Configurable workspace destination subfolders for conversation session exports (`.md`, `.json`, `.html`).
  - Live destination path preview in Conversation Settings.
  - Workspace Session Browser with 1-click preview and session import.
  - Canonical path-traversal sandboxing restricting file writes strictly within the selected workspace boundary.
- **Native Git Checkpoints & Zero-Risk Rollback**:
  - Workspace commit timeline tracking commit hashes, commit messages, authors, dates, and dirty working tree status.
  - Interactive patch diff modal to preview file differences before reverting.
  - Zero-risk rollback engine executing automated untracked safety stashes (`pre-rollback-auto-stash-...`) prior to checkout.
- **Unified MCP Hub & Subagents Swarm DAG**:
  - Multi-transport MCP server configuration (stdio, sse, websocket) with JSON-RPC ping probe.
  - Live subagents swarm DAG visualization tracking hierarchical agent relationships, execution logs, and transcripts.
- **Architecture Decision Records**:
  - Added `adr_001_cagy_fork.md`: Decouple legacy codebase into clean CAGY platform.
  - Added `adr_002_knowledge_vault_second_brain.md`: Obsidian-compatible vault and active learning model.
  - Added `adr_003_token_economics_and_context_diet.md`: Token economics engine and 25k context debt threshold.
  - Added `adr_004_git_checkpoints_and_zero_risk_rollback.md`: Git checkpoints and zero-risk auto-stash rollback.
  - Added `adr_005_workspace_destination_export_and_sandboxing.md`: Sandboxed session export and workspace browser.

### Changed
- Refactored Web UI theme system with WCAG AA high-contrast variable (`--accent-contrast`) ensuring accessible text contrast on solid accent buttons across light and dark themes.
- Cleaned up settings panels, restored orphaned controls, and removed redundant legacy toggles.
- Expanded automated test suite to 63 hermetic unit and integration tests across 10 test modules.

### Security
- Added strict path validation and traversal guards preventing `../` escape during session exports and vault file manipulation.
- Sealed container build against credential exposure (`.env*`, `auth.json`, `google_token.json`, `*.pem`, `*.key`).
