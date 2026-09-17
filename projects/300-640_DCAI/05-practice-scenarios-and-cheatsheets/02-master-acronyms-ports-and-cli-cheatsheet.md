# Master Acronyms, Standards, Ports, & CLI Cheat Sheet — Cisco 300-640 DCAI

> **Official Curriculum Reference:** All Domains (1.0 through 4.0)  
> **Purpose:** The ultimate rapid-revision cheat sheet for the final 48 hours before your exam.

---

## 1. Master Acronym Glossary

| Acronym | Full Name | Plain-English Role in AI Infrastructure |
| :--- | :--- | :--- |
| **ACI** | Application Centric Infrastructure | Cisco SDN policy and microsegmentation fabric managed by APIC. |
| **APIC** | Application Policy Infrastructure Controller | Central policy clustering engine for Cisco ACI fabrics. |
| **BTH** | Base Transport Header | 12-byte InfiniBand header containing OpCode, QPN, and PSN in RoCEv2. |
| **CDU** | Coolant Distribution Unit | Central pump and heat exchanger manifold in liquid-cooled facilities. |
| **CIMC** | Cisco Integrated Management Controller | Baseboard Management Controller (BMC) for Cisco UCS servers. |
| **CNP** | Congestion Notification Packet | High-priority packet (OpCode 0x81) sent by receiver to throttle RoCEv2 sender. |
| **D2C** | Direct-to-Chip | Liquid cold plates mounted directly on GPU/CPU silicon. |
| **DCGM** | Data Center GPU Manager | NVIDIA host agent monitoring GPU health, clocks, power, and XID errors. |
| **DLB** | Dynamic Load Balancing | Cisco Nexus ASIC feature dynamically routing flowlets away from congested paths. |
| **DOM** | Digital Optical Monitoring | Real-time sensor measuring laser Tx and Rx optical power in transceivers. |
| **DWRR** | Deficit Weighted Round Robin | Packet scheduling algorithm used by ETS to guarantee bandwidth percentages. |
| **ECN** | Explicit Congestion Notification | RFC 3168 mechanism marking CE bits (`11`) in IP headers before drops occur. |
| **ETS** | Enhanced Transmission Selection | IEEE 802.1Qaz standard allocating minimum bandwidth percentages per class. |
| **GDS** | GPUDirect Storage | NVIDIA direct DMA path between NVMe flash and GPU HBM memory. |
| **HBM** | High Bandwidth Memory | 3D-stacked memory silicon adjacent to GPU core providing TB/s bandwidth. |
| **MIG** | Multi-Instance GPU | Physical silicon partitioning of NVIDIA GPUs (up to 7 isolated instances). |
| **MQC** | Modular QoS CLI | Cisco 3-step QoS configuration framework (`class-map`, `policy-map`, `service-policy`). |
| **NCCL** | NVIDIA Collective Communications Library | Multi-GPU collective communication library (AllReduce, AllGather). |
| **NDFC** | Nexus Dashboard Fabric Controller | Central fabric automation and Day-0/1/2 management tool (formerly DCNM). |
| **NDI** | Nexus Dashboard Insights | Real-time streaming telemetry and AI anomaly detection application. |
| **NVSwitch** | NVIDIA On-Board Switch Chip | On-board crossbar connecting up to 8 GPUs at 900 GB/s over NVLink. |
| **PFC** | Priority-based Flow Control | IEEE 802.1Qbb standard pausing individual CoS queues to make Ethernet lossless. |
| **POAP** | Power-On Auto Provisioning | Zero-touch switch bootstrapping via DHCP and NDFC. |
| **PUE** | Power Usage Effectiveness | Efficiency metric: $\frac{\text{Total Facility Power}}{\text{IT Compute Power}}$. Target: 1.15. |
| **RAG** | Retrieval-Augmented Generation | AI pattern combining vector database search with LLM generation. |
| **RDHx** | Rear-Door Heat Exchanger | Liquid radiator door mounted on server rack exhaust absorbing up to 50kW. |
| **RoCEv2** | RDMA over Converged Ethernet v2 | Routable RDMA encapsulated in UDP port 4791 across Layer 3 fabrics. |
| **WRED** | Weighted Random Early Detection | Algorithmic queue marking curve used to trigger ECN bits. |
| **XOFF / XON** | Transmit Off / Transmit On | PFC pause and unpause signaling triggers based on buffer watermarks. |

---

## 2. Ports, Standards, & Key Thresholds Quick Reference

| Parameter | Standard Value | Exam Context |
| :--- | :--- | :--- |
| **RoCEv2 Destination UDP Port** | **`4791`** | Mandatory UDP destination port in RoCEv2 headers. |
| **RoCEv2 Source UDP Port** | **Dynamic Hash (49152–65535)** | Randomized per QP flow to provide entropy for ECMP. |
| **RoCEv1 EtherType** | **`0x8915`** | Non-routable legacy L2 RoCE. |
| **PFC Standard** | **IEEE 802.1Qbb** | 802.1Qbb Flow Control per priority. |
| **ETS Standard** | **IEEE 802.1Qaz** | 802.1Qaz Bandwidth Allocation. |
| **ECN RFC** | **RFC 3168** | IP header bits 6 & 7 (ECT=01/10, CE=11). |
| **Standard AI CoS Queue** | **CoS 3** | Universal lossless No-Drop queue for RoCEv2. |
| **Standard AI DSCP Markings** | **DSCP 24 (CS3) or 26 (AF31)** | Layer 3 QoS markings mapped to `qos-group 3`. |
| **Fabric System MTU** | **`9216` Bytes** | Jumbo frame standard for Cisco Nexus AI fabrics. |
| **Host NIC MTU** | **`9000` Bytes** | Standard server host OS payload MTU. |
| **PFC Watchdog Default Timer** | **`100` Milliseconds** | Time before stuck queue is quarantined as slow-drain. |
| **Air Cooling Thermal Limit** | **~30 to 35 kW per rack** | Densities above 35kW mandate liquid cooling. |

---

## 3. Essential Formulas Cheat Sheet

### 1. Ring AllReduce Bus Bandwidth Formula
$$\text{busbw} = \text{algbw} \times 2 \times \left(\frac{N - 1}{N}\right)$$
- For large clusters ($N \gg 1$): **$\text{busbw} \approx 2 \times \text{algbw}$**.

### 2. Power Usage Effectiveness (PUE)
$$\text{PUE} = \frac{\text{Total Facility Power}}{\text{IT Equipment Compute Power}}$$
- Ideal modern AI target: **1.10 to 1.25**.

### 3. Non-Blocking Fabric Oversubscription
$$\text{Oversubscription Ratio} = \frac{\text{Total Downlink Bandwidth (to Servers)}}{\text{Total Uplink Bandwidth (to Spines)}}$$
- AI Back-End Fabric Target: **Strictly 1:1 (Non-Blocking)**.

### 4. Checkpoint Write Bandwidth
$$\text{Required Write Throughput} = \frac{\text{Total Checkpoint Size (GB)}}{\text{Target Write Time Window (Seconds)}}$$

---

## 4. Master Cisco NX-OS AI QoS Configuration Template

```text
! 1. Classification (type qos)
class-map type qos match-any CM_ROCE
  match dscp 26
  match dscp 24
  match cos 3

policy-map type qos PM_QOS_INGRESS
  class CM_ROCE
    set qos-group 3
  class class-default
    set qos-group 0

! 2. Network-QoS Policy (type network-qos)
class-map type network-qos CM_NETQOS_ROCE
  match qos-group 3

policy-map type network-qos PM_NETWORK_QOS
  class CM_NETQOS_ROCE
    pause pfc-cos 3
  class class-default
    mtu 9216

! 3. Queuing Policy (type queuing)
class-map type queuing CM_QUEUE_ROCE
  match qos-group 3

policy-map type queuing PM_QUEUING_EGRESS
  class type queuing CM_QUEUE_ROCE
    bandwidth percent 70
    random-detect ecn
    random-detect minimum-threshold 150 kbytes maximum-threshold 1500 kbytes
  class type queuing class-default
    bandwidth percent 30

! 4. Global System Activation
system qos
  service-policy type qos input PM_QOS_INGRESS
  service-policy type network-qos PM_NETWORK_QOS
  service-policy type queuing output PM_QUEUING_EGRESS

! 5. Interface Activation & Watchdog
interface Ethernet1/1-16
  mtu 9216
  priority-flow-control mode on
  priority-flow-control watch-dog-interval 100
```

---

## 5. Master Host & Linux CLI Diagnostic Snippets

```bash
# 1. Test 400G RoCEv2 line-rate write bandwidth
ib_write_bw -d mlx5_0 192.168.10.2 -R -F --report_gbits

# 2. Check GPU clock throttling and thermal slowdowns
nvidia-smi -q -d PERFORMANCE,CLOCK

# 3. Check PCIe negotiated bus width (must be 16x)
nvidia-smi -q -d BUS

# 4. Check for GPU hardware faults & XID errors
dmesg -T | grep -i "NVRM: Xid"

# 5. Check NUMA core pinning
numactl --hardware
```
