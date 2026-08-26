# 15 - Troubleshooting Playbook & Top FAQs for NC2

## 1. Diagnostic Framework & Troubleshooting Methodology

Troubleshoot methodically through the infrastructure layers: **Cloud Provider Layer** -> **Network Fabric & Flow** -> **Nutanix OS & DSF** -> **User VM Layer**.

```
+-----------------------------------------------------------------------------------------------+
|                            LAYERED DIAGNOSTIC WORKFLOW                                        |
+-----------------------------------------------------------------------------------------------+
| Layer 1: Cloud Provider    | Check AWS/Azure/GCP quotas, IAM roles, bare-metal node status.   |
+----------------------------+------------------------------------------------------------------+
| Layer 2: Network Fabric    | Verify ENIs, Azure delegated subnets, Flow Gateway health,       |
|                            | MTU sizing, BGP peering with Route Server / vWAN / Cloud Router. |
+----------------------------+------------------------------------------------------------------+
| Layer 3: Nutanix Storage   | Check DSF health via NCC (`ncc health_checks run_all`), CVM      |
|          & Services        | quorum, Cassandra metadata ring, Stargate I/O services.          |
+----------------------------+------------------------------------------------------------------+
| Layer 4: Workloads & DR    | Verify VirtIO drivers, Recovery Plan mapping, NearSync lag.      |
+-----------------------------------------------------------------------------------------------+
```

---

## 2. Essential Diagnostic CLI Commands

Execute these commands from any Controller VM (CVM) via SSH (`nutanix@<CVM-IP>`):

```bash
# 1. Run Comprehensive Nutanix Cluster Health Checks
ncc health_checks run_all

# 2. Check Cluster Service Status (All nodes)
cluster status

# 3. Check CVM I/O and System Statistics
allssh "cvm_stat"

# 4. Inspect Open vSwitch Network Uplinks & Bridges on AHV
allssh "manage_ovs show_uplinks"

# 5. Check Disk Status and Hardware Fault Tolerance
ncli disk ls
ncli cluster get-domain-fault-tolerance-status

# 6. Check Nutanix Genesis Master & Daemon Status
allssh "genesis status"

# 7. Test Network Latency and MTU across all CVMs and AHV hosts
allssh "ping -c 3 -M do -s 8972 <TARGET-CVM-IP>"  # AWS Jumbo Frames test
allssh "ping -c 3 -M do -s 1422 <TARGET-CVM-IP>"  # Standard Overlay MTU test

# 8. View Real-Time Storage I/O Logs
tail -f /home/nutanix/data/logs/stargate.out
```

---

## 3. Top Frequently Asked Questions (FAQs)

1. **Q: What hypervisors are supported on NC2?**  
   *A:* Nutanix AHV is the native and supported hypervisor running directly on cloud bare-metal.
2. **Q: Is NC2 a nested virtualization solution?**  
   *A:* No. AHV runs on bare-metal physical servers with direct access to physical CPUs, NVMe SSDs, and network adapters.
3. **Q: What is the minimum cluster size for NC2?**  
   *A:* Minimum **3 bare-metal nodes** for standard production (RF2). Minimum **5 nodes** for RF3.
4. **Q: Do I have to re-IP my virtual machines when migrating from on-premises to NC2?**  
   *A:* No. Flow Virtual Networking (FVN) overlay VPCs preserve identical IP addresses and subnet masks.
5. **Q: What RPO is achievable with Nutanix Disaster Recovery on NC2?**  
   *A:* As low as **1 minute** using NearSync replication (AOS 6.7.1.5+), and **RPO=0 (Synchronous)** for intra-region low-latency setups.
6. **Q: What is Cluster Hibernate and Resume?**  
   *A:* It takes a snapshot of cluster data and metadata to Amazon S3 / Azure Blob and terminates physical bare-metal servers, reducing compute costs to $0 when idle.
7. **Q: How long does it take to resume a hibernated NC2 cluster?**  
   *A:* Typically **30 to 45 minutes** to provision new bare-metal nodes, restore storage metadata, and bring services online.
8. **Q: Can I use my existing on-premises Nutanix licenses for NC2?**  
   *A:* Yes. Nutanix Cloud-Based Licensing (CBL) allows 100% portable BYOL across on-prem and cloud.
9. **Q: Can I purchase NC2 through AWS or Azure Marketplaces?**  
   *A:* Yes. NC2 counts against AWS EDP and Azure MACC enterprise spend commitments.
10. **Q: What tools should I use to size an NC2 deployment?**  
    *A:* Use **Nutanix Sizer** (`sizer.nutanix.com`) to model workloads, CVM overhead, storage deduplication, and N+1 high availability.
