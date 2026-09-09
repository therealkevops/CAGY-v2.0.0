# RoCEv2 vs. InfiniBand: The Great AI Fabric Showdown
> **Space**: `ai-kb`  
> **Target Audience**: Infrastructure Architects, Network Engineers, Enterprise CTOs, and AI Consultants  
> **Related Architecture**: [[spaces/ai-kb/architecture/ai_cluster_topology|AI Cluster & Lossless Fabric Topology]]  
> **Related Guides**: [[spaces/ai-kb/notes/lossless-networking-in-gpu-nodes|Lossless Networking in GPU Nodes]], [[spaces/ai-kb/notes/scale-out-nvlink-and-ethernet-guide|Scale-Out NVLink & Ethernet]], [[spaces/ai-kb/notes/cisco-ai-solutions-architect-guide|Cisco AI Solutions Architecture]]  
> **Architectural Decisions**: [[spaces/ai-kb/decisions/adr_002_rocev2_lossless_ethernet|ADR 002: Lossless RoCEv2 with PFC & ECN]]

---

## Executive Summary: The Battle for the AI Backend

When building a high-performance GPU cluster for Large Language Model (LLM) training or distributed inference, the compute nodes (the GPUs) cannot succeed in isolation. Because distributed AI workloads require frequent, synchronized data exchange across thousands of GPUs, **the network fabric is the actual computer**.

For over two decades, the undisputed king of High-Performance Computing (HPC) has been **InfiniBand**. But as artificial intelligence exploded from academic supercomputing into mainstream enterprise data centers and hyperscale clouds, **RoCEv2 (RDMA over Converged Ethernet v2)** emerged as a formidable challenger.

Today, this represents the single biggest architectural debate in enterprise AI:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                       THE TWO FABRIC PHILOSOPHIES                           │
├─────────────────────────────────────────────────────────────────────────────┤
│                    INFINIBAND (The Bespoke Race Car)                        │
│  • Nature: Proprietary, purpose-built HPC network owned by NVIDIA.          │
│  • Flow Control: Native hardware credit-based (strictly zero buffer drops). │
│  • Control Plane: Centralized SDN controller (Subnet Manager / UFM).        │
│  • Ecosystem: Turnkey, single-vendor, closed appliance model.               │
│  • Best For: Academic HPC labs, turnkey greenfield pods (<4,000 GPUs).      │
├─────────────────────────────────────────────────────────────────────────────┤
│                    RoCEv2 ETHERNET (The High-Speed Superhighway)             │
│  • Nature: Open, multi-vendor enterprise networking running on UDP/IP.      │
│  • Flow Control: Priority Flow Control (PFC) + ECN/DCQCN congestion control.│
│  • Control Plane: Decentralized, battle-tested BGP / Clos routing.           │
│  • Ecosystem: Open standards (Cisco Silicon One, Arista, Broadcom, Spectrum)│
│  • Best For: Hyperscale AI factories (10,000+ GPUs), enterprise data centers│
└─────────────────────────────────────────────────────────────────────────────┘
```

The multi-billion dollar question for infrastructure leaders is simple:
> *"Do we buy NVIDIA's turnkey, proprietary InfiniBand fabric, or do we deploy Lossless RoCEv2 Ethernet across our data center?"*

This guide breaks down the physical mechanics, routing architectures, operational economics, and future roadmaps of both technologies in plain English.

---

## Part 1: How RDMA Works on Both Technologies

Both InfiniBand and RoCEv2 are designed to deliver **RDMA (Remote Direct Memory Access)**. 

In standard enterprise networking (TCP/IP), every packet must be inspected by the host CPU, copied between multiple operating system kernel buffers, and translated before it reaches application memory. This creates 10 to 50 microseconds of overhead—an eternity that starves GPUs waiting for tensor synchronization.

RDMA bypasses the CPU and operating system kernel entirely:
* The network interface card (NIC/HCA) reads data directly from local GPU VRAM.
* The NIC streams that data across the physical network.
* The receiving NIC writes the data directly into remote GPU VRAM with **zero CPU involvement** and **sub-microsecond latency**.

```
                           THE RDMA REVOLUTION
                           
   TRADITIONAL TCP/IP (Slow, High CPU Overhead):
   [GPU VRAM] ──► [Host RAM] ──► [OS Kernel Buffer] ──► [TCP Stack] ──► [NIC]
                       (Multiple buffer copies & CPU interruptions: ~20-50 μs)

   RDMA (InfiniBand or RoCEv2 - Direct GPU-to-GPU):
   [GPU VRAM] ═══════════════════════► [NIC/HCA] ═══════════════► [Wire]
                       (Zero-copy, bypasses OS & CPU: <2 μs)
```

The critical difference between InfiniBand and RoCEv2 is **not what they do (RDMA)**, but **the physical wire protocol and flow control mechanisms they use to guarantee zero packet loss**.

---

## Part 2: Flow Control: Credit-Based vs. PFC/ECN

In an AI cluster running collective communications (such as `All-Reduce`), a single dropped packet forces the entire cluster of thousands of GPUs to freeze while the missing chunk is retransmitted. Both networks must be **strictly lossless**, but they achieve this in opposite ways.

### 2.1 InfiniBand: Hardware Credit-Based Flow Control
InfiniBand was designed from day one (over 20 years ago) as an inherently lossless point-to-point interconnect.

* **How it Works**: Communication operates on a strict **token (credit) system** at Layer 2.
  * Switch Port A and Switch Port B constantly talk to each other.
  * Port B tells Port A: *"I have buffer space for exactly 64 packets. Here are 64 credits."*
  * Port A transmits packets, decrementing its credit balance with each send.
  * When Port A runs out of credits, **it physically stops transmitting**. It cannot send another bit until Port B drains its buffer and grants new credits.
* **The Result**: Buffers **can never overflow**. Packet loss due to buffer exhaustion is mathematically impossible at the physical layer.
* **Latency**: Switch-hop latency is ultra-low: **~130 to 200 nanoseconds**.

```
                   INFINIBAND CREDIT-BASED FLOW CONTROL
                   
    [Sender Switch]                                    [Receiver Switch]
           │                                                   │
           │ ◄─────── "I have room for 4 packets" (Credits) ───┤
           │                                                   │
           ├─── Packet 1 (Balance: 3) ────────────────────────►│
           ├─── Packet 2 (Balance: 2) ────────────────────────►│
           ├─── Packet 3 (Balance: 1) ────────────────────────►│
           ├─── Packet 4 (Balance: 0) ────────────────────────►│
           │                                                   │
     [BLOCKED: Must wait for receiver to process and issue new credits]
```

---

### 2.2 RoCEv2: Simulating Losslessness on a Lossy Foundation
Standard Ethernet is historically "best-effort": if a switch port gets congested, it simply throws excess packets in the trash (tail drop) and relies on TCP to retransmit.

To run RDMA over Ethernet, **RoCEv2** wraps native InfiniBand transport packets inside standard UDP/IP headers. To prevent the underlying Ethernet switches from dropping packets, network engineers combine two advanced mechanisms:

#### A. Priority Flow Control (PFC - IEEE 802.1Qbb)
* PFC carves a single Ethernet physical cable into **8 virtual lanes (priorities)**.
* Standard traffic (SSH, logging, web) runs on lossy lanes (e.g. Priority 0).
* AI RDMA traffic runs exclusively on a dedicated lossless lane (typically **Priority 3**).
* When a switch buffer on Priority 3 fills beyond a high-water threshold, the switch emits a **PFC PAUSE frame** back upstream, telling the sender to pause that specific priority for a few microseconds.

#### B. Explicit Congestion Notification & DCQCN (ECN - RFC 3168 / 802.1Qau)
* Relying solely on PFC PAUSE frames causes severe problems: **PFC Deadlocks**, **Head-of-Line (HoL) blocking**, and **PFC Storms** (where pause frames propagate backwards like a traffic jam across the entire data center).
* To prevent this, RoCEv2 uses **DCQCN (Data Center Quantized Congestion Notification)**:
  1. As switch queues begin to fill, the switch marks the **ECN bits** in the IP header of packets passing through.
  2. When the receiving GPU NIC sees the marked packet, it sends a Congestion Notification Packet (CNP) back to the sender.
  3. The sender immediately dials back its transmission rate *before* buffers overflow and *before* PFC pause frames are triggered.

```
                      RoCEv2 LOSSLESS ETHERNET HYGIENE
                      
   Proactive Throttle (ECN / DCQCN):
   [Sender NIC] ◄─── Congestion Notification (CNP) ─── [Receiver NIC]
         │ (Smoothly ramps down rate before buffers overflow)
         ▼
   Last-Resort Emergency Brake (PFC Pause):
   [Upstream Switch] ◄════ PAUSE Priority 3 ══════════ [Congested Switch]
```

* **Latency**: Modern cut-through Ethernet switches (like Cisco Silicon One G200 or Broadcom Tomahawk 5) deliver switch-hop latencies of **~400 to 800 nanoseconds**. While slightly higher than InfiniBand in synthetic benchmarks, this 300 ns difference is minuscule compared to GPU compute and kernel launch times in real-world distributed workloads.

---

## Part 3: Routing Architecture: Centralized SDN vs. Distributed BGP

The biggest operational difference between InfiniBand and RoCEv2 lies in **how the network routes traffic and handles failures**.

### 3.1 InfiniBand: The Centralized Subnet Manager (OpenSM / UFM)
InfiniBand does not use standard IP routing protocols (like BGP or OSPF). Instead, the entire fabric is orchestrated by a centralized software controller called the **Subnet Manager (SM)**.

* **How it Routes**:
  * The Subnet Manager discovers all HCAs, cables, and switches in the cluster.
  * It calculates global Linear Forwarding Tables (LFTs) and pushes them down into each switch’s hardware.
  * Switches follow strict, pre-programmed destination paths.
* **The Scalability Boundary**:
  * Because the Subnet Manager must maintain global state, large InfiniBand fabrics are typically capped at **~40,000 nodes** per subnet before routing calculation times and topology discovery become unwieldy.
* **The Failure Blast Radius**:
  * When a core InfiniBand switch or optical link fails, the Subnet Manager must detect the topology change, recalculate paths across the entire cluster, and re-program switch forwarding tables.
  * During this reconvergence event, the fabric experiences micro-pauses or throughput dips that can disrupt ongoing training jobs.

```
                 INFINIBAND CENTRALIZED SUBNET MANAGER
                 
                     ┌───────────────────────────┐
                     │   CENTRALIZED CONTROLLER  │
                     │  (Subnet Manager / UFM)   │
                     └─────────────┬─────────────┘
                                   │ Pushes routing tables (LFTs)
            ┌──────────────────────┼──────────────────────┐
            ▼                      ▼                      ▼
     [Quantum Switch]       [Quantum Switch]       [Quantum Switch]
            │                      │                      │
     (No routing intelligence; purely executes controller's pre-computed paths)
```

---

### 3.2 RoCEv2 Ethernet: Decentralized, Battle-Tested Clos / BGP
RoCEv2 runs on top of standard Layer 3 IP networking. It uses the exact same routing architecture that powers the global internet and hyperscale cloud datacenters: **BGP (Border Gateway Protocol) over a multi-stage Clos topology**.

* **How it Routes**:
  * Every leaf and spine switch runs its own independent BGP routing daemon.
  * Paths are computed dynamically using Equal-Cost Multi-Path (ECMP).
  * There is **no centralized controller** to crash or bottleneck the network.
* **Scalability**:
  * RoCEv2 fabrics routinely scale to **hundreds of thousands of endpoints** across multiple data halls (e.g., Meta’s 24,000+ GPU AI clusters, Microsoft Azure, and AWS).
* **Fault Isolation**:
  * Using **BFD (Bidirectional Forwarding Detection)**, switches detect a dead link in **under 10 milliseconds**.
  * The switch immediately removes that next-hop from its local ECMP group and routes traffic over alternate healthy spines without waiting for a central controller.

```
                    RoCEv2 DISTRIBUTED BGP / CLOS FABRIC
                    
         [Spine 1]               [Spine 2]               [Spine 3]
             ▲                       ▲                       ▲
             │       BGP ECMP        │       BGP ECMP        │
             └───────────────┬───────┴───────────────┬───────┘
                             ▼                       ▼
                        [Leaf 1]                [Leaf 2]
                             │                       │
                         [GPU Node]              [GPU Node]
      (Decentralized: Each switch makes autonomous routing decisions in hardware)
```

---

## Part 4: The Congestion Problem: Adaptive Routing vs. Packet Spraying

AI workloads create massive **"elephant flows"**—prolonged, full-bandwidth data transfers between pairs of GPUs. How each fabric distributes these flows across available network paths determines actual fabric efficiency.

### 4.1 InfiniBand: Hardware Adaptive Routing (AR)
InfiniBand handles elephant flows using proprietary **Adaptive Routing (AR)** built into Quantum switches:
* Switches monitor output buffer queues in real time.
* If a primary path is congested, the switch dynamically routes individual packets along alternate paths to the same destination.
* Because InfiniBand hardware guarantees ordered packet delivery or rapid reassembly at the receiving HCA, this achieves **~85% to 90% effective bisection utilization**.

### 4.2 RoCEv2: The Evolution to Dynamic Packet Spraying
Historically, standard Ethernet suffered from **ECMP hash collisions**:
* If two 400G elephant flows happened to hash to the same spine switch, that link became congested while parallel spine links sat completely empty.
* This caused buffer overflows, triggered PFC PAUSE storms, and gave early RoCEv1/RoCEv2 deployments a bad reputation.

**The Modern Solution: Dynamic Packet Spraying & Direct Load Balancing (DLB)**:
* Modern AI Ethernet switches (such as **Cisco Silicon One G200**, Broadcom Tomahawk 5, and NVIDIA Spectrum-4) do not rely on static 5-tuple hash ECMP.
* Instead, they perform **packet-level spraying**: they break elephant flows into individual packets and spray them round-robin across all available spine links.
* Smart NICs (like NVIDIA BlueField-3 SuperNICs or modern Broadcom/Intel adapters) reorder out-of-order packets in hardware at line rate before writing to GPU memory.
* **The Result**: Modern lossless RoCEv2 Ethernet matches or exceeds InfiniBand, achieving **up to 95% effective bisection bandwidth**.

```
                         PACKET SPRAYING LOAD BALANCING
                         
   LEGACY ECMP (Hash Collision - Hotspots):
   Flow A ──┐
            ├──► [Spine Link 1: OVERLOADED 800G] ──► Buffer Full / PFC Pause!
   Flow B ──┘
                 [Spine Link 2: IDLE 0G] ──────────► Wasted Bandwidth!

   MODERN DYNAMIC SPRAYING (Cisco Silicon One / Spectrum-X):
   Flow A ──┐    Packets 1, 3, 5 ──► [Spine Link 1: 50% Load]
            ├──►                                              ──► NIC Reorders in HW
   Flow B ──┘    Packets 2, 4, 6 ──► [Spine Link 2: 50% Load]
```

---

## Part 5: The Economics & Operational Realities (CapEx vs. OpEx)

When enterprise procurement teams and network architects sit at the decision table, the conversation quickly moves beyond pure packets to **supply chain, staffing, and total cost of ownership (TCO)**.

### 5.1 Single-Vendor Lock-In vs. Open Ecosystem

```
┌───────────────────────────────┬─────────────────────────────────────────────┐
│ METRIC                        │ INFINIBAND                                  │ RoCEv2 ETHERNET                 │
├───────────────────────────────┼─────────────────────────────────────────────┼─────────────────────────────────┤
│ Primary Silicon Supplier      │ NVIDIA (Mellanox) exclusively               │ Cisco, Broadcom, NVIDIA, Arista │
│ Optical Transceiver Standard  │ Proprietary LinkX optics / AOCs             │ Standard Multi-Vendor MSA optics│
│ Hardware Multi-Sourcing       │ Zero (100% single vendor)                   │ Complete (freedom of choice)    │
│ Supply Chain Risk             │ High (allocation quotas & single point)     │ Low (diverse global suppliers)  │
│ Pricing Power                 │ Premium (vendor dictates margins)           │ Competitive market pricing      │
└───────────────────────────────┴─────────────────────────────────────────────┴─────────────────────────────────┘
```

* **InfiniBand Lock-in**: If you buy an NVIDIA Quantum InfiniBand cluster, you must buy NVIDIA switches, NVIDIA HCAs, NVIDIA LinkX cables, and NVIDIA UFM licenses. If NVIDIA faces optical supply constraints or prioritizes hyperscaler allocations, your enterprise project waits.
* **RoCEv2 Openness**: With RoCEv2, an enterprise can buy **Cisco Nexus 9000 switches (Silicon One)**, connect them with third-party QSFP-DD/OSFP optical transceivers, and connect servers equipped with NVIDIA ConnectX, Broadcom Thor, or Intel NICs.

---

### 5.2 Network Operations & Staffing
* **The InfiniBand Silo**: 
  * Enterprise network engineering teams do not know InfiniBand. They have spent decades mastering BGP, OSPF, EVPN, Wireshark, SNMP, and Python network automation for Ethernet.
  * Operating InfiniBand requires hiring specialized HPC administrators or buying expensive managed services to troubleshoot `ibnetdiscover`, `ibdiagnet`, and Subnet Manager failures.
* **The RoCEv2 Advantage**:
  * RoCEv2 is Ethernet. It uses the same cabling, the same patch panels, the same optical test equipment, the same syslog collectors, and the same network observability tools (Splunk, Datadog, Prometheus, gNMI streaming telemetry) already running in the enterprise NOC.

---

## Part 6: The Frontier: Ultra Ethernet Consortium (UEC)

While RoCEv2 solved the multi-vendor and scalability issues of InfiniBand, network engineers acknowledge that **RoCEv2 is still an adaptation**: it wraps an old HPC protocol inside UDP, relying on fragile PFC pause frames to prevent packet loss.

To build the permanent, open standard for the next 20 years of AI supercomputing, the industry formed the **Ultra Ethernet Consortium (UEC)** in 2023 under the Linux Foundation.

### Who is in the UEC?
Founding and steering members include: **Cisco, AMD, Intel, Meta, Microsoft, Google, Broadcom, Arista, and Hewlett Packard Enterprise**.

### What UEC Solves:
1. **Goodbye PFC**: UEC eliminates the need for Priority Flow Control (PFC) PAUSE frames, eliminating the risk of PFC deadlocks and buffer pauses entirely.
2. **Native Packet Spraying**: UEC mandates out-of-order packet delivery and hardware reassembly as a native Layer 3/4 standard.
3. **Ultra Ethernet Transport (UET)**: A brand-new transport protocol designed from scratch for AI collectives, featuring sub-microsecond congestion feedback, multipath routing, and selective packet retransmission.
4. **Result**: UEC gives enterprises all the zero-loss, ultra-low latency benefits of InfiniBand with the infinite scale, multi-vendor competition, and lower cost of Ethernet.

```
                    THE EVOLUTION OF AI BACKEND FABRICS
                    
   1999 - 2020: HPC Dominance
   ┌────────────────────────────────────────────────────────────────────────┐
   │ InfiniBand: Dedicated, proprietary, credit-based HPC supercomputing.  │
   └───────────────────────────────────┬────────────────────────────────────┘
                                       ▼
   2020 - 2025: The Bridge Era
   ┌────────────────────────────────────────────────────────────────────────┐
   │ Lossless RoCEv2: RDMA over Ethernet using PFC & ECN + Dynamic Spraying.│
   │ (Powers modern Meta, Microsoft, and Cisco enterprise AI clusters).     │
   └───────────────────────────────────┬────────────────────────────────────┘
                                       ▼
   2026+: The Unified Open Future
   ┌────────────────────────────────────────────────────────────────────────┐
   │ Ultra Ethernet Consortium (UEC): Native AI transport, no PFC,         │
   │ hardware out-of-order delivery, open multi-vendor hyperscale fabric.   │
   └────────────────────────────────────────────────────────────────────────┘
```

---

## Part 7: Comprehensive Head-to-Head Comparison Matrix

| Architectural Dimension | NVIDIA InfiniBand (Quantum-2 / X800) | RoCEv2 Lossless Ethernet (Cisco / Spectrum-X) | Ultra Ethernet Consortium (UEC Future) |
| :--- | :--- | :--- | :--- |
| **Physical Reach** | ~100 meters (data hall / short reach) | 100m - 10km+ (campus & metro reach) | 100m - 10km+ |
| **Per-Hop Latency** | ~130 – 200 ns | ~400 – 800 ns | ~400 – 600 ns |
| **Flow Control** | Hardware Link Credits (Zero-loss native) | PFC (802.1Qbb) + ECN / DCQCN | Native Congestion Control (No PFC) |
| **Routing Protocol** | Centralized SDN (Subnet Manager) | Distributed BGP / Clos ECMP | Distributed IP Clos Fabric |
| **Maximum Fabric Scale** | ~40,000 nodes per subnet | 100,000+ nodes (unlimited) | 1,000,000+ nodes |
| **Link Failure Recovery** | Centralized recomputation (slow, global) | BFD sub-10ms local failover | Native fast multipath re-routing |
| **Load Balancing** | Hardware Adaptive Routing (Proprietary) | Dynamic Packet Spraying / DLB | Native per-packet multi-pathing |
| **Vendor Ecosystem** | 100% Single Vendor (NVIDIA) | Multi-vendor (Cisco, Broadcom, Arista) | Open multi-vendor industry standard |
| **NetOps Skillset** | Specialized HPC administrators | Existing Enterprise Network Engineers | Existing Enterprise Network Engineers |
| **Relative CapEx** | High premium (optics + proprietary silicon) | 20% – 40% lower cost per port | Competitive open market pricing |

---

## Part 8: Architect’s Decision Framework & Customer Playbook

When advising enterprise customers on whether to deploy InfiniBand or RoCEv2 Ethernet, use this decision framework:

```
                            FABRIC SELECTION TREE
                                      │
                     [ Is this a standalone, turnkey ]
                     [ AI pod with < 1,000 GPUs and  ]
                     [ dedicated HPC administration? ]
                                     / \
                               YES  /   \  NO
                                   /     \
                                  ▼       ▼
                       [ Choose INFINIBAND ]  [ Does the customer value multi-vendor ]
                       • Fast turnkey deploy  [ freedom, existing NetOps tooling, or  ]
                       • Zero tuning needed   [ scaling to > 10,000 GPUs / campus?   ]
                       • Pure NVIDIA DGX pod                                 │
                                                                             ▼ YES
                                                                   [ Choose RoCEv2 ETHERNET ]
                                                                   • Cisco Silicon One / Nexus
                                                                   • NVIDIA Spectrum-X
                                                                   • Lower TCO & open supply
                                                                   • Future-proof to UEC
```

---

### Technical Field Cheat Sheet: Handling Customer Objections

#### Objection 1: *"Isn't InfiniBand always faster than Ethernet for AI?"*
* **The Reality**: In synthetic micro-benchmarks measuring single-hop ping latency, InfiniBand's ~150 ns latency beats Ethernet's ~500 ns latency. However, in **real-world distributed LLM training** (where GPUs spend milliseconds performing matrix math between synchronization barriers), network transmission represents less than 10-15% of total iteration time. With modern dynamic packet spraying (DLB), RoCEv2 achieves **95%+ fabric utilization**, matching InfiniBand training completion times within 1-3% while offering significantly higher overall network scale.

#### Objection 2: *"I heard RoCEv2 is a nightmare to configure and prone to PFC deadlocks."*
* **The Reality**: In 2018, manual PFC/ECN buffer tuning on early 25G/100G switches was difficult and fragile. Modern AI fabrics (such as Cisco Nexus HyperFabric, Cisco Silicon One G200, and NVIDIA Spectrum-X) feature **automated AI fabric profiles** and dynamic load balancing. Switches and NICs automatically negotiate congestion thresholds, dynamic buffers, and DCQCN parameters out of the box, eliminating manual tuning and preventing PFC deadlocks.

#### Objection 3: *"Why did Meta build their giant 24,000-GPU AI cluster on RoCEv2 instead of InfiniBand?"*
* **The Reality**: Hyperscalers operate at scales where single-vendor lock-in is unacceptable. Meta built their 24,000-GPU clusters (and subsequent 64,000-GPU clusters) using RoCEv2 on standard Ethernet switches (Arista and Minipack whiteboxes) because:
  1. It scales seamlessly across standard data center topologies.
  2. Their existing site reliability and network engineering teams can monitor and manage it with standard Linux and BGP tools.
  3. It allows multi-sourcing silicon and optics, reducing capital expenditures by hundreds of millions of dollars.

---

## Related Knowledge & Architecture Links
- **Master Knowledge Hub**: [[spaces/ai-kb/notes/overview|AI-KB Overview]]
- **Lossless Networking Guide**: [[spaces/ai-kb/notes/lossless-networking-in-gpu-nodes|Lossless Networking in GPU Nodes: The Complete Plain-English Guide]]
- **Scale-Up vs. Scale-Out**: [[spaces/ai-kb/notes/scale-out-nvlink-and-ethernet-guide|Scale-Up vs. Scale-Out: NVLink & Ethernet]]
- **Cisco AI Portfolio**: [[spaces/ai-kb/notes/cisco-ai-solutions-architect-guide|Cisco AI Solutions Architecture]]
- **Cluster Sizing Guide**: [[spaces/ai-kb/notes/gpu-node-and-cluster-sizing-guide|GPU Node & Cluster Sizing Guide]]
- **Reference Topology**: [[spaces/ai-kb/architecture/ai_cluster_topology|AI Cluster & Lossless Fabric Topology]]
- **Architectural Decision Record**: [[spaces/ai-kb/decisions/adr_002_rocev2_lossless_ethernet|ADR 002: Lossless RoCEv2 with PFC & ECN]]
