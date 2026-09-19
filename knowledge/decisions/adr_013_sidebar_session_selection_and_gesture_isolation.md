# ADR 013: Sidebar Session Selection and Pointer Gesture Isolation

## Context & Problem Statement
Users experienced an intermittent failure where selecting a session from the sidebar in the WebUI (`http://localhost:8989`) completely failed to load the conversation into the chat interface. Server logs revealed zero incoming HTTP requests (`/api/session?session_id=...`) when clicking session items.

Investigation revealed two root causes across the frontend navigation flow:
1. **Pointer Event Trapping & Over-Aggressive Drag Thresholds**:
   - `_renderOneSession` bound `onpointerdown`, `onpointermove`, `onpointercancel`, `onpointerleave`, and `onpointerup` to all devices without providing an authoritative `el.onclick` listener.
   - Mouse clicks naturally drift slightly (6–12px on macOS trackpads). `_promoteSessionDrag(dx, dy)` had a 5px threshold (`dx <= 5 && dy <= 5`), classifying normal trackpad clicks as `_gestureState = 'dragging'`.
   - On mouse release, `_finishSessionGesture` checked `wasDragging` and aborted navigation (`return false`), dropping the click.
   - Similarly, slight pointer movement across element boundaries fired `onpointerleave`, clearing the gesture state to `'idle'` and aborting the subsequent `pointerup`.
   - Mouse pointer devices do not participate in swipe actions (`_isSessionSwipeTarget()` returns false for mouse), making mouse gesture tracking counter-productive.
2. **Same-Session Guard vs. Unrendered UI State**:
   - In `loadSession`, `if (currentSid === sid && !forceReload)` returned early. If the page was stuck on `emptyState` ("New Chat"), had 0 loaded messages, or was displaying an error/loading placeholder (e.g. after a transient network drop), clicking the session in the sidebar was treated as a no-op visit, leaving the user stranded on the empty state.
3. **Panel Navigation**:
   - `_openSidebarSession` did not ensure switching to the `'chat'` panel if another panel (such as `vault` or `settings`) was active.

## Decision
1. **Exempt Mouse Pointers from Swipe Gesture Interception**:
   - In `el.onpointerdown`, `el.onpointermove`, `el.onpointercancel`, and `el.onpointerup`, immediately return when `e.pointerType === 'mouse'`.
   - In `_promoteSessionDrag`, ignore mouse pointers and increase the touch drag slop threshold to 12px.
2. **Provide Authoritative `el.onclick` and Keyboard Activation**:
   - Bind an explicit, resilient `el.onclick` handler on `.session-item` that coordinates with touch gestures (`_lastTouchHandledTime`) and handles action menu click dismissals, multi-select toggles, and session loading.
   - Add `tabindex="0"` and keyboard `Enter` / `Space` activation on `.session-item`.
3. **Self-Healing Same-Session Reloads in `loadSession`**:
   - In `loadSession`, proceed with loading even if `currentSid === sid` when the UI is displaying `emptyState`, has 0 rendered messages, or shows recovery/loading placeholders.
   - In `_openSidebarSession`, pass `force: true` when recovery is needed and switch to `panel = 'chat'`.
4. **Cache Invalidation & Version Bump**:
   - Bump `WEBUI_VERSION = "2.0.2-agy"` in `webui/api/updates.py` to ensure browsers and the Service Worker (`sw.js`) fetch fresh assets.

## Consequences & Verification
- Desktop mouse and trackpad clicks open sidebar sessions with zero latency.
- Mobile and touch swipe-to-archive / swipe-to-delete gestures remain fully functional on touch devices without synthetic click duplication.
- Clicking an active session when the chat pane is empty restores the full message history.
- All 226 hermetic tests in `./run-tests.sh` pass.
- Clean `npm run lint` validation across HTML, JS, and Python.

## Related Documents
- [[adr_012_http_keepalive_dispatch_resilience]]
- [[adr_010_frontend_layout_modernization]]
- [[adr_008_frontend_ui_decomposition]]
