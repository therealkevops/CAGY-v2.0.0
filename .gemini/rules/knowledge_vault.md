# Antigravity Knowledge Vault & Long-Term Memory

> [!IMPORTANT]
> This context is automatically compiled from the workspace Knowledge Vault (`/workspace/knowledge/`).
> Retain these principles, user preferences, and architectural decisions across all turns.

## User Preferences & Profile
### Engineering Conventions & Standards
# Engineering Conventions & Standards

Guidelines enforced across the CAGY project:

1. **Hermetic & Zero-Dependency Testing**:
   - Always write tests with standard library `unittest` (never external `pytest`) to guarantee zero-dependency execution across host and container environments.
   - See [[architecture/cagy_unified]] for test runner integration.
2. **Container Path Confinement**:
   - All tool executions, shell commands, and file operations resolve inside `/workspace` or relative paths. Do not reference host `/Users/...` paths.
   - Referenced in [[user/profile]].
3. **Multi-Architecture Integrity**:
   - Maintain full compatibility with `linux/amd64` and `linux/arm64` via Docker Buildx.
   - See [[decisions/adr_001_cagy_fork]] for context on decoupling legacy code.

### User Profile & Identity
# User Profile & Identity

- **Role**: Lead Cloud & Systems Architect (Senior Nutanix, AWS & Linux Infrastructure Engineer)
- **Communication Style**: Direct, highly analytical, concise. Prefer clean architecture, explicit rationale, and executable verification steps.
- **Operating Environment**: macOS host paired with Docker Debian container (`agy-unified`) mounted at `/workspace`.
- **Primary Tooling**: Google Antigravity CLI (`agy`), Python 3.11+, modern Node.js, `uv`/`uvx` MCP ecosystem, and Git.
- **Linked Context**:
  - See [[user/conventions]] for core codebase rules.
  - See [[architecture/cagy_unified]] for execution namespace details.

## Architectural Principles
### CAGY Architecture & Execution Model
# CAGY Architecture & Execution Model

The Containerized Antigravity (CAGY) platform bridges the official Google Antigravity Linux binary (`agy`) with an agentic WebUI running inside a unified Debian container.

- **Execution Namespace**: Docker container `agy-unified` running Debian Bookworm with mounted workspace at `/workspace`.
- **Process Supervisor**: `supervisord` manages both `agy-webui` (port 8989) and background CLI streams.
- **Model Context Protocol (MCP) Hub**: Pre-bundled with `uv`/`uvx` to execute Python and Node MCP servers on demand.
- **Related Notes**:
  - Engineered according to [[user/conventions]].
  - Initial foundation captured in [[decisions/adr_001_cagy_fork]].
  - Configured for the environment defined in [[user/profile]].

## Key Architectural Decisions
### ADR 001: Fork and Decouple Hermes into Clean Antigravity (CAGY)
# ADR 001: Fork and Decouple Hermes into Clean Antigravity (CAGY)

- **Date**: 2026-09-04
- **Status**: Accepted & Released (`v2.0-cagy`)

## Context & Problem Statement
The original codebase contained ~48,000 lines of legacy Hermes UI, multi-provider logic, dead chat commands, and unneeded assets, obscuring the primary mission: a high-performance, containerized environment for Google Antigravity.

## Decision Drivers
- Need for deterministic `stream-json` bridge to Google `agy` CLI.
- Footprint reduction and automated CI pipeline.
- Alignment with [[user/profile]] and [[user/conventions]].

## Considered Options
1. Retain legacy Hermes code alongside AGY bridge.
2. Complete purge of dead Hermes code and rebranding to CAGY.

## Decision Outcome
Option 2: Completely pruned 48,000+ lines of dead Hermes code, tagged official `v2.0-cagy` release, and architected the unified system detailed in [[architecture/cagy_unified]].
