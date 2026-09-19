# ADR 014: Composer State Hardening and Cross-Module S Object Exposure

## Status
Accepted

## Date
2026-09-19

## Context
Following the sidebar gesture isolation refactor in ADR 013, the WebUI chat interface became completely unresponsive in the browser, and clicking sidebar sessions failed to open conversations.

Through end-to-end headless Chrome inspection via Chrome DevTools Protocol (CDP), the browser console revealed recurring uncaught exceptions on startup and click:
```text
[CONSOLE error] [agy] boot failed TypeError: Cannot read properties of null (reading 'pendingFiles')
    at _composerHasContent (http://localhost:8989/static/ui-composer.js:106:21)
    at _applyBusyComposerPlaceholder (http://localhost:8989/static/ui-composer.js:163:7)
    at applyBotName (http://localhost:8989/static/boot.js:2177:57)
```
and on session selection:
```text
[CONSOLE error] [sessions] Failed to open session on click: TypeError: Cannot read properties of null (reading 'pendingFiles')
    at _composerHasContent (http://localhost:8989/static/ui-composer.js:106:21)
    at getComposerPrimaryAction (http://localhost:8989/static/ui-composer.js:132:22)
    at updateSendBtn (http://localhost:8989/static/ui-composer.js:204:18)
    at unlockComposerForClarify (http://localhost:8989/static/ui-composer.js:100:3)
    at hideClarifyCard (http://localhost:8989/static/messages.js:8715:55)
    at loadSession (http://localhost:8989/static/sessions.js:1638:43)
```

## Root Cause Analysis
1. **Unexposed Lexical State (`const S`)**: In `webui/static/ui.js`, the core state store was declared with `const S = { ... }`. In standard ECMAScript non-module scripts, `const` declarations live in the script lexical environment and are not automatically assigned as properties of `window`.
2. **Conflicting Shadowed Helper (`_getState`)**: In `webui/static/ui-composer.js`, `_getState()` was originally defined to return lexical `S` or a fallback `{ busy: false, pendingFiles: [] }`. However, `webui/static/ui-layout.js` (loaded immediately after `ui-composer.js` in `index.html`) declared a duplicate `function _getState()` that only checked `window.S || global.S || null`.
3. **Cascading Failure**: Because `window.S` was undefined, `ui-layout.js`'s `_getState()` shadowed `ui-composer.js`'s definition and returned `null`. Every subsequent invocation of `_composerHasContent()`, `getComposerPrimaryAction()`, and `updateSendBtn()` threw a `TypeError` when dereferencing `S.pendingFiles` or `S.busy`. This aborted `boot.js` halfway through initial page load (preventing initial session restore) and crashed `loadSession()` whenever any sidebar session was clicked.

## Decision & Implementation
1. **Expose `window.S` and `global.S`**:
   In `webui/static/ui.js`, explicitly attach `S` to global scope:
   ```javascript
   const S = { ... };
   if (typeof window !== 'undefined') window.S = S;
   if (typeof global !== 'undefined') global.S = S;
   ```
2. **Harmonize and Harden `_getState()`**:
   In both `webui/static/ui-composer.js` and `webui/static/ui-layout.js`, implement a unified `_getState()` function that checks lexical `S`, `window.S`, and `global.S`, and always falls back to an initialized singleton fallback object (`window._fallbackComposerState`), guaranteeing that `_getState()` never returns `null` or `undefined`.
3. **Defensive Guards in Composer Handlers**:
   In `webui/static/ui-composer.js`, all dereferences of state (`_composerHasContent`, `_getExplicitBusyCommandAction`, `getComposerPrimaryAction`, `_applyBusyComposerPlaceholder`, `setBusy`, and `_largeTextPasteFileName`) defensively handle `S` with safe accessors `(S && S.pendingFiles) || []`.
4. **Synchronize Panel State**:
   In `webui/static/panels.js`, mirror `_currentPanel` to `window._currentPanel` whenever the active panel changes. In `webui/static/sessions.js`, safely access `window._currentPanel` to avoid lexical temporal dead zone issues.
5. **Cache Busting**:
   Bumped `WEBUI_VERSION` to `2.0.3-agy` in `webui/api/updates.py` to ensure all clients receive the updated scripts immediately.

## Verification
- **CDP Headless Browser Diagnostics**: Verified clean startup with 0 console errors, successful session selection and message rendering, and active Send button responsiveness upon input dispatch.
- **Hermetic Test Suite**: Verified 226/226 tests passing via `./run-tests.sh` (`test_syntax_and_imports`, `test_run_agent`, `test_vault_and_graph`, `test_swarm_and_mcp`).
- **Linter**: Passed `npm run lint` across HTML, JS, and Python with 0 warnings/errors.
- **User Confirmation**: User confirmed chat window is fully operational and sessions are restored.
