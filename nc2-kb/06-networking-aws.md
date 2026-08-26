# 06 - NC2 Networking Deep Dive: Amazon Web Services (AWS)

## 1. Executive Architectural Overview

Nutanix Cloud Clusters (NC2) on AWS runs the complete Nutanix Enterprise Cloud OS (AHV and AOS) natively on dedicated **Amazon EC2 Bare-Metal Instances** (e.g., `i4i.metal`, `i3en.metal`, `m6id.metal`). 

Because AHV executes directly on physical server hardware, it interfaces directly with the AWS **Nitro System** PCI network adapters (Elastic Network Adapters - ENA).

```
+-----------------------------------------------------------------------------------------------+
|                             NC2 ON AWS NETWORKING MODES                                       |
+------------------------------------+----------------------------------------------------------+
| Networking Model                   | Primary Mechanism & Use Cases                            |
+------------------------------------+----------------------------------------------------------+
| 1. Native AWS Networking           | AHV binds User VM vNICs to secondary private IPs on      |
|    (Underlay ENI Mode)             | AWS Elastic Network Interfaces (ENIs). Direct AWS VPC IP.|
|                                    | Zero encapsulation overhead. Optimal for cloud adjacence.|
+------------------------------------+----------------------------------------------------------+
| 2. Flow Virtual Networking (FVN)   | Software-defined Geneve overlay (UDP 6081) on top of AWS |
|    (Overlay VPC Mode)              | VPC. Supports overlapping CIDRs, multi-tenant isolation, |
|                                    | and non-disruptive lift-and-shift with zero re-IPing.    |
+------------------------------------+----------------------------------------------------------+
```

---

## 2. AHV Host Open vSwitch (OVS) Architecture on AWS

On every AWS bare-metal node, AHV configures a standardized Open vSwitch (OVS) datapath:

```mermaid
graph TD
    subgraph AWS_BareMetal_Host["AWS Bare-Metal Host (e.g. i4i.metal)"]
        subgraph Physical_Nitro_ENA["AWS Nitro ENA Interfaces"]
            ENA_0["Primary ENA: eth0 (Host / CVM)"]
            ENA_1["Secondary ENA: eth1 (User VMs)"]
            ENA_N["Dynamic ENAs: ethN (Scaled by CNC)"]
        end

        subgraph OVS_Bridge_br0["Open vSwitch Bridge: br0"]
            Bond_br0_up["Bond: br0-up"]
            Port_Host["Internal Port: br0 (AHV Host IP)"]
            Port_vhost0["Tap Port: vhost0 (CVM IP - DSF Storage Traffic)"]
            Port_vnet0["Tap Port: vnet0 (User VM 1)"]
            Port_vnet1["Tap Port: vnet1 (User VM 2)"]
            CNC_Engine["Cloud Network Controller (OpenFlow Engine)"]
        end

        subgraph Linux_Bridge_virbr0["Linux Bridge: virbr0 (Internal Loopback)"]
            Virbr0_Port["virbr0 (192.168.5.1 <-> CVM 192.168.5.254)"]
        end
    end

    ENA_0 --- Bond_br0_up
    ENA_1 --- Bond_br0_up
    ENA_N --- Bond_br0_up
    Bond_br0_up --- OVS_Bridge_br0
    OVS_Bridge_br0 --- Port_Host
    OVS_Bridge_br0 --- Port_vhost0
    OVS_Bridge_br0 --- Port_vnet0
    OVS_Bridge_br0 --- Port_vnet1
    CNC_Engine -->|Injects Flow Rules| OVS_Bridge_br0
```

### Bridge & Port Roles
1. **`br0`**: The primary OVS data switch handling all external communication, inter-node CVM storage replication, and guest VM traffic.
2. **`br0-up`**: The OVS uplink bond aggregating the physical AWS ENA interfaces attached to the instance.
3. **`vhost0`**: The dedicated tap interface connecting the resident Controller VM (CVM) to `br0` for Distributed Storage Fabric (DSF) traffic.
4. **`virbr0`**: An isolated internal Linux bridge on subnet `192.168.5.0/24` used for private local IPC and heartbeats between AHV and CVM.

---

## 3. Native AWS Networking: Cloud Network Controller (CNC) & ENI Lifecycle

In Native AWS Networking mode, guest virtual machines participate directly in the AWS VPC subnet IP space.

```mermaid
sequenceDiagram
    autonumber
    participant Admin as Prism Central / Admin
    participant CNC as Cloud Network Controller (on AHV Host)
    participant CPM as Cloud Port Manager (Subcomponent)
    participant AWS as AWS EC2 Nitro API
    participant OVS as Open vSwitch (br0)
    participant UVM as User VM (Guest OS)

    Admin->>CNC: Create & Power On User VM (Subnet: 10.0.2.0/24)
    CNC->>CPM: Request IP & ENI Binding for VM vNIC
    CPM->>AWS: ec2:AssignPrivateIpAddresses (or create ENI if full)
    AWS-->>CPM: Allocated Secondary IP: 10.0.2.55 on ENI eni-01a2b3c4
    CPM->>OVS: Program OpenFlow Rule (Bind VM MAC & Tap to ENI eni-01a2b3c4)
    CNC->>UVM: AHV IPAM Intercepts DHCP & Injects 10.0.2.55
    UVM->>OVS: Transmits Initial Packet (Egress)
    AWS->>AWS: VPC Emits Gratuitous ARP (GARP) across VPC Top-of-Rack
    Note over UVM,AWS: VM communicates directly with AWS S3, RDS, & Internet
```

### 1. Cloud Network Controller (CNC) & Cloud Port Manager (CPM)
*   **CNC** is a leaderless, distributed daemon running on every AHV host.
*   **Cloud Port Manager** acts as the cloud interface adapter within CNC:
    *   Tracks attached ENIs and their current IP allocations.
    *   Each ENI supports 1 primary IP and up to **49 secondary private IPs** (on `i4i.metal` / `i3en.metal`).
    *   When an ENI reaches its secondary IP limit, CPM automatically invokes `ec2:CreateNetworkInterface` and `ec2:AttachNetworkInterface` to bind a new ENI to the physical host without disruption.

### 2. Live Migration Protocol & Gratuitous ARP (GARP)
When a User VM live-migrates from AHV Host 1 to AHV Host 2:
1. **Memory State Sync**: VM RAM pages migrate over `br0` using AWS VPC high-speed Nitro interconnects (up to 75-100 Gbps).
2. **IP Detachment / Re-Attachment**:
   - Host 1 CPM calls `ec2:UnassignPrivateIpAddresses`.
   - Host 2 CPM calls `ec2:AssignPrivateIpAddresses` to attach the secondary IP to Host 2's ENI.
3. **Gratuitous ARP (GARP)**: AWS Nitro fabric emits GARP frames, updating AWS VPC mapping tables so traffic routes immediately to Host 2 without TCP session drops.

### 3. Exclusivity of AHV Managed Subnets
*   NC2 on AWS **strictly requires Managed Subnets** (IPAM enabled in Prism).
*   Unmanaged subnets are not supported in native AWS networking because AHV IPAM must orchestrate IP allocation directly with the AWS DHCP and EC2 routing tables.

---

## 4. Flow Virtual Networking (FVN) on AWS: Transit VPC & Overlays

When organizations need overlapping subnets, zero-re-IP migrations, or strict isolation, NC2 on AWS utilizes **Flow Virtual Networking (FVN)**.

```mermaid
graph TD
    subgraph AWS_VPC_Underlay["AWS VPC Underlay (10.0.0.0/16) - MTU 9001"]
        Transit_ENI["Transit VPC Gateway ENI (10.0.1.50)"]
        AWS_TGW["AWS Transit Gateway / Direct Connect"]
    end

    subgraph Nutanix_Flow_Overlay["Nutanix Flow Virtual Networking (FVN Overlay)"]
        subgraph Flow_Transit_VPC["Flow Transit VPC (Virtual Router)"]
            VR_Transit["Transit Virtual Router & SNAT / Floating IP Engine"]
        end

        subgraph User_VPC_Prod["User VPC 1: Production (192.168.10.0/24)"]
            VM_Prod_Web["Prod Web (192.168.10.11)"]
            VM_Prod_DB["Prod DB (192.168.10.12)"]
        end

        subgraph User_VPC_Dev["User VPC 2: Dev (192.168.10.0/24 - Overlapping)"]
            VM_Dev_Web["Dev Web (192.168.10.11)"]
        end
    end

    VM_Prod_Web -->|Geneve Encapsulation UDP 6081| VR_Transit
    VM_Prod_DB -->|Geneve Encapsulation UDP 6081| VR_Transit
    VM_Dev_Web -->|Geneve Encapsulation UDP 6081| VR_Transit
    VR_Transit -->|SNAT / Floating IP Mapping| Transit_ENI
    Transit_ENI --> AWS_TGW
```

---

## 5. Enterprise Interconnect: AWS Transit Gateway (TGW) & Direct Connect

```mermaid
graph LR
    subgraph OnPrem["On-Premises Datacenter"]
        OnPrem_GW["Enterprise Core Router"]
    end

    subgraph AWS_Hybrid_Network["AWS Hybrid Connectivity Fabric"]
        DX["AWS Direct Connect (Dedicated 10G/100G)"]
        DXGW["Direct Connect Gateway"]
        TGW["AWS Transit Gateway (TGW)"]
    end

    subgraph NC2_VPC["NC2 AWS VPC"]
        NC2_Mgmt["Management Subnet (AHV/CVM)"]
        NC2_Transit["Transit VPC Subnet (FVN)"]
    end

    OnPrem_GW --> DX --> DXGW --> TGW
    TGW --> NC2_Mgmt
    TGW --> NC2_Transit
```

*   **AWS Transit Gateway (TGW)** connects NC2 VPCs, corporate on-premises networks over Direct Connect, and other application VPCs.
*   **Jumbo Frames Support**: AWS Direct Connect and Transit Gateway support up to **MTU 8500 / 9001**, allowing end-to-end Jumbo Frame transmission between on-premises Nutanix clusters and NC2 on AWS for maximum NearSync replication throughput.

---

## 6. Diagnostic & Verification Commands for NC2 on AWS

```bash
# View all attached ENIs, MAC addresses, and secondary IPs on AHV host
manage_ovs show_uplinks
manage_ovs show_interfaces

# Check Cloud Network Controller daemon health
allssh "genesis status | grep -i cloud_network_controller"
tail -f /home/nutanix/data/logs/cloud_network_controller.out

# Verify Open vSwitch Flow Tables & Bridge Rules
ovs-ofctl dump-flows br0 -O OpenFlow13

# Ping target AHV host or CVM with 8972 payload (AWS Jumbo Frames test)
ping -c 4 -M do -s 8972 <TARGET-CVM-OR-HOST-IP>
```
