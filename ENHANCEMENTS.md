# Architectural Enhancements to the Standard Google Antigravity (AGY) Harness

> **A Technical Deep-Dive into Containerization, Knowledge Graph Memory, and Token Economics for High-Performance Autonomous Coding**

---

## Executive Summary

The official **Google Antigravity (AGY)** engine provides a powerful foundation for agentic code generation, terminal tool use, and multi-agent delegation. However, the standard upstream AGY harness is designed primarily as a host-level command-line tool with transient, session-scoped memory and unmonitored context growth.

**Containerized Antigravity (CAGY)** transforms the raw `agy` CLI into an enterprise-grade, sandboxed developer platform. This document outlines every architectural enhancement introduced over the standard AGY harness, with particular emphasis on **Token Economics, Long-Horizon Context Efficiency, and Turn-0 Memory Leverage for Knowledge-Based Work**.

---

## 1. High-Level Comparison: Upstream AGY vs. CAGY Platform

| Architectural Dimension | Standard Upstream AGY Harness | CAGY Enhanced Platform |
| :--- | :--- | :--- |
| **Execution Environment** | Unconfined host process; shell tools run directly on macOS/Linux/Windows host OS. | **Hermetic Linux Container Namespace** (`agy-unified`) with isolated filesystem and safe blast radius. |
| **Toolchain & Runtime** | Relies on host-installed Python, Node, Git, Ripgrep; prone to host environment drift. | **Pre-bundled, turnkey toolchain**: Node 22, Python 3.11, Git, Ripgrep, `uv`/`uvx`, supervisor daemon. |
| **User Interface** | Terminal CLI stream or closed proprietary IDE window. | **Modern Browser WebUI** (`http://localhost:8989`) with pairing canvas, dual themes, and WCAG AA contrast. |
| **Long-Term Memory** | Static flat files (`MEMORY.md`, `SOUL.md`) or ad-hoc prompt re-explaining. | **Obsidian-Compatible Knowledge Vault** (`/vault`) with bi-directional `[[wikilinks]]` and 2D physics graph. |
| **Cross-Session Recall** | Manual re-prompting; agent forgets architectural decisions when session restarts. | **Turn-0 Automatic Rule Compilation**: Prioritized vault notes auto-compiled into `.gemini/rules/`. |
| **Token Efficiency** | Unmonitored prompt accumulation; unbounded context debt leading to attention degradation. | **Token Economics Engine** (`/analytics`) with 25k Context Debt Threshold and Context Diet Advisor. |
| **Active Learning** | No native mechanism to extract decisions from turns into persistent artifacts. | **`/memorize` & 1-Click `🧠` Action**: Auto-classifies insights into sequential ADRs with entity wikilinking. |
| **Safety & Rollback** | Destructive manual Git commands or terminal resets. | **Git Checkpoint Engine & Zero-Risk Rollback**: Commit patch diffs + automated pre-rollback safety stashing. |
| **Session Archiving** | Client-side browser download blob only. | **Sandboxed Workspace Destination Selector**: Direct `.md`/`.json`/`.html` export + 1-click Workspace Browser. |
| **Tool Extensibility** | Static `mcp.json` editing via text editor. | **Unified MCP Hub** (`/mcp`): Live JSON-RPC latency probes and quick-connect presets. |
| **Subagent Telemetry** | Raw JSONL file logs on disk. | **Subagent Swarms DAG** (`/swarm`): Interactive hierarchy tree, step-by-step logs, and transcript viewer. |

---

## 2. Deep-Dive: Token Efficiency & Context Economics for Knowledge Work

In high-complexity engineering workflows, LLMs do not fail because they lack raw intelligence—they fail because of **Context Bloat, Attention Degradation, and Repetitive Context Re-prompting**. 

The CAGY platform is engineered from the ground up to solve the fundamental friction between long-term knowledge retention and token consumption.

```mermaid
graph TD
    subgraph Traditional Harness (Unbounded Context Debt)
        T1["Turn 1 (2,000 tok)"] --> T5["Turn 5 (15,000 tok)"]
        T5 --> T15["Turn 15 (45,000 tok)"]
        T15 --> T25["Turn 25 (85,000 tok)<br>⚠️ High Cost, Attention Decay, Missed Instructions"]
    end

    subgraph CAGY Context Diet & Knowledge Vault
        C1["Turn 1: Turn-0 Rules Recall (600 tok)"] --> C5["Turns 2-10: Rapid Execution"]
        C5 --> C12["Turn 12: Context Debt Alert (25,000 tok)"]
        C12 --> M["1-Click /memorize Takeaways"]
        M --> V["Atomic Notes Saved to knowledge/decisions/"]
        V --> R["Auto-Compile .gemini/rules/knowledge_vault.md"]
        R --> NEW["/new Clean Session (Turn-0 Memory: 100% Wisdom, 0% Debt)"]
    end
```

### 2.1 The Problem: The Cost of Long-Horizon Context Debt

In standard conversational harnesses, prompt tokens accumulate linearly with every turn. In an extended coding session:

1. **Quadratic Financial Inefficiency**: If a conversation reaches 50,000 prompt tokens by Turn 15, the operator pays for all 50,000 tokens on Turn 16 even if the user message is a simple one-line prompt like *"run the unit tests"*. Over a 30-turn session, carrying unpruned history wastes millions of tokens on repetitive conversational boilerplate.
2. **Attention Degradation ("Lost in the Middle")**: Extensive research shows LLM attention and instruction-following fidelity degrade significantly when prompts exceed 30,000 tokens. Crucial architectural constraints stated in Turn 2 get ignored in Turn 22.
3. **The Re-Prompting Penalty**: When an operator finally abandons a bloated session and starts a new one, standard harnesses force them to manually re-explain the project architecture, directory structure, coding conventions, and prior decisions. This typically wastes **3,000 to 6,000 tokens per session start**.

---

### 2.2 The Solution: The 25,000-Token "Context Debt" Threshold

CAGY implements a formal **Context Diet Strategy** backed by real-time telemetry:

- **25,000 Token Boundary**: Research and empirical benchmarking identify 25k prompt tokens as the "sweet spot" where reasoning throughput, instruction adherence, and cost remain optimal.
- **Context Debt Alert**: When active session prompt tokens cross 25k, the WebUI displays visual status chips and the Context Diet Advisor warns the operator that continued turns carry diminishing returns.
- **The Handoff Workflow**:
  1. The operator clicks `🧠 Memorize` on critical architectural decisions or runs `/memorize`.
  2. CAGY distills the key learnings into atomic Markdown files in `/workspace/knowledge/`.
  3. The operator runs `/new`.
  4. The new session starts with **zero debt**, but retains **100% of the project knowledge** via pre-turn compiled rules.

---

### 2.3 Mathematical Model & Metrics

CAGY introduces formal formulas implemented in `webui/api/analytics.py` and visualized on `/analytics`:

#### 1. Memory Leverage Ratio (MLR)
Quantifies the compression density of the Knowledge Vault versus the tokens injected into the prompt:

$$\text{MLR} = \frac{\text{Total Words in Knowledge Vault}}{\text{Tokens in Compiled Rules}}$$

*Example*: A vault containing 12 comprehensive architectural notes, ADRs, and coding standards totaling **5,400 words** compiles into **650 rule tokens**:

$$\text{MLR} = \frac{5{,}400}{650} = \mathbf{8.3\times\text{ Leverage}}$$

Every token spent in the prompt delivers the context of over 8 words of human-curated engineering wisdom.

#### 2. Avoided Context Debt (Cumulative Token Savings)
Calculates prompt tokens saved across all sessions by having permanent turn-0 vault recall instead of repetitively re-prompting context:

$$\text{Tokens Saved} = \text{Notes Count} \times \min(\text{Sessions Count}, 10) \times 4{,}500\text{ tokens}$$

Over 20 sessions across a multi-component project, this avoids **90,000 to 180,000 redundant prompt tokens**.

#### 3. Real-Time Generation Throughput (TPS)
Tracks actual model inference speed in tokens per second timed directly via high-resolution wall-clock streams:

$$\text{TPS} = \frac{\text{Output Tokens}}{\text{Stream Duration (seconds)}}$$

#### 4. Multi-Model Economic Equivalency Matrix
Demonstrates real-time financial savings by computing exact cost on the active model (Gemini 3.8 Flash at \$0.075/1M input, \$0.30/1M output) versus legacy or heavyweight alternatives:
- **Gemini 1.5 Pro**: $16.6\times$ cost factor (\$1.25 in / \$5.00 out).
- **Claude 3.5 Sonnet**: $40.0\times$ cost factor (\$3.00 in / \$15.00 out).

---

## 3. Obsidian-Style Knowledge Vault & Second Brain (`/vault`)

Standard AGY relies on static prompt instruction files. CAGY integrates an **Obsidian-compatible Knowledge Vault** directly into the workspace at `/workspace/knowledge/` (`./knowledge/` on host).

### 3.1 Taxonomy & Structure
```text
knowledge/
├── user/          # Developer profile, tone preferences, coding conventions
├── architecture/  # System topology, container execution models, cloud design
├── decisions/     # Architecture Decision Records (adr_001_..., adr_002_...)
└── notes/         # Domain guides, research topics, reference documentation
```

### 3.2 Key Capabilities
- **Bi-Directional Wikilinks**: Connects notes using native Obsidian syntax: `[[target]]` or `[[target|Alias]]`.
- **2D Force-Directed Physics Graph**: Real-time simulation of spring-Coulomb layout with collision buffering, degree centrality scaling, and category color-coding.
- **Ego-Network Depth Filtering**: Inspect local clusters with 1-click `[ All | 1-Hop | 2-Hop ]` Breadth-First Search (BFS) neighborhood views, preserving node coordinates to prevent visual disorientation.
- **Code Span Filtering**: Backticked syntax examples (e.g. `` `[[example]]` ``) are intelligently excluded from link extraction, ensuring **0 false-positive unresolved links**.
- **Full-Text Body Search**: Toggle between title/tag search and full-text body search with line-numbered snippet previews (`L24: ...`) and match highlighting.
- **Vault Health Analytics**: Real-time monitoring of orphan nodes, unresolved links, and connectivity density.
- **100% Desktop Obsidian Interoperability**: Open `./knowledge/` directly in the official desktop Obsidian application.

---

## 4. Active Learning Engine & Turn-0 Compilation (`/memorize`)

Upstream AGY does not learn from its interactions. In CAGY, the agent actively learns and structures its own second brain:

```text
Human/Agent Interaction  ──►  1-Click [🧠 Memorize] or /memorize [insight]
                                        │
                                        ▼
                           Auto-Classify Category (ADR / Arch / User)
                                        │
                                        ▼
                           Derive Sequential ADR Number (adr_001 -> adr_005)
                                        │
                                        ▼
                           Synthesize Entity Wikilinks ([[target]])
                                        │
                                        ▼
                           Write Markdown to /workspace/knowledge/
                                        │
                                        ▼
                           Pre-Turn Compile .gemini/rules/knowledge_vault.md
                                        │
                                        ▼
                           Turn-0 Context Injected for Next Agent Turn
```

### 4.1 Automated Rule Compilation
Before every conversation turn, `webui/run_agent.py` invokes `sync_vault_to_rules()`. This scans all vault markdown files, extracts titles, tags, and content, and compiles them into `.gemini/rules/knowledge_vault.md`.

Because the Antigravity engine natively reads `.gemini/rules/*.md` into every request, **the agent has instantaneous Turn-0 recall of all architectural decisions across all sessions without human prompting**.

---

## 5. Sandboxing, Blast Radius & Container Architecture

Upstream AGY spawns shell tools directly on the host machine. If an agent executes `rm -rf`, alters system files, or installs global packages, the developer's workstation is at risk.

### 5.1 Hermetic Linux Container Namespace
- **Process Isolation**: All shell tools, python scripts, file operations, and package installations run inside the `agy-unified` container (Debian Bookworm).
- **Turnkey Toolchain**: Pre-bundles Node.js 22, Python 3.11, Git, Ripgrep, `uv`/`uvx`, and the Google `agy` CLI.
- **Host Protection (Safe Blast Radius)**: The host OS (macOS, Windows, or Linux) remains completely insulated from unintended agent side-effects.
- **Cross-Host Portability**: The same Docker image runs identically on developer laptops, bare VPS instances, or CI/CD runners.
- **Standalone Execution**: Fully packaged image supports zero-mount execution (`docker run -d -p 8989:8989 ghcr.io/therealkevops/cagy:latest`) as well as local development hot-reloading via Docker Compose.

---

## 6. Workspace Safety: Git Checkpoints & Zero-Risk Rollback

Autonomous agents refactoring code can make unwanted edits across dozens of files. Standard AGY provides no built-in visual diffing or safe rollback mechanism.

### 6.1 Native Git Checkpoints (`webui/api/spaces.py`)
- **Visual Commit Timeline**: Surfaces recent Git commits directly in the WebUI Spaces tab with commit hash, message, author, date, and dirty tree status.
- **Commit Patch Diff Viewer**: Generates unified diffs between commits or against the working tree, allowing human operators to inspect file changes before reverting.
- **Zero-Risk Auto-Stash Rollback**: When a rollback is requested, CAGY automatically creates an untracked safety stash (`git stash push -u -m "pre-rollback-auto-stash-..."`) before resetting state. **Human uncommitted work and untracked files are never destroyed**.

---

## 7. Sandboxed Workspace Session Archiving & Browser

Upstream AGY limits session export to browser Blob downloads, forcing developers to manually move files into repositories.

### 7.1 Workspace Destination Selector (`webui/api/session.py`)
- **Direct Workspace Output**: In **Conversation Settings**, operators choose a destination subfolder within their workspace (e.g. `docs/sessions/` or `transcripts/`).
- **Triple Format Serialization**:
  - **Markdown (`.md`)**: Formatted conversation transcript with YAML headers and speaker turns.
  - **Session JSON (`.json`)**: Raw state dump for programmatic backup or reloading.
  - **Styled HTML (`.html`)**: Self-contained visual report with dark/light themes.
- **Strict Directory Traversal Sandboxing**: Canonical path validation strictly enforces that file operations cannot escape the selected workspace boundary via `../`.
- **Workspace Session Browser**: Discovers saved exports in the target subfolder with metadata (title, message count, timestamp) and provides **1-click session import** back into the chat interface.

---

## 8. Developer Experience & UI Innovations

- **Composer Slash Popover (`/`)**: Floating autocomplete palette with category pills (`Workflow`, `Memory`, `Tools`, `Agents`, `System`, `Session`) and keyboard navigation for all signature workflows (`/plan`, `/goal`, `/memorize`, `/vault`, `/swarm`, `/mcp`, `/skills`, `/grill-me`, `/status`).
- **Artifact & Diff Preview Canvas**: Tri-mode drawer (`[ Code | Diff | Live Preview ]`) featuring side-by-side Git diffs against HEAD, sandboxed HTML/SVG live rendering, and direct file editing.
- **Unified MCP Hub (`/mcp`)**: Native manager for `mcp.json` with one-click presets (Docker, GitHub, Postgres, SQLite, Puppeteer) and live JSON-RPC connection probes.
- **Subagents Swarm Visualizer (`/swarm`)**: Real-time DAG hierarchy viewer tracking hierarchical agent relationships, execution logs, and transcripts.
- **WCAG AA High-Contrast Accessibility**: Solid accent buttons utilize dedicated high-contrast CSS variables (`--accent-contrast`) ensuring legibility across light and dark modes.

---

## Conclusion: The CAGY Advantage

By combining **hermetic container sandboxing** with an **Obsidian-compatible Knowledge Vault** and **Token Economics analytics**, CAGY solves the two greatest challenges of agentic pair-programming:

1. **Safety & Reproducibility**: Giving autonomous agents full shell and toolchain freedom inside a bounded, safe blast radius.
2. **Context Longevity & Efficiency**: Replacing bloated, 80k-token conversational debt with lean, structured, 600-token Turn-0 knowledge recall.
