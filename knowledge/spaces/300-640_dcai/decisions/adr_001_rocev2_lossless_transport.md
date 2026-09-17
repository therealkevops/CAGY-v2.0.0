# ADR 001: Standardization on RoCEv2 for AI Back-End Interconnect

- **Status**: Accepted
- **Date**: 2026-09-11
- **Deciders**: Lead Systems & Cloud Architect
- **Tags**: #decision #adr #rocev2 #infiniband #networking #cisco #300-640_dcai

## Context & Problem Statement
AI distributed model training requires ultra-high-bandwidth, zero-copy, sub-microsecond interconnects to synchronize gradient tensors during collective AllReduce operations. Historically, InfiniBand was the default choice for high-performance computing (HPC). However, proprietary hardware lock-in, vendor supply chain single-sourcing, and siloed administrative toolchains present significant operational liabilities for enterprise-scale deployments.

## Decision Drivers
1. **Interoperability**: Ability to run on standard, multi-vendor 400G/800G Ethernet hardware (Cisco Nexus 9000).
2. **Layer 3 Routability**: Need to traverse standard IP subnets and leverage BGP-EVPN architectures.
3. **Total Cost of Ownership (TCO)**: Unifying data center storage, compute management, and AI clusters over converged Ethernet infrastructure.
4. **Zero-Copy Performance**: Direct GPU HBM memory access without CPU interrupts.

## Considered Options
1. **Proprietary InfiniBand Architecture** (NDR 400G / XDR 800G)
2. **RoCEv1 (Layer 2 RDMA over Ethernet)**
3. **RoCEv2 (Routable RDMA over UDP Port 4791)**

## Decision Outcome
Chosen Option: **RoCEv2 (Option 3)**.

### Rationale
- **UDP Port 4791 Encapsulation**: RoCEv2 is fully routable across Layer 3 IP networks, allowing large-scale multi-tier leaf-spine topologies.
- **Dynamic Flow Hashing**: Randomization of the UDP source port enables native Equal-Cost Multi-Path (ECMP) and Dynamic Load Balancing (DLB) across Cisco Nexus switches.
- **Lossless Convergence**: Combining Priority-based Flow Control (PFC - 802.1Qbb) on CoS 3 and Explicit Congestion Notification (ECN - RFC 3168) provides InfiniBand-equivalent latency with Ethernet economic scale.

## Consequences
- **Positive**:
  - Leverages enterprise-grade Cisco Nexus 9000 Cloud Scale and Silicon One ASICs.
  - Unified telemetry through Nexus Dashboard Insights and Intersight.
  - No proprietary subnet managers or dedicated InfiniBand gateways required.
- **Negative / Risks**:
  - Requires strict, zero-tolerance buffer configuration (headroom calculus, XOFF/XON thresholds) to prevent PFC deadlocks.
  - Mandates fabric-wide MTU 9216 (Jumbo Frames).

## Interlinked Context
- See [[lossless_ethernet_rocev2_topology]] for the detailed network design.
- See [[adr_002_dynamic_load_balancing_flowlets]] for resolving ECMP hash collisions with RoCEv2.
- See [[lossless_ethernet_pfc_ecn_rocev2_guide]] for configuration parameters.
