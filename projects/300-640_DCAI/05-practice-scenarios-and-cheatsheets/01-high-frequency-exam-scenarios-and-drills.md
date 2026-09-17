# High-Frequency Exam Scenarios & Practice Drills — Cisco 300-640 DCAI

> **Official Curriculum Reference:** All Domains (1.0 through 4.0)  
> **Purpose:** Test and validate your knowledge against realistic, scenario-based exam questions with comprehensive plain-English rationales and trap explanations.

---

## Scenario 1: Lossless Fabric & Congestion Control

### Question
An engineer is deploying an 8-node AI cluster using Cisco Nexus 9300-GX2 switches for distributed foundation model training over RoCEv2. During initial testing with large gradient tensor bursts, the PyTorch training job aborts with an NCCL communication timeout. An inspection of the switch interfaces reveals significant packet drops on egress queues, while no PFC pause frames were ever transmitted. Which two configuration errors explain why the fabric failed to remain lossless? (Choose two.)

- [ ] A. ECN WRED minimum threshold was set higher than the PFC XOFF threshold.
- [ ] B. The `type network-qos` policy omitted the `pause pfc-cos 3` command for the lossless class.
- [ ] C. The interface MTU was configured to 9216 on the switch and 9000 on the host.
- [ ] D. The switch `class-map type qos` matched DSCP 26, but the host application transmitted packets tagged with DSCP 0.
- [ ] E. The UCS Fabric Interconnect had Fabric Failover disabled on the vNIC.

<details>
<summary><strong>View Answer & Plain-English Rationale</strong></summary>

**Correct Answers:** **B and D**

**Plain-English Explanation:**
- **Why B is correct:** For a Cisco Nexus switch to pause incoming traffic and prevent buffer overflow, PFC must be explicitly enabled inside the `policy-map type network-qos` using `pause pfc-cos 3`. If this line is missing, the switch operates in standard lossy mode and drops overflowing packets instead of sending pause frames!
- **Why D is correct:** If the host application transmitted packets with DSCP 0 (Best Effort), the switch never classified them into `qos-group 3`. The traffic dropped into `class-default` (standard lossy queue), where buffer exhaustion caused immediate packet drops.
- **Why the others are wrong:**
  - *A is wrong:* ECN marking does not trigger PFC; even if ECN were misconfigured, PFC should still trigger when buffers fill up if PFC is properly enabled.
  - *C is wrong:* A switch MTU of 9216 easily accommodates a host MTU of 9000.
  - *E is wrong:* Disabling Fabric Failover is the *recommended* best practice for RoCEv2, not an error.
</details>

---

## Scenario 2: ECMP Elephant Flow Polarization

### Question
A multi-node deep learning cluster connects to four Cisco Nexus 9364C-GX spine switches. Network telemetry in Nexus Dashboard Insights shows that Spine-01's 400G uplinks are running at 98% utilization with high buffer occupancy, while Spine-02, Spine-03, and Spine-04 remain below 5% utilization. What is the root cause of this imbalance, and what is the recommended Cisco solution?

- [ ] A. RoCEv1 is in use; enable BGP-EVPN to distribute Layer 2 frames.
- [ ] B. Static 5-tuple ECMP hashing caused multiple 400G elephant flows to collide on Spine-01; enable Dynamic Load Balancing (DLB) with Flowlet switching.
- [ ] C. The host SuperNICs are generating invalid UDP checksums; enable UDP checksum recalculation on the leaf switches.
- [ ] D. PFC pause storms have frozen Spine-02, Spine-03, and Spine-04; disable PFC globally.

<details>
<summary><strong>View Answer & Plain-English Rationale</strong></summary>

**Correct Answer:** **B**

**Plain-English Explanation:**
- **Why B is correct:** In AI clusters, there are very few parallel flows per server, but each flow is an "elephant" running at 400 Gbps. When traditional static ECMP hashes the IP 5-tuple, multiple elephant flows randomly hash to the exact same spine uplink (**Hash Collision / Polarization**), overloading Spine-01 while the others sit idle. Cisco's **Dynamic Load Balancing (DLB) and Flowlet switching** monitor queue depth in real-time and dynamically steer flowlets to idle spines.
- **Why the others are wrong:**
  - *A is wrong:* RoCEv1 cannot be routed across spines; EVPN does not resolve elephant flow hashing collisions.
  - *C is wrong:* RoCEv2 explicitly sets the UDP checksum to 0x0000 by design.
  - *D is wrong:* PFC frames do not freeze idle spines; disabling PFC destroys the lossless fabric guarantee.
</details>

---

## Scenario 3: GPU Hardware & Interconnect Sizing

### Question
An enterprise AI architect is evaluating server nodes for training a 70-billion parameter Large Language Model using Tensor Parallelism (TP) across multiple GPUs. Which interconnect technology is mandatory between the GPUs within each server node to support the communication frequency required by Tensor Parallelism?

- [ ] A. PCIe Gen 5 x16 bus
- [ ] B. 400G RoCEv2 Ethernet
- [ ] C. NVIDIA NVLink 4 / NVSwitch mesh
- [ ] D. 64G Fibre Channel

<details>
<summary><strong>View Answer & Plain-English Rationale</strong></summary>

**Correct Answer:** **C**

**Plain-English Explanation:**
- **Why C is correct:** Tensor Parallelism splits individual matrix math calculations *at every single layer* of the neural network. This demands sub-microsecond latency and hundreds of gigabytes per second of bandwidth. Only **NVLink 4 with NVSwitches (providing up to 900 GB/s per GPU)** can sustain Tensor Parallelism.
- **Why the others are wrong:**
  - *A is wrong:* PCIe Gen 5 x16 tops out at 64 GB/s—far too slow for Tensor Parallelism.
  - *B is wrong:* RoCEv2 is used for *scale-out* inter-node Data Parallelism across the cluster, not intra-node Tensor Parallelism.
  - *D is wrong:* Fibre Channel is a storage protocol, not an inter-GPU computing bus.
</details>

---

## Scenario 4: Storage Architecture & Checkpointing

### Question
A cluster of 256 GPUs trains a foundation model. Every 45 minutes, training pauses to write a 2-terabyte model checkpoint to storage. The storage subsystem currently requires 12 minutes to commit each checkpoint. What is the impact on GPU cluster efficiency, and which technology directly mitigates this bottleneck?

- [ ] A. Cluster loses ~27% of productive compute time; deploy a parallel scale-out file system (e.g., Weka / VAST) with GPUDirect Storage (GDS).
- [ ] B. Cluster loses ~5% of compute time; deploy an iSCSI SAN array with 10k SAS drives.
- [ ] C. GPUs automatically continue training in background memory; no compute time is lost.
- [ ] D. Cluster efficiency is unaffected; convert the storage network to RoCEv1.

<details>
<summary><strong>View Answer & Plain-English Rationale</strong></summary>

**Correct Answer:** **A**

**Plain-English Explanation:**
- **Why A is correct:** If the cluster runs for 45 minutes and then stops for 12 minutes, total cycle time is $45 + 12 = 57\text{ minutes}$. The idle time is $12 / 57 \approx 21\%\text{ to }27\%$. Writing a 2 TB checkpoint requires multi-gigabyte/sec write throughput. Deploying a **parallel file system with GPUDirect Storage (GDS)** allows the GPUs to dump checkpoints straight to NVMe flash over RoCEv2 in under 30 seconds, reducing compute loss to under 1%!
- **Why the others are wrong:**
  - *B is wrong:* SAS drives and iSCSI are far too slow and will make checkpointing even worse.
  - *C is wrong:* Standard synchronous checkpointing freezes the PyTorch training loop until the write completes to prevent memory state corruption.
  - *D is wrong:* RoCEv1 is non-routable Layer 2 and does not accelerate storage writes.
</details>

---

## Scenario 5: PFC Pause Storm & Slow-Drain Quarantine

### Question
A Cisco Nexus 9300-GX2 leaf switch generates the following syslog message:  
`%QOS-4-PFC_WATCHDOG_ACTION: Interface Ethernet1/7 Queue 3 was paused for 100 ms. Watchdog action taken: Shutdown pause and drain queue.`  
What triggered this event, and what is the primary purpose of the PFC Watchdog action?

- [ ] A. A BGP peer on Eth1/7 went down; the switch shut down the port to prevent routing loops.
- [ ] B. A slow-drain server on Eth1/7 failed to consume packets and continuously asserted PFC pause frames; the watchdog disabled pause on that queue to prevent congestion from spreading across the fabric.
- [ ] C. The optical transceiver on Eth1/7 exceeded the maximum transmit laser threshold; the switch disabled the laser.
- [ ] D. The switch ran out of TCAM space; the watchdog flushed the QoS table.

<details>
<summary><strong>View Answer & Plain-English Rationale</strong></summary>

**Correct Answer:** **B**

**Plain-English Explanation:**
- **Why B is correct:** If a server experiences a PCIe lockup or driver hang, its incoming buffers fill up, causing it to spam the switch with PFC pause frames indefinitely (**Slow-Drain / Pause Storm**). Without protection, the switch would pause its upstream spines, paralyzing the entire data center. The **PFC Watchdog** detects that Queue 3 has been frozen for over 100ms, overrides the pause, and drains the trapped packets to isolate the failure to that single server.
- **Why the others are wrong:**
  - *A is wrong:* This is a QoS queue event, not a routing protocol event.
  - *C is wrong:* Digital Optical Monitoring does not trigger `%QOS-4-PFC_WATCHDOG_ACTION`.
  - *D is wrong:* TCAM exhaustion does not trigger queue draining.
</details>

---

## Scenario 6: Cisco UCS vNIC Best Practices for RoCEv2

### Question
An engineer is creating a vNIC template in Cisco Intersight for UCS X-Series servers running distributed AI training workloads. Which set of parameters must be configured on the vNIC to guarantee compatibility with lossless RoCEv2 fabrics?

- [ ] A. MTU 1500, Enable RoCEv2, Enable Fabric Failover, QoS System Class = Best Effort.
- [ ] B. MTU 9216, Enable RoCEv2, Disable Fabric Failover, QoS System Class = Platinum (Packet Drop = No).
- [ ] C. MTU 9000, Disable RoCEv2, Enable Fabric Failover, QoS System Class = Fibre Channel.
- [ ] D. MTU 1500, Enable RoCEv2, Disable Fabric Failover, QoS System Class = Gold.

<details>
<summary><strong>View Answer & Plain-English Rationale</strong></summary>

**Correct Answer:** **B**

**Plain-English Explanation:**
- **Why B is correct:**
  1. **MTU 9216 (Jumbo Frames):** RoCEv2 transfers large gradient tensors; jumbo frames are mandatory to avoid packet fragmentation and CPU overhead.
  2. **Enable RoCEv2:** Configures the Cisco VIC hardware to expose RoCEv2 RDMA capabilities to the OS.
  3. **Disable Fabric Failover:** RoCEv2 utilizes dual-rail multi-plane software failover. Enabling VIC hardware failover can cause out-of-order packets and break active RDMA Queue Pairs.
  4. **Platinum (Packet Drop = No):** Binds the vNIC to the lossless system class configured with PFC.
- **Why the others are wrong:**
  - Options A, C, and D contain fatal misconfigurations (MTU 1500, Best Effort lossy class, or Fabric Failover enabled).
</details>

---

## Scenario 7: Benchmarking & Bandwidth Math

### Question
An engineer runs the NCCL `all_reduce_perf` test across 64 GPU nodes and observes an algorithm bandwidth (`algbw`) of 185 GB/s. What is the approximate bus bandwidth (`busbw`) expected on the output, and why?

- [ ] A. ~92.5 GB/s, because encryption halves the effective bus speed.
- [ ] B. ~185 GB/s, because bus bandwidth is always identical to algorithm bandwidth.
- [ ] C. ~370 GB/s, because the Ring AllReduce algorithm requires sending and receiving data twice ($2 \times \frac{N-1}{N}$).
- [ ] D. ~740 GB/s, because PCIe Gen 5 operates in full-duplex 4x mode.

<details>
<summary><strong>View Answer & Plain-English Rationale</strong></summary>

**Correct Answer:** **C**

**Plain-English Explanation:**
- **Why C is correct:** In distributed Ring AllReduce, every GPU transmits and receives chunks of data during both the ReduceScatter phase and the AllGather phase. The mathematical formula connecting the two is:
  $$\text{busbw} = \text{algbw} \times 2 \times \left(\frac{N - 1}{N}\right)$$
  For 64 nodes ($N = 64$), the multiplier is $2 \times \frac{63}{64} \approx 1.96875 \approx 2.0$.  
  Therefore, $\text{busbw} \approx 185 \times 2 = 370\text{ GB/s}$.
- **Why the others are wrong:**
  - A, B, and D ignore the fundamental mathematics of the Ring AllReduce algorithm.
</details>

---

## Scenario 8: Power & Cooling Density

### Question
A data center manager is preparing a row of racks for new high-density AI servers. Each rack will contain four 8-GPU servers with each server drawing 10.2 kW of power at peak compute load. Which facility architecture is required to sustainably support this deployment?

- [ ] A. Standard raised-floor air cooling delivering 10 kW per rack with 120V single-phase power.
- [ ] B. Direct-to-Chip (D2C) liquid cooling or active Rear-Door Heat Exchangers (RDHx) with 415V 3-phase power delivering > 45 kW per rack.
- [ ] C. Uncontained hot-aisle air cooling with redundant 208V single-phase PDUs.
- [ ] D. Standard CRAC perimeter cooling units with cold aisle containment capped at 25 kW.

<details>
<summary><strong>View Answer & Plain-English Rationale</strong></summary>

**Correct Answer:** **B**

**Plain-English Explanation:**
- **Why B is correct:** Four servers at 10.2 kW each equal **40.8 kW of server compute load alone**, excluding top-of-rack switches and storage (total rack load $> 45\text{ kW}$).
  - Traditional air cooling cannot dissipate more than ~30–35 kW per rack.
  - A rack load of 45 kW+ **mandates liquid cooling (Direct-to-Chip or RDHx)**.
  - Furthermore, delivering 45 kW over single-phase 120V/208V would require dangerously thick copper cables; **415V 3-phase power** is the industry standard for high-density AI.
- **Why the others are wrong:**
  - Options A, C, and D describe air cooling architectures that will cause catastrophic thermal throttling and circuit breaker trips at 45 kW rack density.
</details>

---

## 9. Quick Exam Drill Checklist

Before walking into the testing center, ensure you can instantly answer:
- [ ] What is the IANA UDP port for RoCEv2? (**4791**)
- [ ] What is the IEEE standard for PFC? (**802.1Qbb**)
- [ ] What is the IEEE standard for ETS? (**802.1Qaz**)
- [ ] What is the RFC for ECN? (**RFC 3168**)
- [ ] Which CoS value is universally standardized for AI RoCEv2? (**CoS 3**)
- [ ] Does ECN operate on ingress buffers or egress queues? (**Egress queues**)
- [ ] Does PFC operate on ingress buffers or egress queues? (**Ingress buffers**)
- [ ] What is the multiplier between `busbw` and `algbw` in AllReduce? (**~2x**)
- [ ] What is the standard oversubscription ratio for an AI back-end fabric? (**1:1 Non-blocking**)
- [ ] What does GPU XID 79 mean? (**GPU has fallen off the PCIe bus due to power/hardware fault**)
