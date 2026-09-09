# Real-World Architecture Walkthrough: Meta's 24,000-GPU Llama 3 Infrastructure
> **Space**: `ai-kb`  
> **Target Audience**: Infrastructure Architects, Network Engineers, Enterprise CTOs, and AI Systems Engineers  
> **Related Architecture**: [[spaces/ai-kb/architecture/ai_cluster_topology|AI Cluster & Lossless Fabric Topology]]  
> **Foundational Guides**: 
> - [[spaces/ai-kb/notes/lossless-networking-in-gpu-nodes|Lossless Networking in GPU Nodes]]
> - [[spaces/ai-kb/notes/rocev2-vs-infiniband-guide|RoCEv2 vs. InfiniBand Showdown]]
> - [[spaces/ai-kb/notes/scale-out-nvlink-and-ethernet-guide|Scale-Up vs. Scale-Out (NVLink & Ethernet)]]
> - [[spaces/ai-kb/notes/gpu-node-and-cluster-sizing-guide|GPU Node & Cluster Sizing Guide]]
> - [[spaces/ai-kb/notes/pytorch-in-the-real-world|PyTorch in the Real World]]
> - [[spaces/ai-kb/notes/vllm-and-llmd-explained|vLLM & LLM-D Deep Dive]]
> - [[spaces/ai-kb/notes/cisco-ai-solutions-architect-guide|Cisco AI Solutions Architecture]]  
> **Architectural Decisions**: [[spaces/ai-kb/decisions/adr_001_vllm_llmd_inference_standard|ADR 001: vLLM & LLM-D]], [[spaces/ai-kb/decisions/adr_002_rocev2_lossless_ethernet|ADR 002: Lossless RoCEv2]]

---

## Executive Summary: The AI Supercomputer in Plain English

When learning artificial intelligence infrastructure, terms like **RoCEv2, NVLink, GQA KV caching, PFC pause frames, PyTorch 4D parallelism, and GPU memory bandwidth** can easily feel like alphabet soup.

To see how all of these pieces snap together into a living, breathing supercomputer, we look at the most documented, large-scale AI engineering project in history: **Meta's infrastructure built to train and serve Llama 3 (including the massive 405-billion parameter model)**.

To picture this system in plain English, imagine an **Industrial Megacity Factory**:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                   THE MEGACITY AI FACTORY ANALOGY                           │
├─────────────────────────────────────────────────────────────────────────────┤
│  1. THE WORKBENCHES (Grand Teton 8-GPU Servers):                            │
│     • Inside each server, 8 workers (GPUs) sit with their elbows touching   │
│       at the same desk.                                                     │
│     • They communicate by whispering across copper traces in nanoseconds    │
│       (Scale-Up NVLink).                                                    │
│                                                                             │
│  2. THE MONORAIL SYSTEM (Scale-Out Network Fabric):                         │
│     • Thousands of workbenches are scattered across a giant warehouse.      │
│     • High-speed monorails (400G RoCEv2 Ethernet or InfiniBand) shuttle     │
│       parts between workbenches at 3.2 Terabits per second per desk.        │
│                                                                             │
│  3. THE ASSEMBLY LINE SUPERVISOR (PyTorch 4D Parallelism):                  │
│     • Orchestrates 24,000 workers so nobody waits with empty hands.         │
│     • Slices math across desks (Tensor Parallelism), down assembly lines    │
│       (Pipeline Parallelism), and shares notes (FSDP).                      │
│                                                                             │
│  4. THE EXPRESS BACKUP SYSTEM (GPUDirect Storage):                          │
│     • Every 2 hours, the factory must save a blueprint of everything built. │
│     • Instead of carrying boxes through hallways, workers beam blueprints   │
│       directly into subterranean flash vaults in 45 seconds flat.           │
└─────────────────────────────────────────────────────────────────────────────┘
```

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                   META'S LLAMA 3 INFRASTRUCTURE AT A GLANCE                 │
├─────────────────────────────────────────────────────────────────────────────┤
│  • Scale: Two distinct clusters of 24,576 NVIDIA H100 GPUs (49,152 total).  │
│  • Compute Servers: OCP "Grand Teton" (8x H100 SXM5 per node).              │
│  • Scale-Up Fabric: 900 GB/s NVLink 4 mesh inside each Grand Teton node.    │
│  • Scale-Out Fabric: The Great Showdown — One 24k cluster on InfiniBand;    │
│    One 24k cluster on Lossless RoCEv2 Ethernet (Arista 7800 + Minipack2).   │
│  • Network Bandwidth: 8x 400G NICs per server = 3.2 Terabits/sec per node!   │
│  • Software Orchestration: PyTorch 4D Parallelism (TP + PP + CP + FSDP).    │
│  • Storage: Tectonic distributed storage over GPUDirect RDMA.               │
│  • Serving: Disaggregated inference fleet running GQA and vLLM.             │
└─────────────────────────────────────────────────────────────────────────────┘
```

This walkthrough follows an AI tensor from the silicon inside an 8-GPU chassis, across the network fabric, through distributed PyTorch algorithms, and into production user serving.

---

## Layer 1: The Compute Node — OCP "Grand Teton" (Scale-Up NVLink)

At the physical foundation of the cluster is Meta's Open Compute Project (OCP) server platform, named **Grand Teton**.

```
                        THE GRAND TETON SERVER CHASSIS
┌─────────────────────────────────────────────────────────────────────────────┐
│                           2x Intel Sapphire Rapids CPUs                     │
│                        2.0 TB Host System Memory (DDR5)                     │
├─────────────────────────────────────────────────────────────────────────────┤
│                       8x NVIDIA H100 SXM5 GPUs (80GB each)                  │
│                     Total Node VRAM: 640 GB HBM3 Memory                     │
│                   Memory Bandwidth: 3.35 TB/s per GPU                       │
├─────────────────────────────────────────────────────────────────────────────┤
│             NVSWITCH FABRIC (Scale-Up Domain: ~100ns Latency)               │
│     All 8 GPUs fully interconnected via NVLink 4 at 900 GB/s per GPU        │
│          Total Bisection Bandwidth: 3.2 TB/s inside the chassis             │
├─────────────────────────────────────────────────────────────────────────────┤
│                 SCALE-OUT NETWORK HANDOFF (8x 400G NICs)                    │
│      Every single GPU has its own dedicated 400 Gbps OSFP network port!     │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Grand Teton in Plain English:
Think of an AI server like a specialized racing truck:
* **The Cabin**: You have 2 standard Intel CPUs that run Linux, manage files, and steer the vehicle.
* **The Jet Thrusters**: In the back, you have **8 massive NVIDIA H100 GPUs** doing all the heavy computational work.
* **The Shared Brain (NVLink)**: As explained in [[spaces/ai-kb/notes/scale-out-nvlink-and-ethernet-guide|Scale-Up vs. Scale-Out]], the 8 GPUs do not talk to each other over network cards. They communicate across an internal copper motherboard called **NVSwitch**. 
  * Each GPU reads data from its neighbor's memory in **sub-100 nanoseconds** at **900 Gigabytes per second** (over 7x faster than the fastest network cable!).
  * To the software, all 8 GPUs behave like **one giant unified GPU with 640 GB of VRAM**.

### How Much Data is 3.2 Terabits per Second?
Grand Teton doesn't make all 8 GPUs squeeze their data through one shared network card:
* **Each GPU gets its own dedicated 400 Gbps network adapter (NIC)**.
* That means a single server pulls **3.2 Terabits per second ($3,200 \text{ Gbps}$)** of network throughput.
* *In plain English*: A single Grand Teton server can stream roughly **80,000 simultaneous 4K Netflix movies across the data center network at the exact same instant!**

---

## Layer 2: The Great Scale-Out Network Showdown (24k RoCEv2 vs. 24k InfiniBand)

When Meta designed this cluster, conventional wisdom in high-performance computing said:
> *"You cannot train frontier AI models on Ethernet. You must buy NVIDIA's proprietary InfiniBand network."*

Meta decided to settle the debate once and for all: **they built TWO identical 24,576-GPU superclusters side-by-side**:
* **Cluster A**: Built with **NVIDIA Quantum-2 InfiniBand**.
* **Cluster B**: Built with **Lossless RoCEv2 Ethernet** (using Arista 7800 chassis switches and Meta Minipack2 switches).

```
                  THE TWO 24,000-GPU CLUSTER DESIGNS
                  
   CLUSTER A: 24,576 GPUs on InfiniBand       CLUSTER B: 24,576 GPUs on RoCEv2 Ethernet
   ┌────────────────────────────────────┐     ┌────────────────────────────────────────┐
   │ • NVIDIA Quantum-2 QM9700 Switches │     │ • Arista 7800 & Minipack2 Switches     │
   │ • Centralized Subnet Manager (UFM) │     │ • Decentralized BGP Clos Topology      │
   │ • Hardware Credit Flow Control     │     │ • PFC (Priority 3) + DCQCN Congestion  │
   │ • NVIDIA Adaptive Routing (AR)     │     │ • Dynamic Packet Spraying & DLB        │
   │ • Proprietary single-vendor stack  │     │ • Open multi-vendor ecosystem          │
   └────────────────────────────────────┘     └────────────────────────────────────────┘
```

### 2.1 How Meta Tamed RoCEv2 Ethernet in Plain English
As detailed in [[spaces/ai-kb/notes/rocev2-vs-infiniband-guide|RoCEv2 vs. InfiniBand Showdown]] and [[spaces/ai-kb/notes/lossless-networking-in-gpu-nodes|Lossless Networking]], standard Ethernet casually drops packets when buffers get full. In distributed AI, **a single dropped packet freezes the entire 24,000-GPU cluster**.

Meta solved this using three plain-English network mechanisms:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    THE THREE ETHERNET STABILIZERS                           │
├─────────────────────────────────────────────────────────────────────────────┤
│  1. PFC (The Emergency Brakes):                                             │
│     • Isolates AI traffic on a VIP lane (Priority 3).                       │
│     • If a switch buffer fills up, it sends an emergency PAUSE frame to the │
│       sender: "Stop sending for 5 microseconds!"                            │
├─────────────────────────────────────────────────────────────────────────────┤
│  2. ECN / DCQCN (The Yellow Caution Lights):                                │
│     • Emergency brakes (PFC) cause traffic pileups if used too often.       │
│     • Instead, when a switch queue reaches just 20% capacity, it marks a    │
│       little warning sticker on passing packets.                            │
│     • When the receiving GPU sees the sticker, it tells the sender to       │
│       smoothly ease off the gas pedal BEFORE the emergency brakes trigger.  │
├─────────────────────────────────────────────────────────────────────────────┤
│  3. DYNAMIC PACKET SPRAYING (Opening All 10 Bridges):                       │
│     • Normally, Ethernet sends all data for one conversation down a single  │
│       wire (ECMP hashing), causing massive traffic jams.                    │
│     • Dynamic spraying chops the data into individual packets and sprays    │
│       them evenly across hundreds of parallel spine links, reassembling     │
│       them in hardware at the receiving GPU.                                │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 2.2 The Production Verdict: Who Won?
* **Training Completion Time**: The 24,000-GPU RoCEv2 Ethernet cluster finished training runs with **identical throughput and speed as the InfiniBand cluster (<1-2% difference)**.
* **The Strategic Victory**: Meta proved that enterprises do not have to be locked into NVIDIA's proprietary network hardware. RoCEv2 allowed Meta to use standard optical cables, open switches, and their existing enterprise network operations team—**saving hundreds of millions of dollars**.

---

## Layer 3: Distributed Software Stack — PyTorch 4D Parallelism

How do you train a 405-billion parameter model across 24,000 GPUs at once?

You cannot fit 405B on one GPU. You cannot even fit it on one 8-GPU Grand Teton server (which has 640 GB VRAM, while 405B training requires ~810 GB for weights plus 1.6 TB for optimizer states!).

Meta used **PyTorch** (covered in [[spaces/ai-kb/notes/pytorch-in-the-real-world|PyTorch in the Real World]]) to implement **4D Parallelism**:

```
                       THE 4D PARALLELISM MATRIX
┌─────────────────────────────────────────────────────────────────────────────┐
│ 1. TENSOR PARALLELISM (TP = 8) - The Operating Room Surgeons                │
│    • Slices individual matrix multiplications across GPUs.                  │
│    • Communication: Ultra-fast, nanosecond synchronization.                 │
│    • Physical Rule: Locked strictly to NVLINK inside the Grand Teton box.   │
├─────────────────────────────────────────────────────────────────────────────┤
│ 2. PIPELINE PARALLELISM (PP = 16) - The Factory Assembly Line               │
│    • Slices the 126 transformer layers into 16 sequential chunks.           │
│    • Communication: Only passes data at layer boundaries.                   │
│    • Physical Rule: Runs across physical servers over 400G RoCEv2 Ethernet. │
├─────────────────────────────────────────────────────────────────────────────┤
│ 3. CONTEXT PARALLELISM (CP = 4 or 8) - The Book Club Readers                │
│    • Slices massive 128,000-token documents across multiple servers.        │
│    • Prevents KV cache from overflowing any single GPU's memory.            │
├─────────────────────────────────────────────────────────────────────────────┤
│ 4. FULLY SHARDED DATA PARALLELISM (FSDP / ZeRO-3) - The Shared Ledger       │
│    • Instead of every worker keeping a full 2TB copy of optimizer notes,    │
│      they split the notes across all 24,000 GPUs and share on demand.       │
└─────────────────────────────────────────────────────────────────────────────┘
```

```
                   HOW A TENSOR FLOWS THROUGH THE FLEET
                   
    [Prompt: 128k Tokens]
              │
              ▼
   [ Context Parallelism (CP) ] ──► Slices the 128k sequence across 4 servers
              │
              ▼
   [ Pipeline Parallelism (PP) ] ──► Passes activations: Server 1 ──(RoCEv2)──► Server 2
              │
              ▼
   [ Tensor Parallelism (TP=8) ] ──► 8 GPUs in Server 1 compute layer math over NVLink
              │
              ▼
   [ FSDP / All-Reduce ] ──────────► Syncs gradients across all 24k GPUs over Network
```

### The NCCL Traffic Director in Plain English:
Underneath PyTorch is **NCCL (NVIDIA Collective Communications Library)**:
* When PyTorch needs to do a **Tensor Parallel** matrix multiply, NCCL automatically routes the data across **NVLink copper**.
* When PyTorch needs to pass data between **Pipeline stages** or sync gradients across the cluster via **FSDP**, NCCL opens raw RDMA connections over the **400G RoCEv2 Ethernet network**.

---

## Layer 4: Storage, Checkpointing & GPUDirect Storage (GDS)

One of the greatest bottlenecks in supercomputing is **checkpointing**.

* A 405-billion parameter model produces **over 2.5 Terabytes of raw checkpoint data per save** (model weights + optimizer states).
* **The Problem**: If 24,000 GPUs have to pause training for 20 minutes every 2 hours while data crawls through the CPU into regular network storage, **over 15% of your $500,000,000 computer sits idle doing nothing!**

```
                      TRADITIONAL vs. GPUDIRECT CHECKPOINTING
                      
   TRADITIONAL CHECKPOINTING (15 - 20 Minutes):
   [GPU VRAM] ──► [Host CPU RAM] ──► [OS Kernel] ──► [TCP Socket] ──► [Disk]
                 (Slow CPU copies choke the system)

   GPUDIRECT STORAGE / RDMA (Under 45 Seconds):
   [GPU VRAM] ══════════════════════════════════════════════════════► [Tectonic NVMe]
                 (Bypasses CPU entirely; writes at wire speed over RoCEv2!)
```

### The Solution in Plain English:
1. **GPUDirect Storage (GDS)**: Meta equipped the GPUs to bypass the host CPU and operating system kernel completely. The GPUs beam their VRAM contents straight out through the 400G network card into flash storage.
2. **The Result**: Checkpoint save times dropped from **15 minutes to under 45 seconds**, saving tens of millions of dollars in wasted compute time.

---

## Layer 5: Sizing, Serving & Inference Fleet (From Training to vLLM)

Once Llama 3 405B was trained, Meta faced the production challenge: **How do you serve a 405-billion parameter model to hundreds of millions of users without blowing up your budget?**

Here is how the principles from our [[spaces/ai-kb/notes/gpu-node-and-cluster-sizing-guide|GPU Sizing Guide]] and [[spaces/ai-kb/notes/vllm-and-llmd-explained|vLLM Guide]] solved this:

### 5.1 Sizing the 405B Model for Serving:
* **Model Weights in FP8**: $405 \text{ Billion} \times 1 \text{ Byte} \times 1.20 \text{ overhead} \approx \mathbf{486 \text{ GB of VRAM}}$.
* **Hardware Selection**:
  * An 8-GPU H100 node ($8 \times 80\text{GB} = 640\text{GB}$) can fit the weights, but leaves only $154 \text{ GB}$ for user KV cache.
  * Meta deployed **NVIDIA H200 servers** ($8 \times 141\text{GB} = 1,128\text{GB}$) or **2-chassis H100 pairs** ($16 \times 80\text{GB} = 1,280\text{GB}$) to leave hundreds of gigabytes of room for concurrent user chats.

### 5.2 Why GQA Saved Millions of Dollars:
* Llama 3 405B uses **Grouped-Query Attention (GQA)** with a 16:1 ratio (128 Query heads share just 8 Key-Value heads).
* *In Plain English*: Instead of 128 detectives each demanding their own personal secretary to take notes, they share 8 secretaries.
* **The Impact**: For a 128,000-token prompt:
  * Without GQA (legacy MHA): A single user's conversation would consume **over 13 GB of VRAM** just for note-taking!
  * With GQA: That exact same conversation consumes only **~0.8 GB of VRAM**, allowing dozens of long-context users to query the model at the same time on a single server!

### 5.3 Disaggregated Prefill & Decode in Production
For long-context prompts (e.g. uploading a 50-page legal contract with a short question), Meta split their inference servers using the pattern from [[spaces/ai-kb/decisions/adr_001_vllm_llmd_inference_standard|ADR 001]]:

```
                    DISAGGREGATED CLUSTER SIZING RATIO
┌───────────────────────────────────────┬─────────────────────────────────────┐
│          PREFILL POOL (Input)         │         DECODE POOL (Output)        │
├───────────────────────────────────────┼─────────────────────────────────────┤
│ • Role: Fast Readers                  │ • Role: Fast Typists                │
│ • Hardware: Compute-Dense H100s       │ • Hardware: Memory-Dense H200s      │
│ • Job: Ingests 50-page documents in   │ • Job: Streams output words at      │
│   parallel in milliseconds.           │   40+ tokens per second.            │
└───────────────────────────────────────┴─────────────────────────────────────┘
        Interconnected via 400G RoCEv2 Fabric for KV Cache streaming!
```

---

## Layer 6: SRE Realities: Surviving 24,000 GPUs

Running 24,000 GPUs running at 700 Watts each is a brutal mechanical challenge.

### 6.1 The Lightbulb Problem (Mean Time Between Failures)
* If you have 1 lightbulb in your bedroom with a 1,000-day lifespan, you rarely change bulbs.
* If you operate a stadium with **24,000 lightbulbs**, one bulb burns out **every single hour**.
* In a 24k-GPU cluster, a GPU, power supply, optical cable, or SSD fails constantly. PyTorch and Kubernetes must automatically detect failures, drain the sick node, reload the latest 45-second checkpoint, and resume training without human intervention.

### 6.2 Silent Data Corruption (The Math Glitch Monster)
The most terrifying failure in AI is not a server catching fire. It is **Silent Data Corruption (SDC)**:
* A single microscopic transistor inside GPU #18,402 develops a tiny flaw.
* When doing math, it calculates $2.0 \times 2.0 = 4.00001$ instead of $4.0$.
* The GPU does **not** crash. Linux reports that everything is fine.
* But that microscopic math glitch ripples through millions of calculations, eventually poisoning the model's weights and ruining weeks of training work!
* **Meta's Solution**: Synthetic checksum test scripts run continuously in the background across all 24,000 GPUs, catching and quarantining "lying" GPUs before they ruin a training run.

---

## Synthesis: How Our Knowledge Base Maps to Meta's Production Reality

Every guide and architecture decision in this repository represents a direct subsystem of this real-world architecture:

```
┌───────────────────────────────────────┬─────────────────────────────────────┐
│ KNOWLEDGE BASE MODULE                 │ PRODUCTION IMPLEMENTATION AT META   │
├───────────────────────────────────────┼─────────────────────────────────────┤
│ PyTorch in the Real World             │ PyTorch 4D Parallelism & NCCL       │
│ AI Infrastructure Explained           │ 128k GQA KV Cache & HBM Bandwidth   │
│ Lossless Networking in GPU Nodes      │ Priority 3 PFC & DCQCN Tuning       │
│ RoCEv2 vs. InfiniBand Showdown        │ The Dual 24,000-GPU Cluster Battle  │
│ Scale-Up vs. Scale-Out                │ Grand Teton NVLink vs Arista Clos   │
│ GPU Node & Cluster Sizing Guide       │ Sizing 405B FP8 & KV Concurrency    │
│ vLLM & LLM-D Deep Dive                │ Disaggregated Production Serving    │
│ Cisco AI Solutions Architecture       │ Enterprise equivalent (Silicon One) │
└───────────────────────────────────────┴─────────────────────────────────────┘
```

---

## Related Knowledge & Architecture Links
- **Master Knowledge Hub**: [[spaces/ai-kb/notes/overview|AI-KB Overview]]
- **Lossless Networking Guide**: [[spaces/ai-kb/notes/lossless-networking-in-gpu-nodes|Lossless Networking in GPU Nodes]]
- **RoCEv2 vs. InfiniBand**: [[spaces/ai-kb/notes/rocev2-vs-infiniband-guide|RoCEv2 vs. InfiniBand Showdown]]
- **Scale-Up vs. Scale-Out**: [[spaces/ai-kb/notes/scale-out-nvlink-and-ethernet-guide|Scale-Up vs. Scale-Out: NVLink & Ethernet]]
- **GPU Sizing Guide**: [[spaces/ai-kb/notes/gpu-node-and-cluster-sizing-guide|The Definitive Guide to Sizing GPU Nodes and Clusters]]
- **Serving Architecture**: [[spaces/ai-kb/notes/vllm-and-llmd-explained|vLLM and LLM-D Deep Dive]]
- **PyTorch Foundations**: [[spaces/ai-kb/notes/pytorch-in-the-real-world|PyTorch in the Real World]]
- **Cisco Enterprise AI**: [[spaces/ai-kb/notes/cisco-ai-solutions-architect-guide|Cisco AI Solutions Architecture]]
- **Reference Topology**: [[spaces/ai-kb/architecture/ai_cluster_topology|AI Cluster & Lossless Fabric Topology]]
- **Architectural Decisions**: 
  - [[spaces/ai-kb/decisions/adr_001_vllm_llmd_inference_standard|ADR 001: vLLM & LLM-D]]
  - [[spaces/ai-kb/decisions/adr_002_rocev2_lossless_ethernet|ADR 002: Lossless RoCEv2]]
