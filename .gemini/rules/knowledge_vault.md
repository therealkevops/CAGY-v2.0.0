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
### Knowledge Vault & 2D Force-Directed Graph Architecture
# Knowledge Vault & 2D Force-Directed Graph Architecture

- **Date**: 2026-09-04
- **Category**: Architecture & Infrastructure
- **Tags**: #memory #knowledge-vault #obsidian #graph #architecture

## Overview & Second Brain Philosophy
The Knowledge Vault serves as the persistent Second Brain for the CAGY agentic platform. Located at `/workspace/knowledge/` (mounted from host `./knowledge/`), it replaces monolithic memory files with a modular, atomic, and interconnected web of standard UTF-8 Markdown documents.

The vault is engineered for 100% interoperability with the desktop **Obsidian** application, allowing human operators to open, browse, and edit the vault on macOS, Windows, or Linux with zero translation layers.

---

## Directory Taxonomy
```text
knowledge/
├── user/          # Developer profile, conventions, tone preferences, workflows
├── architecture/  # System design, container execution models, MCP topology
├── decisions/     # Architecture Decision Records (adr_XXX_<name>.md)
└── notes/         # Domain guides, research topics, and technical references
```

---

## Core Capabilities & Mechanisms

### 1. Bi-Directional Wikilinking & Graph Topology
- Connects notes using standard Obsidian syntax: `[[target]]`, `[[folder/note]]`, or `[[target|Alias]]`.
- The backend parser (`webui/api/vault.py`) resolves outgoing links, inverts them into backlinks, and extracts `#tags` while ignoring Markdown headers.
- Powers the interactive **2D Force-Directed Physics Graph** on `/vault`.
- Supports **Ego Network depth filtering** (`All`, `1-Hop`, `2-Hop`) to inspect local clusters around an active note without visual clutter.

### 2. Composer & Editor Wikilink Autocomplete
- Typing `[[` inside the chat composer or note editor triggers an inline fuzzy-search popover.
- Selecting a note auto-inserts the resolved wikilink, ensuring effortless interlinking during conversation and authoring.

### 3. Pre-Turn Rule Compilation (`_sync_agy_memory`)
- Prior to every conversation turn (`webui/run_agent.py`), the engine scans the vault and compiles prioritized notes into `.gemini/rules/knowledge_vault.md`.
- Because Antigravity automatically loads all `.gemini/rules/*.md` files into the agent's context, knowledge from the vault is permanently recalled at Turn 0 across all sessions.

### 4. Active Learning & `/memorize`
- When the user runs `/memorize [insight]` or clicks the `🧠` bookmark icon on a chat message:
  1. Auto-classifies category (`user`, `architecture`, `decisions`, or `notes`).
  2. Synthesizes sequential ADR numbers for architectural choices.
  3. Scans existing nodes to insert relevant `[[wikilinks]]`.
  4. Writes the note and immediately recompiles rules for zero-downtime recall.
- When `/memorize` is run without arguments, the agent reviews recent turns and synthesizes key takeaways into new vault notes.

---

## Related Notes & References
- Built on the core environment in [[architecture/cagy_unified]].
- Governed by standards in [[user/conventions]] and [[user/profile]].
- Contextualized in [[decisions/adr_002_knowledge_vault_second_brain]].
- Evaluated by the metrics in [[architecture/token_economics_and_analytics]].

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

### Token Economics & Memory ROI Analytics Architecture
# Token Economics & Memory ROI Analytics Architecture

- **Date**: 2026-09-04
- **Category**: Architecture & Analytics
- **Tags**: #analytics #token-economics #memory-roi #context-diet

## Overview
The Token Economics and Memory ROI analytics engine (`webui/api/analytics.py` and `webui/static/analytics.js`) quantifies prompt token consumption, calculates real-time API expenditure, and measures the concrete efficiency dividends delivered by the [[architecture/knowledge_vault_and_graph]].

It is accessible in the Web UI via `/analytics`, `/efficiency`, or the left navigation rail.

---

## Core Analytics Modules & Formulas

### 1. Memory Leverage Ratio (MLR)
Quantifies how effectively comprehensive architectural documentation is compressed into high-signal turn-0 rules:

$$\text{MLR} = \frac{\text{Total Words in Knowledge Vault}}{\text{Tokens in Compiled Knowledge Vault Rule}}$$

A high MLR indicates strong semantic density, ensuring the agent operates with vast domain awareness without consuming excessive prompt tokens.

### 2. Cumulative Avoided Context Debt
Measures the tokens and conversation turns saved across all sessions by having permanent turn-0 vault recall:
- Instead of repeatedly re-explaining conventions, system topologies, or ADRs in lengthy prompts, the agent loads compiled rules automatically.
- Tracks estimated cumulative prompt tokens avoided and dollars saved against commercial LLM pricing tiers.

### 3. Context Accumulation Curve & Debt Threshold
- An interactive SVG visualizer charting prompt token growth across conversation turns in the active session.
- Establishes the **Context Debt Threshold at 25,000 tokens**.
- When context accumulation crosses 25k tokens, the **Context Diet Advisor** triggers proactive guidance to:
  1. `/memorize` active takeaways into the [[architecture/knowledge_vault_and_graph]].
  2. Start a clean `/new` session with zero context baggage while retaining 100% memory continuity.

### 4. Real-Time Pricing & Multi-Model Comparative Matrix
Calculates session costs based on token telemetry ($0.075/1M input, $0.30/1M output on Gemini 3.8 Flash) and projects comparative costs on larger/frontier models:
- Gemini 1.5 Pro (~16.6x multiplier)
- Claude 3.5 Sonnet (~40x multiplier)

### 5. Inline Message Telemetry Chips
Displays per-message token counts, duration, and generation speed (tok/s) in assistant message footers, toggleable via `/usage`.

---

## Related Notes & References
- Quantifies memory efficiency for [[architecture/knowledge_vault_and_graph]].
- Decision rationale documented in [[decisions/adr_003_token_economics_and_context_diet]].
- Configured for the user profile in [[user/profile]].

### Artifact Canvas & Visual Diff Viewer Architecture
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

### Nutanix Cloud Clusters (NC2) Advisory Architecture
# Nutanix Cloud Clusters (NC2) Advisory Architecture

- **Date**: 2026-09-04
- **Category**: Architecture & Solutions
- **Tags**: #nc2 #nutanix #hybrid-cloud #aws #azure #gcp #gc2

## Overview
The Nutanix Cloud Clusters (NC2) advisory ecosystem combines a 16-chapter comprehensive reference architecture in `/workspace/nc2-kb/` with the specialized **`nc2-advisor`** domain skill in `/workspace/skills/nc2-advisor/`.

It enables the agent to function as a Principal Nutanix Solutions Architect, delivering evidence-based sizing, network validation, and disaster recovery planning across AWS, Microsoft Azure, Google Cloud Platform (GCP), and Sovereign Government Clouds (GC2).

---

## Core Advisory Subsystems

### 1. The 16-Chapter NC2 Knowledge Base (`/workspace/nc2-kb/`)
- Chapters `01` to `05`: Core architecture, AWS bare-metal, Azure dedicated nodes, GCP instances, and Government Cloud Clusters (GC2).
- Chapters `06` to `10`: Cloud-specific networking deep dives:
  - AWS Nitro ENA, Cloud Network Controller (CNC), Cloud Port Manager, and Transit VPCs.
  - Azure Delegated Subnets, Flow Gateways, and **Azure Virtual WAN (vWAN) vs Azure Route Server (ARS) vs Azure Internal Load Balancer (ILB)**.
  - GCP Alias IP ranges, gVNIC, and Google Cloud Router BGP.
  - GC2 air-gapped embedded orchestration in Prism Element.
  - Hybrid WAN interconnects (Direct Connect, ExpressRoute, Interconnect) and DR mapping.
- Chapters `11` to `16`: DSF storage pools, 1-Click LCM upgrades, **Cluster Hibernate & Resume** (up to 98% savings), NCP licensing, STIG security, diagnostics, and IaC/Terraform automation.

### 2. Built-in Advisor Tooling (`skills/nc2-advisor/`)
- **Automated Sizing Calculator** (`scripts/nc2_sizer.py`):
  - Calculates physical vCPU, RAM, raw NVMe capacity, CVM/AHV overhead, RF2/RF3 replication factors, data reduction (1.5x–2.5x), and N+1 rebuild headroom.
- **Network & MTU Budget Validator** (`scripts/nc2_network_validator.py`):
  - Validates Geneve encapsulation headroom (50 bytes), TCP MSS clamping rules (1410 on Azure/GCP, 8911 on AWS), and ENI secondary IP thresholds.
- **Decision Matrix** (`resources/decision_matrix.md`):
  - Fast-path decision trees for hyperscaler selection, Azure routing mechanisms, and DR RPO tiers.

---

## Related Notes & References
- Aligns with the Lead Architect profile in [[user/profile]].
- Memory managed within the second brain in [[architecture/knowledge_vault_and_graph]].
- Context monitored via [[architecture/token_economics_and_analytics]].

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

### ADR 003: Implement Token Economics Engine & 25k Context Debt Threshold
# ADR 003: Implement Token Economics Engine & 25k Context Debt Threshold

- **Date**: 2026-09-04
- **Status**: Accepted
- **Tags**: #decisions #adr #analytics #token-economics #context-diet

## Context & Problem Statement
Long-running conversational sessions inevitably accumulate massive prompt token contexts. In extended coding sessions, carrying 30k–80k tokens of conversational history introduces three critical liabilities:
1. **Context Bloat & Degradation**: LLM attention and instruction following degrade over long horizons ("needle in a haystack" problem).
2. **Exponential Token Inefficiency**: Re-sending 50k tokens on every turn wastes API quota and multiplies cost.
3. **Delayed Recovery**: When errors occur deep in a session, recovering is slower and more expensive than starting fresh.

## Decision Drivers
- Make prompt token consumption and generation throughput transparent in real time.
- Establish a clear threshold for when a session should be pruned or refreshed.
- Quantify the financial and token ROI delivered by the [[architecture/knowledge_vault_and_graph]].

## Considered Options
1. **Silent Sliding Window Truncation**: Silently drop older conversation messages when reaching a token limit. (Rejected: Causes abrupt context loss and hallucination).
2. **Context Diet Strategy with Explicit 25k Threshold & Vault Handoff**: Monitor prompt token accumulation, flag the 25k token boundary as "Context Debt", and provide 1-click tools to `/memorize` critical takeaways into the vault and start a clean `/new` session.

## Decision Outcome
**Option 2 was chosen**:
- Built the Token Economics and Memory ROI analytics engine (`/analytics`).
- Established the **25,000-token Context Debt Threshold**.
- Introduced the **Memory Leverage Ratio (MLR)** to track memory compression density.
- Integrated the **Context Diet Advisor** to guide human operators when to snapshot decisions and restart with zero debt.

## Consequences & Linked Systems
- Architecture detailed in [[architecture/token_economics_and_analytics]].
- Memory handoff backed by [[architecture/knowledge_vault_and_graph]].
- Aligned with developer workflow preferences in [[user/profile]].

### ADR 002: Adopt Obsidian-Compatible Knowledge Vault & Active Learning Model
# ADR 002: Adopt Obsidian-Compatible Knowledge Vault & Active Learning Model

- **Date**: 2026-09-04
- **Status**: Accepted
- **Tags**: #decisions #adr #knowledge-vault #second-brain #active-learning

## Context & Problem Statement
Prior to this decision, agent memory relied on static flat files (`MEMORY.md`, `USER.md`, `SOUL.md`) or ad-hoc session journals. As projects grew in architectural complexity, this approach had several major drawbacks:
1. Flat memory files became bloated, unorganized, and difficult to maintain.
2. The agent had no automated way to interlink concepts, resulting in fragmented context.
3. Memory could not be edited or explored outside the Web UI without manual file hacking.
4. The agent could not autonomously synthesize new architectural decisions from conversation turns into structured memory.

## Decision Drivers
- Need for a clean, modular, and atomic second-brain architecture.
- 100% interoperability with the desktop Obsidian application via native Markdown and `[[wikilinks]]`.
- Turn-0 cross-session recall without blowing the context window.
- Visual discovery via an interactive 2D Force-Directed Knowledge Graph.

## Considered Options
1. **Vector Database / RAG Pipeline**: Store chunks in ChromaDB or SQLite-vec with embedding retrieval.
2. **Monolithic Extended MEMORY.md**: Continue appending summaries to a single text file.
3. **Obsidian-Compatible Knowledge Vault with Pre-Turn Rule Compilation**: Structured markdown folders (`user/`, `architecture/`, `decisions/`, `notes/`) with bi-directional wikilinks and auto-compiled `.gemini/rules/knowledge_vault.md`.

## Decision Outcome
**Option 3 was chosen**:
- All knowledge is stored as atomic Markdown files in `/workspace/knowledge/`.
- Pre-turn compilation automatically injects prioritized vault notes into `.gemini/rules/knowledge_vault.md`, granting zero-cost instant recall across turns.
- Active learning is enabled via `/memorize` and chat message bookmarks (`🧠`).
- Direct Obsidian compatibility preserves full human ownership and offline readability.

## Consequences & Linked Systems
- Detailed architecture codified in [[architecture/knowledge_vault_and_graph]].
- Evaluated and tracked by [[architecture/token_economics_and_analytics]].
- Supersedes flat-file patterns discussed in [[architecture/cagy_unified]].

### ADR 004: Native Git Checkpoints and Zero-Risk Auto-Stash Rollback
# ADR 004: Native Git Checkpoints and Zero-Risk Auto-Stash Rollback

- **Date**: 2026-09-04
- **Status**: Accepted
- **Tags**: #decisions #adr #checkpoints #rollback #git #safety

## Context & Problem Statement
When autonomous agents execute complex file modifications, code refactoring, and multi-file code generation, human operators require continuous oversight and reliable rollback mechanisms. Previous workflows faced key challenges:
1. Reverting an undesirable turn required manual shell execution of Git commands.
2. Direct `git reset --hard` carried catastrophic data loss risks for human operators who had uncommitted modifications or untracked files in their workspace.
3. Operators could not inspect unified commit patch diffs directly in the Web UI before deciding whether to roll back.

## Decision Drivers
- Provide human operators with instant visual awareness of workspace commits and modifications.
- Ensure rollback operations are 100% zero-risk: no uncommitted work or untracked files may ever be destroyed.
- Enable interactive commit patch diff inspection in the Web UI prior to reverting.
- Gracefully handle edge cases such as non-Git directories, clean trees, and merge conflicts.

## Considered Options
1. **Custom File-Snapshotting Engine**: Recursively duplicate changed files into a hidden `.cagy_snapshots/` folder before every agent execution. (Rejected: Heavy disk I/O overhead, potential storage bloat, out-of-sync with operator Git history).
2. **Destructive Git Reset**: Issue raw `git reset --hard <commit>` directly. (Rejected: Destroys unstaged human edits and untracked files with no recovery mechanism).
3. **Native Git Checkpoint Engine with Zero-Risk Auto-Stash Safety Net**: Query Git log/status for commit checkpoints, provide a unified patch diff viewer, and execute rollbacks with an automatic safety stash (`git stash push -u -m "pre-rollback-auto-stash-..."`) before checking out target state.

## Decision Outcome
**Option 3 was chosen**:
- Built backend endpoints in `webui/api/spaces.py`:
  - `GET /api/spaces/checkpoints`: Returns recent Git commits, active branch, author, and dirty working tree status.
  - `GET /api/spaces/diff`: Generates unified diffs between commits or against the working tree.
  - `POST /api/spaces/rollback`: Creates a timestamped untracked safety stash before safely checking out the target commit or resetting modifications.
- Integrated an interactive Checkpoints list and Commit Diff modal into the Web UI Spaces tab.
- Added comprehensive hermetic test coverage in `tests/test_rollback.py`.

## Consequences & Linked Systems
- Works seamlessly with [[architecture/artifact_canvas_and_diff_viewer]] for visual code inspection.
- Protects developer working state aligned with [[user/conventions]].
- Integrated into the containerized execution model described in [[architecture/cagy_unified]].

### ADR 005: Workspace Destination Export & Session Sandboxing
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
