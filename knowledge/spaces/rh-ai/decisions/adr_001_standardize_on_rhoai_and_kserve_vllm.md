# ADR 001: Standardize on Red Hat OpenShift AI (RHOAI) with KServe & vLLM for Model Serving

> **Space**: `rh-ai`  
> **Status**: `Accepted`  
> **Date**: 2026-09-09  
> **Context**: [[spaces/rh-ai/notes/04-openshift-ai-rhoai-architecture|RHOAI Platform Architecture]] | [[spaces/rh-ai/notes/06-model-serving-kserve-and-vllm|KServe & vLLM Serving]]

## Context & Problem Statement
Serving Large Language Models (LLMs) and generative AI across hybrid cloud environments requires an enterprise-hardened, multi-tenant Kubernetes platform. Ad-hoc deployments using raw Docker containers or unmanaged Python microservices lead to excessive GPU memory waste, lack of token streaming controls, missing security guardrails, and operational fragmentation across clouds.

## Decision Outcome
**Adopt Red Hat OpenShift AI (RHOAI) as the enterprise AI platform standard, deploying KServe paired with the vLLM ServingRuntime for high-throughput model inference**:
- **Operator-Driven Lifecycle**: Deploy and maintain the platform via the `rhods-operator` using declarative `DataScienceCluster` and `DSCInitialization` Custom Resources.
- **vLLM as Serving Engine**: Standardize on vLLM within KServe to leverage PagedAttention (reducing KV cache memory fragmentation to <4%), continuous batching, and chunked prefill.
- **Dual-Mode Deployment**: Use KServe Serverless (Knative + Istio) for sporadic/development endpoints with scale-to-zero, and RawDeployment with KEDA queue-based autoscaling for high-volume production endpoints.
- **Storage Substrate**: Standardize on OpenShift Data Foundation (ODF NooBaa S3) for immutable model artifact storage and fast checkpoint loading.
- **Related Notes**: [[spaces/rh-ai/notes/06-model-serving-kserve-and-vllm|KServe & vLLM Guide]] and [[spaces/rh-ai/architecture/rhoai_system_topology|RHOAI System Topology]].
