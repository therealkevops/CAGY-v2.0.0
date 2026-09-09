# InstructLab Alignment & Synthetic Data Pipeline Architecture

> **Space**: `rh-ai`  
> **Category**: `architecture`  
> **Status**: Approved Reference  
> **Target Tooling**: InstructLab CLI (`ilab`), IBM Granite, Ray Distributed

---

## 1. End-to-End Alignment Workflow

The Large-scale Alignment for chatBots (LAB) methodology operationalizes continuous enterprise knowledge transfer without manual human annotation:

```mermaid
sequenceDiagram
    autonumber
    actor SME as Subject Matter Expert / Engineer
    participant Git as Enterprise Taxonomy Git Repo
    participant SDG as Synthetic Data Generator (Teacher)
    participant Critic as Quality & Safety Filter
    participant Trainer as Phased Multi-Stage Trainer
    participant Eval as Differential Benchmark Engine
    participant Reg as Model Registry / KServe

    SME->>Git: PR with domain qna.yaml + markdown docs
    Git->>Git: CI syntax check (ilab taxonomy diff)
    Git->>SDG: Triggers ilab data generate
    SDG->>SDG: Samples document chunks & prompts Teacher Model
    SDG->>Critic: Emits hundreds of synthetic Q&A pairs
    Critic->>Critic: Discards ungrounded or hallucinated pairs
    Critic->>Trainer: Outputs verified instruction dataset
    Trainer->>Trainer: Phase 1: Knowledge Tuning (Low learning rate)
    Trainer->>Trainer: Phase 2: Skills Tuning (Instruction masked)
    Trainer->>Eval: Emits candidate student model checkpoint
    Eval->>Eval: Runs MMLU, MT-Bench, and PR-specific questions
    alt Evaluation Passed (Delta >= Target)
        Eval->>Reg: Promotes checkpoint to Model Registry
        Reg->>KServe: Triggers automated canary serving deployment
    else Degradation Detected
        Eval-->>SME: Rejects PR with detailed failure metrics
    end
```

---

## 2. Multi-Phase Training Pipeline Mechanics

```mermaid
flowchart LR
    subgraph Inputs["Curated Inputs"]
        DOCS["Domain Markdown Docs"]
        SEEDS["5-10 Seed Q&As"]
    end

    subgraph Generation["Synthetic Generation"]
        TEACHER["High-Capacity Teacher\n(Mixtral 8x7B / Granite 20B)"]
        SYNTH["Synthetic Instruction Expander"]
    end

    subgraph TrainingPhases["Phased Fine-Tuning"]
        P1["Phase 1: Knowledge Tuning\n(Embedding facts & reference data)"]
        P2["Phase 2: Skills Tuning\n(Conversational flow & JSON tool calling)"]
    end

    subgraph OutputModel["Artifact Delivery"]
        ALIGNED["Aligned Granite Model Checkpoint"]
    end

    Inputs --> Generation
    TEACHER --> SYNTH
    SYNTH --> P1
    P1 --> P2
    P2 --> OutputModel
```

---

## Related Knowledge & Decision Links
- **Knowledge Hub**: [[spaces/rh-ai/notes/overview|Rh-Ai Knowledge Hub]]
- **Alignment Guide**: [[spaces/rh-ai/notes/03-instructlab-and-model-alignment|InstructLab Deep Dive]]
- **RHEL AI Deployment**: [[spaces/rh-ai/notes/02-rhel-ai-architecture-and-deployment|RHEL AI Deployment]]
- **Architectural Decision**: [[spaces/rh-ai/decisions/adr_002_instructlab_taxonomy_governance|ADR 002: InstructLab Taxonomy Governance]]
