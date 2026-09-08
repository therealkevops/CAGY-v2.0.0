# ADR 001: Adopt vLLM and LLM-D as Enterprise AI Serving & Orchestration Standard
> **Space**: `ai-kb`  
> **Status**: `Accepted`  
> **Date**: 2026-09-08  
> **Context**: [[spaces/ai-kb/notes/vllm-and-llmd-explained|vLLM & LLM-D Guide]] | [[spaces/ai-kb/notes/overview|AI-KB Overview]]

## Context & Problem Statement
Serving LLMs in enterprise production requires high concurrent throughput, low latency (TTFT and TPOT), and efficient memory utilization. Naive Python web servers and standard load balancers suffer from severe VRAM fragmentation, cache misses, and head-of-line blocking.

## Decision Outcome
**Adopt vLLM as the single-node inference engine and LLM-D as the distributed Kubernetes orchestrator**:
- **vLLM Engine**: Deployed for PagedAttention (reducing memory waste from 70%+ to <4%) and continuous batching.
- **LLM-D Scheduler**: Deployed on Kubernetes for cache-aware routing (3x throughput boost) and disaggregated prefill/decode pools.
- **Related Notes**: [[spaces/ai-kb/notes/ai-infrastructure-explained|AI Infrastructure]] and [[spaces/ai-kb/architecture/ai_cluster_topology|Cluster Topology]].
