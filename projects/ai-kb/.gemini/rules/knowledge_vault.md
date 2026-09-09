# Antigravity Knowledge Vault & Long-Term Memory

> [!IMPORTANT]
> This context is automatically compiled from the workspace Knowledge Vault (`/workspace/knowledge/`).
> Active Scope: **Ai-Kb**
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
> **Tiered Context Diet Active**: Space knowledge (244.5 KB) exceeds the full-text limit (20.0 KB). Compiled into a high-density Decision Matrix and Executive Abstracts Map to optimize prompt efficiency. To retrieve complete notes on-demand, run `python3 skills/knowledge-vault/scripts/recall_vault.py "<query>"`.

## Architectural Decisions Matrix
| ADR | Title | Status | Summary & Key Decision |
|:---|:---|:---:|:---|
| [[spaces/ai-kb/decisions/adr_002_rocev2_lossless_ethernet|ADR 002]] | ADR 002: Standardize on Lossless RoCEv2 with PFC and ECN for AI Backend Fabric | `Accepted` | **Adopt RoCEv2 over Lossless Ethernet with PFC (Priority Flow Control) and ECN (Explicit Congestion Notification)**: - **Zero Loss**: Lossless traffic class configured on Priority 3 with PFC to eliminate tail drops.... |
| [[spaces/ai-kb/decisions/adr_001_vllm_llmd_inference_standard|ADR 001]] | ADR 001: Adopt vLLM and LLM-D as Enterprise AI Serving & Orchestration Standard | `Accepted` | **Adopt vLLM as the single-node inference engine and LLM-D as the distributed Kubernetes orchestrator**: - **vLLM Engine**: Deployed for PagedAttention (reducing memory waste from 70%+ to <4%) and continuous... |

### Key Architectural Decision Abstracts
- **[[spaces/ai-kb/decisions/adr_002_rocev2_lossless_ethernet|ADR 002: Standardize on Lossless RoCEv2 with PFC and ECN for AI Backend Fabric]]** (`Accepted`): **Adopt RoCEv2 over Lossless Ethernet with PFC (Priority Flow Control) and ECN (Explicit Congestion Notification)**: - **Zero Loss**: Lossless traffic class configured on Priority 3 with PFC to eliminate tail drops....
- **[[spaces/ai-kb/decisions/adr_001_vllm_llmd_inference_standard|ADR 001: Adopt vLLM and LLM-D as Enterprise AI Serving & Orchestration Standard]]** (`Accepted`): **Adopt vLLM as the single-node inference engine and LLM-D as the distributed Kubernetes orchestrator**: - **vLLM Engine**: Deployed for PagedAttention (reducing memory waste from 70%+ to <4%) and continuous...

## Architectural Principles & System Design (Abstracts)
- **[[spaces/ai-kb/architecture/ai_cluster_topology|AI Cluster & Lossless Fabric Topology]]**: > **Space**: `ai-kb` > **Status**: Approved Architecture > **Related Hub**: [[spaces/ai-kb/notes/overview|AI-KB Overview]] > **Decisions**: [[spaces/ai-kb/decisions/adr_001_vllm_llmd_inference_standard|ADR 001: vLLM & LLM-D]],...

## Ai-Kb Domain Knowledge & Guides (Map)
- **[[spaces/ai-kb/notes/overview|AI Infrastructure & Solutions Knowledge Base Overview]]**: > **Space**: `ai-kb` > **Domain**: Enterprise AI Infrastructure, GPU Clusters, Model Serving & Fabric Networking > **Status**: Active Reference Hub This space serves as the comprehensive technical repository and second brain for...
- **[[spaces/ai-kb/notes/lossless-networking-in-gpu-nodes|Lossless Networking in GPU Nodes: The Complete Plain-English Guide]]**: > **Purpose**: A comprehensive, non-technical field guide explaining why standard computer networks choke on artificial intelligence, what **lossless networking** actually means, how technologies like **RDMA, RoCEv2, PFC, and ECN** keep...
- **[[spaces/ai-kb/notes/cisco-ai-solutions-architect-guide|Technical Field Guide: AI Solutions Architecture & Infrastructure]]**: > **Role Context**: Technical Advisor / AI Solutions Architect (Cisco & Enterprise AI Ecosystem) > **Purpose**: A comprehensive, plain-English reference breaking down every technical qualification, architectural pillar, and ecosystem...
- **[[spaces/ai-kb/notes/vllm-and-llmd-explained|vLLM and LLM-D: The Complete Plain-English Guide]]**: > **Purpose**: A comprehensive, non-technical field brief explaining the two most critical open-source technologies in production AI serving: **vLLM** (the high-performance inference engine) and **LLM-D** (the intelligent distributed...
- **[[spaces/ai-kb/notes/gpu-node-and-cluster-sizing-guide|The Definitive Guide to Sizing GPU Nodes and Clusters for LLM Serving]]**: > **Space**: `ai-kb` > **Target Audience**: Infrastructure Architects, SREs, Capacity Planners, and Technical Advisors > **Related Architecture**: [[spaces/ai-kb/architecture/ai_cluster_topology|AI Cluster & Lossless Fabric Topology]] >...
- **[[spaces/ai-kb/notes/rocev2-vs-infiniband-guide|RoCEv2 vs. InfiniBand: The Great AI Fabric Showdown]]**: > **Space**: `ai-kb` > **Target Audience**: Infrastructure Architects, Network Engineers, Enterprise CTOs, and AI Consultants > **Related Architecture**: [[spaces/ai-kb/architecture/ai_cluster_topology|AI Cluster & Lossless Fabric...
- **[[spaces/ai-kb/notes/real-world-architecture-meta-llama3|Real-World Architecture Walkthrough: Meta's 24,000-GPU Llama 3 Infrastructure]]**: > **Space**: `ai-kb` > **Target Audience**: Infrastructure Architects, Network Engineers, Enterprise CTOs, and AI Systems Engineers > **Related Architecture**: [[spaces/ai-kb/architecture/ai_cluster_topology|AI Cluster & Lossless Fabric...
- **[[spaces/ai-kb/notes/scale-out-nvlink-and-ethernet-guide|Scale-Up vs. Scale-Out: NVLink, NVSwitch, and Ethernet in GPU Clusters]]**: > **Space**: `ai-kb` > **Target Audience**: Infrastructure Architects, Network Engineers, SREs, and Technical Consultants > **Related Architecture**: [[spaces/ai-kb/architecture/ai_cluster_topology|AI Cluster & Lossless Fabric...
- **[[spaces/ai-kb/notes/pytorch-in-the-real-world|PyTorch in the Real World: A Plain-English Brief]]**: > **Purpose**: A non-technical, conceptual field guide explaining what PyTorch is, why it dominates the AI industry, how engineers use it day-to-day, and how the world's biggest tech companies deploy it to solve real-world problems. If...
- **[[spaces/ai-kb/notes/ai-infrastructure-explained|AI Infrastructure Explained: GPUs, vLLM, and LLM-D]]**: > **Source**: [KodeKloud — AI Infrastructure Explained (GPUs, vLLM, and LLM-D)](https://www.youtube.com/watch?v=hBzUokVYQkI) > **Speaker**: Mumshad Mannambeth > **Target Audience**: SREs, Systems Administrators, DevOps & Platform...
