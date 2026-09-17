# High-Performance Model Serving: KServe & vLLM in Plain English

> **Focus Domain**: LLM Inference Engines, KServe Orchestration, vLLM Runtime, Implementation Walkthrough, Autoscaling  
> **Audience**: Platform Engineers, Cloud Architects, Systems Engineers, Enterprise Developers  
> **Target Platforms**: Red Hat OpenShift AI (RHOAI), Kubernetes, Nutanix Cloud Platform  
> **Status**: Production Reference

---

## 1. Executive Summary: What Is Model Serving?

In the AI world, there are two distinct phases:
1. **Training (Going to School)**: The model reads trillions of words over months of time using thousands of GPUs. It costs millions of dollars and creates the model's "brain" (the weight files).
2. **Serving / Inference (Going to Work)**: The trained model sits on a server, waiting for employee or customer questions. When a question arrives, it must answer in real time—streaming words back within milliseconds.

**Model serving is the engine room of enterprise AI.** If your serving infrastructure is poorly designed, your AI will be slow, crash under traffic spikes, and waste 80% of your expensive GPU hardware.

Red Hat OpenShift AI (and modern AI architectures) standardizes on two complementary technologies to solve this:
* **KServe**: The **Cloud-Native Host / Maitre D'**. It manages container lifecycles, handles security, routes web traffic, monitors health, and calls in more GPU servers when traffic surges.
* **vLLM**: The **High-Performance Kitchen Engine**. It lives inside the container, takes incoming prompts, executes GPU matrix math, and streams tokens back with zero wasted GPU memory.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                 THE SERVING ARCHITECTURE IN PLAIN ENGLISH                   │
├─────────────────────────────────────────────────────────────────────────────┤
│  [ Enterprise App / Chatbot / Copilot ]                                     │
│         │                                                                   │
│         ▼  1. HTTPS Query: "Summarize this 10-page contract..."             │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │ KSERVE (The Host & Traffic Manager)                                   │  │
│  │  • OpenShift Ingress & Envoy Proxy: Checks security tokens & routes.  │  │
│  │  • KEDA Autoscaler: Monitors queue length; adds GPUs when busy.       │  │
│  └───────────────────────────────────┬───────────────────────────────────┘  │
│                                      │                                      │
│                                      ▼ 2. Internal Port 8080 (REST/gRPC)    │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │ vLLM RUNTIME (The High-Performance Engine Block)                      │  │
│  │  • PagedAttention: Allocates GPU memory like hotel rooms (no waste).  │  │
│  │  • Continuous Batching: Subways stop every token step (no jams).     │  │
│  │  • Chunked Prefill: Slices heavy documents into small parcels.        │  │
│  └───────────────────────────────────┬───────────────────────────────────┘  │
│                                      │                                      │
│                                      ▼ 3. PCIe / NVLink Bus                 │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │ PHYSICAL GPU HARDWARE (NVIDIA L40S, A100, H100)                       │  │
│  │  • High-Bandwidth VRAM: Holds static weights + dynamic KV cache.     │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Why Traditional AI Serving Broke (The 3 Fatal Problems)

Before vLLM, companies served models using standard Python frameworks (like raw PyTorch or Hugging Face Transformers). In production, these frameworks failed for three specific reasons:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                   THE THREE FATAL PROBLEMS OF NAIVE SERVING                 │
├─────────────────────────────────────────────────────────────────────────────┤
│  1. MEMORY FRAGMENTATION (The Mandatory Presidential Suite Problem):        │
│     • A model can support up to 8,192 tokens of conversation.               │
│     • Traditional engines reserved all 8,192 tokens in GPU memory for EVERY │
│       person who connected—even if they only asked a 10-word question!       │
│     • Result: 80% to 90% of your $30,000 GPU's memory sat completely empty,│
│       yet the server ran out of memory (OOM) after only 4 users!            │
├─────────────────────────────────────────────────────────────────────────────┤
│  2. HEAD-OF-LINE BLOCKING (The Tour Bus Problem):                           │
│     • Traditional engines grouped 16 queries into a "static batch."         │
│     • Like a tour bus, the batch could NOT finish until every single person │
│       got back on board. If 15 users asked for a 1-sentence answer, but 1   │
│       user asked for a 2,000-word essay, all 15 users sat frozen waiting!   │
├─────────────────────────────────────────────────────────────────────────────┤
│  3. PREFILL STARVATION (The Freight Truck Problem):                         │
│     • When an employee uploads a 40-page PDF into a RAG chatbot, the GPU    │
│       must "read" all 10,000 words before answering.                        │
│     • Without chunking, reading that document locked the GPU compute cores  │
│       for 3 whole seconds, freezing active word streaming for every other   │
│       employee in the company.                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. How vLLM Fixes It: Three Inventions Explained

vLLM was invented by researchers at UC Berkeley to solve these exact bottlenecks:

### 3.1 PagedAttention: The "Hotel Keycard" System
Instead of forcing every user to pre-book a giant contiguous block of GPU memory, vLLM borrows an idea from 1960s operating systems: **Virtual Memory Paging**.
* vLLM cuts GPU memory into tiny, fixed-size blocks called **pages** (usually 16 tokens each).
* When you ask a 10-word question, vLLM hands you **one 16-token page**.
* If your conversation keeps going, vLLM assigns another page wherever there is an empty slot in physical VRAM (non-contiguous memory).
* **The Result**: Memory waste drops from **80% down to under 4%**. You can now serve **2x to 4x more concurrent users** on the exact same GPU!

```
Logical Chat:    [ Page 0 (Tokens 0-15) ]  ──►  [ Page 1 (Tokens 16-31) ]
                           │                               │
Physical VRAM:             ▼                               ▼
                     GPU Slot #14                    GPU Slot #82
```

### 3.2 Continuous Batching: The "Subway Train" System
Instead of a tour bus that leaves once and waits for everyone, vLLM operates like an **express subway train**:
* The train stops at the platform at **every single token step** (every 15 to 25 milliseconds).
* As soon as a user's question finishes generating, that user steps off the train.
* A brand new user waiting on the platform immediately steps into the empty seat on the very next token iteration.
* **The Result**: The GPU never spins idle waiting for slow users, and throughput increases by **5x to 10x**.

### 3.3 Chunked Prefill: The "Express Parcel" System
When someone uploads a massive 10,000-token document, vLLM chops the document into small parcels (e.g., 512 tokens each).
* It interleaves these 512-token reading steps in between the rapid streaming steps of other chatting users.
* **The Result**: New heavy documents start processing immediately without causing ongoing user streams to stutter or drop frames.

---

## 4. Real-World Implementation Example: Step-by-Step

Let's walk through a concrete, production implementation.

### The Scenario:
* **Goal**: Deploy the **IBM Granite 8B Instruct** model for an enterprise customer service copilot.
* **Platform**: Red Hat OpenShift AI running on a worker node with **1x NVIDIA L40S GPU (48GB VRAM)**.
* **Namespace**: `customer-service-ai`.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                 STEP-BY-STEP IMPLEMENTATION WORKFLOW                        │
├─────────────────────────────────────────────────────────────────────────────┤
│  STEP 1: Create the Storage Secret (Connect to S3 / Nutanix Objects).       │
│  STEP 2: Define the vLLM Serving Runtime (The Container Engine Template).   │
│  STEP 3: Deploy the InferenceService (Launch the Model Pod).                │
│  STEP 4: Configure KEDA Autoscaling (Scale on Queue Depth).                 │
│  STEP 5: Call the Live Endpoint from Application Code (Python & cURL).      │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

### Step 1: Connect to Model Weights (Storage Secret)

The model weights (`.safetensors` files) live in an S3-compatible bucket (such as Nutanix Objects, OpenShift Data Foundation Ceph, or AWS S3). We create a Kubernetes Secret so KServe can download them securely:

```yaml
# File: 01-storage-secret.yaml
apiVersion: v1
kind: Secret
metadata:
  name: model-storage-secret
  namespace: customer-service-ai
  labels:
    opendatahub.io/dashboard: "true"
stringData:
  aws_access_key_id: "ENTERPRISE_STORAGE_KEY"
  aws_secret_access_key: "ENTERPRISE_STORAGE_SECRET"
  aws_s3_endpoint: "https://s3.corp.internal"
  aws_s3_bucket: "model-weights-repository"
  aws_default_region: "us-east-1"
```

*Apply it to the cluster:*
```bash
oc apply -f 01-storage-secret.yaml
```

---

### Step 2: Define the vLLM Serving Runtime

The `ClusterServingRuntime` tells OpenShift AI how to run the vLLM container, what command-line flags to pass, and which ports to expose.

```yaml
# File: 02-vllm-runtime.yaml
apiVersion: serving.kserve.io/v1alpha1
kind: ClusterServingRuntime
metadata:
  name: vllm-runtime
spec:
  annotations:
    openshift.io/display-name: "vLLM Serving Runtime (Enterprise CUDA)"
  supportedModelFormats:
    - name: vLLM
      version: "1"
      autoSelect: true
  containers:
    - name: kserve-container
      image: registry.redhat.io/rhoai/vllm-cuda-py311:latest
      command: ["python3", "-m", "vllm.entrypoints.openai.api_server"]
      args:
        - "--port=8080"
        - "--model=/mnt/models"
        - "--gpu-memory-utilization=0.90"
        - "--max-model-len=8192"
        - "--tensor-parallel-size=1"
        - "--enable-chunked-prefill=true"
        - "--enforce-eager"
      ports:
        - containerPort: 8080
          protocol: TCP
      env:
        - name: HF_HOME
          value: /tmp/hf_home
```

#### What These Flags Mean in Plain English:
* `--port=8080`: The network port where vLLM listens for incoming chat queries.
* `--model=/mnt/models`: The folder inside the container where KServe mounts the downloaded model files.
* `--gpu-memory-utilization=0.90`: Reserves **90% of GPU VRAM** for model weights and PagedAttention KV-cache. The remaining 10% is left for PyTorch workspace buffers, preventing sudden out-of-memory crashes.
* `--max-model-len=8192`: Caps the conversation context window at 8,192 tokens (~6,000 words).
* `--tensor-parallel-size=1`: Tells vLLM to run on **1 physical GPU**. If serving a giant 70B model across 4 GPUs, you would set this to `4`.
* `--enable-chunked-prefill=true`: Activates the "express parcel" feature, preventing big document uploads from freezing other active chats.

---

### Step 3: Deploy the Model Instance (`InferenceService`)

The `InferenceService` is the primary custom resource. It binds the model weights to the vLLM runtime and requests the GPU hardware:

```yaml
# File: 03-granite-inferenceservice.yaml
apiVersion: serving.kserve.io/v1beta1
kind: InferenceService
metadata:
  name: granite-8b-customer-service
  namespace: customer-service-ai
  annotations:
    # Use RawDeployment for enterprise production (zero cold-start penalty)
    serving.kserve.io/deploymentMode: RawDeployment
spec:
  predictor:
    model:
      modelFormat:
        name: vLLM
      runtime: vllm-runtime
      storageUri: "s3://model-weights-repository/granite-8b-instruct/"
      resources:
        requests:
          cpu: "4"
          memory: 16Gi
          nvidia.com/gpu: "1"
        limits:
          cpu: "8"
          memory: 32Gi
          nvidia.com/gpu: "1"
```

#### What Happens When You Run `oc apply -f 03-granite-inferenceservice.yaml`:
1. **KServe Operator Intercepts**: KServe creates a standard Kubernetes Deployment, Service, and OpenShift Route.
2. **Storage Initializer Runs**: An init-container connects to S3 using the secret from Step 1, downloads the SafeTensors weights to `/mnt/models`, and exits.
3. **vLLM Boots on the GPU**: The vLLM container launches, loads the 16 GB of weights into the L40S GPU VRAM, builds the PagedAttention memory tables, and starts listening on port 8080.
4. **Route Exposed**: OpenShift creates an HTTPS route: `https://granite-8b-customer-service-customer-service-ai.apps.ocp.corp.internal`.

---

### Step 4: Call the Live Model from Application Code

Because vLLM provides a 100% **OpenAI-compatible REST API**, developers do not need custom SDKs. Any existing library (LangChain, LlamaIndex, official OpenAI SDK) works instantly.

#### Option A: Python Application Code (Streaming Tokens)

```python
#!/usr/bin/env python3
"""
Enterprise Customer Service Copilot Client
Calls the internal OpenShift AI vLLM endpoint using the standard OpenAI library.
"""
import os
import sys
import time
from openai import OpenAI

# 1. Point to the internal OpenShift AI KServe route
ROUTE_HOST = "granite-8b-customer-service-customer-service-ai.apps.ocp.corp.internal"
API_KEY = os.getenv("OPENSHIFT_TOKEN", "default-user-token")

client = OpenAI(
    base_url=f"https://{ROUTE_HOST}/v1",
    api_key=API_KEY
)

print("Connecting to Granite 8B on OpenShift AI...\n")
start_time = time.time()
first_token_received = False

# 2. Submit conversation prompt with streaming enabled
response = client.chat.completions.create(
    model="granite-8b-instruct",
    messages=[
        {"role": "system", "content": "You are an expert enterprise customer support assistant."},
        {"role": "user", "content": "What are our warranty return policies for enterprise hardware?"}
    ],
    temperature=0.2,
    max_tokens=500,
    stream=True  # Enables real-time token streaming
)

# 3. Stream tokens to console as they arrive from GPU
print("Assistant: ", end="", flush=True)
for chunk in response:
    if chunk.choices and chunk.choices[0].delta.content:
        if not first_token_received:
            ttft = (time.time() - start_time) * 1000
            first_token_received = True
        print(chunk.choices[0].delta.content, end="", flush=True)

print(f"\n\n[Performance Telemetry: Time-to-First-Token = {ttft:.1f}ms]")
```

#### Option B: Direct cURL Call (Server-Sent Events)

```bash
# Get your active OpenShift authentication token
TOKEN=$(oc whoami -t)
ENDPOINT="https://granite-8b-customer-service-customer-service-ai.apps.ocp.corp.internal/v1/chat/completions"

curl -k -X POST "$ENDPOINT" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "granite-8b-instruct",
    "messages": [
      {"role": "system", "content": "You are an enterprise AI assistant."},
      {"role": "user", "content": "Explain PagedAttention in one sentence."}
    ],
    "stream": true,
    "temperature": 0.1
  }'
```

*Output streams back token-by-token in real time:*
```
data: {"choices":[{"delta":{"content":"Paged"}}]}
data: {"choices":[{"delta":{"content":"Attention"}}]}
data: {"choices":[{"delta":{"content":" partitions"}}]}
data: {"choices":[{"delta":{"content":" GPU"}}]}
data: {"choices":[{"delta":{"content":" memory"}}]}
data: {"choices":[{"delta":{"content":" like"}}]}
data: {"choices":[{"delta":{"content":" virtual"}}]}
data: {"choices":[{"delta":{"content":" memory"}}]}
data: {"choices":[{"delta":{"content":" pages."}}]}
data: [DONE]
```

---

### Step 5: Implement Autoscaling with KEDA (The "Office Printer" Model)

Standard Kubernetes autoscaling looks at CPU or GPU percentage. **For AI inference, that fails completely:**
* Even a single user typing a question causes GPU matrix cores to spike to **95%+ utilization** during calculation.
* If you scale based on GPU utilization, Kubernetes will mistakenly spin up 8 expensive GPUs for just 2 people typing!

#### The Plain-English Solution: The Office Printer Queue
Think of a GPU like an office printer:
* You do **not** buy a second printer just because someone is currently printing a page (the printer is 100% busy, but only 1 person is using it).
* You buy a second printer only when you see **5 people physically standing in line in the hallway holding documents** waiting for their turn!

vLLM tracks this exact metric: `vllm:num_requests_waiting`. We use **KEDA (Kubernetes Event-Driven Autoscaling)** to monitor it:

```yaml
# File: 04-keda-autoscaler.yaml
apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata:
  name: vllm-queue-scaler
  namespace: customer-service-ai
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: granite-8b-customer-service-predictor
  minReplicaCount: 1     # Always keep 1 GPU warm (zero cold start)
  maxReplicaCount: 4     # Scale up to 4 GPUs during heavy rushes
  cooldownPeriod: 300    # Wait 5 minutes after traffic calms down before removing a GPU
  triggers:
    - type: prometheus
      metadata:
        serverAddress: https://thanos-querier.openshift-monitoring.svc:9091
        metricName: vllm_num_requests_waiting
        query: sum(vllm:num_requests_waiting{namespace="customer-service-ai"})
        threshold: "5"   # Add a new GPU whenever 5 or more requests are stuck in line
```

---

## 5. GPU Memory Math in Plain English (No Ph.D. Required)

To size your infrastructure accurately, you only need to calculate **three memory buckets**:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                 THE THREE GPU MEMORY BUCKETS                                │
├─────────────────────────────────────────────────────────────────────────────┤
│  TOTAL VRAM NEEDED = [ Model Weights ] + [ Working Scratchpad ] + [ Buffer ]│
│                                                                             │
│  1. Model Weights (The Brain):                                              │
│     • The fixed size of the model file loaded permanently into VRAM.        │
│     • In FP16 (16-bit precision): Each parameter takes 2 bytes.             │
│       -> 8 Billion Parameters × 2 bytes = 16 GB VRAM.                       │
│     • In FP8 (8-bit precision): Each parameter takes 1 byte.                │
│       -> 8 Billion Parameters × 1 byte = 8 GB VRAM.                         │
│                                                                             │
│  2. KV Cache (The Scratchpad / Working Memory):                             │
│     • The memory the AI uses to remember what has already been said in the   │
│       conversation so it doesn't repeat itself.                             │
│     • For IBM Granite 8B: Uses Grouped Query Attention (GQA).               │
│       Each token of conversation takes approximately ~144 KB of VRAM.       │
│     • A full 8,192-token conversation takes ~1.18 GB of KV cache.          │
│                                                                             │
│  3. CUDA Activation Overhead (The Workshop Floor):                          │
│     • Temporary scratch memory needed by NVIDIA CUDA drivers during math.   │
│     • Takes roughly 2 GB to 4 GB.                                           │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Real-World Sizing Example: 1x NVIDIA L40S GPU (48GB VRAM)

Let's do the math for our Granite 8B customer support deployment:
* **Total Physical VRAM**: 48 GB
* **vLLM Safety Limit (`--gpu-memory-utilization=0.90`)**: $48 \times 0.90 = 43.2 \text{ GB}$
* **Minus Model Weights**: $43.2 - 16.0 = 27.2 \text{ GB}$
* **Minus CUDA Overhead**: $27.2 - 3.0 = \mathbf{24.2 \text{ GB}}$ left exclusively for KV-Cache!

**What does 24.2 GB of KV-Cache buy you?**
* At 1.18 GB per maximum 8k conversation: **20 simultaneous users** can have massive, 6,000-word deep conversations at the exact same second.
* If users have normal 1,000-word conversations (~150 MB each): **over 160 simultaneous conversations** run on that single GPU without a single drop in speed!

---

## 6. Sizing Quick Reference Table

| Target Model | Model Parameters | Precision | Weight Size | Minimum GPU Required | Concurrent Active Streams |
| :--- | :---: | :---: | :---: | :--- | :---: |
| **Granite 8B / Llama 3 8B** | 8 Billion | FP16 (16-bit) | 16 GB | **1x NVIDIA L40S (48GB)** | **25 – 40 users** |
| **Granite 8B (Quantized)** | 8 Billion | AWQ (4-bit) | 4.5 GB | **1x NVIDIA L4 (24GB)** | **15 – 25 users** |
| **Granite 20B Code** | 20 Billion | FP16 (16-bit) | 40 GB | **1x NVIDIA A100/H100 (80GB)** | **30 – 50 users** |
| **Llama 3.1 70B / Granite 70B**| 70 Billion | FP16 (16-bit) | 140 GB | **4x NVIDIA L40S (4x 48GB)** or **2x H100 (80GB)** | **30 – 60 users** |

---

## 7. Day-2 Operations & Troubleshooting Cheatsheet

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    COMMON PROBLEMS & ONE-LINE FIXES                         │
├─────────────────────────────────────────────────────────────────────────────┤
│  1. ERROR: "CUDA out of memory during initialization"                       │
│     • Why: You gave vLLM 95% of VRAM, leaving zero room for CUDA kernels.   │
│     • Fix: Lower `--gpu-memory-utilization` from `0.95` down to `0.88`.     │
│                                                                             │
│  2. ERROR: "Cold starts taking 4 minutes after zero traffic"                 │
│     • Why: Serverless mode (`minScale: 0`) wiped the pod, so it must        │
│       redownload 16 GB of weights over the network before answering.        │
│     • Fix: Set `serving.knative.dev/minScale: 1` or switch to RawDeployment.│
│                                                                             │
│  3. SYMPTOM: Real-time word streaming stutters when someone uploads a PDF    │
│     • Why: The GPU stopped streaming to process a giant 8,000-token prompt. │
│     • Fix: Add `--enable-chunked-prefill=true` to the vLLM arguments.       │
│                                                                             │
│  4. ERROR: "Connection reset by peer" during peak traffic                   │
│     • Why: Incoming traffic exceeded vLLM's internal max waiting queue.     │
│     • Fix: Deploy KEDA ScaledObject with threshold `5` to scale pods.       │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Related Knowledge & Architecture Links
- **Knowledge Hub**: [[spaces/rh-ai/notes/overview|Rh-Ai Knowledge Hub]]
- **Platform Architecture**: [[spaces/rh-ai/notes/04-openshift-ai-rhoai-architecture|OpenShift AI Platform Architecture]]
- **Hardware Sizing**: [[spaces/rh-ai/notes/10-hardware-acceleration-and-cluster-sizing|Hardware Acceleration & Cluster Sizing Guide]]
- **Distributed Fleets (`llm-d`)**: [[spaces/rh-ai/notes/12-nvidia-nim-vllm-llmd-and-lora-adapters|NVIDIA NIM, vLLM, LLM-D & Dynamic LoRA Adapters]]
- **Security & Governance**: [[spaces/rh-ai/notes/13-enterprise-ai-security-and-governance|Enterprise AI Security & Governance]]
- **RAG Architecture**: [[spaces/rh-ai/notes/14-enterprise-retrieval-augmented-generation-rag|Enterprise Retrieval-Augmented Generation (RAG)]]
- **Nutanix Stack**: [[spaces/rh-ai/notes/15-nutanix-enterprise-ai-nkp-vllm-and-llmd|Nutanix Enterprise AI, NKP, vLLM & LLM-D]]
- **Architectural Decision**: [[spaces/rh-ai/decisions/adr_001_standardize_on_rhoai_and_kserve_vllm|ADR 001: Standardize on RHOAI & vLLM]]

---
*Reference architecture documentation for `/workspace/projects/rh-ai`.*
