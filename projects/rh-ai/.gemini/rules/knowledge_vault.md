# Antigravity Knowledge Vault & Long-Term Memory

> [!IMPORTANT]
> This context is automatically compiled from the workspace Knowledge Vault (`/workspace/knowledge/`).
> Active Scope: **Rh-Ai**
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
> **Tiered Context Diet Active**: Space knowledge (161.2 KB) exceeds the full-text limit (20.0 KB). Compiled into a high-density Decision Matrix and Executive Abstracts Map to optimize prompt efficiency. To retrieve complete notes on-demand, run `python3 skills/knowledge-vault/scripts/recall_vault.py "<query>"`.

## Architectural Decisions Matrix
| ADR | Title | Status | Summary & Key Decision |
|:---|:---|:---:|:---|
| [[spaces/rh-ai/decisions/adr_001_standardize_on_rhoai_and_kserve_vllm|ADR 001]] | ADR 001: Standardize on Red Hat OpenShift AI (RHOAI) with KServe & vLLM for Model Serving | `Accepted` | **Adopt Red Hat OpenShift AI (RHOAI) as the enterprise AI platform standard, deploying KServe paired with the vLLM ServingRuntime for high-throughput model inference**: - **Operator-Driven Lifecycle**: Deploy and... |
| [[spaces/rh-ai/decisions/adr_002_instructlab_taxonomy_governance|ADR 002]] | ADR 002: Adopt InstructLab Git Taxonomy for Enterprise Knowledge Grounding and Synthetic Data Generation | `Accepted` | **Adopt the InstructLab LAB (Large-scale Alignment for chatBots) methodology and Git-based taxonomy as the organization's standard for model customization**: - **Git-Centric Taxonomy**: Store all domain knowledge and... |
| [[spaces/rh-ai/decisions/adr_003_heterogeneous_hardware_acceleration|ADR 003]] | ADR 003: Standardize Heterogeneous Hardware Acceleration Framework (NVIDIA, AMD, Intel) | `Accepted` | **Adopt a multi-vendor, operator-abstracted hardware acceleration strategy across OpenShift AI and RHEL AI**: - **Operator-Driven Decoupling**: Abstract silicon hardware via the **NVIDIA GPU Operator**, **AMD ROCm... |

### Key Architectural Decision Abstracts
- **[[spaces/rh-ai/decisions/adr_001_standardize_on_rhoai_and_kserve_vllm|ADR 001: Standardize on Red Hat OpenShift AI (RHOAI) with KServe & vLLM for Model Serving]]** (`Accepted`): **Adopt Red Hat OpenShift AI (RHOAI) as the enterprise AI platform standard, deploying KServe paired with the vLLM ServingRuntime for high-throughput model inference**: - **Operator-Driven Lifecycle**: Deploy and...
- **[[spaces/rh-ai/decisions/adr_002_instructlab_taxonomy_governance|ADR 002: Adopt InstructLab Git Taxonomy for Enterprise Knowledge Grounding and Synthetic Data Generation]]** (`Accepted`): **Adopt the InstructLab LAB (Large-scale Alignment for chatBots) methodology and Git-based taxonomy as the organization's standard for model customization**: - **Git-Centric Taxonomy**: Store all domain knowledge and...
- **[[spaces/rh-ai/decisions/adr_003_heterogeneous_hardware_acceleration|ADR 003: Standardize Heterogeneous Hardware Acceleration Framework (NVIDIA, AMD, Intel)]]** (`Accepted`): **Adopt a multi-vendor, operator-abstracted hardware acceleration strategy across OpenShift AI and RHEL AI**: - **Operator-Driven Decoupling**: Abstract silicon hardware via the **NVIDIA GPU Operator**, **AMD ROCm...

## Architectural Principles & System Design (Abstracts)
- **[[spaces/rh-ai/architecture/rhoai_system_topology|Red Hat OpenShift AI (RHOAI) System Topology & Control Plane Architecture]]**: > **Space**: `rh-ai` > **Category**: `architecture` > **Status**: Approved Reference > **Target Platform**: OpenShift Container Platform 4.16+, OpenShift AI 2.14+ Red Hat OpenShift AI (RHOAI) decouples user-facing interactive...
- **[[spaces/rh-ai/architecture/instructlab_alignment_pipeline|InstructLab Alignment & Synthetic Data Pipeline Architecture]]**: > **Space**: `rh-ai` > **Category**: `architecture` > **Status**: Approved Reference > **Target Tooling**: InstructLab CLI (`ilab`), IBM Granite, Ray Distributed The Large-scale Alignment for chatBots (LAB) methodology operationalizes...
- **[[spaces/rh-ai/architecture/hybrid_cloud_ai_fabric|Hybrid Cloud AI Infrastructure & Networking Fabric]]**: > **Space**: `rh-ai` > **Category**: `architecture` > **Status**: Approved Reference > **Target Topology**: Multi-Cluster OpenShift, Bare Metal GPU Nodes, Hyperscalers Distributed AI models require non-blocking, line-rate communication...

## Rh-Ai Domain Knowledge & Guides (Map)
- **[[spaces/rh-ai/notes/overview|Red Hat Enterprise AI Knowledge Base Overview]]**: > **Space**: `rh-ai` > **Domain**: Red Hat OpenShift AI (RHOAI), RHEL AI, InstructLab, IBM Granite, KServe, Hardware Acceleration > **Status**: Active Reference Hub > **Target Version**: RHEL AI 1.x, OpenShift AI 2.14+, OpenShift...
- **[[spaces/rh-ai/notes/01-red-hat-ai-portfolio-overview|Red Hat AI Portfolio Overview & Strategic Architecture]]**: > **Focus Domain**: Enterprise AI Strategy, Full-Stack Architecture, and Ecosystem Mapping > **Audience**: Cloud & Systems Architects, Platform Engineers, Enterprise ML Practitioners > **Status**: Production Reference Enterprise AI...
- **[[spaces/rh-ai/notes/06-model-serving-kserve-and-vllm|High-Performance Model Serving (KServe & vLLM)]]**: > **Focus Domain**: LLM Inference Engines, KServe Orchestration, vLLM Optimization, Autoscaling > **Audience**: Platform Engineers, Systems Architects, MLOps Engineers > **Status**: Production Reference Serving Large Language Models...
- **[[spaces/rh-ai/notes/05-distributed-training-and-orchestration|Distributed Training & Workload Orchestration (Ray, Kueue, PyTorch)]]**: > **Focus Domain**: Distributed Compute, Batch Scheduling, Ray on Kubernetes, Kueue Queuing, High-Speed Fabric > **Audience**: Platform Engineers, MLOps Architects, Distributed Systems Specialists > **Status**: Production Reference...
- **[[spaces/rh-ai/notes/04-openshift-ai-rhoai-architecture|Red Hat OpenShift AI (RHOAI) Platform Architecture]]**: > **Focus Domain**: Kubernetes-Native AI Platform, Operator Lifecycle, Multi-Tenancy, Distributed Orchestration > **Audience**: Cloud Architects, OpenShift Platform Engineers, Site Reliability Engineers, MLOps Architects > **Status**:...
- **[[spaces/rh-ai/notes/02-rhel-ai-architecture-and-deployment|Red Hat Enterprise Linux AI (RHEL AI) Architecture & Deployment Guide]]**: > **Focus Domain**: Operating System Level AI Appliances, Bootable Containers (`bootc`), Single-Node Alignment > **Audience**: Systems Engineers, Linux Administrators, Infrastructure Architects > **Status**: Production Reference **Red...
- **[[spaces/rh-ai/notes/03-instructlab-and-model-alignment|InstructLab & LAB Alignment Methodology]]**: > **Focus Domain**: Model Alignment, Synthetic Data Generation (SDG), Taxonomy-Driven Fine-Tuning > **Audience**: AI Engineers, ML Scientists, Domain Specialists, Automation Architects > **Status**: Production Reference Traditional...
- **[[spaces/rh-ai/notes/10-hardware-acceleration-and-cluster-sizing|Hardware Acceleration, GPU Math & Cluster Sizing Guide]]**: > **Focus Domain**: Hardware Accelerators, Heterogeneous Compute, VRAM Mathematical Sizing, Cluster Topologies > **Audience**: Infrastructure Architects, Capacity Planners, Hardware Engineers, Platform SREs > **Status**: Production...
- **[[spaces/rh-ai/notes/08-ansible-and-openshift-lightspeed|Enterprise Generative AI & Automation (Ansible & OpenShift Lightspeed)]]**: > **Focus Domain**: Domain-Specific Generative AI, Infrastructure as Code Automation, SRE Assistants > **Audience**: Systems Administrators, DevOps Engineers, Automation Architects, SREs > **Status**: Production Reference Generic...
- **[[spaces/rh-ai/notes/09-governance-trustyai-and-mlops-pipelines|Governance, Safety, TrustyAI & MLOps Pipelines]]**: > **Focus Domain**: AI Governance, Model Explainability, Bias Detection, Model Registry, Automated Pipelines > **Audience**: MLOps Engineers, Compliance Officers, Platform Architects, Enterprise Data Scientists > **Status**: Production...
- **[[spaces/rh-ai/notes/07-ibm-granite-models-and-open-foundation|IBM Granite Models & Enterprise Open Foundation Architecture]]**: > **Focus Domain**: Open Foundation Models, Apache 2.0 Licensing, IP Indemnification, Model Sizing > **Audience**: AI Architects, Enterprise Compliance Officers, Lead Software Engineers > **Status**: Production Reference Most...
- **[[spaces/rh-ai/notes/11-executive-pitch-competition-and-swot-analysis|Red Hat AI: Executive Pitch, Competitive Landscape & SWOT Analysis]]**: > **Focus Domain**: Executive Strategy, Market Positioning, Competitive Battlecards, SWOT Analysis > **Audience**: Enterprise Architects, Technology Strategists, Field CTOs, Sales Engineering Leadership > **Status**: Production...
- **[[spaces/rh-ai/notes/12-nvidia-nim-vllm-llmd-and-lora-adapters|NVIDIA NIM, vLLM, LLM-D & Dynamic LoRA Adapters: Architectural Relationship & Deep Dive]]**: > **Focus Domain**: Inference Microservices, Engine Internals, Distributed Fleet Routing, Multi-LoRA Serving > **Audience**: Enterprise Infrastructure Architects, MLOps Engineers, Principal Systems Engineers > **Target Platforms**: Red...
