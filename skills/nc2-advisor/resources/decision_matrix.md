# NC2 Architecture Decision Matrix

This reference guide provides quick-look decision matrices for advising enterprise customers on Nutanix Cloud Clusters.

---

## 1. Cloud Provider Selection Matrix

```
+------------------------------------------------------------------------------------------------------------------------+
|                                    CLOUD PROVIDER SELECTION DECISION MATRIX                                            |
+--------------------------+-----------------------------+-------------------------------+-------------------------------+
| Criteria                 | NC2 on AWS                  | NC2 on Microsoft Azure        | NC2 on Google Cloud (GCP)     |
+--------------------------+-----------------------------+-------------------------------+-------------------------------+
| Primary Workload Drivers | AWS Native services (S3/RDS)| Azure Enterprise Apps, AD/M365| Google BigQuery, Vertex AI,   |
|                          | High I/O Databases          | Microsoft SQL Server          | Cloud-Native Container stacks |
+--------------------------+-----------------------------+-------------------------------+-------------------------------+
| Enterprise Commitments   | AWS EDP (Enterprise Disc.)  | Microsoft MACC Spend Commit   | GCP Committed Use Discounts   |
+--------------------------+-----------------------------+-------------------------------+-------------------------------+
| Storage Capacity Density | `i3en.metal` (60TB / node)  | `AN64` (38.4TB / node)        | `c3-standard-176-bm` (40TB)   |
+--------------------------+-----------------------------+-------------------------------+-------------------------------+
| Compute Density (Cores)  | `i4i.metal` (128 vCPUs)     | `AN64` (128 vCPUs)            | `c3-standard-176-bm` (176 vC) |
+--------------------------+-----------------------------+-------------------------------+-------------------------------+
| Hibernation Support      | Native AWS S3 snapshotting  | Standard (Blob storage)       | Standard (Cloud Storage)      |
+--------------------------+-----------------------------+-------------------------------+-------------------------------+
```

---

## 2. Azure Flow Gateway Routing Decision Tree

```
Are you deploying NC2 on Azure?
  │
  ├─► Is your enterprise network standardized on Azure Virtual WAN?
  │     └─► YES: Use [Azure Virtual WAN (vWAN) Hub-and-Spoke]
  │               - Direct eBGP peering with vHub Router (ASN 65515).
  │               - Apply traffic inspection policies via Secured Virtual Hub.
  │
  ├─► Is your Prism Central version 7.5 or later on a new greenfield cluster?
  │     └─► YES (Recommended): Use [Azure Internal Load Balancer (ILB)]
  │               - Eliminates 2 dedicated BGP VMs.
  │               - Eliminates Azure Route Server hourly charges.
  │               - 5s TCP Port 22 health probes with sub-second failover.
  │
  └─► Do you have complex multi-hop BGP peering with third-party NVAs?
        └─► YES: Use [Azure Route Server (ARS)]
                  - Dedicated BGP Gateway VMs in Gateway Subnet.
                  - 8 BGP peer limit per Route Server.
```

---

## 3. Disaster Recovery RPO Tiering

```
+------------------------------------------------------------------------------------------------------------------------+
|                                    DISASTER RECOVERY ARCHITECTURAL TIERS                                               |
+-------------------+---------------------+-------------------------+----------------------------------------------------+
| Tier / Engine     | Target RPO SLA      | Target RTO SLA          | Minimum Network Latency & Infrastructure           |
+-------------------+---------------------+-------------------------+----------------------------------------------------+
| Synchronous       | **RPO = 0**         | Instant (< 1 minute)    | < 5ms RTT Latency (Intra-region / Same Metro Area) |
| (Metro)           | (Zero Data Loss)    |                         | 10 Gbps+ dedicated Direct Connect / ExpressRoute   |
+-------------------+---------------------+-------------------------+----------------------------------------------------+
| NearSync          | **1 to 15 minutes** | Low (< 15 minutes)      | < 15ms RTT Latency; AOS 6.7.1.5+                   |
|                   |                     |                         | High-throughput hybrid interconnect (1G to 10G)    |
+-------------------+---------------------+-------------------------+----------------------------------------------------+
| Asynchronous      | **>= 60 minutes**   | Moderate (15 - 60 mins) | Tolerant of WAN latency / Lower bandwidth links    |
|                   |                     |                         | Any standard IPsec VPN or Direct Connect           |
+-------------------+---------------------+-------------------------+----------------------------------------------------+
```
