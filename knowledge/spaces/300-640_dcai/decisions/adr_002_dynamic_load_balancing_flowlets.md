# ADR 002: Dynamic Load Balancing (DLB) & Flowlet Switching Adoption

- **Status**: Accepted
- **Date**: 2026-09-11
- **Deciders**: Lead Systems & Cloud Architect
- **Tags**: #decision #adr #dlb #flowlets #ecmp #nexus #loadbalancing #300-640_dcai

## Context & Problem Statement
AI distributed training traffic consists of very few, multi-gigabyte "elephant flows" streaming at 400 Gbps. Under standard static 5-tuple ECMP hashing, multiple elephant flows frequently hash to the same spine uplink, causing acute buffer congestion and packet drops, while adjacent spine uplinks remain idle (polarization).

## Decision Drivers
1. **Fabric Utilization**: Must achieve $>85\%$ aggregate link utilization across all spine switches.
2. **Packet Ordering Integrity**: Must prevent out-of-order packets from overwhelming legacy host network stacks.
3. **Hardware-Line-Rate Execution**: Load-balancing decisions must be made in hardware ASICs without CPU interrupts.

## Considered Options
1. **Static 5-Tuple ECMP**
2. **Dynamic Load Balancing (DLB) with Flowlet Switching**
3. **Per-Packet Round-Robin (Packet Spraying)**

## Decision Outcome
Chosen Option: **Dynamic Load Balancing (DLB) with Flowlet Switching (Option 2)**.

### Rationale
- **ASIC Queue Monitoring**: Cisco Nexus 9000 Cloud Scale and Silicon One ASICs monitor egress queue depths in real-time, steering flows to the least congested path.
- **Zero Out-of-Order Packets**: By waiting for a natural silence gap (`flowlet-gap 64`) that exceeds the inter-path latency difference, flows are split safely across uplinks without triggering packet reassembly overhead on host systems.
- **Graceful Failover**: In the event of a spine link flap, DLB dynamically redirects flowlets to surviving paths in microseconds.

## Consequences
- **Positive**:
  - Eliminates ECMP hashing collisions and hot spots.
  - Slashes AllReduce tail latency across multi-node clusters.
- **Negative / Risks**:
  - Requires tuning the `flowlet-gap` timer to ensure it exceeds the maximum fabric Round-Trip Time (RTT) variance.

## Interlinked Context
- See [[lossless_ethernet_rocev2_topology]] for the fabric topology.
- See [[adr_001_rocev2_lossless_transport]] for the transport protocol.
- See [[ai_infrastructure_troubleshooting_playbook]] for diagnosing link skew.
