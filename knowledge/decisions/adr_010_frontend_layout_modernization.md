# ADR 010: Frontend Layout & Panel State Modernization

- **Date**: 2026-09-18
- **Status**: Accepted
- **Tags**: #decisions #adr #frontend #ui #layout #responsive #pwa #refactoring #architecture #testing

## Context & Problem Statement
Following the modularization of theme management, notifications, and chat composer in [[decisions/adr_008_frontend_ui_decomposition]], `webui/static/boot.js` still contained substantial non-boot responsibilities:
1. **Viewport Detection & Virtual Inset Geometry**: Breakpoint matching (`_isCompactWorkspaceViewport`, `_isPhoneWidthViewport`, `_isDesktopWidth`, `_isTouchKeyboardViewport`), dynamic virtual viewport inset computation for mobile keyboards (`_syncKeyboardBottomInset`), and visual viewport resize debouncers (`_forceMobileViewportReflow`).
2. **Workspace Panel Lifecycle & Modes**: State machines for panel modes (`closed`, `browse`, `preview`), inline width persistence (`agy-panel-w`), panel UI synchronization, aria labeling, and toolbar toggle adapters (`openWorkspacePanel`, `closeWorkspacePanel`, `toggleWorkspacePanel`, `syncWorkspacePanelState`, `syncWorkspacePanelUI`, `handleWorkspaceClose`).
3. **Desktop Sidebar Collapse & Mobile Gestures**: Desktop sidebar collapse state (`_SIDEBAR_COLLAPSED_KEY`), ARIA announcements (`_syncSidebarAria`), keyboard shortcut integration, mobile drawer toggles, and PWA edge-swipe gestures (`_installPwaSidebarSwipeGesture`).
4. **Split-Pane Resizers**: Draggable splitter handle handlers (`initResize`, `_initResizePanels`) for sidebar and workspace panels.

This concentration hindered maintenance, made layout regressions harder to detect, and inflated `boot.js` beyond 3,500 lines.

## Decision Drivers
- Extract layout state, sidebar collapse/expand, workspace panel toggling, mobile gestures, and split-pane resizers into a dedicated, clean domain file: `webui/static/ui-layout.js`.
- Maintain 100% backward compatibility for all global functions and properties (`window.*`) so existing callers across `index.html`, `panels.js`, `vault.js`, `workspace.js`, `commands.js`, `slash_palette.js`, and `sessions.js` function with zero modification.
- Guarantee strict lexical isolation to prevent top-level scope collisions in the browser execution context and Node VM script analysis.
- Verify comprehensive automated testing (190 hermetic tests passing) and zero linter warnings (`npm run lint`).

## Considered Options
1. **Status Quo (Keep Layout in `boot.js`)**: Continue keeping layout, viewport geometry, mobile gestures, and resizers in `boot.js`. (Rejected: Technical debt, high cognitive overhead, difficult to unit test).
2. **Full Frontend Bundler (Vite / Rollup)**: Migrate frontend scripts into ES modules and bundled assets. (Rejected: High blast radius, breaks live container debugging and hot reload, breaks non-bundled execution assumptions).
3. **Incremental Domain Extraction into `ui-layout.js`**: Extract layout and panel state into a dedicated standalone script loaded early in `index.html` with explicit global bridging and defensive guards.

## Decision Outcome
**Option 3 was chosen** and implemented across 4 structured sprints:

1. **Sprint L1 — `webui/static/ui-layout.js` Implementation**:
   - Centralized layout constants (`_SIDEBAR_COLLAPSED_KEY`, `SIDEBAR_MIN`, `SIDEBAR_MAX`, `PANEL_MIN`, `PANEL_MAX`, `_PWA_SIDEBAR_SWIPE_*`).
   - Implemented viewport detection and virtual viewport keyboard inset syncing (`_syncKeyboardBottomInset`, `_forceMobileViewportReflow`, `_installViewportResizeListeners`).
   - Encapsulated workspace panel state with reactive getter/setter interoperability (`_workspacePanelMode`, `getWorkspacePanelMode`, `setWorkspacePanelMode`, `syncWorkspacePanelState`, `syncWorkspacePanelUI`).
   - Encapsulated desktop sidebar collapse, ARIA mirroring, and PWA edge gestures (`toggleSidebar`, `expandSidebar`, `toggleMobileSidebar`, `closeMobileSidebar`, `_installPwaSidebarSwipeGesture`).
   - Centralized resizable panel drag handles (`initResize`, `_initResizePanels`).
   - Added Node module exports and automated unit test `test_ui_layout_js_module` in `tests/test_syntax_and_imports.py`.

2. **Sprint L2 — Integration & `boot.js` Trimming**:
   - Added `<script src="static/ui-layout.js?v=__WEBUI_VERSION__" defer></script>` to `webui/static/index.html` immediately following `ui-composer.js`.
   - Removed 472 redundant lines from `webui/static/boot.js`.
   - Verified that `test_static_scripts_no_lexical_scope_collisions` and all 190 tests passed cleanly.

3. **Sprint L3 — Quality Gate & Second Brain Vault Sync**:
   - Executed full test suite (`./run-tests.sh`) with 190/190 passing tests.
   - Clean lint check (`npm run lint` — HTMLHint, JS syntax check, Ruff).
   - Authored ADR 010 and synchronized Knowledge Vault into active turn rules.

4. **Sprint L4 — Deployment & Verification**:
   - Merged feature branch into `main` and pushed upstream.
   - Hot-restarted `agy-webui` supervisor service in container.

## Consequences & Linked Systems
- **Clean Separation of Concerns**: `boot.js` is now strictly focused on app initialization, session loading, gateway SSE orchestration, and event listener bindings.
- **Improved Maintainability**: All responsive layout, drawer, and split-pane resizer logic is contained in a single 450-line modular file with zero external runtime dependencies.
- **Interlinks**: Complements [[decisions/adr_008_frontend_ui_decomposition]], [[decisions/adr_009_centralized_sqlite_architecture]], and [[decisions/adr_007_routes_decomposition]].
