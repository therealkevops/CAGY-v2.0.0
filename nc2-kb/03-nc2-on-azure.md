# 03 - Nutanix Cloud Clusters (NC2) on Microsoft Azure

## 1. Overview of NC2 on Microsoft Azure

Nutanix Cloud Clusters (NC2) on Azure provides dedicated bare-metal Azure hardware running Nutanix AHV and AOS directly within the customer's Microsoft Azure subscription.

---

## 2. Supported Azure Bare-Metal Node Types

Azure offers dedicated bare-metal SKUs purpose-built and certified for Nutanix Cloud Clusters:

```
+-------------------------------------------------------------------------------------------------------+
|                               AZURE BARE-METAL INSTANCE SPECIFICATIONS                                |
+---------------+---------+---------+------------------------+---------------------+--------------------+
| Node SKU      | vCPUs   | RAM(GiB)| Local NVMe Storage     | Network Uplink      | Target Workload    |
+---------------+---------+---------+------------------------+---------------------+--------------------+
| AN64          | 128     | 1024    | 8x 4.8 TB NVMe         | 100 Gbps            | Ultra-high compute,|
| (4th Gen Xeon)|         | (1 TB)  | (38.4 TB Raw NVMe)     | Accelerated Net     | Large DBs, AI/ML   |
+---------------+---------+---------+------------------------+---------------------+--------------------+
| AN36P         | 72      | 768     | 6x 3.45 TB NVMe        | 40 / 100 Gbps       | High Memory/VDI,   |
| (Cascade Lake)| (36 C)  |         | (20.7 TB Raw NVMe)     | Accelerated Net     | General Enterprise |
+---------------+---------+---------+------------------------+---------------------+--------------------+
| AN36          | 72      | 576     | 6x 3.09 TB NVMe        | 40 Gbps             | Standard Prod, DR, |
| (Skylake 6140)| (36 C)  |         | (18.56 TB Raw NVMe)    | Accelerated Net     | Test/Development   |
+---------------+---------+---------+------------------------+---------------------+--------------------+
```

---

## 3. Azure Storage & Security Integration

### 1. Distributed Storage Fabric (DSF)
*   Aggregates the physical NVMe drives across the `AN36` / `AN36P` / `AN64` nodes into a high-performance distributed pool.
*   Supports standard **RF2** (minimum 3 nodes) and **RF3** (minimum 5 nodes) protection.
*   Inline compression, deduplication, and EC-X erasure coding reduce raw Azure storage consumption.

### 2. Azure Key Vault Native Encryption
*   NC2 on Azure supports software-based Data-at-Rest Encryption (DAR) using **Azure Key Vault** as the external Key Management Service (KMS).
*   Keys are managed directly in the customer's Key Vault, satisfying strict regulatory requirements without requiring on-prem HSMs.

---

## 4. Azure Identity & Custom Role Setup

NC2 utilizes a Microsoft Entra ID (Azure AD) **App Registration & Service Principal** to orchestrate resources within the customer's subscription.

### Required Custom Role Definition (Sample JSON snippet)
```json
{
  "Name": "Nutanix-NC2-Azure-Role",
  "IsCustom": true,
  "Description": "Grants NC2 Orchestrator permissions to manage bare-metal nodes and networking in Azure",
  "Actions": [
    "Microsoft.Compute/virtualMachines/*",
    "Microsoft.Compute/disks/*",
    "Microsoft.Network/virtualNetworks/*",
    "Microsoft.Network/networkInterfaces/*",
    "Microsoft.Network/routeTables/*",
    "Microsoft.Network/routeServers/*",
    "Microsoft.Network/loadBalancers/*",
    "Microsoft.BareMetal/AzureHostedService/*",
    "Microsoft.Resources/subscriptions/resourceGroups/*"
  ],
  "NotActions": [],
  "AssignableScopes": [
    "/subscriptions/YOUR-AZURE-SUBSCRIPTION-ID"
  ]
}
```

---

## 5. Step-by-Step Deployment Workflow on Azure

### Step 1: Azure Quota & Subscription Validation
1. Verify that your Azure subscription has sufficient quota allocated for `AN36`, `AN36P`, or `AN64` bare-metal nodes in the target Azure Region.
2. Register the `Microsoft.BareMetal` resource provider in your Azure Subscription:
   ```bash
   az provider register --namespace Microsoft.BareMetal
   ```

### Step 2: Entra ID App Registration
1. Register an Application in Microsoft Entra ID (`NC2-Azure-Connector`).
2. Generate a Client Secret and note the `Application (client) ID`, `Directory (tenant) ID`, and `Secret Value`.
3. Assign the custom **Nutanix-NC2-Azure-Role** to the Service Principal.

### Step 3: VNet & Subnet Preparation
1. Create a dedicated Azure Virtual Network (e.g., `10.100.0.0/16`).
2. Create the **Bare-Metal Delegated Subnet** (e.g., `10.100.1.0/24`) delegated to `Microsoft.BareMetal/AzureHostedService`.
3. Create the **Flow Gateway Subnet** (e.g., `10.100.2.0/24`).

### Step 4: Cluster Provisioning via NC2 Console
1. Log into `cloud.nutanix.com` -> **Create Cluster** -> **Microsoft Azure**.
2. Select Azure Subscription, Region, VNet, Delegated Subnet, and Flow Gateway Subnet.
3. Choose Bare-Metal Node Type (`AN36P` / `AN64`) and Node Count (Minimum 3).
4. Configure Flow Gateway Deployment Mode (Scale-Out Active/Active) and Routing Method (Azure Internal Load Balancer).
5. Initiate cluster build (~40 to 50 minutes build time).

*(For in-depth Azure Flow Gateway architectures, vWAN, Route Server, and ILB routing, see [**07-networking-azure.md**](file:///workspace/nc2-kb/07-networking-azure.md).)*
