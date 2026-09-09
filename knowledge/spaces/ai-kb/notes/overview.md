# AI Infrastructure & Solutions Knowledge Base Overview
> **Space**: `ai-kb`  
> **Domain**: Enterprise AI Infrastructure, GPU Clusters, Model Serving & Fabric Networking  
> **Status**: Active Reference Hub

---

## Knowledge Map & Domain Guides

This space serves as the comprehensive technical repository and second brain for **Enterprise AI Infrastructure, Acceleration, Model Serving, and High-Performance Fabric Design**.

```
                           AI-KB KNOWLEDGE TOPOLOGY
┌─────────────────────────────────────────────────────────────────────────────┐
│                             HIGH-LEVEL APPLICATIONS                         │
│   [[spaces/ai-kb/notes/cisco-ai-solutions-architect-guide|Enterprise AI Solutions]] ◄──► [[spaces/ai-kb/notes/pytorch-in-the-real-world|PyTorch in the Real World]] │
├─────────────────────────────────────────────────────────────────────────────┤
│                             MODEL SERVING & SCHEDULING                      │
│   [[spaces/ai-kb/notes/vllm-and-llmd-explained|vLLM & LLM-D Serving]] ◄────────► [[spaces/ai-kb/notes/ai-infrastructure-explained|Hardware & Inference Loop]]  │
├─────────────────────────────────────────────────────────────────────────────┤
│                             NETWORK FABRIC & HARDWARE                       │
│   [[spaces/ai-kb/notes/lossless-networking-in-gpu-nodes|Lossless Networking (RoCEv2/PFC)]] ◄──► [[spaces/ai-kb/architecture/ai_cluster_topology|Cluster Topology]] │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Core Knowledge Modules

1. **Foundations & Frameworks**:
   - [[spaces/ai-kb/notes/pytorch-in-the-real-world|PyTorch in the Real World]]: Deep dive into Tensors, CUDA acceleration, Autograd, and real-world enterprise deployments across Tesla, Meta, and Big Tech.
   - [[spaces/ai-kb/notes/ai-infrastructure-explained|AI Infrastructure Explained]]: The math behind transformers, GPU VRAM bandwidth constraints, and the two-phase generation cycle (Prefill vs. Decode / TTFT vs. TPOT).

2. **Serving Engines & Fleet Orchestration**:
   - [[spaces/ai-kb/notes/vllm-and-llmd-explained|vLLM and LLM-D Deep Dive]]: PagedAttention, continuous batching, cache-aware routing, and disaggregated prefill/decode on Kubernetes.
   - Related Decisions: [[spaces/ai-kb/decisions/adr_001_vllm_llmd_inference_standard|ADR 001: Adopt vLLM & LLM-D as Enterprise Serving Standard]].

3. **High-Performance AI Networking & Fabric**:
   - [[spaces/ai-kb/notes/lossless-networking-in-gpu-nodes|Lossless Networking in GPU Nodes]]: Solving GPU starvation, RoCEv2 vs. InfiniBand, Priority Flow Control (PFC), ECN/DCQCN, packet spraying, and the Ultra Ethernet Consortium (UEC).
   - [[spaces/ai-kb/notes/rocev2-vs-infiniband-guide|RoCEv2 vs. InfiniBand Showdown]]: Deep architectural clash—credit-based flow control vs. PFC/ECN, Subnet Manager vs. BGP Clos, adaptive routing vs. packet spraying, vendor lock-in vs. multi-vendor economics, and UEC.
   - [[spaces/ai-kb/notes/scale-out-nvlink-and-ethernet-guide|Scale-Up vs. Scale-Out (NVLink & Ethernet)]]: Debunking "NVLink over Ethernet", memory semantics vs. packet routing, GB200 NVL72 130 TB/s copper spine, Spectrum-X RoCEv2, and open standards (UALink vs. UEC).
   - Related Decisions: [[spaces/ai-kb/decisions/adr_002_rocev2_lossless_ethernet|ADR 002: Standardize on Lossless RoCEv2 with PFC and ECN]].

4. **Solutions Architecture & Customer Playbooks**:
   - [[spaces/ai-kb/notes/cisco-ai-solutions-architect-guide|Cisco AI Solutions Architecture]]: Enterprise AI solution design, Cisco Silicon One G200, Cisco Nexus HyperFabric, UCS servers, and customer discovery frameworks.

5. **Capacity Planning & Sizing**:
   - [[spaces/ai-kb/notes/gpu-node-and-cluster-sizing-guide|GPU Node & Cluster Sizing Guide]]: The physics of VRAM, model weight footprints, GQA KV cache calculations, memory bandwidth decode limits (TPOT), and sharding rules (TP/PP) for 8B, 70B, and 405B models.

6. **Real-World Case Studies & Architectural Walkthroughs**:
   - [[spaces/ai-kb/notes/real-world-architecture-meta-llama3|Real-World Architecture Walkthrough (Meta Llama 3 Infrastructure)]]: How Meta combined Grand Teton H100 servers (NVLink Scale-Up), tested 24k RoCEv2 vs. InfiniBand fabrics, tuned PFC/DCQCN, implemented PyTorch 4D parallelism, and sized 405B training and inference.

---

## Architectural Principles & System Design
- [[spaces/ai-kb/architecture/ai_cluster_topology|AI Cluster & Fabric Reference Architecture]]: Multi-node GPU topology connecting compute nodes, storage fabrics, and intelligent Kubernetes routing.
