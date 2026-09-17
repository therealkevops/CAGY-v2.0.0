# Antigravity Knowledge Vault & Long-Term Memory

> [!IMPORTANT]
> This context is automatically compiled from the workspace Knowledge Vault (`/workspace/knowledge/`).
> Active Scope: **300-640_Dcai**
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

> [!NOTE]
> **Tiered Context Diet Active**: Space knowledge (26.7 KB) exceeds the full-text limit (20.0 KB). Compiled into a high-density Decision Matrix and Executive Abstracts Map to optimize prompt efficiency. To retrieve complete notes on-demand, run `python3 skills/knowledge-vault/scripts/recall_vault.py "<query>"`.

## Architectural Decisions Matrix
| ADR | Title | Status | Summary & Key Decision |
|:---|:---|:---:|:---|
| [[spaces/300-640_dcai/decisions/adr_001_rocev2_lossless_transport|ADR 001]] | ADR 001: Standardization on RoCEv2 for AI Back-End Interconnect | `Accepted` | Chosen Option: **RoCEv2 (Option 3)**. - **UDP Port 4791 Encapsulation**: RoCEv2 is fully routable across Layer 3 IP networks, allowing large-scale multi-tier leaf-spine topologies. - **Dynamic Flow Hashing**:... |
| [[spaces/300-640_dcai/decisions/adr_002_dynamic_load_balancing_flowlets|ADR 002]] | ADR 002: Dynamic Load Balancing (DLB) & Flowlet Switching Adoption | `Accepted` | Chosen Option: **Dynamic Load Balancing (DLB) with Flowlet Switching (Option 2)**. - **ASIC Queue Monitoring**: Cisco Nexus 9000 Cloud Scale and Silicon One ASICs monitor egress queue depths in real-time, steering... |

### Key Architectural Decision Abstracts
- **[[spaces/300-640_dcai/decisions/adr_001_rocev2_lossless_transport|ADR 001: Standardization on RoCEv2 for AI Back-End Interconnect]]** (`Accepted`): Chosen Option: **RoCEv2 (Option 3)**. - **UDP Port 4791 Encapsulation**: RoCEv2 is fully routable across Layer 3 IP networks, allowing large-scale multi-tier leaf-spine topologies. - **Dynamic Flow Hashing**:...
- **[[spaces/300-640_dcai/decisions/adr_002_dynamic_load_balancing_flowlets|ADR 002: Dynamic Load Balancing (DLB) & Flowlet Switching Adoption]]** (`Accepted`): Chosen Option: **Dynamic Load Balancing (DLB) with Flowlet Switching (Option 2)**. - **ASIC Queue Monitoring**: Cisco Nexus 9000 Cloud Scale and Silicon One ASICs monitor egress queue depths in real-time, steering...

## Architectural Principles & System Design (Abstracts)
- **[[spaces/300-640_dcai/architecture/lossless_ethernet_rocev2_topology|Lossless Ethernet & RoCEv2 Network Architecture]]**: The back-end AI cluster network must provide deterministic, **lossless Layer 3 transport** for GPU-to-GPU collective operations (AllReduce, AllGather). Traditional TCP flow control introduces prohibitive retransmission latency that...
- **[[spaces/300-640_dcai/architecture/cisco_ucs_nexus_ai_fabric|Cisco UCS & Nexus AI Compute Fabric Architecture]]**: This architecture establishes the physical and logical integration of **Cisco UCS X-Series modular compute** and **Cisco Nexus 9000 switches**, orchestrating GPU-accelerated workloads across high-density AI data halls. 1....

## 300-640_Dcai Domain Knowledge & Guides (Map)
- **[[spaces/300-640_dcai/notes/dcai_curriculum_domain_map|Cisco 300-640 DCAI Curriculum Domain Map]]**: *Master AI workload mechanics, the data pipeline lifecycle, and Cisco turnkey architectures.* - **1.1 Workload Types**: RAG, Distributed Training (Data, Tensor, Pipeline Parallelism), Inference (KV cache, TTFT), and Generative AI. -...
- **[[spaces/300-640_dcai/notes/ai_infrastructure_troubleshooting_playbook|AI Infrastructure Troubleshooting & Diagnostic Playbook]]**: When multi-node distributed training jobs stall, drop packets, or abort with NCCL timeouts, follow this 4-step triage sequence: - **Symptom**: Whole rack stalls; `show priority-flow-control` shows millions of `Rx-PPP` on a single leaf port.
- **[[spaces/300-640_dcai/notes/lossless_ethernet_pfc_ecn_rocev2_guide|Lossless Ethernet, PFC, ECN, & RoCEv2 Reference Guide]]**: In AI distributed computing, gradient synchronization during AllReduce collective calls makes clusters extraordinarily sensitive to tail latency. A single dropped packet stalls hundreds of synchronized GPUs. Lossless Ethernet converts...
- **[[spaces/300-640_dcai/notes/cisco_ai_compute_ucs_nexus_architecture|Cisco AI Compute (UCS & Nexus) Architecture Reference Guide]]**: Cisco UCS revolutionizes high-density AI computing by combining modular blade agility with dedicated GPU accelerators via the **UCS X-Series Modular System**: | Policy Category | Configuration Target | Mandatory Setting for AI RoCEv2 |...
- **[[spaces/300-640_dcai/notes/overview|Cisco 300-640 DCAI Knowledge Base Space Overview]]**: This space container serves as the primary **Second Brain & Knowledge Graph Memory Engine** for the **Cisco 300-640 DCAI (Implementing Cisco Data Center AI Infrastructure)** certification and engineering repository. It bridges deep...
