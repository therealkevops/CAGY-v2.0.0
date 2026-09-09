# ADR 002: Standardize on Lossless RoCEv2 with PFC and ECN for AI Backend Fabric
> **Space**: `ai-kb`  
> **Status**: `Accepted`  
> **Date**: 2026-09-08  
> **Context**: [[spaces/ai-kb/notes/lossless-networking-in-gpu-nodes|Lossless Networking Guide]] | [[spaces/ai-kb/notes/rocev2-vs-infiniband-guide|RoCEv2 vs. InfiniBand Guide]] | [[spaces/ai-kb/architecture/ai_cluster_topology|Cluster Topology]]

## Context & Problem Statement
Distributed AI model training (All-Reduce operations) and disaggregated inference KV cache transfers are extremely sensitive to packet drops. A single dropped packet causes catastrophic GPU starvation. We must choose between proprietary InfiniBand and open Ethernet.

## Decision Outcome
**Adopt RoCEv2 over Lossless Ethernet with PFC (Priority Flow Control) and ECN (Explicit Congestion Notification)**:
- **Zero Loss**: Lossless traffic class configured on Priority 3 with PFC to eliminate tail drops.
- **Congestion Management**: DCQCN / ECN proactive marking to throttle senders before PFC pause frames trigger.
- **Enterprise Open Standards**: Integrates natively with Cisco Nexus switches and enterprise NetOps tooling, tracking future transition to the Ultra Ethernet Consortium (UEC).
- **Related Notes**: [[spaces/ai-kb/notes/rocev2-vs-infiniband-guide|RoCEv2 vs. InfiniBand Guide]], [[spaces/ai-kb/notes/cisco-ai-solutions-architect-guide|Cisco AI Solutions Guide]], and [[spaces/ai-kb/notes/overview|AI-KB Overview]].
