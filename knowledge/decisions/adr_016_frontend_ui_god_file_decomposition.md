# ADR 016 — Frontend `ui.js` God-File Decomposition

**Date**: 2026-09-20 → 2026-09-22
**Status**: Accepted
**Author**: Antigravity / Kevin

## Context

`webui/static/ui.js` had grown to **21,454 lines** — a single god-file containing every frontend concern: media viewers, model pickers, scroll pinning, tool cards, worklog rendering, TTS, health monitors, update banners, code viewers, and more. It was untestable in isolation, had zero module boundaries, and made PRs very risky.

The constraint was hard: **no ES modules, no bundler**. All scripts are loaded as plain non-module `<script defer>` tags. Cross-module communication must use `window.*` globals.

## Decision

Extract `ui.js` into focused domain modules using a systematic sprint plan. Each sprint:
1. Identifies a clean boundary in `ui.js` (section header comments)
2. Extracts the block into `ui-<domain>.js` with a JSDoc header + `window.*` exports + `module.exports` for Node testing
3. Removes the block from `ui.js`, replacing it with a one-line comment stub
4. Registers the new script in `index.html` **before** `ui.js` (so dependents can call extracted globals)
5. Passes `npm run lint` + `./run-tests.sh` (229 tests) before committing

## Modules Extracted

| Sprint | Module | Lines | Exports | Domain |
|--------|--------|-------|---------|--------|
| F-M1 | `ui-media-viewer.js` | 992 | 25 | Image lightbox, Mermaid viewer, media players, inline renderers |
| F-M2 | `ui-model-picker.js` | 3,235 | 101 | Model picker, quota indicator, reasoning chip, toolsets |
| F-M3 | `ui-tool-renderer.js` | 1,194 | 67 | Tool call cards, live worklog helpers, action rendering |
| F-M4 | `ui-scroll.js` | 509 | 58 | Scroll pinning, anchor management, pull-to-refresh |
| F-M5 | `ui-system.js` | 3,237 | 174 | Inflight state, todo, health monitors, update banner |
| F-M6 | `ui-worklog.js` | 5,870 | 217 | Transparent turns, 3D progress, live footer, streaming rows |
| F-M7 | `ui-tts.js` | 448 | 18 | Web Speech API, ElevenLabs, OpenAI TTS |
| F-M8 | `ui-code-viewer.js` | 1,321 | 59 | JSON/YAML viewer, PDF preview, HTML sandbox iframe |

**Total extracted**: ~16,806 lines across 8 modules
**`ui.js` reduction**: 21,454 → 5,773 lines (**−73.1%**)

## Load Order (index.html)

```
events.js → api.js → ui-theme.js → i18n.js → icons.js → mermaid.min.js
→ assistant_turn_anchors.js → ui-notifications.js → ui-composer.js → ui-layout.js
→ ui-media-viewer.js → ui-model-picker.js → ui-tool-renderer.js
→ ui-scroll.js → ui-system.js → ui-worklog.js → ui-tts.js → ui-code-viewer.js
→ ui.js → workspace.js → terminal.js → sessions.js → commands.js
→ messages.js → subagents.js → mcp_hub.js → diff_viewer.js → skills_wizard.js
→ slash_palette.js → vault.js → analytics.js → panels.js → boot.js → outline.js
```

## Consequences

**Positive**:
- `ui.js` is now a manageable 5,773-line coordinator with only the remaining uncategorised state
- Each extracted module is independently testable via Node.js `require()` + `module.exports`
- `const`/`let` lexical collision test (`test_static_scripts_no_lexical_scope_collisions`) passes cleanly — no duplicate declarations
- 100% backward compatibility: every external caller (`sessions.js`, `messages.js`, `panels.js`, `boot.js`, etc.) works without changes via `window.*`
- New module test added: `test_ui_media_viewer_js_module` (13 assertions on media functions + constants)

**Negative / Trade-offs**:
- 8 more HTTP round-trips (mitigated by `defer` + browser caching; no bundler was a constraint)
- `window.*` global namespace is now the cross-module API — which is intentional given no-bundler constraint

## Related

- [[adr_008_frontend_ui_decomposition]] — earlier phase (api.js, ui-theme.js, ui-notifications.js, ui-composer.js, ui-layout.js)
- [[adr_015_webui_routes_deep_domain_decomposition]] — analogous backend decomposition (routes.py)
