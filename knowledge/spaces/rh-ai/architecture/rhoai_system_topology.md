# Red Hat OpenShift AI (RHOAI) System Topology & Control Plane Architecture

> **Space**: `rh-ai`  
> **Category**: `architecture`  
> **Status**: Approved Reference  
> **Target Platform**: OpenShift Container Platform 4.16+, OpenShift AI 2.14+

---

## 1. System Topology Overview

Red Hat OpenShift AI (RHOAI) decouples user-facing interactive environments, cluster-wide batch scheduling, and low-latency model inference into a layered Kubernetes-native topology.

```mermaid
flowchart TD
    subgraph ControlPlane["OpenShift AI Operator & Control Plane"]
        RHODS["rhods-operator\n(Namespace: redhat-ods-operator)"]
        DSC["DataScienceCluster (default-dsc)"]
        DSCI["DSCInitialization (default-dsci)"]
        DASH["OpenShift AI Dashboard\n(Namespace: redhat-ods-applications)"]
        RHODS -->|Reconciles| DSC
        RHODS -->|Reconciles| DSCI
        DSC --> DASH
    end

    subgraph UserSpaces["Data Science Workspaces (Per Project / Namespace)"]
        WB["Workbenches\n(Jupyter / VS Code / RStudio)"]
        KFP["Data Science Pipelines (DSP)\n(Tekton Execution Engine)"]
        MR_CLI["Model Registry Client"]
    end

    subgraph BatchCompute["Distributed Compute & Queuing Tier"]
        KUEUE["Kueue Batch Scheduler\n(Gang Scheduling & Cohorts)"]
        KRAY["KubeRay Operator\n(Ray Clusters & RayJobs)"]
        KTO["Kubeflow Training Operator\n(PyTorchJob / MPIJob)"]
        KUEUE --> KRAY
        KUEUE --> KTO
    end

    subgraph ServingTier["High-Performance Model Serving Tier"]
        KS["KServe Controller"]
        VLLM["vLLM Serving Runtime\n(PagedAttention, Continuous Batching)"]
        MM["ModelMesh Serving Runtime\n(High-Density Multi-Model)"]
        ISTIO["OpenShift Service Mesh (Istio)\n+ Envoy Gateway (mTLS)"]
        KNATIVE["OpenShift Serverless (Knative)\n(Queue-Proxy & Autoscaling)"]
        KS --> VLLM
        KS --> MM
        ISTIO --> KNATIVE --> KS
    end

    subgraph StorageTier["OpenShift Data Foundation (Ceph)"]
        RBD["Ceph RBD Block\n(Workbench Home Dirs)"]
        CFS["CephFS Shared File\n(Distributed Datasets RWX)"]
        S3["NooBaa Object Store\n(Model Checkpoints & S3 Artifacts)"]
    end

    subgraph GovernanceTier["TrustyAI & Governance Tier"]
        TY["TrustyAI Operator\n(Fairness, Drift, LIME/SHAP)"]
        MREG["Model Registry Service\n(Metadata, Lineage, Versions)"]
    end

    DASH --> UserSpaces
    UserSpaces --> StorageTier
    UserSpaces --> BatchCompute
    BatchCompute --> StorageTier
    BatchCompute --> ServingTier
    ServingTier --> GovernanceTier
    ServingTier --> StorageTier
```

---

## 2. Component Boundaries & Network Flows

1. **Ingress & Service Mesh Flow**:
   - Client requests enter via OpenShift Ingress Router (HAProxy).
   - Ingress passes requests to the Istio Ingress Gateway in `istio-system`.
   - Istio applies mTLS and routes through Knative Queue-Proxy to the vLLM container on port 8080.
2. **Batch Training Flow**:
   - Data scientists submit jobs via the CodeFlare SDK or Kubernetes manifests to a Kueue `LocalQueue`.
   - Kueue checks quota against the `ClusterQueue`. When all requested GPUs are available, it gates all Pods simultaneously (Gang Scheduling).
   - Distributed PyTorch pods initialize NCCL over secondary SR-IOV/RoCEv2 networks configured via Multus CNI.
3. **Artifact Flow**:
   - Model weights and datasets reside in Ceph Object Storage (NooBaa S3).
   - KServe storage initializers mount S3 buckets directly into the runtime container filesystem (`/mnt/models`) via S3 credentials stored in Kubernetes Secrets.

---

## Related Knowledge & Decision Links
- **Knowledge Hub**: [[spaces/rh-ai/notes/overview|Rh-Ai Knowledge Hub]]
- **Domain Guide**: [[spaces/rh-ai/notes/04-openshift-ai-rhoai-architecture|RHOAI Platform Architecture]]
- **Serving Guide**: [[spaces/rh-ai/notes/06-model-serving-kserve-and-vllm|KServe & vLLM Deep Dive]]
- **Architectural Decision**: [[spaces/rh-ai/decisions/adr_001_standardize_on_rhoai_and_kserve_vllm|ADR 001: Standardize on RHOAI, KServe & vLLM]]
