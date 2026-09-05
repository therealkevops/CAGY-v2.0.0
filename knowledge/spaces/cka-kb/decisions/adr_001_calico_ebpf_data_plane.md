# ADR 001: Prioritize Calico eBPF Data Plane for Lab Drills

- **Date**: 2026-09-05
- **Status**: Accepted
- **Space**: `cka-kb`
- **Tags**: #cka #networking #calico #ebpf #adr

## Context & Problem Statement
During intensive CKA practice drills involving hundreds of Pods and rapid NetworkPolicy churn, standard Linux iptables mode introduces rule processing latency, sequential table lookup overhead, and complex packet tracing during live triage.

## Decision
We adopt the Calico eBPF data plane as the standard networking and policy enforcement engine across all CKA lab clusters and simulator environments.

## Consequences
- **Positive**:
  - Sub-millisecond NetworkPolicy evaluation via kernel eBPF maps rather than linear iptables traversal.
  - Transparent source IP preservation across cluster nodes.
  - Native service routing bypassing `kube-proxy` iptables bottlenecks.
- **Trade-off**:
  - Requires Linux kernel 5.3+ and BTF (BPF Type Format) support, natively available in modern distributions and the containerized test namespace.
  - Candidates must still understand standard `iptables` triage for clusters running legacy kube-proxy.

## Interlinks & Related Knowledge
- **Space Hub**: [[overview|CKA Space Overview]]
- **Architecture**: [[network_and_dns_model|Kubernetes Network & DNS Architecture]]
- **Related Decisions**:
  - [[adr_002_gateway_api_adoption|ADR 002: Gateway API Adoption for Ingress Routing]]
- **Global Context**: [[user/profile|Developer Profile]]
