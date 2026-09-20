# ADR 015: Deep Domain Decomposition of WebUI Routes & Handlers

- **Date**: 2026-09-20
- **Status**: Accepted
- **Tags**: #decisions #adr #routes #webui #refactoring #architecture #modularity #session #cache

## Context & Problem Statement
In ADR 007, the top-level HTTP request dispatchers `handle_get` and `handle_post` were modularized into clean domain sub-dispatchers. However, `webui/api/routes.py` remained a monolithic module exceeding 16,200 lines. It contained hundreds of domain business logic functions, background threading helpers, state machine controllers, and cache implementations:
- Approvals & user clarifiers
- Chat turn execution, streaming lifecycle, and active run tracking
- Model resolution, provider discovery, and profile configuration
- MCP protocol management, custom skills, cron scheduling, and memory
- Workspace file operations and git version control handlers
- Session compression, handoff summaries, import/export, and full-text search
- Session list caching, LRU invalidation, and complex sidebar payload builders

This enormous file size impeded navigation, slowed IDE analysis, complicated code reviews, and created high merge-conflict risk for concurrent development.

## Decision Drivers
- Decompose `webui/api/routes.py` into dedicated, highly cohesive domain modules.
- Preserve 100% backward compatibility: every extracted function, constant, and helper must remain accessible via re-export on `api.routes` so existing tests, server bindings, and external integrations function without modification.
- Maintain a strict zero-regression quality gate: 100% test pass rate across the full 228-test suite (`./run-tests.sh`) and zero lint/type errors (`npm run lint`) prior to every sprint commit.
- Preserve all non-WebUI project assets (specifically DCAI certification materials in `projects/300-640_DCAI/`).

## Considered Options
1. **Status Quo (Monolithic Route Module)**: Keep all handler functions in `routes.py` while keeping dispatchers separate. (Rejected: 16k+ lines violated codebase health standards and increased bug risk).
2. **Breaking Architectural Rewrite**: Redesign handler signatures and move functions without re-exports. (Rejected: High blast radius, breaking dozens of test modules, third-party monkeypatches, and cross-module references).
3. **Deep Domain Module Migration with Re-Export Façade**: Systematically extract domain logic into dedicated modules (`route_approvals.py`, `route_chat.py`, `route_config_models.py`, `route_tools_mcp.py`, `route_workspace_git.py`, `route_session.py`, `route_session_list_cache.py`), while maintaining explicit re-exports on `api.routes` for complete backward compatibility.

## Decision Outcome
**Option 3 was chosen** and executed across Sprints M1 through M7:

1. **Sprint M1 (Approvals & Clarifiers — Commit `81aee10`)**:
   - Extracted 22 approval and clarifier handlers into `webui/api/route_approvals.py` (1,922 lines).
   - Covered `_handle_approve`, `_handle_clarify`, `_handle_clarification_response`, session YOLO gating, and tool approval lifecycle.
2. **Sprint M2 (Chat Execution & Streaming — Commits `2304111`, `a5877b8`)**:
   - Extracted chat turn runners, SSE streaming handlers, generation tracking, terminal execution, and run cancellation into `webui/api/route_chat.py` (3,746 lines).
3. **Sprint M3 (Config & Models — Commits `4b99b06`, `070b46b`)**:
   - Extracted model resolution, provider capabilities, live models caching, and prompt presets into `webui/api/route_config_models.py` (3,090 lines).
4. **Sprint M4 (Tools, MCP, Skills, Cron & Memory — Commits `f3344d5`, `39fdbb7`, `1982aaa`)**:
   - Extracted MCP server/tool registries, subagents, artifacts, skill management, cron runners, and memory handlers into `webui/api/route_tools_mcp.py` (3,354 lines).
5. **Sprint M5 (Workspace & Git Operations — Commits `e18e9ce`, `567c7b9`, `956d859`)**:
   - Extracted git status/branch/diff/commit/remote handlers, workspace folder management, file CRUD, directory listing, escape sandbox authorization, and zip downloads into `webui/api/route_workspace_git.py` (2,079 lines).
6. **Sprint M6 (Session Lifecycle, Import/Export & List Cache — Commits `31ee8f6`, `4c37738`, `969e335`)**:
   - **M6.1**: Extracted session compression and handoff summary generation into `webui/api/route_session.py`.
   - **M6.2**: Extracted session export (JSON/HTML), workspace export, full-text transcript search, and CLI session import/refresh into `webui/api/route_session.py` (4,118 lines total).
   - **M6.3**: Extracted orphan session pruner, sidebar payload builder, and cached list manager into `webui/api/route_session_list_cache.py` (1,597 lines).
7. **Sprint M7 (Final Consolidation & Vault Sync)**:
   - Verified 100% symbol re-export integrity on `api.routes`.
   - Updated migration plan and recorded ADR 015 in Knowledge Vault.

## Consequences & Linked Systems
- **Codebase Health**: `webui/api/routes.py` reduced from **16,215 lines** to **9,615 lines** (net reduction of 6,600 lines, -40.7%).
- **Domain Cohesion**: Core functionalities are now isolated in cleanly scoped modules with explicit dependencies and typed interfaces.
- **Backward Compatibility**: 100% backward compatibility maintained via `api.routes` re-export façade; zero test modifications required.
- **Test Integrity**: Full test suite passes 228/228 tests across all sprints.
- **Interlinks**: Complements [[decisions/adr_007_routes_decomposition]], [[decisions/adr_006_streaming_step_decomposition]], and [[decisions/adr_008_frontend_ui_decomposition]].
