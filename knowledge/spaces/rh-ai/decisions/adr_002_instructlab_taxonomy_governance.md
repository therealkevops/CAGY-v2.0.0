# ADR 002: Adopt InstructLab Git Taxonomy for Enterprise Knowledge Grounding and Synthetic Data Generation

> **Space**: `rh-ai`  
> **Status**: `Accepted`  
> **Date**: 2026-09-09  
> **Context**: [[spaces/rh-ai/notes/03-instructlab-and-model-alignment|InstructLab Alignment]] | [[spaces/rh-ai/notes/02-rhel-ai-architecture-and-deployment|RHEL AI Deployment]]

## Context & Problem Statement
Fine-tuning foundation models on enterprise proprietary knowledge typically requires costly manual human annotation, expensive Reinforcement Learning from Human Feedback (RLHF), and risks catastrophic forgetting of general reasoning capabilities. Furthermore, untracked training data introduces legal risks and reproducibility failures.

## Decision Outcome
**Adopt the InstructLab LAB (Large-scale Alignment for chatBots) methodology and Git-based taxonomy as the organization's standard for model customization**:
- **Git-Centric Taxonomy**: Store all domain knowledge and procedural skills as version-controlled YAML files (`qna.yaml`) in a centralized Git repository.
- **Strict Separation of Knowledge & Skills**:
  - `knowledge/`: Grounded strictly in verified reference documents with attribution metadata.
  - `skills/`: Compositional reasoning and procedural step-by-step tasks.
- **Synthetic Data Generation (SDG)**: Leverage high-capacity teacher models (Mixtral 8x7B / Granite 20B) to generate hundreds of synthetic instruction pairs from seed examples, verified by critic filtering.
- **Phased Fine-Tuning**: Enforce two-phase training (Knowledge Tuning followed by Skills Tuning) to eliminate catastrophic forgetting.
- **Promotion Lifecycle**: Prototype and validate locally on RHEL AI instances, then promote verified taxonomy PRs to OpenShift AI distributed training clusters.
- **Related Notes**: [[spaces/rh-ai/notes/03-instructlab-and-model-alignment|InstructLab Guide]] and [[spaces/rh-ai/architecture/instructlab_alignment_pipeline|InstructLab Pipeline Architecture]].
