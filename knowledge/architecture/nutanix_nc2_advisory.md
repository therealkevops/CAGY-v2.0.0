# Nutanix Cloud Clusters (NC2) Advisory Architecture

- **Date**: 2026-09-04
- **Category**: Architecture & Solutions
- **Tags**: #nc2 #nutanix #hybrid-cloud #aws #azure #gcp #gc2

## Overview
The Nutanix Cloud Clusters (NC2) advisory ecosystem combines a 16-chapter comprehensive reference architecture in `/workspace/nc2-kb/` with the specialized **`nc2-advisor`** domain skill in `/workspace/skills/nc2-advisor/`.

It enables the agent to function as a Principal Nutanix Solutions Architect, delivering evidence-based sizing, network validation, and disaster recovery planning across AWS, Microsoft Azure, Google Cloud Platform (GCP), and Sovereign Government Clouds (GC2).

---

## Core Advisory Subsystems

### 1. The 16-Chapter NC2 Knowledge Base (`/workspace/nc2-kb/`)
- Chapters `01` to `05`: Core architecture, AWS bare-metal, Azure dedicated nodes, GCP instances, and Government Cloud Clusters (GC2).
- Chapters `06` to `10`: Cloud-specific networking deep dives:
  - AWS Nitro ENA, Cloud Network Controller (CNC), Cloud Port Manager, and Transit VPCs.
  - Azure Delegated Subnets, Flow Gateways, and **Azure Virtual WAN (vWAN) vs Azure Route Server (ARS) vs Azure Internal Load Balancer (ILB)**.
  - GCP Alias IP ranges, gVNIC, and Google Cloud Router BGP.
  - GC2 air-gapped embedded orchestration in Prism Element.
  - Hybrid WAN interconnects (Direct Connect, ExpressRoute, Interconnect) and DR mapping.
- Chapters `11` to `16`: DSF storage pools, 1-Click LCM upgrades, **Cluster Hibernate & Resume** (up to 98% savings), NCP licensing, STIG security, diagnostics, and IaC/Terraform automation.

### 2. Built-in Advisor Tooling (`skills/nc2-advisor/`)
- **Automated Sizing Calculator** (`scripts/nc2_sizer.py`):
  - Calculates physical vCPU, RAM, raw NVMe capacity, CVM/AHV overhead, RF2/RF3 replication factors, data reduction (1.5x–2.5x), and N+1 rebuild headroom.
- **Network & MTU Budget Validator** (`scripts/nc2_network_validator.py`):
  - Validates Geneve encapsulation headroom (50 bytes), TCP MSS clamping rules (1410 on Azure/GCP, 8911 on AWS), and ENI secondary IP thresholds.
- **Decision Matrix** (`resources/decision_matrix.md`):
  - Fast-path decision trees for hyperscaler selection, Azure routing mechanisms, and DR RPO tiers.

---

## Related Notes & References
- Aligns with the Lead Architect profile in [[user/profile]].
- Memory managed within the second brain in [[architecture/knowledge_vault_and_graph]].
- Context monitored via [[architecture/token_economics_and_analytics]].
