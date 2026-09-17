# Cisco UCS & Nexus AI Compute Fabric Architecture

- **Space**: `300-640_dcai`
- **Related Project**: `/workspace/projects/300-640_DCAI/02-ai-infrastructure-components-and-architecture/02-compute-architecture-and-gpu-connectivity.md`
- **Tags**: #cisco #ucs #intersight #nexus #gpus #nvlink #hbm3e

## 1. Architectural Overview
This architecture establishes the physical and logical integration of **Cisco UCS X-Series modular compute** and **Cisco Nexus 9000 switches**, orchestrating GPU-accelerated workloads across high-density AI data halls.

```mermaid
graph TD
    Intersight["[[overview|Cisco Intersight Cloud Operations]]"]

    subgraph FabricLayer["Nexus Switching Fabric"]
        SpineGroup["Nexus 9364C / 9800 Spines (400G/800G)"]
        LeafGroup["Nexus 9300-GX2 Leafs (PFC CoS 3, MTU 9216)"]
        SpineGroup <--> LeafGroup
    end

    subgraph ComputeLayer["Cisco UCS Compute Pod"]
        FI["UCS 6536 Fabric Interconnects (36x 100G/400G)"]
        X9508["UCS X9508 Modular Chassis"]
        X210c["UCS X210c M7 Compute Node"]
        X440p["UCS X440p PCIe GPU Node (4x NVIDIA L40S/H100)"]
        
        FI <--> X9508
        X9508 --- X210c
        X210c <-->|X-Fabric PCIe Gen 5| X440p
    end

    LeafGroup <--> FI
    Intersight -.->|Domain & Server Profiles| FI
    Intersight -.->|Telemetry & Health| X9508
```

## 2. Core Architectural Principles
1. **Midplane-Free Chassis Dynamics**: The UCS X9508 eliminates the traditional chassis midplane, enabling direct front-to-back laminar airflow supporting up to 54 kW power delivery and high-wattage GPUs.
2. **X-Fabric PCIe Expansion**: Allows modular compute blades (X210c) to attach horizontally to full-length GPU expansion nodes (X440p) with native PCIe Gen 5 line rates.
3. **Strict 1:1 Non-Blocking Spine-Leaf**: Eliminates oversubscription in the back-end GPU fabric, guaranteeing that AllReduce collective bursts never experience spine buffer drops.

## 3. Interlinked Context
- See [[cisco_ai_compute_ucs_nexus_architecture]] for UCS policy configuration guides.
- See [[ai_infrastructure_troubleshooting_playbook]] for triaging straggler nodes and thermal events.
- See [[adr_001_rocev2_lossless_transport]] for the transport foundation.
