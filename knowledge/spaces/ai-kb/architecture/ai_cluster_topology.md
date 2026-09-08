# AI Cluster & Lossless Fabric Topology
> **Space**: `ai-kb`  
> **Status**: Approved Architecture  
> **Related Hub**: [[spaces/ai-kb/notes/overview|AI-KB Overview]]  
> **Decisions**: [[spaces/ai-kb/decisions/adr_001_vllm_llmd_inference_standard|ADR 001: vLLM & LLM-D]], [[spaces/ai-kb/decisions/adr_002_rocev2_lossless_ethernet|ADR 002: RoCEv2 Fabric]]

---

## 1. System Topology Overview

The AI cluster topology integrates high-density GPU compute servers (e.g. Cisco UCS / NVIDIA HGX), a non-blocking 400G/800G lossless RoCEv2 network fabric, and an intelligent Kubernetes inference control plane.

```
                     AI INFERENCE & TRAINING TOPOLOGY
                     
                             [ User Request ]
                                    │
                                    ▼
                         [ Inference Gateway ]
                                    │
                                    ▼
                         [ LLM-D Scheduler ]
              (Directs cache-aware & queue-aware traffic)
                                    │
           ┌────────────────────────┴────────────────────────┐
           ▼                                                 ▼
  [ Prefill Pod Pool ]                              [ Decode Pod Pool ]
  (Compute Dense: H100)                             (Memory Dense: H200)
           │                                                 ▲
           └───────────── 400G/800G RoCEv2 Fabric ───────────┘
                      (Lossless PFC & ECN / Packet Spraying)
```

## 2. Key Subsystems
- **Compute Layer**: Detailed in [[spaces/ai-kb/notes/ai-infrastructure-explained|AI Infrastructure Explained]] and [[spaces/ai-kb/notes/cisco-ai-solutions-architect-guide|Cisco AI Solutions Architecture]].
- **Serving Engine**: High-throughput inference powered by [[spaces/ai-kb/notes/vllm-and-llmd-explained|vLLM & LLM-D]].
- **Backend Fabric**: Lossless RDMA network detailed in [[spaces/ai-kb/notes/lossless-networking-in-gpu-nodes|Lossless Networking in GPU Nodes]].
- **Development Layer**: Distributed training and perception workflows running on [[spaces/ai-kb/notes/pytorch-in-the-real-world|PyTorch]].
