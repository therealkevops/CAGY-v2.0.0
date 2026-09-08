# AI Infrastructure Explained: GPUs, vLLM, and LLM-D
> **Source**: [KodeKloud — AI Infrastructure Explained (GPUs, vLLM, and LLM-D)](https://www.youtube.com/watch?v=hBzUokVYQkI)  
> **Speaker**: Mumshad Mannambeth  
> **Target Audience**: SREs, Systems Administrators, DevOps & Platform Engineers  
> **Key Timestamp Highlighted**: [39:35 (2375s)](https://www.youtube.com/watch?v=hBzUokVYQkI&t=2375s) — *Disaggregated Prefill and Decode in LLM-D*

---

## Executive Summary & Big Picture

The AI boom is not just a machine learning revolution; it is fundamentally a **massive systems infrastructure challenge**. 

- **The Capital Surge**: Major cloud titans (Amazon, Google, Microsoft, Meta) are spending upwards of **$700 billion** on AI infrastructure in a single year—four times what was spent prior to ChatGPT's launch, with McKinsey projecting **$7 trillion** poured into data centers by 2030.
- **The Core Problem**: The engineers needed to run these platforms do not need to be data scientists training models from scratch. They need to be infrastructure specialists (SREs, DevOps, SysAdmins) who understand how to keep ultra-expensive GPUs saturated, eliminate memory bottlenecks, route requests intelligently, and manage distributed pods on Kubernetes.
- **The Journey**: From a single number calculation on a chip, to the auto-regressive generation loop, memory bandwidth walls, the KV cache, model servers (vLLM), and intelligent routing/orchestration with **LLM-D** on **Kubernetes**.

---

## 1. Demystifying the AI Model

### What is ChatGPT Really?
The name "ChatGPT" is a composite of two distinct layers:
1. **Chat (The Application)**: The standard web frontend, user interface, authentication, and API endpoints.
2. **GPT (The Model)**: The neural network engine running behind the scenes, reading the prompt, doing math, and generating the response.

Other models like Claude (Anthropic), Gemini (Google), and Llama (Meta) follow the exact same architectural pattern: a software application wrapped around an inference model.

### What is a Model in Plain English?
Strip away the AI mystique: a model is essentially a **mathematical machine that takes an input number, performs an operation, and returns an output number**.
- **The Doubling Box**: Imagine a box where you drop in `3` and get `6`, or drop in `10` and get `20`. The formula is fixed (`Input × Weight = Output`). The number `2` inside is the **weight**. If you change the weight to `3`, the exact same formula now triples numbers instead of doubling them.
- **Predicting Real Things (House Prices)**: To predict a house price, you feed multiple measurable factors (square footage, number of bedrooms, age) into a formula. Each factor gets a weight indicating how much it influences the price:
  $$\text{Price} = (\text{Size} \times W_1) + (\text{Bedrooms} \times W_2) - (\text{Age} \times W_3) + \text{Base}$$
- **What "Training" Actually Means**: Nobody guesses the weights by hand. You feed the formula thousands of actual historical house sales. The training algorithm iteratively adjusts the weights until the formula accurately predicts the true prices.
- **Scaling to Human Language**: Sentences and language nuances are infinitely more complex than house prices. To handle language, the formula expands: millions of inputs, chained operations, and billions of weights arranged in hierarchical layers where the output of one layer feeds the next.

### What is a Transformer?
- A **Transformer** is simply a specific mathematical blueprint/arrangement of calculations and attention layers designed for processing sequences.
- The formula for a transformer is standardized and compact—it fits in roughly a single page of code and is broadly identical across modern models (GPT, Claude, Llama).
- **The Secret**: Models don't differ because of their code formula; they differ because of their **weights** (billions of numbers derived from pre-training).

### A Model is Just a File on Disk
- A trained model is literally a **static file containing billions of floating-point numbers** stored on disk (e.g., Safetensors or GGUF files).
- **Typical Model Sizes**:
  - *Small models*: ~2 GB
  - *Mid-size models*: ~16 GB
  - *Large models (70B parameters)*: ~140 GB
  - *Giant models (500B+ parameters)*: Several hundred gigabytes.
- Running inference simply means: loading that massive file off disk into memory, feeding in prompt tokens, computing the math through the formula, and streaming out the resulting tokens.

---

## 2. Hardware Foundations: CPUs vs. GPUs & The Memory Wall

### Why Can't You Just Run It on Your Laptop CPU?
A modern laptop has plenty of compute, but computers are split into two separate components:
1. **CPU (The Processor)**: A brilliant generalist with a small number of very powerful cores. It executes complex, sequential logic one step at a time at very high clock speeds.
2. **RAM (System Memory)**: Physical storage where data and instructions reside, located across a motherboard bus from the CPU.

```
Standard Computer Architecture:
+---------------+         PCIe / Memory Bus          +---------------+
|   System RAM  |  <==============================>  |      CPU      |
|  (Far Away)   |          (~64 GB/s pipe)           | (Few Fast     |
+---------------+                                    |  Serial Cores)|
                                                     +---------------+
```

- **The Problem**: Running an LLM requires **billions of tiny, independent multiplications**. These calculations do not depend on each other and can all happen at the exact same time in parallel.
- When fed billions of parallel math problems, a CPU is forced to queue them up sequentially, a few at a time. The CPU will get every calculation right, but you will wait minutes or hours for a single sentence.

### Enter the GPU: Thousands of Cores in Parallel
- A GPU is built the opposite way of a CPU: instead of a handful of large, fast cores, it contains **thousands of small, simple cores** running simultaneously.
- **Raw Power**: While a server CPU can deliver ~10 TFLOPS (trillion floating-point operations per second), a modern GPU delivers ~1,000 TFLOPS—over **100x more math every second**.

### The GPU Memory Bottleneck (VRAM)
Solving the compute bottleneck introduces a new hardware challenge: **feeding the cores**.
- If model weights remained in standard system RAM, the GPU would have to pull weights across a narrow PCIe bus (~64 GB/s). That bus is ~50x too slow for thousands of hungry GPU cores, leaving cores starved and idle 98% of the time.
- **The Fix — VRAM (Video RAM)**: High-speed memory mounted directly on the GPU board immediately adjacent to the silicon cores.
  - VRAM channels deliver **terabytes per second (TB/s)** rather than gigabytes per second.
  - **The Catch**: VRAM is physically compact and expensive. An entry-level Nvidia T4 has only 16 GB of VRAM; an A100 has 80 GB. A 70B parameter model requires 140 GB just to load its weights, meaning it physically cannot fit onto a single standard card.

### The Three Numbers that Define Every GPU
Every AI accelerator in the data center is evaluated by three core metrics:
1. **Compute (TFLOPS)**: How many mathematical operations the cores can execute per second.
2. **Capacity (VRAM in GB)**: How much weight and cache data fits directly onto the card.
3. **Bandwidth (TB/s)**: How fast the GPU cores can read data out of its own onboard memory.

#### Hardware Generations Comparison Table
| Hardware Platform | Compute Power | Memory Capacity | Memory Bandwidth | Primary Role |
| :--- | :--- | :--- | :--- | :--- |
| **Desktop PC** | 1 – 3 TFLOPS | 32 – 64 GB RAM | ~0.09 TB/s | General computing |
| **Rackmount Server** | 5 – 10 TFLOPS | 128 – 512+ GB RAM | ~0.5 TB/s | Standard enterprise workloads |
| **Nvidia A100** | 312 TFLOPS | 80 GB VRAM | ~2.0 TB/s | Previous-gen workhorse |
| **Nvidia H100** | 990 TFLOPS | 80 GB VRAM | ~3.35 TB/s | Compute-heavy inference & training |
| **Nvidia H200** | 990 TFLOPS | **141 GB VRAM** | **~4.8 TB/s** | Memory-heavy decode & large models |
| **Nvidia B200 (Blackwell)**| 2,250 TFLOPS | 192 GB VRAM | ~8.0 TB/s | Next-gen unified giant workloads |

---

## 3. Serving Models: From PyTorch to vLLM

### Why PyTorch Scripts Don't Work in Production
With ~6 lines of Python using `torch` and Hugging Face, you can load a small model and generate text. However, a local script:
- Runs once for a single user and terminates.
- Cannot listen for continuous concurrent web traffic.
- Has no connection pooling, request queueing, dynamic batching, or memory management.

### Model Servers & vLLM
Production demands a **Model Server**—an always-on daemon that keeps model weights permanently resident in VRAM and exposes a standardized REST API.
- **vLLM**: The leading open-source model engine built on PyTorch.
- **OpenAI-Compatible API**: vLLM exposes the exact endpoints (`/v1/chat/completions`) as OpenAI. Any existing application, client library, or tool built for OpenAI works with your private vLLM server without changing code.
- **Startup Cost**: Running `vllm serve <model>` copies the multi-gigabyte weight file from NVMe disk into GPU VRAM (taking 1–2 minutes).
- **Scale Cost**: Model servers are heavy. You cannot spin up new replicas in milliseconds. Every new replica requires an additional, expensive GPU with its own dedicated VRAM.

---

## 4. How LLMs Generate Text: Tokens & The Two Phases

### Tokens: The Atomic Currency of LLMs
LLMs do not read letters or full words; they process **tokens**.
- **Rule of Thumb**: 1 token $\approx$ 0.75 words (or 100 tokens $\approx$ 75 words).
- Example: *"Serving LLMs is not like serving web apps"* is 8 words, but breaks into 9 tokens.
- Everything in AI systems is measured, budgeted, and billed in tokens: prompt size, context windows, API fees, and generation speed.

### The Auto-Regressive Generation Loop
An LLM has only one fundamental capability: **given a sequence of prior tokens, predict the single next most likely token**.

```
Generation Loop:
[User Prompt] ──────────────────────────► [LLM Predicts Token 1]
[User Prompt + Token 1] ────────────────► [LLM Predicts Token 2]
[User Prompt + Token 1 + Token 2] ──────► [LLM Predicts Token 3]
... (Repeats 100 times for a 100-token answer)
```

- A 100-token response is not one big calculation; it is the model running that full cycle **100 times in a row**, one lap per token.
- **Why output streams**: Text appears word-by-word on your screen because the application streams each token the moment that loop iteration finishes.
- **Key Infrastructure Implications**:
  1. **Long Duration**: Unlike web APIs that resolve in 15 ms, an LLM request takes seconds because it must run hundreds of sequential laps.
  2. **Extreme Request Variance**: Asking "What is the capital of France?" takes 7 input tokens and 2 output tokens. Asking "Summarize this 50-page PDF" requires 30,000 input tokens. Both hit the same server endpoint, but one is thousands of times heavier than the other.

---

## 5. Prefill vs. Decode (TTFT vs. TPOT)

Every LLM generation request is split into two radically different halves:

```
                          TOTAL REQUEST TIMELINE
│◄────────── TTFT ──────────►│◄────────────────────── Generation ──────────────────────►│
┌────────────────────────────┬─────────────────────────────────────────────────────────┐
│       PREFILL PHASE        │                      DECODE PHASE                       │
│      (The Initial Pause)   │                 (The Streaming Output)                  │
├────────────────────────────┼─────────────────────────────────────────────────────────┤
│ • Reads entire prompt      │ • Generates 1 token per pass                            │
│ • All tokens at once       │ • Model weights read from VRAM every single token       │
│ • Compute-Bound (Cores)    │ • Memory-Bandwidth-Bound (VRAM Speed)                   │
│ • Parallel throughput      │ • Sequential loop (Tails)                               │
└────────────────────────────┴─────────────────────────────────────────────────────────┘
```

### Phase 1: The Prefill Phase (The Initial Pause)
- The model reads the entire prompt you supplied. Because the prompt is already known upfront, all prompt tokens are processed simultaneously in a single, massive parallel sweep.
- Thousands of GPU cores fire at once to chew through the prompt, ending with the generation of the very first output token.
- **Bottleneck**: **Compute-bound** (how fast the cores can crunch floating-point operations).
- **Metric**: **TTFT (Time To First Token)**. Short prompts have imperceptible TTFT; giant prompts create noticeable pauses.

### Phase 2: The Decode Phase (The Stream)
- The model writes the answer token-by-token.
- For *every single token generated*, the GPU must read the **entire model weights** out of VRAM into the compute cores, calculate the next token, discard the weights, and repeat.
- The compute cores are barely doing any work here; they spend most of their time waiting for the weights to be streamed over from VRAM.
- **Bottleneck**: **Memory-bandwidth-bound** (how fast VRAM can deliver data to cores).
- **The Math behind the Speed Limit**:
  - Suppose a model is 16 GB and your GPU memory bandwidth is 3.2 TB/s (3,200 GB/s).
  - Maximum model reads per second = $\frac{3200 \text{ GB/s}}{16 \text{ GB}} = 200 \text{ reads/sec}$.
  - Since 1 read = 1 token, the absolute physical speed ceiling is **200 tokens/sec**.
- **Metric**: **TPOT (Time Per Output Token)**. This is the latency between consecutive tokens (e.g., $1 / 200 = 5 \text{ ms}$ per token).

---

## 6. Memory Optimizations: KV Cache & Prefix Caching

### The Problem: Naive Re-calculation
If an LLM recomputed the entire prompt and all generated tokens from scratch for every single new word, every single token would require a fresh prefill pause. The chat would grind to a halt.

### The Solution: The KV Cache
- During prefill, the intermediate mathematical representations (Keys and Values) of all processed tokens are saved directly in GPU VRAM—this is the **KV Cache**.
- For all subsequent decode steps, the model does not reprocess earlier tokens. It reads past context directly from the KV cache and only computes the single new token.
- **Result**: You pay the prefill penalty once; subsequent tokens stream instantly.

### Multi-Turn Conversations & Prefix Caching
In a standard multi-turn chat, the LLM itself has no memory. When you send message #2, the client resends the entire history:
$$\text{Turn 2 Request} = [\text{Prompt 1}] + [\text{Answer 1}] + [\text{Prompt 2}]$$

- If the server wiped its cache after Turn 1, it must re-prefill the entire conversation history from scratch on Turn 2, causing growing latency as conversations lengthen.
- **Prefix Caching (Prompt Caching)**:
  - The server retains KV cache blocks in memory, keyed by the hash of the text prefix.
  - When Turn 2 arrives, the model recognizes it already computed Prompt 1 + Answer 1. It pulls those blocks from cache and only runs prefill on the brand-new message.
  - **Shared System Prompts**: If thousands of corporate users query an assistant with the same 2,000-word system instruction, the model computes that prefix once and reuses it for everyone.
  - **Economic Impact**: Cached input tokens typically cost **90% less** (e.g., Anthropic/OpenAI prompt caching discounts) and eliminate up to 80% of TTFT latency.

---

## 7. Scaling Up: Batching & Sharding

### Batching: Packing Multiple Users onto One GPU
If a GPU is forced to read the entire 16 GB model from VRAM just to produce 1 token for 1 user, it is wasting massive capacity.
- **The Core Idea**: Use that exact same memory read to calculate the next token for **50 users at the same time**.
- **The Benefit**: Individual latency stays nearly identical (~5 ms per token), but total server throughput multiplies by 50x. Without dynamic batching, running GPUs would be financially unsustainable.
- **The Batching Ceiling (Memory, NOT Compute)**:
  - You cannot batch an infinite number of users.
  - While model weights are fixed in size, **every active user requires their own KV cache allocation in VRAM**.
  - As soon as free VRAM is exhausted by active KV caches, the server is full. New users must wait in a queue, or older caches must be evicted.
  - *This is why ChatGPT displays "At Capacity" messages*: the GPU cores have plenty of idle math capacity, but the VRAM is 100% full of KV caches.

### Sharding: Splitting Giant Models Across GPUs
When a model is too large for any single card (e.g., a 70B parameter model needing 140 GB, or 500B+ needing hundreds of GBs on an 80 GB GPU), it must be **sharded** across multiple GPUs.

```
                    SHARDING STRATEGIES
1. Inter-Layer (Pipeline Parallelism)   2. Intra-Layer (Tensor Parallelism)
   [Layers 1-10]  --> GPU 1                [Layer 1 Top Half]    --> GPU 1
   [Layers 11-20] --> GPU 2                [Layer 1 Bottom Half] --> GPU 2
   (Light communication between nodes)     (Chatty: constant sync over NVLink)
```

1. **Intra-Layer Sharding (Tensor Parallelism)**:
   - Splits individual matrix multiplications within every single layer across GPUs (GPU 1 does half the math, GPU 2 does the other half, and they sum results).
   - **Hardware Requirement**: Highly "chatty." Requires ultra-high-speed **NVLink** interconnects (~900+ GB/s). Works brilliantly inside a single 8-GPU server chassis.
2. **Inter-Layer Sharding (Pipeline Parallelism)**:
   - Assigns whole sequential layers to different GPUs (e.g., GPU 1 handles layers 1–16, GPU 2 handles 17–32).
   - **Hardware Requirement**: Only requires passing intermediate outputs between layer boundaries. Well-suited for standard network fabrics connecting different physical servers.
- **Rule of Thumb**: Keep chatty intra-layer communication inside the physical box (NVLink); keep lightweight pipeline communication between separate boxes.

---

## 8. Why Traditional Load Balancing Fails for AI

In standard web architecture, a reverse proxy (NGINX, AWS ALB, Round-Robin) distributes traffic evenly across a pool of stateless app servers. **This completely breaks for LLM workloads for two reasons**:

```
                       TRADITIONAL LOAD BALANCING FAILURE
                    
   Request 1 (Turn 1) ──────────► [Server A] (Generates KV Cache in VRAM)
   
   Request 2 (Turn 2) ──[Round──► [Server B] (Cache Miss! Must re-read
                         Robin]               entire history from scratch)
                         
   Result: Throws away expensive cached work on $30,000 GPUs!
```

1. **Cache Blindness**:
   - Turn 1 goes to Server A, which builds and stores the KV cache in VRAM.
   - Turn 2 gets routed by Round-Robin to Server B. Server B has never seen this conversation, forcing it to re-read the entire history and perform an expensive, redundant prefill.
   - Standard load balancers destroy cache locality and squander expensive GPU cycles.
2. **Request Heterogeneity & Queue Blindness**:
   - Web load balancers assume requests are relatively uniform.
   - An LLM load balancer cannot see that Request 1 is a 5-token ping while Request 2 is a 50-page document analysis.
   - If it routes the 50-page request to a server already running 30 active decode streams, that server's memory saturates, and all 30 users experience stuttering and severe latency degradation.

---

## 9. LLM-D: Intelligent Routing & Orchestration on Kubernetes
*(Highlighted Segment: [39:35 / 2375s](https://www.youtube.com/watch?v=hBzUokVYQkI&t=2375s))*

To solve these fundamental limitations, **Red Hat, Google, IBM, and NVIDIA** collaborated on an open-source project called **LLM-D** (`llm-d.ai`).

LLM-D is a **distributed inference engine and intelligent scheduler** that sits in front of a fleet of vLLM model servers running on Kubernetes.

```
                           LLM-D ARCHITECTURE
                           
                            [ Client Request ]
                                    │
                                    ▼
                         [ Inference Gateway ]
                                    │
                                    ▼
                          [ LLM-D Scheduler ]
                     (Inspects Cache, VRAM, Queues)
                                    │
             ┌──────────────────────┴──────────────────────┐
             ▼                                             ▼
    [ Prefill Pool Pods ]                        [ Decode Pool Pods ]
   (Compute-Heavy: e.g. H100)                   (Memory-Heavy: e.g. H200)
   • One-time prompt reading                    • Token-by-token streaming
   • Generates KV Cache                         • Low TPOT latency
             │                                             ▲
             └────────── Fast KV Cache Transfer ───────────┘
```

### The Three Core Decision Pillars of LLM-D
1. **Cache-Aware Routing**:
   - Tracks which server pods hold existing KV caches.
   - When a conversation follow-up arrives, LLM-D routes it directly to the pod that already holds the conversation's prefix, skipping prefill entirely.
   - **Performance Gain**: Yields up to **3x overall system throughput** and **cuts TTFT in half** on the exact same hardware.
2. **Memory & Queue State Inspection**:
   - Queries the internal state of vLLM pods in real time: percentage of VRAM occupied by KV caches, current batch fullness, and waiting queue depth.
   - Never overwhelms a server that is near capacity.
3. **Disaggregated Prefill and Decode (The Key Architectural Breakthrough at 39:35)**:
   - On a single shared GPU, prefill and decode fight for resources. When a user pastes a massive prompt, the GPU pauses ongoing decode streams to compute the prefill, causing jitter for everyone else.
   - **The Disaggregation Solution**: LLM-D physically decouples the fleet into two specialized pools:
     - **Prefill Pool**: High compute power (e.g., Nvidia H100s) dedicated solely to reading prompts and building KV caches.
     - **Decode Pool**: High VRAM capacity and memory bandwidth (e.g., Nvidia H200s) dedicated solely to streaming output tokens.
   - When the prefill pod finishes processing a prompt, it transfers the KV cache over a fast network link directly to a decode pod, which streams the answer to the user.
   - **Benefit**: Up to **70% more tokens per second** on identical infrastructure, completely isolating streaming users from prompt spikes.

---

## 10. LLM-D "Well-Lit Paths"

Manually tuning dozens of distributed inference knobs (cache affinity thresholds, prefill/decode instance ratios, batch sizes, tensor parallel counts) can take weeks. LLM-D provides pre-validated, measured configuration recipes called **Well-Lit Paths**:

1. **Path 1: Optimized Baseline**:
   - A single homogeneous pool of identical vLLM servers with **cache-aware routing** enabled.
   - The fastest, simplest production upgrade with immediate throughput gains.
2. **Path 2: Disaggregated Prefill & Decode**:
   - Decoupled prefill and decode pools with cross-node KV cache transfers.
   - The optimal architecture for heavy-input workloads (e.g., document summarization, complex RAG pipelines, long-context code analysis).
3. **Path 3: Sharded Giant Model Fleet**:
   - Distributes single giant models across multi-GPU nodes acting as unified logical servers.
   - Manages inter-node networking and parallel tensor splits automatically.

---

## 11. Production Deployment on Kubernetes

### Why Kubernetes?
According to CNCF surveys, **66% of organizations hosting GenAI in production use Kubernetes** for inference. Kubernetes provides battle-tested lifecycle management, self-healing, rolling updates, and declarative scaling.

### The Deployment Flow
Deploying LLM-D on a Kubernetes cluster is accomplished with standard Helm charts:
```bash
# 1. Install the LLM-D intelligent router and scheduler
helm install router oci://registry/llm-d-router

# 2. Deploy the model topology (specifying model URI, prefill/decode pod counts)
helm install model oci://registry/llm-d-model -f values.yaml
```

### Why StatefulSets Fail & Why LeaderWorkerSet Was Built
Traditional Kubernetes workload controllers are insufficient for sharded models:
- **Deployments**: Treat every pod as fungible, identical, and independent.
- **StatefulSets**: Assign sticky network ordinals (`pod-0`, `pod-1`), but still treat pods as independent replicas (like database replicas).
- **The LLM Reality**: A model sharded across 4 pods is **not 4 servers—it is 1 single server cut into 4 pieces**.
  - If shard #2 crashes, the entire model is broken and cannot answer questions.
  - Scaling must spin up an entire new group of 4 shards simultaneously, not just 1 isolated pod.
- **The Solution — LeaderWorkerSet (LWS)**:
  - A specialized Kubernetes custom controller that manages a **leader pod + $N$ worker pods** as a single atomic unit.
  - Health checks, restarts, and scales the entire group together.

### Day-2 Operations
- **Self-Healing**: If a worker pod or node crashes, Kubernetes reschedules the group; LLM-D updates its routing table immediately.
- **Independent Elastic Auto-scaling**:
  - Decode queue backing up? Autoscale the decode pool.
  - Spike in large prompt ingest? Autoscale the prefill pool.
- **Single Ingress Endpoint**: Applications talk only to the **Inference Gateway** URL, which transparently handles scheduling, cache routing, and worker dispatch.

---

## 12. Summary & Career Roadmap for Infrastructure Engineers

The central message of the course: **Serving AI in production is systems infrastructure, not data science.**

| ML / Data Science Domain | Systems & Infrastructure Domain (This Course) |
| :--- | :--- |
| Training loss & backpropagation | High-throughput request routing |
| Dataset cleaning & tokenizers | VRAM allocation & memory bandwidth bottlenecks |
| Hyperparameter tuning | Pod lifecycle & LeaderWorkerSet management |
| Model architecture experimentation | Caching layers (KV Cache & Prefix Caching) |
| Math research | Kubernetes scaling, Helm deployments, & cost optimization |

### Key Takeaways
1. **Models are static files** loaded into GPU VRAM to run massively parallel arithmetic.
2. **Inference is two-phased**: Prefill is compute-bound (TTFT); Decode is memory-bandwidth-bound (TPOT).
3. **Memory is the ultimate constraint**: VRAM capacity limits batch size and concurrent users; memory bandwidth limits generation speed.
4. **Traditional load balancing breaks**: You must use cache-aware, load-aware routing (LLM-D) to avoid wasting expensive GPU cycles.
5. **Kubernetes is the standard runtime**: Leveraging primitives like Gateway API, vLLM, and LeaderWorkerSet bridges existing DevOps skills directly into the AI era.

---
*Summary generated for `/workspace/projects/ai-kb` based on the official KodeKloud AI Infrastructure curriculum.*

---

## Related Knowledge & Architecture Links
- **Knowledge Hub**: [[spaces/ai-kb/notes/overview|AI-KB Overview]]
- **Serving & Scheduling**: [[spaces/ai-kb/notes/vllm-and-llmd-explained|vLLM and LLM-D Deep Dive]]
- **Development Tooling**: [[spaces/ai-kb/notes/pytorch-in-the-real-world|PyTorch in the Real World]]
- **Cluster Network Fabric**: [[spaces/ai-kb/notes/lossless-networking-in-gpu-nodes|Lossless Networking in GPU Nodes]]
