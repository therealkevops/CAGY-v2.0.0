# ADR 007: HTTP Route Dispatcher Modularization & Domain Decomposition

- **Date**: 2026-09-18
- **Status**: Accepted
- **Tags**: #decisions #adr #routes #webui #refactoring #architecture #testing

## Context & Problem Statement
In `webui/api/routes.py`, the core HTTP request dispatchers `handle_get` and `handle_post` grew into monolithic mega-dispatchers:
- `handle_get`: 2,136 lines containing raw string matching, deeply nested auth assertions, session querying, git operations, file system reads, MCP listings, and fallback error handling.
- `handle_post`: 2,901 lines mixing pre-CSRF handlers, raw streaming uploads, multipart decoders, session lifecycle, git operations, config saves, provider key handling, and background task signals.

This caused:
1. **Extreme Coupling**: Disparate architectural concerns (auth, system health, session recovery, git, file management, model configuration, vault knowledge, MCP tools) were intertwined in two monolithic functions.
2. **Regression Vulnerability**: Any single route modification risked inadvertently altering headers, query string parsing, or diagnostic tracing across other endpoints.
3. **Testing Gaps**: Routing decisions could not be verified in isolation without running broad multi-service tests.

## Decision Drivers
- Decompose both `handle_get` and `handle_post` into modular, domain-specific sub-dispatchers without altering any HTTP endpoint contract, status code, JSON response shape, or security invariant.
- Preserve 100% backward compatibility for all WebUI endpoints, query parameters, CORS headers, CSRF protections, and session visibility guards.
- Implement comprehensive automated test coverage for every domain before and during refactoring to guarantee zero regressions.

## Considered Options
1. **Status Quo (Monolithic Functions)**: Keep `handle_get` and `handle_post` in their original form. (Rejected: Created unacceptable code maintenance risk and violated codebase health standards).
2. **Framework Migration (FastAPI / Flask / Starlette)**: Rewrite the WebUI routing layer with a third-party framework. (Rejected: Massive blast radius, heavy new runtime dependencies, and high risk of breaking custom streaming SSE and threading architectures).
3. **Domain-Specific Sub-Dispatcher Modularization**: Group endpoints into 9 architectural domains and extract domain sub-dispatchers (`_handle_get_xxx` and `_handle_post_xxx`) that return `True`/response if handled or `None` if unhandled, condensing the top-level `handle_get` and `handle_post` to clean routing tables.

## Decision Outcome
**Option 3 was chosen** and implemented across 8 incremental sprints:
1. **Sprint R1 (`tests/test_routes_dispatch.py`)**: Built a comprehensive HTTP route dispatch test suite covering 9 architectural domains (Shell, Auth/CSRF, System Health, Models/Settings, Sessions, Workspaces/Git, Tools/MCP/Vault, Chat/Stream, Negative 404 & CORS preflights), expanding the automated suite from 150 to 169 tests.
2. **Sprint R2 (`_handle_get_static_and_shell`, `_handle_get_auth`, `_handle_get_system`)**: Extracted static HTML/manifest, auth status/sessions, and system telemetry/health endpoints.
3. **Sprint R3 (`_handle_get_config_and_models`)**: Extracted models catalog, providers, reasoning effort, plugins, and fixed pre-existing argument count bug in `channel_version_badge`.
4. **Sprint R4 (`_handle_get_session`)**: Extracted session retrieval, status, metadata, lineage, recovery audit, and session listing caches.
5. **Sprint R5 (`_handle_get_workspace_and_git`, `_handle_get_chat_and_stream`, `_handle_get_tools_and_mcp`)**: Extracted workspaces/git status, streaming status/clarify/approval, and MCP tools/skills/subagents/vault. `handle_get` was condensed from 2,136 lines down to **42 lines**!
6. **Sprint R6 (`_handle_post_auth`, `_handle_post_session`, `_handle_post_chat_and_stream`)**: Extracted POST auth handlers, session CRUD/pin/export/import, and chat streaming execution/terminal routes.
7. **Sprint R7 (`_handle_post_pre_body`, `_handle_post_workspace_and_git`, `_handle_post_config_and_settings`, `_handle_post_tools_and_mcp`)**: Extracted raw streaming pre-body routes (uploads/vault/mcp hub), git and filesystem operations, model configuration and onboarding, and tool/update operations. Expanded route dispatch tests to 22 tests (172 total suite).
8. **Sprint R8 (Orchestrator Condensation & Verification)**: Condensed `handle_post` from 2,901 lines down to **110 lines**, verified full test suite (172/172 tests passing in ~12.7s), and verified the container bridge compatibility probe.

## Consequences & Linked Systems
- Code reduction: Over 4,800 lines of spaghetti routing logic replaced by clean, modular domain controllers and two concise dispatch tables.
- Route dispatch test suite: Added 22 dedicated HTTP endpoint integration tests in `tests/test_routes_dispatch.py`.
- Automated test suite: Expanded to 172 tests with 100% pass rate.
- Interlinks: Complements [[decisions/adr_006_streaming_step_decomposition]] and [[architecture/cagy_unified]].
