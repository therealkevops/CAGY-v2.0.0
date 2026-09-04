# Artifact Canvas & Visual Diff Viewer Architecture

- **Date**: 2026-09-04
- **Category**: Architecture & UI Engine
- **Tags**: #canvas #diff-viewer #live-preview #artifacts #ui

## Overview
The Artifact Canvas and Visual Diff Viewer (`webui/api/diff_viewer.py` and `webui/static/diff_viewer.js`) provides a unified file inspector, structured diff viewer, and sandboxed live preview canvas inside the right-hand panel of the Web UI.

It eliminates the need to switch external tools or open terminal editors to inspect file changes or verify generated frontend artifacts.

---

## The Three Preview Modes

```
+-------------------------------------------------------------------------------+
|                       UNIFIED CANVAS & PREVIEW MODES                          |
+-------------------+-----------------------------------------------------------+
| Mode              | Architecture & Capability                                 |
+-------------------+-----------------------------------------------------------+
| 1. Code Editor    | Standalone, syntax-highlighted code viewer and editor.    |
|    (`source`)     | Allows direct file viewing and saving on disk without an  |
|                   | active agent session. Supports breadcrumbs & line numbers.|
+-------------------+-----------------------------------------------------------+
| 2. Visual Diff    | Computes line-by-line structured diffs against Git HEAD   |
|    (`diff`)       | via Python's `difflib.SequenceMatcher`. Displays added /  |
|                   | removed chunks, gutters, and side-by-side unified scroll. |
+-------------------+-----------------------------------------------------------+
| 3. Live Preview   | Sandboxed `<iframe>` environment rendering live HTML,     |
|    (`preview`)    | CSS, JavaScript, SVG graphics, and interactive canvases.  |
|                   | Includes an **Expand Canvas** fullscreen modal.           |
+-------------------+-----------------------------------------------------------+
```

---

## Backend API Contract (`webui/api/diff_viewer.py`)
- **GET `/api/diff/file?path=<rel_path>`**: Resolves the target file against the `/workspace` root and executes Git diff against HEAD to compute added, removed, and unmodified line tuples.
- **POST `/api/diff/compute`**: Accepts `{ original, modified, filename }` and computes an ad-hoc structured diff payload for virtual artifacts or unsaved memory buffers.

---

## Related Notes & References
- Integrates with the platform architecture in [[architecture/cagy_unified]].
- Adheres to testing conventions in [[user/conventions]].
- Supports artifact generation evaluated in [[architecture/token_economics_and_analytics]].
