# 13 - Licensing, Pricing & Sizing Methodology for NC2

## 1. Nutanix Software Licensing Framework

Nutanix software licensing for NC2 is based on a flexible, portable, capacity-based model (Core and TiB metrics) under the **Nutanix Cloud Platform (NCP)** portfolio.

```
+-----------------------------------------------------------------------------------------------+
|                            NUTANIX CLOUD PLATFORM (NCP) TIERS                                 |
+---------------------+-------------------------------------------------------------------------+
| Software Edition    | Capabilities & Inclusions                                               |
+---------------------+-------------------------------------------------------------------------+
| NCI Starter         | Core HCI storage & compute, basic AHV virtualization, data locality.   |
+---------------------+-------------------------------------------------------------------------+
| NCI Pro             | Enterprise DSF, Inline Compression, Deduplication, EC-X, Native DR,   |
| (Most Popular)      | Multi-cluster management with Prism Central.                            |
+---------------------+-------------------------------------------------------------------------+
| NCI Ultimate        | Advanced multi-site disaster recovery, Synchronous replication (Metro), |
|                     | Software data-at-rest encryption, Advanced Flow Virtual Networking.     |
+---------------------+-------------------------------------------------------------------------+
| NCM                 | Nutanix Cloud Manager: Intelligent Operations, Cost Governance,         |
| (Starter/Pro/Ult)   | Self-Service / Calm Application Automation, Security Compliance.        |
+---------------------+-------------------------------------------------------------------------+
```

---

## 2. Procurement Models: BYOL vs Cloud Marketplace (PAYG)

1. **Bring Your Own License (BYOL)**:
   - Move active term-based licenses seamlessly between physical on-premises servers and cloud bare-metal instances without conversion fees.
2. **Cloud Marketplace (PAYG / 1-Click)**:
   - Purchase Nutanix software licenses directly via AWS Marketplace or Azure Marketplace on an hourly or multi-year basis.
   - Fully eligible to count towards **AWS Enterprise Discount Program (EDP)** and **Microsoft Azure Consumption Commitment (MACC)** spend commitments.

---

## 3. Total Cost of Ownership (TCO) Breakdown

```
+-----------------------------------------------------------------------------------------------+
|                               TOTAL COST OF OWNERSHIP (TCO)                                   |
+------------------------------------+----------------------------------------------------------+
| Cost Component                     | Billing Entity & Optimization Opportunities              |
+------------------------------------+----------------------------------------------------------+
| 1. Nutanix Software License        | Nutanix (BYOL) or Cloud Marketplace (PAYG).              |
|                                    | Optimize via multi-year term commitments.                |
+------------------------------------+----------------------------------------------------------+
| 2. Cloud Bare-Metal Compute        | Billed directly by AWS / Azure / GCP.                    |
|                                    | Optimize using 1-yr / 3-yr Reserved Instances (RIs),     |
|                                    | Savings Plans, or Hibernate & Resume during off-hours.   |
+------------------------------------+----------------------------------------------------------+
| 3. Cloud Networking & Storage      | Billed directly by AWS / Azure / GCP.                    |
|                                    | Intra-AZ traffic is free; Inter-AZ / Egress billed by GB.|
|                                    | S3/Blob storage for backups and hibernation snapshots.   |
+------------------------------------+----------------------------------------------------------+
```

---

## 4. Cluster Sizing Methodology & Resource Overhead

### 1. CVM and Hypervisor Footprint Overhead
```
+-----------------------------------------------------------------------------------------------+
|                             NODE OVERHEAD SIZING GUIDELINES                                   |
+-------------------+--------------------+--------------------+---------------------------------+
| Component         | vCPUs Reserved     | RAM Reserved       | Notes                           |
+-------------------+--------------------+--------------------+---------------------------------+
| AHV Hypervisor    | 2 vCPUs            | 4 to 8 GB RAM      | Host OS and virtualization stack|
+-------------------+--------------------+--------------------+---------------------------------+
| Controller VM     | 8 to 16 vCPUs      | 32 to 64 GB RAM    | Base: 32 GB; Expand to 64 GB    |
| (CVM Base)        |                    |                    | when Deduplication/Flow enabled |
+-------------------+--------------------+--------------------+---------------------------------+
| Total Host Avail  | Total Node vCPUs   | Total Node RAM     | Remainder is 100% available     |
| for User VMs      | minus (10-18)      | minus (36-72 GB)   | for User Virtual Machines       |
+-------------------+--------------------+--------------------+---------------------------------+
```

### 2. Usable Storage Capacity Formula

$$\text{Usable Storage} = \frac{(\text{Raw NVMe Disks} - \text{CVM System Partitions}) \times \text{Efficiency Factor}}{\text{Replication Factor (2 or 3)}}$$

*   **Raw Storage**: Sum of all physical NVMe drives across bare-metal nodes.
*   **System Reserve**: ~150 GB per node for AHV and CVM operating system partitions.
*   **Replication Factor (RF2)**: Divides storage pool by 2 for mirror copies.
*   **Efficiency Multiplier**: Typical enterprise workload data reduction (Compression + EC-X): **1.5x to 2.0x**.
*   **N+1 High Availability Reserve**: Reserve 1 node's storage capacity for automatic rebuild headroom.
