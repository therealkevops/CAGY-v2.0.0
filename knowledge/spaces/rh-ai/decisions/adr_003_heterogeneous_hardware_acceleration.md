# ADR 003: Standardize Heterogeneous Hardware Acceleration Framework (NVIDIA, AMD, Intel)

> **Space**: `rh-ai`  
> **Status**: `Accepted`  
> **Date**: 2026-09-09  
> **Context**: [[spaces/rh-ai/notes/10-hardware-acceleration-and-cluster-sizing|Hardware Sizing Guide]] | [[spaces/rh-ai/notes/05-distributed-training-and-orchestration|Distributed Orchestration]]

## Context & Problem Statement
Over-reliance on a single hardware accelerator vendor exposes enterprise AI operations to severe supply chain delays, high procurement premiums, and limited architecture choice. Different AI workloads possess distinct hardware requirements: LLM training requires high memory bandwidth and fast all-reduce fabrics, while high-density inference can be efficiently handled by lower-cost accelerators.

## Decision Outcome
**Adopt a multi-vendor, operator-abstracted hardware acceleration strategy across OpenShift AI and RHEL AI**:
- **Operator-Driven Decoupling**: Abstract silicon hardware via the **NVIDIA GPU Operator**, **AMD ROCm Operator**, and **Intel Gaudi Operator**, orchestrated by Node Feature Discovery (NFD).
- **Workload Tiering**:
  - **Tier 1 (High-Scale Distributed Training & 70B+ Serving)**: NVIDIA H100 SXM5 / AMD Instinct MI300X (192GB HBM3) interconnected with RoCEv2 400GbE / InfiniBand fabrics.
  - **Tier 2 (Enterprise RAG & 8B-20B Model Serving)**: NVIDIA L40S (48GB GDDR6) or A100 PCIe for general enterprise inference and InstructLab SDG.
  - **Tier 3 (Edge / Developer Workstations)**: Single NVIDIA L4 / A10G or CPU fallback on RHEL AI bootable containers.
- **Batch Schedulers**: Utilize Kueue ResourceFlavors to map tasks to appropriate GPU architectures dynamically based on job priority and memory demands.
- **Related Notes**: [[spaces/rh-ai/notes/10-hardware-acceleration-and-cluster-sizing|Hardware Sizing Guide]] and [[spaces/rh-ai/architecture/hybrid_cloud_ai_fabric|Hybrid Cloud AI Fabric]].
