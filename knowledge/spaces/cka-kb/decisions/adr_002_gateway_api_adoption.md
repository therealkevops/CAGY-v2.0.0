# ADR 002: Gateway API Adoption for Ingress Routing

- **Date**: 2026-09-05
- **Status**: Accepted
- **Space**: `cka-kb`
- **Tags**: #cka #networking #gateway-api #ingress #adr

## Context & Problem Statement
The CNCF CKA curriculum (v1.35 and 2025/2026 refresh) has incorporated the **Kubernetes Gateway API** as the modern, role-oriented standard for North-South ingress traffic management. While legacy `networking.k8s.io/v1 Ingress` is still covered, students and administrators must master the decoupled `GatewayClass`, `Gateway`, and `HTTPRoute` resources to handle header routing, canary splits, and cross-namespace attachments.

## Decision
We establish **Gateway API** as the primary ingress routing architecture in our CKA knowledge space and lab drills, treating legacy Ingress v1 as a compatibility fallback.

## Implementation Standard
1. All routing labs provide dual solutions: modern `gateway.networking.k8s.io/v1` `HTTPRoute` primary, and `networking.k8s.io/v1` `Ingress` secondary.
2. Ensure CRDs are installed and verified via `kubectl get crd gateways.gateway.networking.k8s.io`.
3. Standardize Envoy Gateway or Contour as the lightweight in-cluster reference implementation.

## Consequences
- **Positive**:
  - Full alignment with recent exam updates and production best practices.
  - Granular separation of concerns: Infrastructure Admin manages `GatewayClass` and `Gateway`; Application Developer manages `HTTPRoute`.
  - Native support for weighted traffic splitting (canary) without proprietary ingress annotations.
- **Trade-off**:
  - Requires deploying Gateway API CRDs if not bundled in the default cluster distribution.
  - Multi-resource manifest syntax requires candidates to practice imperative template generation.

## Interlinks & Related Knowledge
- **Space Hub**: [[overview|CKA Space Overview]]
- **Architecture**: [[network_and_dns_model|Kubernetes Network & DNS Architecture]]
- **Related Decisions**:
  - [[adr_001_calico_ebpf_data_plane|ADR 001: Calico eBPF Data Plane for Lab Drills]]
- **Playbooks & Study Guides**:
  - [[exam_triage_and_speedrun_playbook|Exam Speedrun & Triage Playbook]]
  - [Gateway API Guide](file:///workspace/projects/cka-kb/03-services-and-networking/05-gateway-api.md)
  - [Ingress Controllers & Resources](file:///workspace/projects/cka-kb/03-services-and-networking/04-ingress-controllers-and-resources.md)
