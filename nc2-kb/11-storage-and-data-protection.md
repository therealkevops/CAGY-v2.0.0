# 11 - Storage, Data Protection & Disaster Recovery in NC2

## 1. Distributed Storage Fabric (DSF) in Cloud Environments

The **Distributed Storage Fabric (DSF)** pools the raw physical NVMe drives attached to bare-metal instances into a resilient, software-defined enterprise storage tier across the cluster.

```
+-----------------------------------------------------------------------------------------------+
|                            DISTRIBUTED STORAGE FABRIC (DSF)                                   |
+-----------------------------------------------------------------------------------------------+
| Bare-Metal Node 1 (CVM 1)       Bare-Metal Node 2 (CVM 2)       Bare-Metal Node 3 (CVM 3)     |
| [NVMe 0..7: 30TB]               [NVMe 0..7: 30TB]               [NVMe 0..7: 30TB]             |
+---------------------------------+-------------------------------+-----------------------------+
|                                  \              |              /                              |
|                                   v             v             v                               |
|                     +-------------------------------------------------------+                 |
|                     |        Unified Cluster Storage Pool (90 TB Raw)       |                 |
|                     +-------------------------------------------------------+                 |
|                                                 |                                             |
|                     +-------------------------------------------------------+                 |
|                     |       Storage Containers (RF2 / RF3 Replication)      |                 |
|                     |       - Inline Compression (LZ4)                      |                 |
|                     |       - Erasure Coding (EC-X 4:1)                     |                 |
|                     |       - Deduplication (Global Metadata Cache)         |                 |
|                     +-------------------------------------------------------+                 |
|                                      /          |          \                                  |
|                                     v           v           v                                 |
|                             [ User VM 1 ]  [ User VM 2 ]  [ User VM 3 ]                       |
+-----------------------------------------------------------------------------------------------+
```

---

## 2. Storage Optimization & Efficiency Technologies

```
+-----------------------------------------------------------------------------------------------+
|                              STORAGE OPTIMIZATION TECHNOLOGIES                                |
+------------------------+------------------------------------+---------------------------------+
| Technology             | Mechanism                          | Impact / Best For               |
+------------------------+------------------------------------+---------------------------------+
| Inline Compression     | Compresses 32KB/64KB data chunks   | 1.5x - 3x storage savings;      |
| (LZ4 Algorithm)        | in CPU memory before writing.      | Zero latency overhead.          |
+------------------------+------------------------------------+---------------------------------+
| Post-Process           | Background compression of cold     | Up to 4x storage reduction for  |
| Compression (GZIP)     | data blocks during idle I/O.       | archival/backup workloads.      |
+------------------------+------------------------------------+---------------------------------+
| Erasure Coding (EC-X)  | Calculates parity stripes across   | Reclaims up to 50% raw storage  |
|                        | nodes (e.g. 4:1 for RF2; 4:2 RF3)  | on cold vDisk blocks.           |
+------------------------+------------------------------------+---------------------------------+
| Global Fingerprint     | De-duplicates identical data       | Ideal for VDI and linked-clone  |
| Deduplication          | blocks across VMs in memory/cache. | virtual desktops.               |
+------------------------+------------------------------------+---------------------------------+
```

---

## 3. Nutanix Disaster Recovery (Formerly Leap)

Nutanix Disaster Recovery provides automated failover, failback, non-disruptive testing, and recovery plan orchestration managed natively within Prism Central.

```mermaid
graph LR
    subgraph Primary_DC["Primary Datacenter (On-Premises)"]
        Prod_PC["Prism Central (Primary)"]
        Prod_VMs["Production VMs"]
        Prod_Cluster["On-Prem AHV / ESXi Cluster"]
    end

    subgraph DR_Target["DR Site: NC2 on AWS / Azure / GCP"]
        DR_PC["Prism Central (Cloud)"]
        DR_Cluster["NC2 Bare-Metal Cluster (AHV)"]
        Failover_VMs["Recovered VMs (Auto-Started)"]
    end

    Prod_Cluster -->|Continuous Replication (NearSync 1-15m RPO)| DR_Cluster
    Prod_PC <-->|Availability Zone Pairing & Policy Sync| DR_PC
```

### Replication Engines Matrix

| Replication Type | Supported RPO | Network Latency Req | Supported NC2 Topologies |
|---|---|---|---|
| **Asynchronous** | >= 60 minutes | Tolerant (High latency / Low BW) | All AOS versions (On-Prem <-> Cloud, Cloud <-> Cloud) |
| **NearSync** | **1 minute to 15 minutes** | Low latency (<15ms RTT recommended) | AOS 6.7.1.5+ (On-Prem <-> NC2, NC2 AWS <-> Azure) |
| **Synchronous** | **Zero RPO (RPO=0)** | Ultra-low latency (<5ms RTT) | AOS 6.8.1+ (Intra-region / Cross-AZ within same region) |

---

## 4. Pilot-Light Disaster Recovery Architecture

```
+-----------------------------------------------------------------------------------------------+
|                               PILOT-LIGHT DR COST MODEL                                       |
+-----------------------------------------------------------------------------------------------+
| Normal Steady State:                                                                          |
| - Minimum 3-Node NC2 cluster running in AWS/Azure/GCP.                                         |
| - NearSync continuously hydrates storage pools with on-premises delta snapshots.              |
| - Zero User VMs running on NC2 (compute/RAM idle).                                            |
|                                                                                               |
| Disaster Event Triggered:                                                                     |
| 1. Recovery Plan triggered via Prism Central API or UI.                                       |
| 2. NC2 Orchestrator auto-scales cluster from 3 nodes -> 12 nodes (adding bare-metal nodes).   |
| 3. Recovery Plan powers on all enterprise workloads in designated boot sequence.              |
| 4. Production traffic redirected to Cloud IP endpoints via DNS (Route 53 / Azure DNS).       |
+-----------------------------------------------------------------------------------------------+
```

---

## 5. Workload Migration with Nutanix Move

**Nutanix Move** migrates running virtual machines into NC2 with near-zero downtime:
*   **Supported Sources**: VMware vSphere (ESXi 6.x-8.x), Microsoft Hyper-V, AWS EC2 native instances, Azure native VMs, Physical Windows/RHEL servers.
*   **Automatic VirtIO Driver Injection**: Pre-installs AHV drivers before cutover.
*   **Scheduled Cutover (<5 min downtime)**: Final delta sync and clean power-on on NC2 AHV.
*   **Test Migration Mode**: Validates application integrity on an isolated overlay network before cutover.

---

## 6. Nutanix Unified Storage (NUS) on NC2

*   **Nutanix Files**: Software-defined scale-out SMB 2.1/3.0 and NFS v3/v4 file server with ransomware protection.
*   **Nutanix Objects**: S3-compliant distributed object storage for cloud-native apps and long-term archiving.
*   **Nutanix Volumes**: Block storage exposed over scale-out iSCSI for physical database clusters.
