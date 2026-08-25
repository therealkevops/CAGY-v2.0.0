# Persistent Memory & Persona Context

This file stores persistent memory, project context, and user preferences across sessions.

## Environment & Architecture
- Single unified container (`agy-unified`) running Antigravity & Hermes WebUI.
- Zero container-to-container networking needed (safe from SentinelOne / Cisco filters).
- Gemini 3.7 Flash/Pro agent integration with tool execution (bash, files, grep).

## User Preferences
- Direct, concise responses with high technical signal.
- Surgical file edits and clean diffs.
- Automatic inspection and validation.
