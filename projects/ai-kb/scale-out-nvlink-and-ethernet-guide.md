# Scale-Up vs. Scale-Out: NVLink, NVSwitch, and Ethernet in GPU Clusters
> **Space**: `ai-kb`  
> **Target Audience**: Infrastructure Architects, Network Engineers, SREs, and Technical Consultants  
> **Related Architecture**: [[spaces/ai-kb/architecture/ai_cluster_topology|AI Cluster & Lossless Fabric Topology]]  
> **Related Guides**: [[spaces/ai-kb/notes/lossless-networking-in-gpu-nodes|Lossless Networking in GPU Nodes]], [[spaces/ai-kb/notes/gpu-node-and-cluster-sizing-guide|GPU Sizing Guide]]

---

## Executive Summary: The #1 Misconception in AI Fabrics

When enterprise infrastructure teams begin planning multi-million-dollar GPU clusters, one question inevitably arises:

> *"NVLink is blazingly fast, and Ethernet is open and everywhere in our datacenter. Can we just scale out NVLink over our 400G/800G Ethernet network?"*

In plain English: **No. You cannot run NVLink over Ethernet.** 

NVLink and Ethernet are not competing standards that can be wrapped inside each other. They operate on **two fundamentally incompatible physical and mathematical principles**:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         THE TWO INTERCONNECT WORLDS                         │
├─────────────────────────────────────────────────────────────────────────────┤
│                    SCALE-UP: NVLink & NVSwitch Fabric                       │
│                        (The Shared Brain / Memory)                          │
│  • Nature: Memory-semantic (Direct CPU/GPU Load/Store instructions)         │
│  • Speed: Terabytes per second (1.8 TB/s per GPU on Blackwell)              │
│  • Latency: Nanoseconds (~100 ns)                                           │
│  • Reach: Inches to meters (copper backplanes within a chassis or rack)     │
├─────────────────────────────────────────────────────────────────────────────┤
│                    SCALE-OUT: Ethernet (RoCEv2 / Spectrum-X)                │
│                        (The Postal Network / Messaging)                     │
│  • Nature: Message-passing (Packets, headers, IP routing, RDMA)             │
│  • Speed: Gigabits per second (400G - 800G per link = 50 - 100 GB/s)       │
│  • Latency: Microseconds (~1.5 - 3 μs = 15x to 30x slower than NVLink)      │
│  • Reach: Hundreds of meters across data center rows and buildings          │
└─────────────────────────────────────────────────────────────────────────────┘
```

Modern AI supercomputers do not choose between NVLink and Ethernet—**they use both in a synchronized two-tier hierarchy**.

---

## Part 1: Why NVLink Cannot Run Over Ethernet

To understand why NVLink cannot be extended across traditional Ethernet cables, we must examine what NVLink actually does at the silicon level.

### 1.1 Memory Semantics vs. Message Passing
* **How Ethernet Works (Message Passing)**: When Server A sends data to Server B over Ethernet, it chops the data into discrete packets, attaches IP and MAC headers, sends them through switches, checks for errors, and reassembles them on arrival. Even with high-speed RDMA, it operates on *buffers and messages*.
* **How NVLink Works (Memory Semantics)**: NVLink is not a messaging protocol. It is an extension of the GPU's internal **memory bus**. 
  * When GPU 1 wants data sitting in GPU 2's High Bandwidth Memory (VRAM), GPU 1 simply issues a standard assembly-level instruction: `LOAD` from address `0x7FFF...`.
  * The NVSwitch routes the electrical signal directly into GPU 2's memory controller.
  * GPU 1 reads GPU 2's VRAM as if it were its own local memory (**Unified Shared Memory**).

```
                      MEMORY ACCESS vs. PACKET TRANSIT
                      
  NVLINK MEMORY SEMANTICS (Direct Memory Access):
  [GPU 1 Core] ════(NVLink: ~100 nanoseconds)════► [GPU 2 VRAM]
               (Load / Store: No Packets, No Headers)
               
  ETHERNET MESSAGE PASSING (Packet Transit):
  [GPU 1 Core] ──► [NIC] ──► [Packet Headers] ──► [Switch] ──► [NIC] ──► [GPU 2]
               (Packet Routing: ~1,500 - 3,000 nanoseconds)
```

---

### 1.2 The Latency & Physical Distance Wall
* **The Speed of Light in Copper/Fiber**: Electrical signals travel roughly 8 inches per nanosecond.
* NVLink operates at **nanosecond latency (~100 ns)**. If an NVLink cable were stretched across a data center room (100 meters), the speed-of-light travel delay alone would exceed 500 nanoseconds—destroying the memory synchronization of the GPU cores.
* **The Copper Constraint**: Driving signals at terabytes-per-second frequencies without burning massive power requires passive copper traces. This limits native high-density NVLink to **under 2 to 3 meters** (inside a single server chassis or a specialized rack backplane).

---

## Part 2: NVIDIA’s Evolution: From NVLink Chassis to NVLink Rack

Historically, NVLink was confined to a single 8-GPU server (e.g. HGX A100 or HGX H100). Inside that box, 8 GPUs communicated over NVLink, while everything outside used Ethernet or InfiniBand.

With the **NVIDIA Blackwell (GB200)** generation, NVIDIA expanded the Scale-Up domain by an entire order of magnitude:

```
                      THE SCALE-UP BOUNDARY EXPANSION
                      
  HOPPER GENERATION (HGX H100):
  ┌────────────────────────────────────────┐
  │ 8-GPU NVLink Domain (Single Chassis)   │  <=== Scale-Out Ethernet starts here!
  └────────────────────────────────────────┘
  
  BLACKWELL GENERATION (GB200 NVL72):
  ┌────────────────────────────────────────────────────────────────────────┐
  │ 72-GPU NVLink Domain (Entire Liquid-Cooled Rack via Copper Spine)       │
  │ • 130 TB/s Bisection Bandwidth across 72 GPUs                          │  <=== Scale-Out Ethernet
  │ • Entire rack acts as ONE giant logical GPU with 13.5 TB of VRAM!      │       starts outside
  └────────────────────────────────────────────────────────────────────────┘       the rack!
```

### The GB200 NVL72 Rack Architecture
* **The Problem**: Frontier models like Llama-3-405B exceed the memory of an 8-GPU server. In the past, sharding a 405B model across servers required splitting it over Ethernet, incurring high latency penalties.
* **The Solution**: The GB200 NVL72 packs **36 Grace CPUs and 72 Blackwell GPUs** into a single 42U rack.
* **The Passive Copper Spine**: Rather than optical cables or network switches, the rear of the rack features a massive **liquid-cooled copper backplane with over 2 miles of high-density copper cabling** connecting 9 NVSwitch trays.
* **The Result**: 72 GPUs communicate over **NVLink 5 at 1.8 TB/s per GPU**, creating a single unified pool with **13.5 TB of fast VRAM** operating at **130 TB/s aggregate bandwidth**. A 405-billion parameter model fits entirely within the NVLink domain of a single rack!

---

## Part 3: The Scale-Out Layer: Ethernet for AI (Spectrum-X & Cisco Silicon One)

Once you reach the boundary of the NVLink rack (72 GPUs), you **must** transition to an open Scale-Out network fabric to connect dozens or hundreds of racks together into an "AI Factory" of 10,000 to 100,000 GPUs.

This is where **Lossless Ethernet (Spectrum-X / Cisco Silicon One RoCEv2)** comes in.

```
                           THE TWO-TIER HYBRID FABRIC
┌─────────────────────────────────────────────────────────────────────────────┐
│                     TIER 2: SCALE-OUT FABRIC (ETHERNET)                     │
│   Connecting Racks across the Data Center via 400G/800G Lossless RoCEv2     │
│   • Cisco Nexus 9000 (Silicon One G200) or NVIDIA Spectrum-4 Switches       │
│   • BlueField-3 / SuperNICs handling dynamic packet spraying                │
│   • Carries Data Parallelism (DP) and Pipeline Parallelism (PP)             │
├─────────────────────────────────────────────────────────────────────────────┤
│                                      ▲                                      │
│                                      │ (Transitions at SuperNIC / Gateway)  │
│                                      ▼                                      │
├─────────────────────────────────────────────────────────────────────────────┤
│                      TIER 1: SCALE-UP FABRIC (NVLINK)                       │
│   Connecting GPUs within the Rack over NVSwitch Copper Spine                │
│   • 1.8 TB/s per GPU; sub-microsecond shared memory access                  │
│   • Carries Tensor Parallelism (TP) and MoE Expert All-to-All               │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

### 3.1 What is NVIDIA Spectrum-X?
Enterprises often ask: *"If we can't run NVLink over Ethernet, what is NVIDIA Spectrum-X?"*

**Spectrum-X is NOT NVLink over Ethernet.** It is **customized, high-performance RoCEv2 Ethernet** designed to eliminate standard Ethernet's inefficiencies:
1. **Spectrum-4 Ethernet Switches**: 51.2 Tbps standard Ethernet switches with custom congestion management silicon.
2. **BlueField-3 SuperNICs**: AI network adapters placed inside GPU servers that synchronize directly with the switches.
3. **Dynamic Packet Spraying**: Breaks elephant flows across all spine paths to prevent hash collisions (as covered in [[spaces/ai-kb/notes/lossless-networking-in-gpu-nodes|Lossless Networking]]).
4. **Fast Telemetry Congestion Control**: Hardware-level nanosecond loop that throttles senders before packet loss occurs, achieving **95% effective Ethernet fabric utilization** (compared to ~60% on standard unmanaged Ethernet).

---

## Part 4: How Software Orchestrates Both Networks (NCCL)

How does a machine learning framework like [[spaces/ai-kb/notes/pytorch-in-the-real-world|PyTorch]] know when to use NVLink versus Ethernet?

The magic happens in **NCCL (NVIDIA Collective Communications Library)**, pronounced *"Nickel"*:

```
                              NCCL FABRIC AWARENESS
                                        │
                 ┌──────────────────────┴──────────────────────┐
                 ▼                                             ▼
       [ Is the target GPU inside                    [ Is the target GPU in
          the same NVLink domain? ]                     a different rack? ]
                 │                                             │
                 ▼ YES                                         ▼ NO
       Use NVLINK MEMORY BUS                         Use SCALE-OUT ETHERNET
       • Zero-copy Load/Store                        • RoCEv2 / Spectrum-X
       • Tensor Parallelism (TP)                     • Pipeline Parallelism (PP)
       • Latency: ~100 nanoseconds                   • Data Parallelism (DP)
                                                     • Latency: ~2 microseconds
```

### Distributed Sizing Mapping:
1. **Tensor Parallelism ($\text{TP}$)**: Slices individual matrix math across GPUs on every single token. Sized strictly within the **Scale-Up NVLink domain** ($\text{TP}=4$ or $\text{TP}=8$ in a server, or up to $\text{TP}=72$ in an NVL72).
2. **Pipeline Parallelism ($\text{PP}$)**: Slices layers between physical server chassis. Sized across the **Scale-Out Ethernet fabric**.
3. **Data Parallelism ($\text{DP}$)**: Slices batches of independent user prompts across different nodes. Operates across the **Scale-Out Ethernet fabric**.

---

## Part 5: The Open Counter-Movement: UALink vs. NVLink

Because NVLink is 100% proprietary to NVIDIA, the rest of the technology industry has rallied to create an open alternative.

### UALink (Ultra Accelerator Link)
Formed in May 2024 by **AMD, Intel, Google, Cisco, Meta, Microsoft, Broadcom, and Hewlett Packard Enterprise**:
* **The Goal**: An open-standard, scale-up interconnect that provides memory-semantic, load/store communication between AI accelerators (GPUs, TPUs, custom ASICs) at terabytes-per-second bandwidth.
* **The Relationship to Ultra Ethernet (UEC)**:
  * **UALink** is the open alternative to **NVLink** (Scale-Up intra-chassis/intra-rack).
  * **UEC (Ultra Ethernet)** is the open alternative to **Spectrum-X / InfiniBand** (Scale-Out inter-rack fabric).

```
                 THE PROPRIETARY vs. OPEN AI FABRIC STACK
┌───────────────────────────────┬─────────────────────────────────────────────┐
│ LAYER                         │ PROPRIETARY NVIDIA STACK │ OPEN STANDARDS STACK            │
├───────────────────────────────┼──────────────────────────┼─────────────────────────────────┤
│ SCALE-UP (Intra-Rack Memory)  │ NVLink 5 + NVSwitch      │ UALink (Ultra Accelerator Link) │
│ SCALE-OUT (Inter-Rack Fabric) │ Spectrum-X / InfiniBand  │ Ultra Ethernet Consortium (UEC) │
│ PHYSICAL SWITCH SILICON       │ NVIDIA Spectrum-4        │ Cisco Silicon One G200 / Tomahawk│
└───────────────────────────────┴──────────────────────────┴─────────────────────────────────┘
```

---

## Part 6: Architect’s Decision Checklist & Customer Responses

When advising customers on interconnect architecture, use these clear rules of thumb:

| Customer Question / Proposal | Architectural Reality & Technical Advisor Response |
| :--- | :--- |
| **"Can we extend NVLink across our campus or across switches?"** | *"No. NVLink operates on nanosecond memory semantics limited to a few meters of copper. Beyond a single chassis or NVL72 rack, all inter-node communication must transition to a Scale-Out RoCEv2 Ethernet or InfiniBand network."* |
| **"Is 400G Ethernet too slow for AI?"** | *"Ethernet is too slow for Tensor Parallelism (which requires terabytes/sec NVLink memory access), but it is ideal for Pipeline and Data Parallelism. Using Cisco Silicon One or Spectrum-X with RoCEv2 gives you near-line-rate scale-out performance over open, standards-based network fabrics."* |
| **"Why is the GB200 NVL72 rack so revolutionary?"** | *"Because it pushes the NVLink scale-up boundary from 8 GPUs to 72 GPUs. It allows massive frontier models (like Llama-3-405B) to run entirely inside one shared memory domain without ever touching slower network switches."* |

---

## Related Knowledge & Architecture Links
- **Knowledge Hub**: [[spaces/ai-kb/notes/overview|AI-KB Overview]]
- **Lossless Ethernet**: [[spaces/ai-kb/notes/lossless-networking-in-gpu-nodes|Lossless Networking in GPU Nodes]]
- **Cluster Sizing**: [[spaces/ai-kb/notes/gpu-node-and-cluster-sizing-guide|GPU Node & Cluster Sizing Guide]]
- **Cisco Fabric Portfolio**: [[spaces/ai-kb/notes/cisco-ai-solutions-architect-guide|Cisco AI Solutions Architecture]]
- **Reference Topology**: [[spaces/ai-kb/architecture/ai_cluster_topology|AI Cluster & Lossless Fabric Topology]]
- **Architectural Decision**: [[spaces/ai-kb/decisions/adr_002_rocev2_lossless_ethernet|ADR 002: Lossless RoCEv2 with PFC & ECN]]
