# Antigravity Knowledge Vault & Long-Term Memory

> [!IMPORTANT]
> This context is automatically compiled from the workspace Knowledge Vault (`/workspace/knowledge/`).
> Active Scope: **Global**
> Retain these principles, user preferences, and architectural decisions across all turns.

## User Preferences & Profile
### User Profile & Identity
# User Profile & Identity

- **Role**: Lead Cloud & Systems Architect (Senior Nutanix, AWS & Linux Infrastructure Engineer)
- **Communication Style**: Direct, highly analytical, concise. Prefer clean architecture, explicit rationale, and executable verification steps.
- **Operating Environment**: macOS host paired with Docker Debian container (`agy-unified`) mounted at `/workspace`.
- **Primary Tooling**: Google Antigravity CLI (`agy`), Python 3.11+, modern Node.js, `uv`/`uvx` MCP ecosystem, and Git.
- **Linked Context**:
  - See [[user/conventions]] for core codebase rules.
  - See [[architecture/cagy_unified]] for execution namespace details.

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

> [!NOTE]
> **Tiered Context Diet Active**: Space knowledge (22.7 KB) exceeds the full-text limit (20.0 KB). Compiled into a high-density Decision Matrix and Executive Abstracts Map to optimize prompt efficiency. To retrieve complete notes on-demand, run `python3 skills/knowledge-vault/scripts/recall_vault.py "<query>"`.

## Architectural Decisions Matrix
| ADR | Title | Status | Summary & Key Decision |
|:---|:---|:---:|:---|
| [[decisions/adr_001_cagy_fork|ADR 001]] | ADR 001: Fork and Decouple Hermes into Clean Antigravity (CAGY) | `Accepted` | Option 2: Completely pruned 48,000+ lines of dead Hermes code, tagged official `v2.0-cagy` release, and architected the unified system detailed in [[architecture/cagy_unified]]. |
| [[decisions/adr_003_token_economics_and_context_diet|ADR 003]] | ADR 003: Implement Token Economics Engine & 25k Context Debt Threshold | `Accepted` | **Option 2 was chosen**: - Built the Token Economics and Memory ROI analytics engine (`/analytics`). - Established the **25,000-token Context Debt Threshold**. - Introduced the **Memory Leverage Ratio (MLR)** to... |
| [[decisions/adr_002_knowledge_vault_second_brain|ADR 002]] | ADR 002: Adopt Obsidian-Compatible Knowledge Vault & Active Learning Model | `Accepted` | **Option 3 was chosen**: - All knowledge is stored as atomic Markdown files in `/workspace/knowledge/`. - Pre-turn compilation automatically injects prioritized vault notes into `.gemini/rules/knowledge_vault.md`,... |
| [[decisions/adr_004_git_checkpoints_and_zero_risk_rollback|ADR 004]] | ADR 004: Native Git Checkpoints and Zero-Risk Auto-Stash Rollback | `Accepted` | **Option 3 was chosen**: - Built backend endpoints in `webui/api/spaces.py`: - `GET /api/spaces/checkpoints`: Returns recent Git commits, active branch, author, and dirty working tree status. - `GET... |
| [[decisions/adr_005_workspace_destination_export_and_sandboxing|ADR 005]] | ADR 005: Workspace Destination Export & Session Sandboxing | `Accepted` | **Option 3 was chosen**: - Built backend endpoints in `webui/api/session.py`: - `POST /api/session/export/workspace`: Resolves subfolder relative to target workspace, validates canonical root boundary, sanitizes... |

### Key Architectural Decision Abstracts
- **[[decisions/adr_001_cagy_fork|ADR 001: Fork and Decouple Hermes into Clean Antigravity (CAGY)]]** (`Accepted`): Option 2: Completely pruned 48,000+ lines of dead Hermes code, tagged official `v2.0-cagy` release, and architected the unified system detailed in [[architecture/cagy_unified]].
- **[[decisions/adr_003_token_economics_and_context_diet|ADR 003: Implement Token Economics Engine & 25k Context Debt Threshold]]** (`Accepted`): **Option 2 was chosen**: - Built the Token Economics and Memory ROI analytics engine (`/analytics`). - Established the **25,000-token Context Debt Threshold**. - Introduced the **Memory Leverage Ratio (MLR)** to...
- **[[decisions/adr_002_knowledge_vault_second_brain|ADR 002: Adopt Obsidian-Compatible Knowledge Vault & Active Learning Model]]** (`Accepted`): **Option 3 was chosen**: - All knowledge is stored as atomic Markdown files in `/workspace/knowledge/`. - Pre-turn compilation automatically injects prioritized vault notes into `.gemini/rules/knowledge_vault.md`,...
- **[[decisions/adr_004_git_checkpoints_and_zero_risk_rollback|ADR 004: Native Git Checkpoints and Zero-Risk Auto-Stash Rollback]]** (`Accepted`): **Option 3 was chosen**: - Built backend endpoints in `webui/api/spaces.py`: - `GET /api/spaces/checkpoints`: Returns recent Git commits, active branch, author, and dirty working tree status. - `GET...
- **[[decisions/adr_005_workspace_destination_export_and_sandboxing|ADR 005: Workspace Destination Export & Session Sandboxing]]** (`Accepted`): **Option 3 was chosen**: - Built backend endpoints in `webui/api/session.py`: - `POST /api/session/export/workspace`: Resolves subfolder relative to target workspace, validates canonical root boundary, sanitizes...

## Architectural Principles & System Design (Abstracts)
- **[[architecture/cagy_unified|CAGY Architecture & Execution Model]]**: The Containerized Antigravity (CAGY) platform bridges the official Google Antigravity Linux binary (`agy`) with an agentic WebUI running inside a unified Debian container. - **Execution Namespace**: Docker container `agy-unified`...
- **[[architecture/knowledge_vault_and_graph|Knowledge Vault & 2D Force-Directed Graph Architecture]]**: The Knowledge Vault serves as the persistent Second Brain for the CAGY agentic platform. Located at `/workspace/knowledge/` (mounted from host `./knowledge/`), it replaces monolithic memory files with a modular, atomic, and...
- **[[architecture/token_economics_and_analytics|Token Economics & Memory ROI Analytics Architecture]]**: The Token Economics and Memory ROI analytics engine (`webui/api/analytics.py` and `webui/static/analytics.js`) quantifies prompt token consumption, calculates real-time API expenditure, and measures the concrete efficiency dividends...
- **[[architecture/artifact_canvas_and_diff_viewer|Artifact Canvas & Visual Diff Viewer Architecture]]**: The Artifact Canvas and Visual Diff Viewer (`webui/api/diff_viewer.py` and `webui/static/diff_viewer.js`) provides a unified file inspector, structured diff viewer, and sandboxed live preview canvas inside the right-hand panel of the...
- **[[architecture/nutanix_nc2_advisory|Nutanix Cloud Clusters (NC2) Advisory Architecture]]**: The Nutanix Cloud Clusters (NC2) advisory ecosystem combines a 16-chapter comprehensive reference architecture in `/workspace/nc2-kb/` with the specialized **`nc2-advisor`** domain skill in `/workspace/skills/nc2-advisor/`. It enables...
