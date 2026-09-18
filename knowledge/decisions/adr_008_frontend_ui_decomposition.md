# ADR 008: Frontend Modernization & UI Domain Extraction

- **Date**: 2026-09-18
- **Status**: Accepted
- **Tags**: #decisions #adr #frontend #ui #webui #refactoring #architecture #testing

## Context & Problem Statement
The WebUI static architecture previously suffered from severe code concentration:
- `webui/static/ui.js`: 21,921 lines containing hundreds of functions intermixing message rendering, DOM queries, toast notifications, dialog flows, chat composer status, lock states, primary button state machines, and paste handling.
- `webui/static/boot.js`: Contained hundreds of lines of theme token parsers, skin validators, CSS injector styling, keydown listeners, and large-text paste file wrappers.
- Network Handling Fragmentation: Multiple ad-hoc HTTP approaches were split between a minimal `apiFetch` in `api.js`, a 124-line custom `api()` wrapper in `workspace.js`, and scattered raw `fetch()` calls with variable error and CSRF handling.

This created:
1. **High Maintenance Overhead**: Navigating, auditing, and maintaining monolithic client scripts was brittle and error-prone.
2. **Duplicated Logic**: Appearance pickers, paste handling, and auto-resize helpers were duplicated across `boot.js` and `ui.js`.
3. **Inconsistent Error Surfaces**: Different UI components handled HTTP failures differently (raw error strings vs JSON payloads vs missing toasts).

## Decision Drivers
- Extract discrete functional domains into specialized, modular JavaScript files with clear single responsibilities:
  - `webui/static/api.js`: Shared network transport, CSRF injection, timeout, retry, and error normalization.
  - `webui/static/ui-theme.js`: Dark/light/system theme toggles, design token skins, font sizes, and extension-registered skins.
  - `webui/static/ui-notifications.js`: Toast notifications, banner alerts, modal prompt/confirm dialogs, and audio chimes.
  - `webui/static/ui-composer.js`: Chat composer lifecycle, primary button actions, textarea auto-resizing, keybindings, and paste handling.
- Maintain 100% backward compatibility for all global functions and properties (`window.*`) so existing scripts (`messages.js`, `sessions.js`, `panels.js`, `commands.js`) function without any disruption.
- Rigorous automated verification at every sprint using `npm run lint` (HTMLHint, JS syntax check across all static files, Ruff) and `./run-tests.sh`.

## Considered Options
1. **Status Quo (Monolithic Files)**: Keep `ui.js` and `boot.js` monolithic. (Rejected: Accruing technical debt and code health liability).
2. **Complete Bundler Migration (Webpack / Vite / Rollup)**: Convert all frontend assets to ES modules and compile into a single bundle. (Rejected: Unacceptably high blast radius; breaks live hot-reload inside the containerized Debian execution environment, risks breaking non-bundled script evaluation order, and prevents runtime extension injection).
3. **Incremental Domain Extraction with Global Bridging**: Extract cohesive domains into standalone scripts loaded in deterministic sequence in `index.html` with explicit `window.*` API contracts.

## Decision Outcome
**Option 3 was chosen** and executed across 5 methodical sprints:

1. **Sprint F1 — Shared `apiFetch` Network Transport (`webui/static/api.js`)**:
   - Implemented `ApiError` class capturing HTTP status, status text, preview message, and parsed JSON payload.
   - Built robust `apiFetch(pathOrUrl, opts)` with automatic CSRF token injection (`X-Agy-CSRF-Token`, `X-Hermes-CSRF-Token`), `AbortController` timeouts, exponential backoff retries on transient errors, 401 redirect protection, and JSON body serialization.
   - Refactored `workspace.js` to delegate `api()` directly to `apiFetch`.
   - Added automated Node and static script integrity tests in `tests/test_syntax_and_imports.py`.

2. **Sprint F2 — Theme & Preference Manager (`webui/static/ui-theme.js`)**:
   - Extracted `_THEMES`, `_SKINS`, `_VALID_THEMES`, `_VALID_SKINS`, `_normalizeAppearance`, `_syncThemeColorMeta`, `_applyTheme`, `_applySkin`, `_pickTheme`, `_pickSkin`, `_applyFontSize`, `_buildSkinPicker`, and `registerAgySkin` from `boot.js` into `ui-theme.js`.
   - Added modern theme API convenience methods (`setTheme`, `getTheme`, `toggleTheme`).
   - Integrated `<script src="static/ui-theme.js">` into `index.html`.

3. **Sprint F3 — Notifications & Toast System (`webui/static/ui-notifications.js`)**:
   - Extracted `showToast`, `dismissToast`, `copyToastText`, `clearToastDismissTimer`, `setToastDismissTimer`, `showConfirmDialog`, `showPromptDialog`, and `showAlertDialog` from `ui.js`.
   - Extracted Web Audio API chimes (`playNotificationSound`, `playAttentionSound`).
   - Standardized banner alert utilities (`showBanner`, `dismissBanner`).
   - Integrated `<script src="static/ui-notifications.js">` into `index.html`.

4. **Sprint F4 — Chat Composer & Keybindings (`webui/static/ui-composer.js`)**:
   - Extracted composer state machines (`lockComposerForClarify`, `unlockComposerForClarify`, `getComposerPrimaryAction`, `updateSendBtn`, `handleComposerPrimaryAction`, `setBusy`, `setComposerStatus`).
   - Extracted textarea auto-resizing (`autoResizeTextarea`) and IME composition checks (`_isImeEnter`).
   - Extracted large-text and screenshot paste handlers (`LARGE_TEXT_PASTE_CHAR_THRESHOLD`, `_largeTextPasteLineCount`, `_shouldAttachLargePastedText`, `_largeTextPasteFile`, `_attachLargePastedText`).
   - Integrated `<script src="static/ui-composer.js">` into `index.html`.

5. **Sprint F5 — End-to-End Verification & Second Brain Sync**:
   - Expanded automated test suite in `tests/test_syntax_and_imports.py` to 7 dedicated tests verifying Node exports and integrity across all 4 new modules.
   - Full test suite execution: 177/177 tests passing in ~14.1s.
   - Synchronized Knowledge Vault and compiled ADR 008 into active rules.

## Consequences & Linked Systems
- **Modularity**: Over 1,000 lines extracted from monolithic files into 3 new clean domain modules (`ui-theme.js`, `ui-notifications.js`, `ui-composer.js`) plus a standardized `api.js`.
- **Zero Regressions**: All 177 unit and integration tests pass with 100% success rate.
- **Interlinks**: Complements [[decisions/adr_007_routes_decomposition]], [[decisions/adr_006_streaming_step_decomposition]], and [[architecture/cagy_unified]].
