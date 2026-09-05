# Antigravity Knowledge Vault & Long-Term Memory

> [!IMPORTANT]
> This context is automatically compiled from the workspace Knowledge Vault (`/workspace/knowledge/`).
> Active Scope: **Cka-Kb**
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
> **Tiered Context Diet Active**: Space knowledge (47.6 KB) exceeds the full-text limit (20.0 KB). Compiled into a high-density Decision Matrix and Executive Abstracts Map to optimize prompt efficiency. To retrieve complete notes on-demand, run `python3 skills/knowledge-vault/scripts/recall_vault.py "<query>"`.

## Architectural Decisions Matrix
| ADR | Title | Status | Summary & Key Decision |
|:---|:---|:---:|:---|
| [[spaces/cka-kb/decisions/adr_004_cgroup_systemd_standardization|ADR 004]] | ADR 004: cgroup systemd Driver Standardization | `Accepted` | A notorious and frequent CKA exam scenario involves a worker node marked as `NotReady` immediately following an upgrade or initial join. When inspecting `systemctl status kubelet`, the service is actively crashing... |
| [[spaces/cka-kb/decisions/adr_001_calico_ebpf_data_plane|ADR 001]] | ADR 001: Prioritize Calico eBPF Data Plane for Lab Drills | `Accepted` | During intensive CKA practice drills involving hundreds of Pods and rapid NetworkPolicy churn, standard Linux iptables mode introduces rule processing latency, sequential table lookup overhead, and complex packet... |
| [[spaces/cka-kb/decisions/adr_003_etcd_backup_restore_strategy|ADR 003]] | ADR 003: etcd Snapshot Backup & Restore Strategy | `Accepted` | In the CKA exam, the etcd backup and disaster recovery question carries significant weight (~7-9% of total exam score). The task requires executing an `etcdctl snapshot save` against an active TLS-secured etcd... |
| [[spaces/cka-kb/decisions/adr_002_gateway_api_adoption|ADR 002]] | ADR 002: Gateway API Adoption for Ingress Routing | `Accepted` | The CNCF CKA curriculum (v1.35 and 2025/2026 refresh) has incorporated the **Kubernetes Gateway API** as the modern, role-oriented standard for North-South ingress traffic management. While legacy... |

### Key Architectural Decision Abstracts
- **[[spaces/cka-kb/decisions/adr_004_cgroup_systemd_standardization|ADR 004: cgroup systemd Driver Standardization]]** (`Accepted`): A notorious and frequent CKA exam scenario involves a worker node marked as `NotReady` immediately following an upgrade or initial join. When inspecting `systemctl status kubelet`, the service is actively crashing...
- **[[spaces/cka-kb/decisions/adr_001_calico_ebpf_data_plane|ADR 001: Prioritize Calico eBPF Data Plane for Lab Drills]]** (`Accepted`): During intensive CKA practice drills involving hundreds of Pods and rapid NetworkPolicy churn, standard Linux iptables mode introduces rule processing latency, sequential table lookup overhead, and complex packet...
- **[[spaces/cka-kb/decisions/adr_003_etcd_backup_restore_strategy|ADR 003: etcd Snapshot Backup & Restore Strategy]]** (`Accepted`): In the CKA exam, the etcd backup and disaster recovery question carries significant weight (~7-9% of total exam score). The task requires executing an `etcdctl snapshot save` against an active TLS-secured etcd...
- **[[spaces/cka-kb/decisions/adr_002_gateway_api_adoption|ADR 002: Gateway API Adoption for Ingress Routing]]** (`Accepted`): The CNCF CKA curriculum (v1.35 and 2025/2026 refresh) has incorporated the **Kubernetes Gateway API** as the modern, role-oriented standard for North-South ingress traffic management. While legacy...

## Architectural Principles & System Design (Abstracts)
- **[[spaces/cka-kb/architecture/network_and_dns_model|Kubernetes Networking, CoreDNS & Traffic Routing Model]]**: Kubernetes mandates four core networking rules that every CNI plugin must enforce without NAT: 1. **Pod-to-Pod Direct Reachability**: Every Pod has a unique IP. All Pods communicate directly with all other Pods on any node without...
- **[[spaces/cka-kb/architecture/control_plane_topology|Kubernetes Control Plane Topology & Component Lifecycle]]**: $$\text{Client Request} \longrightarrow \text{Authentication (Cert/Webhook/Token)} \longrightarrow \text{Authorization (RBAC/Node)} \longrightarrow \text{Mutating Admission} \longrightarrow \text{Schema Validation} \longrightarrow...
- **[[spaces/cka-kb/architecture/storage_architecture|Kubernetes Storage Architecture & Container Storage Interface (CSI)]]**: - *Risk*: May provision the storage in Availability Zone A, while the Pod gets scheduled to a node in Availability Zone B, causing `VolumeZoneConflict`. - **`WaitForFirstConsumer`**: Delays volume provisioning and binding until a Pod...

## Cka-Kb Domain Knowledge & Guides (Map)
- **[[spaces/cka-kb/notes/overview|CKA Knowledge Base Space Overview]]**: This space container serves as the primary **Second Brain & Knowledge Graph Memory Engine** for the Certified Kubernetes Administrator (CKA) repository. It unifies architectural blueprints, architectural decision records (ADRs),...
- **[[spaces/cka-kb/notes/cka_curriculum_domain_map|CKA Curriculum Domain Map & Knowledge Graph Matrix]]**: 1. [Node & Kubelet Troubleshooting](file:///workspace/projects/cka-kb/01-troubleshooting/01-node-and-kubelet-troubleshooting.md) 2. [Control Plane...
- **[[spaces/cka-kb/notes/exam_triage_and_speedrun_playbook|CKA Exam Triage & Speedrun Playbook]]**: The CKA exam runs inside a browser-based PSI Remote Proctor terminal (120 minutes, ~15-17 questions, 66% passing score). Every second spent typing manual YAML or scrolling through deep documentation submenus costs precious points....
- **[[spaces/cka-kb/notes/troubleshooting_decision_trees|CKA Troubleshooting Decision Trees & Mental Models]]**: 1. **Top-Down or Bottom-Up?** - **Bottom-Up**: Infrastructure failure (Node NotReady, Kubelet crash, Container runtime stopped). - **Top-Down**: Application or config failure (Pod CrashLoop, Service selector mismatch, NetworkPolicy...
