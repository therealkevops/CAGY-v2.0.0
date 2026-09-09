# The Definitive Guide to Sizing GPU Nodes and Clusters for LLM Serving
> **Space**: `ai-kb`  
> **Target Audience**: Infrastructure Architects, SREs, Capacity Planners, and Technical Advisors  
> **Related Architecture**: [[spaces/ai-kb/architecture/ai_cluster_topology|AI Cluster & Lossless Fabric Topology]]  
> **Related Guides**: [[spaces/ai-kb/notes/ai-infrastructure-explained|AI Infrastructure Explained]], [[spaces/ai-kb/notes/vllm-and-llmd-explained|vLLM & LLM-D Deep Dive]], [[spaces/ai-kb/notes/lossless-networking-in-gpu-nodes|Lossless Networking]], [[spaces/ai-kb/notes/scale-out-nvlink-and-ethernet-guide|Scale-Out NVLink & Ethernet]], [[spaces/ai-kb/notes/rocev2-vs-infiniband-guide|RoCEv2 vs. InfiniBand Showdown]], [[spaces/ai-kb/notes/cisco-ai-solutions-architect-guide|Cisco AI Solutions Guide]]  
> **Serving Standards**: [[spaces/ai-kb/decisions/adr_001_vllm_llmd_inference_standard|ADR 001: vLLM & LLM-D]], [[spaces/ai-kb/decisions/adr_002_rocev2_lossless_ethernet|ADR 002: Lossless RoCEv2]]

---

## Executive Summary: Sizing GPUs in Plain English

Sizing infrastructure for Large Language Models (LLMs) like Llama 3, Mistral, or DeepSeek is completely different from traditional web servers, databases, or virtual machines.

In traditional enterprise IT, if traffic spikes, your CPU usage climbs from 30% to 80%. If things get too busy, you simply spin up another VM. 

In AI infrastructure, **everything is dictated by strict physical hardware boundaries**. If you miscalculate by even a few megabytes, your server does not slow down—**it crashes instantly** with an unrecoverable `CUDA Out of Memory` error.

To understand how to size a GPU, think of a GPU worker at an **Office Desk**:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                       THE OFFICE DESK ANALOGY                               │
├─────────────────────────────────────────────────────────────────────────────┤
│  1. THE DESK SURFACE (VRAM Capacity - Gigabytes):                           │
│     • The model weights are giant, heavy encyclopedias permanently open     │
│       on the desk.                                                          │
│     • The active user conversations (KV Cache) are notepads piled up on the │
│       remaining desk space.                                                 │
│     • If you take one more phone call than the desk has room for, papers    │
│       fall off the edge and the worker faints (CUDA Out of Memory crash).   │
├─────────────────────────────────────────────────────────────────────────────┤
│  2. THE HAND-FLIPPING SPEED (Memory Bandwidth - Terabytes/sec):             │
│     • To generate a single new word, the worker must flip through EVERY     │
│       single page of the 70-billion-page encyclopedia from front to back!   │
│     • How fast their hands can flip pages (VRAM bandwidth in TB/s) is the   │
│       absolute physical limit on how fast words appear on a user's screen.  │
├─────────────────────────────────────────────────────────────────────────────┤
│  3. COMMUNICATION BETWEEN DESKS (Interconnect - NVLink vs. Network):        │
│     • If an encyclopedia is too massive for one desk, you split it across   │
│       multiple workers sitting side-by-side.                                │
│     • If they can look at each other's pages in real time (NVLink), they     │
│       work smoothly together.                                               │
│     • If they sit in different buildings and must mail letters back and     │
│       forth (Ethernet), every sentence grinds to a halt.                    │
└─────────────────────────────────────────────────────────────────────────────┘
```

When sizing any GPU cluster, you work through the **Three-Step Sizing Pyramid**:

```
                       THE THREE-STEP SIZING PYRAMID
┌─────────────────────────────────────────────────────────────────────────────┐
│                          STEP 3: TRAFFIC & SLA SIZING                       │
│    How many nodes do we need to hit our QPS, TTFT (<500ms), and TPOT        │
│    (>30 tokens/sec) targets under peak concurrent load?                     │
├─────────────────────────────────────────────────────────────────────────────┤
│                         STEP 2: THE KV CACHE POOL                           │
│    How much remaining VRAM must be reserved for active user context,        │
│    multi-turn chat history, and concurrent batch sizes?                     │
├─────────────────────────────────────────────────────────────────────────────┤
│                         STEP 1: MODEL WEIGHT BASELINE                       │
│    How many gigabytes of VRAM are required just to load the model file      │
│    into memory based on parameter count and precision (FP16/FP8/INT4)?      │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Part 1: Step 1 — Sizing the Model Weights Baseline

Before your GPU can answer a single user prompt, it must load the model's brain into its ultra-fast memory (VRAM).

### 1.1 What is a "Parameter" in Plain English?
Think of a model parameter as an **adjustable tuning knob** inside the neural network. 
* A **7B or 8B model** (like Llama-3-8B) has roughly 8 billion knobs.
* A **70B model** (like Llama-3-70B) has 70 billion knobs.
* A **405B frontier model** has 405 billion knobs.

During inference, every single knob is a number that must sit permanently in GPU memory.

---

### 1.2 What is "Precision" (Quantization) in Plain English?
Precision is simply **how many bytes of memory you use to record each knob**:

* **FP16 / BF16 (16-bit Float = 2 Bytes per parameter)**: 
  * *Plain English*: High-definition precision. Like writing down numbers with several decimal places (e.g., `3.14159`). This is the raw format the model was trained in.
  * *Memory Rule*: Every 1 billion parameters requires **$2.0 \text{ GB}$ of VRAM**.
* **FP8 (8-bit Float = 1 Byte per parameter)**: 
  * *Plain English*: The sweet spot for modern AI. Like rounding slightly (e.g., `3.14`). Human users cannot tell the difference in output quality, but it **cuts memory usage in half** and nearly doubles generation speed on modern NVIDIA Hopper (H100) and Blackwell (B200) GPUs.
  * *Memory Rule*: Every 1 billion parameters requires **$1.0 \text{ GB}$ of VRAM**.
* **INT4 / AWQ (4-bit Integer = 0.5 Bytes per parameter)**: 
  * *Plain English*: Heavy compression. Like converting a high-res photo into a lightweight JPEG. Good for running smaller models on budget GPUs, but can slightly reduce reasoning accuracy on complex tasks.
  * *Memory Rule*: Every 1 billion parameters requires **$0.5 \text{ GB}$ of VRAM**.

---

### 1.3 The Weight Formula & The "20% Margin Rule"

$$\text{Weight VRAM (GB)} = \left( \frac{\text{Parameters} \times \text{Bytes per Parameter}}{10^9} \right) \times 1.20$$

> [!IMPORTANT]
> **Why the $+20\%$ multiplier?**  
> If an 8B model in FP16 takes $16 \text{ GB}$ of raw weights, why won't it run on a $16 \text{ GB}$ GPU?  
> Because the GPU needs working room: the CUDA runtime context, PyTorch activation buffers, intermediate tensors, and scratchpad memory take up about 15% to 20% of extra VRAM. **Never size a server right at the edge of the raw weights.**

---

### 1.4 The "Mixture of Experts" (MoE) Trap in Plain English
Models like **Mixtral 8x7B** or **DeepSeek-V3 (671B)** are called "Mixture of Experts" (MoE). 
* People often say: *"DeepSeek-V3 only uses 37 billion active parameters per token, so can I run it on a small 80GB GPU?"*
* **The answer is NO.**
* **The Hospital Specialist Analogy**: Imagine a hospital with 671 specialist doctors on staff. When a patient walks in with a heart flutter, only 2 cardiologists examine them (37B active parameters). But you still have to pay rent, heat, and desk space for all 671 doctors sitting in the building!
* **Sizing Rule**: You must size your VRAM for the **TOTAL parameter count (671B)**, not the active parameter count.

---

### 1.5 Model Weight Footprint Cheat Sheet

| Model Family | Total Parameters | FP16 (2 Bytes) | FP8 (1 Byte) - *Recommended* | INT4 (0.5 Bytes) | Minimum GPUs to Boot Weights |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Llama-3-8B / Mistral-7B** | 8 Billion | **~19.2 GB** | **~9.6 GB** | **~4.8 GB** | 1x L4 (24GB) or 1x L40S (48GB) |
| **Mixtral-8x7B (MoE)** | 47B Total | **~112 GB** | **~56 GB** | **~28 GB** | 2x A100 (80GB) or 1x H200 (141GB) |
| **Llama-3-70B** | 70 Billion | **~168 GB** | **~84 GB** | **~42 GB** | 4x H100 (80GB) or 2x H200 (141GB) |
| **Llama-3-405B** | 405 Billion | **~972 GB** | **~486 GB** | **~243 GB** | 8x H200 (141GB) or 16x H100 (80GB) |
| **DeepSeek-V3 (MoE)** | 671 Billion | **~1,610 GB**| **~805 GB** | **~402 GB** | 8x B200 (192GB) or 16x H200 (141GB) |

---

## Part 2: Step 2 — Sizing the KV Cache & User Concurrency Pool

Loading the model weights is only Step 1. If you stop here, your GPU can boot, but it cannot handle multiple users or long documents.

The dynamic consumer of memory is the **KV Cache (Key-Value Cache)**.

```
                         GPU VRAM ALLOCATION BREAKDOWN
┌───────────────────────────────────────────────┬─────────────────────────────┐
│             STATIC RESERVATION                │      DYNAMIC USER POOL      │
│               (Model Weights)                 │       (KV Cache Pool)       │
├───────────────────────────────────────────────┼─────────────────────────────┤
│ • Fixed size (e.g. 70B in FP8 = ~84 GB)       │ • Grows with concurrency    │
│ • Never changes during server run             │ • Grows with context length │
│ • Pinned permanently in memory                │ • Managed by vLLM           │
└───────────────────────────────────────────────┴─────────────────────────────┘
```

### 2.1 What is the KV Cache in Plain English?
Large Language Models have no internal long-term memory. When an LLM is typing out the 50th word in an answer:
* It does not just look at word #49.
* It must look back at **every single word** from word #1 to word #49, plus the entire original prompt!
* Without a cache, the GPU would have to completely re-read and re-calculate all 49 previous words just to produce word #50. That would make generation impossibly slow.
* **The Solution**: The GPU writes down a mathematical summary of previous words into a scratchpad called the **KV Cache**.
* **The Catch**: That scratchpad takes up real, precious GPU VRAM. As your users send longer documents or more users chat simultaneously, the scratchpad expands until it consumes all remaining memory.

---

### 2.2 Modern Efficiency: MHA vs. GQA in Plain English
Why do modern models like Llama 3 handle far more users than older models like GPT-3?

* **Old Architecture: MHA (Multi-Head Attention)**:
  * *The Analogy*: A team of 32 detectives working on a case. Each detective demands their own personal secretary taking a full copy of all notes.
  * 32 detectives = 32 full copies of the KV cache. VRAM ran out almost immediately.
* **Modern Standard: GQA (Grouped-Query Attention)**:
  * *The Analogy*: The 32 detectives share a pool of 8 secretaries (an 8:1 ratio).
  * Used in **Llama 3, Mistral, and Qwen**. 
  * **Result: Slashes KV cache memory usage by 80% to 87.5%**, allowing you to serve 4x to 8x more concurrent users on the exact same GPU!

---

### 2.3 The Plain-English KV Cache Formula

For modern GQA models like Llama-3-70B:
* **Each token in a conversation consumes roughly $0.33 \text{ KB}$ ($0.00033 \text{ MB}$) of VRAM**.
* A standard **1,000 tokens of context** (about 750 English words) consumes **$0.33 \text{ MB}$**.
* A long **4,000-token conversation** consumes **$1.31 \text{ MB}$**.

$$\text{Total KV Cache VRAM (GB)} = \text{Concurrent Users} \times \frac{\text{Avg Context Length (Tokens)} \times 0.33 \text{ MB}}{1,000,000}$$

#### Real-World Sizing Example: 64 Active Users on Llama-3-70B
Suppose an enterprise wants to serve **64 concurrent users**, each working with an average document/chat length of **4,000 tokens**:

$$\text{KV Cache Needed} = 64 \text{ users} \times 4,000 \text{ tokens} \times 0.000328 \text{ MB} \approx \mathbf{84 \text{ GB of VRAM}}$$

Now do the total node math:
* Model Weights in FP8: **$84 \text{ GB}$**
* KV Cache for 64 Users: **$84 \text{ GB}$**
* System Headroom: **$15 \text{ GB}$**
* **Total VRAM Required**: $84 + 84 + 15 = \mathbf{183 \text{ GB}}$

#### The Hardware Selection Reality Check:
* $2 \times \text{H100 (80GB)} = 160 \text{ GB}$ $\longrightarrow$ **CRASH!** (Not enough VRAM for 64 concurrent users).
* $4 \times \text{H100 (80GB)} = 320 \text{ GB}$ $\longrightarrow$ **PERFECT.** Supports 64 users with plenty of headroom for bursts.
* $2 \times \text{H200 (141GB)} = 282 \text{ GB}$ $\longrightarrow$ **IDEAL.** Handles the workload on half the GPU count because of the H200's massive 141GB memory!

---

## Part 3: Sizing for Latency & Throughput (The Speed Limit)

Having enough VRAM ensures your server won't crash. But will it be fast enough for users?

### 3.1 The Two Phases of LLM Latency
Every interaction with an LLM has two distinct phases with two completely different hardware bottlenecks:

```
                          TOTAL USER LATENCY PROFILE
│◄───────── TTFT (Compute-Bound) ─────────►│◄───────── TPOT (Bandwidth-Bound) ─────────►│
┌──────────────────────────────────────────┬──────────────────────────────────────────┐
│              PREFILL PHASE               │               DECODE PHASE               │
│  The GPU reads the user's prompt.        │  The GPU generates output one word at a  │
│  "How long until the cursor starts       │  time. "How fast do words stream across  │
│  typing?"                                │  the screen?"                            │
│  • Bottleneck: Raw TFLOPS (Compute)      │  • Bottleneck: Memory Bandwidth (GB/s)   │
└──────────────────────────────────────────┴──────────────────────────────────────────┘
```

1. **TTFT (Time To First Token)**: The delay between clicking "Submit" and seeing the first character appear. The GPU processes all prompt tokens in parallel. This is **compute-bound** (governed by raw GPU TFLOPS).
2. **TPOT (Time Per Output Token)**: The streaming speed as words appear on screen. The GPU must generate words one at a single time. This is **memory-bandwidth-bound** (governed by how fast the GPU can read its own VRAM).

---

### 3.2 Why Decoding is Bottlenecked by Memory Bandwidth
To generate just **one single new word**, the GPU must sweep the **entire model weights** out of VRAM, through its compute cores, and back.

* If your model weighs $70 \text{ GB}$ in VRAM, the GPU must read all $70 \text{ GB}$ of data just to write the word *"The"*.
* To write the next word *"quick"*, it must read all $70 \text{ GB}$ again!
* Therefore, your token generation speed is physically limited by **Memory Bandwidth**:

$$\text{Max Single-Stream Speed (Tokens/sec)} = \frac{\text{GPU Memory Bandwidth (GB/s)}}{\text{Model Weight Size (GB)}}$$

$$\text{Time Per Output Token (TPOT)} = \frac{1}{\text{Tokens per second}}$$

#### Real-World Hardware Comparison for Llama-3-70B (FP8: 70 GB Weights)

| GPU Platform | Memory Bandwidth | Max Single-User Speed | TPOT (Latency per Token) | User Experience |
| :--- | :--- | :--- | :--- | :--- |
| **Nvidia A100 (80GB SXM)** | $2,039 \text{ GB/s}$ | $\frac{2039}{70} = \mathbf{29.1 \text{ tok/s}}$ | $34.3 \text{ ms/token}$ | Acceptable (human reading speed) |
| **Nvidia H100 (80GB SXM)** | $3,350 \text{ GB/s}$ | $\frac{3350}{70} = \mathbf{47.8 \text{ tok/s}}$ | $20.9 \text{ ms/token}$ | Fast and snappy |
| **Nvidia H200 (141GB SXM)**| $4,800 \text{ GB/s}$ | $\frac{4800}{70} = \mathbf{68.5 \text{ tok/s}}$ | $14.6 \text{ ms/token}$ | Blazing fast (43% faster than H100!)|
| **Nvidia B200 (192GB)** | $8,000 \text{ GB/s}$ | $\frac{8000}{70} = \mathbf{114.2 \text{ tok/s}}$| $8.7 \text{ ms/token}$ | Instantaneous stream |

> [!TIP]
> **Why the H200 is an Inference Beast**: Notice that the H200 has the exact same compute cores as an H100, but generates tokens **43% faster**. Why? Because its memory bandwidth increased from $3.35 \text{ TB/s}$ to $4.8 \text{ TB/s}$. For LLM generation, memory speed matters far more than compute cores!

---

### 3.3 Dynamic Batching: The Bus vs. Taxi Analogy
If reading $70 \text{ GB}$ of weights only yields 48 tokens/sec for one user, how do cloud providers serve thousands of users economically?

Through **Continuous / Dynamic Batching** (powered by [[spaces/ai-kb/notes/vllm-and-llmd-explained|vLLM]]):

* **The Taxi (Single User)**: A car drives down the highway carrying 1 passenger. It burns a full tank of gas (sweeps 70 GB of weights) to serve 1 person.
* **The Bus (Batched Users)**: A bus drives down the exact same highway carrying 64 passengers. It burns almost the exact same fuel, but delivers 64 people to their destination at once!
* **The Math**: When the GPU reads the 70GB of weights from memory, it multiplies those weights against the prompts of **64 users simultaneously** in a single memory sweep.
* **Throughput Multiplier**: 
  $$\text{Total Output} = 64 \text{ users} \times 40 \text{ tok/s} = \mathbf{2,560 \text{ tokens/sec per GPU group!}}$$

---

## Part 4: Distributed Parallelism: Slicing Models Across GPUs

When a model is too large to fit inside a single GPU's memory, you must slice (shard) it across multiple GPUs.

There are three ways to slice an AI model, each with strict physical networking rules:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                   THE THREE PARALLELISM STRATEGIES                          │
├─────────────────────────────────────────────────────────────────────────────┤
│  1. TENSOR PARALLELISM (TP) - The Operating Room Surgeons                   │
│     • Slices individual math matrices within a single layer across GPUs.    │
│     • Highly chatty: GPUs must synchronize after EVERY SINGLE TOKEN!        │
│     • RULE: Keep strictly inside 1 physical server over NVLink.             │
│     • Never run TP over network cables (Ethernet/InfiniBand).               │
├─────────────────────────────────────────────────────────────────────────────┤
│  2. PIPELINE PARALLELISM (PP) - The Factory Assembly Line                   │
│     • Slices layers sequentially across servers (Node 1 does Layers 1-40;   │
│       Node 2 does Layers 41-80).                                            │
│     • Low communication: Only passes data at layer boundaries.              │
│     • RULE: Use across physical servers connected by 400G RoCEv2.           │
├─────────────────────────────────────────────────────────────────────────────┤
│  3. DATA PARALLELISM (DP) - Multiple Grocery Checkout Lanes                 │
│     • Replicates the entire model onto separate GPU sets.                   │
│     • Each set handles different users completely independently.            │
│     • Doubles or triples your total user throughput (QPS).                  │
└─────────────────────────────────────────────────────────────────────────────┘
```

```
                   DISTRIBUTED SHARDING HEURISTIC
┌─────────────────────────────────────────────────────────────────────────────┐
│ 1. DOES IT FIT ON 1 GPU?                                                    │
│    YES ──► Run 1 instance per GPU (Data Parallelism).                       │
│    NO  ──► Go to Step 2.                                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│ 2. DOES IT FIT ON 1 PHYSICAL SERVER (8 GPUs over NVLink)?                   │
│    YES ──► Use Tensor Parallelism (TP = 2, 4, or 8) inside the node.        │
│    NO  ──► Go to Step 3.                                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│ 3. REQUIRES MULTIPLE PHYSICAL NODES?                                        │
│    Use Pipeline Parallelism (PP) between nodes over 400G RoCEv2,            │
│    combined with Tensor Parallelism (TP=8) inside each node.                │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Part 5: Production Sizing Blueprints (Real-World Recipes)

Here are production-tested hardware recipes for the four most common enterprise deployments:

---

### Recipe 1: Internal Enterprise RAG & Code Assistant (8B Models)
* **Target Model**: Llama-3-8B-Instruct or Mistral-7B
* **Use Cases**: Internal Q&A, summarizing meeting notes, code autocompletion.
* **Target Concurrency**: Up to 100 simultaneous users; average 4,000-token context.
* **Hardware Sizing**:
  * **Model Weights (FP8)**: $\sim 10 \text{ GB}$
  * **KV Cache (100 users @ 4k tokens)**: $\sim 16 \text{ GB}$
  * **Total VRAM Needed**: $10 + 16 + 5 = \mathbf{31 \text{ GB}}$
* **Recommended Hardware**:
  * **Cost-Optimized**: **$1 \times \text{NVIDIA L40S (48GB)}$** or **$1 \times \text{A100 (80GB PCIe)}$**.
  * **Parallelism**: $\text{TP} = 1$ (A single GPU handles the entire model easily).

---

### Recipe 2: The Enterprise Workhorse (70B Models)
* **Target Model**: Meta Llama-3-70B-Instruct
* **Use Cases**: Complex multi-step reasoning, contract analysis, customer service agents.
* **Target Concurrency**: 150+ concurrent users; average 8,000-token context; target latency $<25 \text{ ms/token}$.
* **Hardware Configurations**:

| Node Configuration | Hardware Spec | Precision | Sharding Strategy | Usable KV Cache Pool | Concurrency (8k context) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Budget Workhorse** | $4 \times \text{A100 (80GB SXM)}$ | FP8 / INT4 | $\text{TP} = 4$ | $\sim 220 \text{ GB}$ | $\sim 85 \text{ users}$ |
| **Production Standard** | $4 \times \text{H100 (80GB SXM)}$ | FP8 Native | $\text{TP} = 4$ | $\sim 236 \text{ GB}$ | $\sim 90 \text{ users}$ |
| **High-Density Node** | $2 \times \text{H200 (141GB SXM)}$| FP16 Native| $\text{TP} = 2$ | $\sim 114 \text{ GB}$ | $\sim 45 \text{ users}$ |
| **Max Scale Server** | $8 \times \text{H100 (80GB SXM)}$ | FP8 Native | $2 \times (\text{TP}=4) \text{ DP}=2$ | $\sim 472 \text{ GB}$ | **$\sim 180 \text{ users}$** |

---

### Recipe 3: The Frontier Giant (405B Models)
* **Target Model**: Meta Llama-3-405B-Instruct
* **Use Cases**: Enterprise research, synthetic data generation, master orchestrator agents.
* **Model Weight Footprint**: $\sim 405 \text{ GB}$ in FP8 precision.
* **Cluster Topologies**:
  * **Option A: Single-Chassis H200s (Easiest & Fastest)**:
    * **Server**: $1 \times \text{Server with } 8 \times \text{NVIDIA H200 (141GB)}$
    * **Total VRAM**: $1,128 \text{ GB}$
    * **Parallelism**: $\text{TP} = 8$ across NVLink.
    * **KV Cache Pool**: $\approx 640 \text{ GB}$ (Supports $\sim 120$ concurrent users).
    * *Advantage*: Zero multi-node networking complexity! Everything runs over internal NVLink copper.
  * **Option B: Multi-Node H100s (Scale-Out Fabric)**:
    * **Servers**: $2 \times \text{Servers, each with } 8 \times \text{H100 (80GB)}$ (16 GPUs total).
    * **Total VRAM**: $1,280 \text{ GB}$
    * **Parallelism**: $\text{TP} = 8$ inside each server, $\text{PP} = 2$ between servers.
    * **Backend Fabric**: Non-blocking **400G RoCEv2 Lossless Ethernet** connecting both chassis.

---

### Recipe 4: Disaggregated Architecture (LLM-D Prefill & Decode Pools)
For document-heavy workloads (e.g., uploading 100-page insurance policies or legal briefs), the prompt reading phase (Prefill) dominates the GPU.

As detailed in [[spaces/ai-kb/notes/vllm-and-llmd-explained|vLLM & LLM-D]], split your cluster into two specialized pools:

```
                    DISAGGREGATED CLUSTER SIZING RATIO
┌───────────────────────────────────────┬─────────────────────────────────────┐
│          PREFILL POOL (Input)         │         DECODE POOL (Output)        │
├───────────────────────────────────────┼─────────────────────────────────────┤
│ • Role: Reads massive 50k prompts     │ • Role: Streams output words        │
│ • Hardware: 2x H100 (80GB)            │ • Hardware: 4x H200 (141GB)         │
│ • Sized For: High TFLOPS compute      │ • Sized For: Giant KV cache memory  │
│   capacity.                           │   and 4.8 TB/s memory bandwidth.    │
└───────────────────────────────────────┴─────────────────────────────────────┘
        Interconnected via 400G RoCEv2 Fabric for KV Cache streaming!
```

---

## Part 6: The 5-Minute "Back of the Napkin" Sizing Worksheet

When speaking with a client or writing an infrastructure proposal, use this quick 5-step checklist:

```
                            THE 5-MINUTE SIZING CHEAT SHEET
                            
  [ 1. WEIGHTS ]   Parameters (Billions) × Bytes per Param (1 for FP8) × 1.2
                   Example: 70B × 1.0 × 1.2 = 84 GB VRAM
                   
  [ 2. KV CACHE ]  Users × Total Tokens × 0.00033 MB
                   Example: 50 users × 4,000 tokens × 0.00033 = 66 GB VRAM
                   
  [ 3. TOTAL ]     Weights (84 GB) + KV Cache (66 GB) = 150 GB Minimum VRAM
                   
  [ 4. HARDWARE ]  Select GPU pool that covers Total VRAM:
                   • 2x H100 (160 GB) — Tight (barely fits)
                   • 4x H100 (320 GB) — Recommended (plenty of burst room)
                   • 2x H200 (282 GB) — Optimal (half the GPUs, huge KV space)
                   
  [ 5. NETWORK ]   • If GPUs ≤ 8: Single server chassis over NVLink.
                   • If GPUs > 8: Use 400G/800G Lossless RoCEv2 between servers.
```

---

## Summary Sizing Matrix

| Metric | 8B Models (Llama 3) | 70B Models (Llama 3) | 405B Models (Llama 3) |
| :--- | :--- | :--- | :--- |
| **Target Precision** | FP16 or FP8 | FP8 Native | FP8 Native |
| **VRAM Needed for Weights** | $\sim 10 - 16 \text{ GB}$ | $\sim 70 - 84 \text{ GB}$ | $\sim 405 - 486 \text{ GB}$ |
| **Minimum GPU Setup** | 1x GPU (L40S / A100) | 4x H100 or 2x H200 | 8x H200 or 16x H100 |
| **Tensor Parallelism (TP)** | $\text{TP} = 1$ | $\text{TP} = 4$ or $\text{TP} = 8$ | $\text{TP} = 8$ (NVLink) |
| **Pipeline Parallelism (PP)**| None | None | $\text{PP} = 2$ (over 400G RoCEv2) |
| **Typical Target Latency** | $< 15 \text{ ms/token}$ | $< 25 \text{ ms/token}$ | $< 45 \text{ ms/token}$ |
| **Recommended Server Chassis**| Single 1U/2U PCIe Server | Cisco UCS C885A / HGX H100 | Multi-chassis HGX / UCS Cluster |

---

## Related Knowledge & Architecture Links
- **Master Hub**: [[spaces/ai-kb/notes/overview|AI-KB Overview]]
- **Hardware Foundations**: [[spaces/ai-kb/notes/ai-infrastructure-explained|AI Infrastructure Explained: Hardware & Transformers]]
- **Serving Engines**: [[spaces/ai-kb/notes/vllm-and-llmd-explained|vLLM and LLM-D Deep Dive]]
- **Lossless Networking**: [[spaces/ai-kb/notes/lossless-networking-in-gpu-nodes|Lossless Networking in GPU Nodes]]
- **Scale-Up vs. Scale-Out**: [[spaces/ai-kb/notes/scale-out-nvlink-and-ethernet-guide|Scale-Up vs. Scale-Out: NVLink & Ethernet]]
- **RoCEv2 vs. InfiniBand**: [[spaces/ai-kb/notes/rocev2-vs-infiniband-guide|RoCEv2 vs. InfiniBand Showdown Guide]]
- **Enterprise Design**: [[spaces/ai-kb/notes/cisco-ai-solutions-architect-guide|Cisco AI Solutions Architecture]]
- **Cluster Topology**: [[spaces/ai-kb/architecture/ai_cluster_topology|AI Cluster & Lossless Fabric Topology]]
- **Architectural Decision**: [[spaces/ai-kb/decisions/adr_001_vllm_llmd_inference_standard|ADR 001: Adopt vLLM & LLM-D]]
