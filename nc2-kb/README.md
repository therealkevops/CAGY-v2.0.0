# Nutanix Cloud Clusters (NC2) - Comprehensive Knowledge Base

Welcome to the **Nutanix Cloud Clusters (NC2) Knowledge Base**. This repository provides an enterprise-grade, deeply technical reference architecture, deployment guides, operational manuals, troubleshooting playbooks, and best practices for running Nutanix software natively across public cloud hyperscalers (Amazon Web Services, Microsoft Azure, and Google Cloud) and air-gapped sovereign clouds (Government Cloud Clusters / GC2).

---

## 📚 Complete Numbered Knowledge Base Structure

```
+------------------------------------------------------------------------------------------------------------------------+
|                                    NUTANIX CLOUD CLUSTERS (NC2) KNOWLEDGE BASE                                         |
+-----+-------------------------------------------------------------------+----------------------------------------------+
| #   | File                                                              | Primary Architectural Focus                  |
+-----+-------------------------------------------------------------------+----------------------------------------------+
| 01  | 01-architecture-overview.md                                       | Core NCP concepts, AOS, AHV, CVM, DSF, SaaS  |
| 02  | 02-nc2-on-aws.md                                                  | AWS Bare-Metal, Nitro, CloudFormation, PPG   |
| 03  | 03-nc2-on-azure.md                                                | Azure Dedicated Nodes, Entra ID, Key Vault   |
| 04  | 04-nc2-on-gcp.md                                                  | GCP Bare-Metal, Hyperdisk, Project Setup     |
| 05  | 05-nc2-government-cloud-gc2.md                                    | GC2 Air-gapped Embedded Orchestration & STIG |
| 06  | 06-networking-aws.md                                              | AWS Native ENI, CNC, CPM, GARP, Transit VPC  |
| 07  | 07-networking-azure.md                                            | Azure Delegated Subnet, Flow Gateway, vWAN,  |
|     |                                                                   | Azure Route Server (ARS), Azure ILB Routing  |
| 08  | 08-networking-gcp.md                                              | GCP Alias IP ranges, gVNIC, Cloud Router BGP |
| 09  | 09-networking-gc2.md                                              | GC2 Zero-Egress Air-Gapped Network Topology  |
| 10  | 10-networking-hybrid-and-multicloud.md                            | Direct Connect, ExpressRoute, Interconnect   |
| 11  | 11-storage-and-data-protection.md                                 | DSF, RF2/RF3, NearSync (1m RPO), Nutanix Move|
| 12  | 12-operations-lifecycle-cost-optimization.md                      | LCM 1-Click Upgrades, Hibernate & Resume     |
| 13  | 13-licensing-pricing-sizing.md                                    | NCI/NCM Tiers, BYOL, Sizer Formulas, CVM Ovh |
| 14  | 14-security-compliance-governance.md                              | AES-256 DAR, KMS, SCMA STIG, FIPS 140-2      |
| 15  | 15-troubleshooting-and-faqs.md                                    | Layered Diagnostics, CLI Toolkit, Top FAQs   |
| 16  | 16-api-iac-and-automation.md                                      | NC2 v2 REST APIs, Terraform, Ansible, Python |
+-----+-------------------------------------------------------------------+----------------------------------------------+
```

---

## 📑 Module Catalog & Direct Links

### Section I: Platform Architecture & Cloud Platforms
*   [**01 - Architecture Overview**](file:///workspace/nc2-kb/01-architecture-overview.md): Core platform components (AOS, AHV, CVM, DSF), NC2 SaaS Control Plane (`cloud.nutanix.com`), Multicloud topologies, and the Shared Responsibility model.
*   [**02 - NC2 on AWS**](file:///workspace/nc2-kb/02-nc2-on-aws.md): EC2 bare-metal instances (`i4i.metal`, `i3en.metal`, `m6id.metal`), AWS IAM role onboarding, and Partition Placement Groups.
*   [**03 - NC2 on Microsoft Azure**](file:///workspace/nc2-kb/03-nc2-on-azure.md): Azure bare-metal SKUs (`AN36`, `AN36P`, `AN64`), Entra ID Service Principals, and Azure Key Vault encryption.
*   [**04 - NC2 on Google Cloud (GCP)**](file:///workspace/nc2-kb/04-nc2-on-gcp.md): Google Compute Engine bare-metal nodes, Google Cloud Hyperdisk boot volumes, and GCP project provisioning.
*   [**05 - Nutanix Government Cloud Clusters (GC2)**](file:///workspace/nc2-kb/05-nc2-government-cloud-gc2.md): Air-gapped sovereign architecture for AWS GovCloud, Azure Government, SC2S/C2S enclaves, embedded orchestration in Prism Element, and DoD IL5/IL6 compliance.

---

### Section II: Deep-Dive Cloud Networking per Provider
*   [**06 - NC2 Networking Deep Dive: AWS**](file:///workspace/nc2-kb/06-networking-aws.md): AWS Nitro ENA, Cloud Network Controller (CNC), Cloud Port Manager (CPM), ENI secondary IP allocation algorithm, Live Migration Gratuitous ARP (GARP), Flow Transit VPC, AWS Transit Gateway, and Jumbo Frames (MTU 9001).
*   [**07 - NC2 Networking Deep Dive: Microsoft Azure**](file:///workspace/nc2-kb/07-networking-azure.md): BareMetal Delegated Subnets (`Microsoft.BareMetal`), Scale-out Flow Gateways (FGW), **Azure Virtual WAN (vWAN) vs Azure Route Server (ARS) vs Azure Internal Load Balancer (ILB)** routing comparison, and TCP MSS clamping (1410).
*   [**08 - NC2 Networking Deep Dive: Google Cloud Platform (GCP)**](file:///workspace/nc2-kb/08-networking-gcp.md): Native GCP VPC Alias IP ranges, Google Virtual NIC (gVNIC), Google Cloud Router BGP dynamic routing, Cloud NAT, and Andromeda SDN.
*   [**09 - NC2 Networking Deep Dive: Government Cloud (GC2)**](file:///workspace/nc2-kb/09-networking-gc2.md): Air-gapped sovereign network topologies, zero-outbound internet routing, localized DNS/NTP/PKI, DISA STIG SCMA verification, and microsegmentation.
*   [**10 - Hybrid & Multicloud WAN Interconnect Architecture**](file:///workspace/nc2-kb/10-networking-hybrid-and-multicloud.md): AWS Direct Connect, Azure ExpressRoute, Google Interconnect, IPsec VPN tunnels, BGP AS Path prepending, and DR network mapping (IP preservation vs remapping).

---

### Section III: Storage, Operations, Security & Automation
*   [**11 - Storage, Data Protection & Disaster Recovery**](file:///workspace/nc2-kb/11-storage-and-data-protection.md): Distributed Storage Fabric (DSF), RF2/RF3 replication, inline compression/dedup/EC-X, Nutanix DR (NearSync 1-minute RPO, Sync RPO=0), Nutanix Move migrations, and Unified Storage (Files/Objects/Volumes).
*   [**12 - Operations, Lifecycle & Cost Optimization**](file:///workspace/nc2-kb/12-operations-lifecycle-cost-optimization.md): Non-disruptive rolling upgrades with LifeCycle Manager (LCM), Auto-scaling elastic clusters, **Cluster Hibernate & Resume** (saving up to 98% in idle compute costs), Prism Central, and Nutanix Central.
*   [**13 - Licensing, Pricing & Sizing Methodology**](file:///workspace/nc2-kb/13-licensing-pricing-sizing.md): Nutanix Cloud Platform (NCI/NCM) tiers, BYOL portability, Cloud Marketplace billing, Nutanix Sizer formulas, CVM footprint overhead, and N+1 resilience headroom.
*   [**14 - Security, Governance & Compliance**](file:///workspace/nc2-kb/14-security-compliance-governance.md): Software-defined AES-256 Data-at-Rest Encryption (AWS KMS / Azure Key Vault / HashiCorp Vault), SAML SSO, granular RBAC, automated STIG compliance (SCMA), and FIPS 140-2 / FedRAMP / SOC2 standards.
*   [**15 - Troubleshooting Playbook & Top FAQs**](file:///workspace/nc2-kb/15-troubleshooting-and-faqs.md): Methodical layered diagnostic framework, essential CLI commands (`ncli`, `ncc`, `cvm_stat`, `ovs-appctl`), MTU & routing resolution steps, and top FAQs.
*   [**16 - API, Infrastructure-as-Code (IaC) & Automation**](file:///workspace/nc2-kb/16-api-iac-and-automation.md): NC2 v2 REST APIs (OAS3), Terraform Provider (`nutanix/nutanix`), NCM Self-Service / Calm blueprints, Ansible playbooks, and Python v4 SDK scripts.

---

## 🧭 Quick Reference Architecture

```
+-------------------------------------------------------------------------------+
|                       NUTANIX HYBRID MULTICLOUD FABRIC                        |
+-------------------------------------------------------------------------------+
|                      Unified Management & Orchestration                       |
|   [ Nutanix Central ] <---> [ NC2 SaaS Console ] <---> [ Prism Central ]     |
|   (Global Visibility)     (cloud.nutanix.com)       (Fleet Management)       |
+-------------------------------------------------------------------------------+
           |                                             |
           v                                             v
+-----------------------------+               +-----------------------------+
|    On-Premises Datacenter   |               |     Hyperscaler Cloud       |
|    (Nutanix HCI Appliance)  |  Hybrid WAN   | (NC2 on AWS / Azure / GCP)  |
| +-------------------------+ | (DX/ExpressR) | +-------------------------+ |
| | User VMs / Applications | | <===========> | | User VMs / Applications | |
| +-------------------------+ |   IPsec VPN   | +-------------------------+ |
| | AHV / ESXi Hypervisor   | |               | | AHV Hypervisor          | |
| +-------------------------+ |               | +-------------------------+ |
| | Nutanix CVM & DSF       | |  Replication  | | Nutanix CVM & DSF       | |
| +-------------------------+ |  (NearSync)   | +-------------------------+ |
| | On-Prem Server Hardware | |               | | Cloud Bare-Metal Nodes  | |
| +-------------------------+ |               | +-------------------------+ |
+-----------------------------+               +-----------------------------+
```
