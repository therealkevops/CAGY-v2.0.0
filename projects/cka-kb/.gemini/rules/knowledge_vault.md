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

## Key Architectural Decisions
### ADR 001: Prioritize Calico eBPF Data Plane for Lab Drills
# ADR 001: Prioritize Calico eBPF Data Plane for Lab Drills

- **Date**: 2026-09-05
- **Status**: Accepted
- **Space**: cka-kb
- **Tags**: #cka #networking #calico #ebpf #adr

## Context & Problem Statement
During intensive CKA practice drills involving hundreds of Pods and rapid NetworkPolicy churn, standard Linux iptables mode introduces rule processing latency and packet filtering overhead.

## Decision
We adopt the Calico eBPF data plane as the standard networking and policy enforcement engine across all CKA lab clusters.

## Consequences
- **Positive**: Sub-millisecond NetworkPolicy evaluation, transparent source IP preservation, and native service routing replacing kube-proxy.
- **Trade-off**: Requires Linux kernel 5.3+ and BTF (BPF Type Format) support, which is natively available in the Linux container and modern host environments.

## Interlinks & References
- [[spaces/cka-kb/notes/overview|CKA Space Overview]]
- [[user/profile|Developer Profile]]

## Cka-Kb Domain Knowledge & Guides
### CKA Knowledge Base Space Overview
# CKA Knowledge Base Space Overview

- **Space**: `cka-kb`
- **Related Project**: `/workspace/projects/cka-kb`
- **Tags**: #kubernetes #cka #certification #devops #space

## Overview
This space container houses architectural decisions, domain notes, and learning workflows specifically for the **Certified Kubernetes Administrator (CKA)** knowledge repository.

## Key Focus Areas
- **00 - Exam Strategy & Environment**: Terminal aliases, speed shortcuts, tmux, and documentation lookup tricks.
- **01 - Troubleshooting**: Control plane, worker nodes, kubelet systemd services, and network diagnostics.
- **02 - Cluster Architecture & Installation**: Kubeadm bootstrap, etcd backup/restore, certificate renewal, and cluster upgrades.
- **03 - Services & Networking**: ClusterIP, NodePort, Ingress, NetworkPolicies, and CNI plugins (Calico / Flannel).
- **04 - Workloads & Scheduling**: Deployments, StatefulSets, DaemonSets, affinities, tolerations, and resource quotas.
- **05 - Storage**: StorageClasses, PersistentVolumes (PV), PersistentVolumeClaims (PVC), and access modes.
- **06 - Hands-On Scenarios**: Practice labs, failure recovery drills, and time-boxed simulation checklists.

## Interlinks & References
- Global Developer Profile: [[user/profile]]
- Workspace Repository: [CKA Repository](file:///workspace/projects/cka-kb/README.md)
