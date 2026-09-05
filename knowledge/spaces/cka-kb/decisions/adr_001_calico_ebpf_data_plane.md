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
