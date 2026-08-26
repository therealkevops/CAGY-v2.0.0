# 10 - Hybrid & Multicloud WAN Interconnect Architecture

## 1. Executive Summary

Enterprise deployments of Nutanix Cloud Clusters (NC2) require resilient, high-throughput, and low-latency network interconnects between on-premises datacenters and public cloud environments (AWS, Azure, GCP), as well as direct cross-cloud links between AWS and Azure.

```
+-----------------------------------------------------------------------------------------------+
|                            HYBRID INTERCONNECT SPEED & LATENCY MATRIX                         |
+--------------------------+---------------------+--------------------+-------------------------+
| Interconnect Solution    | Supported Bandwidth | Typical Round-Trip | Ideal Nutanix Use Case  |
|                          |                     | Latency (RTT)      |                         |
+--------------------------+---------------------+--------------------+-------------------------+
| AWS Direct Connect (DX)  | 1 Gbps to 100 Gbps  | < 5 ms             | NearSync DR (1-15m RPO),|
|                          |                     |                    | Live VM Migration (Move)|
+--------------------------+---------------------+--------------------+-------------------------+
| Azure ExpressRoute       | 1 Gbps to 100 Gbps  | < 5 ms             | NearSync DR (1-15m RPO),|
|                          |                     |                    | Ultra-low latency I/O   |
+--------------------------+---------------------+--------------------+-------------------------+
| GCP Dedicated Interconn. | 10 Gbps to 100 Gbps | < 5 ms             | NearSync DR, BigData    |
+--------------------------+---------------------+--------------------+-------------------------+
| IPsec VPN (Site-to-Site) | 500 Mbps to 5 Gbps  | 20 ms to 80 ms     | Async DR (RPO >= 1 hr), |
|                          | (Tunnel limited)    |                    | Backup / Dev / Test     |
+--------------------------+---------------------+--------------------+-------------------------+
```

---

## 2. End-to-End Hybrid Topology Blueprint

```mermaid
graph TD
    subgraph OnPrem_DC["On-Premises Core Datacenter"]
        OnPrem_Cluster["Nutanix On-Premises HCI (AHV / ESXi)"]
        OnPrem_Edge["Redundant Edge Routers (BGP ASN 65001)"]
    end

    subgraph WAN_Fabric["Dedicated Private Interconnect Fabric"]
        AWS_DX["AWS Direct Connect (DX Gateway)"]
        Azure_ER["Azure ExpressRoute (ER Gateway)"]
        GCP_IC["Google Cloud Interconnect"]
    end

    subgraph Cloud_AWS["NC2 on AWS"]
        AWS_TGW["AWS Transit Gateway"]
        AWS_NC2["NC2 AWS Bare-Metal Cluster"]
    end

    subgraph Cloud_Azure["NC2 on Microsoft Azure"]
        Azure_vWAN["Azure Virtual WAN / Route Server"]
        Azure_NC2["NC2 Azure Bare-Metal Cluster"]
    end

    subgraph Cloud_GCP["NC2 on Google Cloud"]
        GCP_Router["Google Cloud Router"]
        GCP_NC2["NC2 GCP Bare-Metal Cluster"]
    end

    OnPrem_Cluster --- OnPrem_Edge
    OnPrem_Edge <-->|Private Peering BGP| AWS_DX
    OnPrem_Edge <-->|Private Peering BGP| Azure_ER
    OnPrem_Edge <-->|Private Peering BGP| GCP_IC

    AWS_DX <--> AWS_TGW <--> AWS_NC2
    Azure_ER <--> Azure_vWAN <--> Azure_NC2
    GCP_IC <--> GCP_Router <--> GCP_NC2

    AWS_NC2 <==== NearSync Cross-Cloud Replication ====> Azure_NC2
```

---

## 3. BGP Routing Policy & Route Propagation

1. **Autonomous System Numbers (ASNs)**:
   - Assign distinct private BGP ASNs (e.g., On-Prem: `65001`, AWS: `64512`, Azure Route Server / vHub: `65515`, GCP: `16550`).
2. **BGP Path Prepending**:
   - When deploying redundant primary (Direct Connect / ExpressRoute) and secondary (IPsec VPN) links, use **AS Path Prepending** on the VPN tunnel to ensure dedicated circuits remain the preferred active forwarding path.
3. **BGP Community Tagging**:
   - Tag Nutanix overlay subnet routes with enterprise BGP communities to control route redistribution at the border gateway.

---

## 4. Disaster Recovery Network Mapping in Prism Central

```
+-----------------------------------------------------------------------------------------------+
|                            DISASTER RECOVERY NETWORK MAPPING MODES                            |
+--------------------------+------------------------------------+-------------------------------+
| Failover Strategy        | Networking Configuration           | Advantage / Considerations    |
+--------------------------+------------------------------------+-------------------------------+
| 1. IP Preservation       | Flow Virtual Networking Overlay    | **Zero Guest OS Re-IP**. Fast |
|    (Same IP Subnets)     | stretched across On-Prem & Cloud   | failover; Applications retain |
|                          | via BGP dynamic route injection.   | identical hardcoded IPs.      |
+--------------------------+------------------------------------+-------------------------------+
| 2. Subnet Remapping      | Recovery Plan automatically remaps | Clean separation of cloud IP  |
|    (New Cloud Subnets)   | On-Prem VLAN 10 -> Cloud Subnet 50.| space; Requires DNS updates   |
|                          | Injects new IP via AHV IPAM DHCP.  | (Route 53 / Azure DNS).       |
+--------------------------+------------------------------------+-------------------------------+
```
