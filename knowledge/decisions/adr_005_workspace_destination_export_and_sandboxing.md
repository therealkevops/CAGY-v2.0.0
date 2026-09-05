# ADR 005: Workspace Destination Export & Session Sandboxing

- **Date**: 2026-09-04
- **Status**: Accepted
- **Tags**: #decisions #adr #session-export #workspace #security #sandboxing

## Context & Problem Statement
In production workflows, users need to archive, export, and import conversation sessions directly into their project repositories (e.g. saving technical discussions as `docs/transcripts/session-2026-09-04.md` or `.json` logs). Previously:
1. Session exports were restricted to standard browser Blob downloads, requiring manual copying into repositories.
2. In-workspace filesystem writes pose severe security risks if directory traversal sequences (`../`) are unchecked.
3. Users had no convenient way to view existing workspace exports or import prior sessions directly back into the active chat.

## Decision Drivers
- Allow direct filesystem export of sessions as Markdown (`.md`), raw JSON (`.json`), or styled HTML (`.html`) into configurable subdirectories.
- Enforce strict security sandboxing to prevent directory traversal and accidental overwriting of sensitive files.
- Provide live destination path previews in Conversation Settings.
- Enable a Workspace Session Browser for listing, previewing, and 1-click importing existing session files.

## Considered Options
1. **Client-Side Downloads Only**: Restrict all exports to browser downloads. (Rejected: Tedious manual step for developers maintaining repository documentation).
2. **Unsanitized Direct File Writing**: Accept arbitrary destination paths from the UI. (Rejected: Critical security vulnerability enabling writes outside the workspace boundary).
3. **Sandboxed Workspace Destination Selector**: Canonical path resolution with strict workspace root anchoring, filename sanitization, traversal validation, and bi-directional import/export.

## Decision Outcome
**Option 3 was chosen**:
- Built backend endpoints in `webui/api/session.py`:
  - `POST /api/session/export/workspace`: Resolves subfolder relative to target workspace, validates canonical root boundary, sanitizes filenames, formats output (`md`, `json`, `html`), and writes the file.
  - `GET /api/session/workspace_exports`: Discovers valid session files in the configured workspace subfolder with metadata (title, size, timestamp, message count).
  - `POST /api/session/import/workspace`: Reads and parses a workspace session file, validating JSON schema or Markdown headers before injecting it into the chat session store.
- Added UI controls in Conversation Settings: Workspace Destination Directory, Subfolder input, Live Path Preview, and Workspace Session Browser with 1-click import.
- Tested extensively with path traversal edge cases in `tests/test_session_workspace_export.py`.

## Consequences & Linked Systems
- Provides an automated repository archiving channel supporting [[decisions/adr_003_token_economics_and_context_diet]].
- Fully respects the multi-workspace directory model in [[architecture/cagy_unified]].
- Strengthens overall filesystem security and sandboxing within the container.
