# 12 - Operations, Lifecycle & Cost Optimization in NC2

## 1. Day-2 Operations & Lifecycle Management

Managing Nutanix Cloud Clusters (NC2) provides the same operational simplicity as on-premises Nutanix appliances, combined with cloud agility.

```
+-----------------------------------------------------------------------------------------------+
|                             NC2 OPERATIONS CONTROL PLANES                                     |
+------------------------------------+----------------------------------------------------------+
| Control Plane                      | Core Operational Functions                               |
+------------------------------------+----------------------------------------------------------+
| NC2 SaaS Console                   | Bare-metal node lifecycle, Cluster creation, scaling,    |
| (cloud.nutanix.com)                | node repairs, Hibernation / Resume, Cloud billing sync.  |
+------------------------------------+----------------------------------------------------------+
| Nutanix Central                    | Global single-pane-of-glass dashboard across on-prem,    |
| (central.nutanix.com)              | AWS, Azure, and GCP Prism Central instances.             |
+------------------------------------+----------------------------------------------------------+
| Prism Central (PC)                 | VM lifecycle, Flow Networking, Microsegmentation, DR     |
| (Local or Cloud Deployment)        | runbooks, App blueprints, Role-Based Access Control.     |
+------------------------------------+----------------------------------------------------------+
| LifeCycle Manager (LCM)            | 1-click rolling non-disruptive software and firmware     |
| (Built into Prism)                 | upgrades (AOS, AHV, NCC, Foundation, Files, Objects).    |
+------------------------------------+----------------------------------------------------------+
```

---

## 2. Cluster Hibernation & Resume Deep Dive

The **Cluster Hibernate and Resume** feature is one of the most powerful cost-optimization mechanisms in NC2, allowing organizations to eliminate expensive bare-metal compute charges when clusters are not actively in use.

```mermaid
sequenceDiagram
    autonumber
    actor Admin as Cloud Admin
    participant Console as NC2 Console (cloud.nutanix.com)
    participant Cluster as NC2 Bare-Metal Cluster
    participant S3 as Object Storage (Amazon S3 / Azure Blob)
    participant Cloud as Cloud Provider (AWS EC2 / Azure BareMetal)

    Note over Admin,Cloud: HIBERNATION WORKFLOW
    Admin->>Console: Triggers "Hibernate Cluster"
    Console->>Cluster: Gracefully shuts down User VMs & Prism Central
    Console->>Cluster: Flushes Distributed Storage Fabric (DSF) metadata to disk
    Cluster->>S3: Uploads cluster configuration, metadata, & storage pool snapshots
    Console->>Cloud: Terminates bare-metal EC2/Azure compute nodes
    Note over Console,Cloud: Bare-metal compute billing STOPS (0$ compute cost)

    Note over Admin,Cloud: RESUME WORKFLOW (On-Demand)
    Admin->>Console: Triggers "Resume Cluster"
    Console->>Cloud: Provisions new bare-metal nodes in target AZ/VNet
    Console->>Cluster: Installs AHV hypervisor and boots CVMs
    Cluster->>S3: Restores cluster configuration & metadata from S3 snapshots
    Cluster->>Cluster: Restores DSF storage pool consistency & network ENIs
    Console->>Admin: Cluster Online (Admin powers on User VMs & Apps)
```

---

## 3. Elastic Cluster Auto-Scaling

NC2 allows clusters to dynamically expand and shrink based on real-time resource utilization:

```
+-----------------------------------------------------------------------------------------------+
|                               AUTO-SCALING POLICIES & THRESHOLDS                              |
+------------------------------------+----------------------------------------------------------+
| Trigger Metric                     | Recommended Threshold & Action                           |
+------------------------------------+----------------------------------------------------------+
| Storage Capacity Utilization       | Trigger Scale-Out when storage exceeds 75% capacity.     |
|                                    | Adds 1 bare-metal node to prevent storage exhaustion.    |
+------------------------------------+----------------------------------------------------------+
| Memory (RAM) Overcommit            | Trigger Scale-Out when host RAM utilization > 85%.       |
+------------------------------------+----------------------------------------------------------+
| CPU Utilization                    | Trigger Scale-Out when cluster CPU average > 80% for 15m.|
+------------------------------------+----------------------------------------------------------+
| Scale-In Guardrails                | Wait for 60-minute cooldown period before removing nodes.|
+------------------------------------+----------------------------------------------------------+
```

---

## 4. Non-Disruptive Upgrades with LifeCycle Manager (LCM)

LCM automates software and firmware updates across the entire hybrid estate without taking user applications offline.

```mermaid
graph TD
    A["Admin clicks 'Update AOS / AHV' in LCM"] --> B["Pre-Upgrade Health Checks (NCC) Verify Cluster Health"]
    B --> C["Select Target Host 1"]
    C --> D["Live-Migrate all User VMs off Host 1 to remaining hosts"]
    D --> E["Host 1 enters Maintenance Mode & executes software update"]
    E --> F["Host 1 reboots into updated AHV / AOS build"]
    F --> G["CVM boots, joins cluster, and verifies DSF data integrity"]
    G --> H["User VMs migrate back (Data Locality restored)"]
    H --> I["Repeat sequentially for Host 2, Host 3 ... Host N"]
    I --> J["1-Click Upgrade Complete with 0 Downtime"]
```
