#!/usr/bin/env python3
"""
Nutanix Cloud Clusters (NC2) Sizing & Capacity Calculator
Calculates usable compute, RAM, raw/usable storage, CVM overhead, RF2/RF3 mirroring,
and N+1 rebuild headroom across AWS, Azure, and Google Cloud bare-metal instances.
"""

import argparse
import sys
import json

NODE_CATALOG = {
    "aws": {
        "i4i.metal": {"vcpus": 128, "ram_gb": 512, "raw_storage_tb": 30.0, "net_gbps": 75, "desc": "Intel 3rd Gen Xeon, 8x 3.75TB NVMe"},
        "i3en.metal": {"vcpus": 96, "ram_gb": 768, "raw_storage_tb": 60.0, "net_gbps": 100, "desc": "Intel Xeon, 8x 7.5TB NVMe (Storage Dense)"},
        "i3.metal": {"vcpus": 72, "ram_gb": 512, "raw_storage_tb": 15.2, "net_gbps": 25, "desc": "Intel Broadwell, 8x 1.9TB NVMe"},
        "m6id.metal": {"vcpus": 128, "ram_gb": 512, "raw_storage_tb": 7.6, "net_gbps": 50, "desc": "Intel Ice Lake, 4x 1.9TB NVMe (Compute Dense)"},
        "z1d.metal": {"vcpus": 48, "ram_gb": 384, "raw_storage_tb": 3.8, "net_gbps": 25, "desc": "High Frequency 4.0GHz Xeon"}
    },
    "azure": {
        "AN64": {"vcpus": 128, "ram_gb": 1024, "raw_storage_tb": 38.4, "net_gbps": 100, "desc": "Intel 4th Gen Xeon, 8x 4.8TB NVMe"},
        "AN36P": {"vcpus": 72, "ram_gb": 768, "raw_storage_tb": 20.7, "net_gbps": 100, "desc": "Intel Cascade Lake, 6x 3.45TB NVMe"},
        "AN36": {"vcpus": 72, "ram_gb": 576, "raw_storage_tb": 18.56, "net_gbps": 40, "desc": "Intel Skylake 6140, 6x 3.09TB NVMe"}
    },
    "gcp": {
        "c2-standard-60-bm": {"vcpus": 60, "ram_gb": 240, "raw_storage_tb": 15.0, "net_gbps": 32, "desc": "Compute-Optimized Bare Metal"},
        "c3-standard-176-bm": {"vcpus": 176, "ram_gb": 704, "raw_storage_tb": 40.0, "net_gbps": 100, "desc": "Sapphire Rapids Bare Metal"}
    }
}

CVM_DEFAULTS = {
    "vcpus": 12,        # Standard CVM allocation
    "ram_gb": 32,       # Standard CVM RAM (expand to 64GB if Deduplication/Flow is heavy)
    "ahv_vcpus": 2,     # Reserved for AHV hypervisor
    "ahv_ram_gb": 6,    # Reserved for AHV host
    "sys_disk_gb": 150  # OS disk overhead per node
}

def calculate_sizing(cloud: str, node_type: str, nodes: int, rf: int, dedup_ratio: float, cvm_ram: int):
    cloud = cloud.lower()
    if cloud not in NODE_CATALOG:
        raise ValueError(f"Unsupported cloud: {cloud}. Choose from {list(NODE_CATALOG.keys())}")
    
    if node_type not in NODE_CATALOG[cloud]:
        raise ValueError(f"Unsupported node type '{node_type}' in {cloud}. Available: {list(NODE_CATALOG[cloud].keys())}")
    
    if rf not in [2, 3]:
        raise ValueError("Replication Factor (RF) must be 2 or 3.")
    
    min_nodes = 3 if rf == 2 else 5
    if nodes < min_nodes:
        print(f"[WARNING] Minimum recommended nodes for RF{rf} is {min_nodes}. Provided: {nodes}", file=sys.stderr)

    spec = NODE_CATALOG[cloud][node_type]
    
    # Per-node overhead
    node_vcpus = spec["vcpus"]
    node_ram_gb = spec["ram_gb"]
    node_raw_tb = spec["raw_storage_tb"]
    
    cvm_overhead_vcpu = CVM_DEFAULTS["vcpus"] + CVM_DEFAULTS["ahv_vcpus"]
    cvm_overhead_ram = cvm_ram + CVM_DEFAULTS["ahv_ram_gb"]
    
    uvm_vcpu_per_node = max(0, node_vcpus - cvm_overhead_vcpu)
    uvm_ram_per_node = max(0, node_ram_gb - cvm_overhead_ram)
    
    # Cluster Totals
    cluster_vcpus_total = node_vcpus * nodes
    cluster_ram_total_gb = node_ram_gb * nodes
    cluster_uvm_vcpus = uvm_vcpu_per_node * nodes
    cluster_uvm_ram_gb = uvm_ram_per_node * nodes
    
    # Storage Calculations
    cluster_raw_tb = node_raw_tb * nodes
    system_reserve_tb = (CVM_DEFAULTS["sys_disk_gb"] * nodes) / 1024.0
    usable_raw_tb = max(0.0, cluster_raw_tb - system_reserve_tb)
    
    # RF Divisor (RF2 = 2 copies, RF3 = 3 copies)
    usable_after_rf_tb = usable_raw_tb / rf
    
    # Effective Capacity after Deduplication / Compression / EC-X
    effective_usable_tb = usable_after_rf_tb * dedup_ratio
    
    # N+1 Headroom (Storage rebuild capacity if 1 node fails)
    n_plus_one_rebuild_reserve_tb = (usable_raw_tb / nodes) / rf
    n_plus_one_usable_tb = max(0.0, usable_after_rf_tb - n_plus_one_rebuild_reserve_tb) * dedup_ratio
    
    return {
        "cloud": cloud.upper(),
        "node_type": node_type,
        "nodes": nodes,
        "replication_factor": f"RF{rf}",
        "data_reduction_ratio": f"{dedup_ratio}x",
        "compute": {
            "total_physical_vcpus": cluster_vcpus_total,
            "total_physical_ram_gb": cluster_ram_total_gb,
            "cvm_and_ahv_overhead_vcpus": cvm_overhead_vcpu * nodes,
            "cvm_and_ahv_overhead_ram_gb": cvm_overhead_ram * nodes,
            "usable_uvm_vcpus": cluster_uvm_vcpus,
            "usable_uvm_ram_gb": cluster_uvm_ram_gb
        },
        "storage": {
            "total_raw_storage_tb": round(cluster_raw_tb, 2),
            "system_reserve_tb": round(system_reserve_tb, 2),
            "usable_storage_pre_reduction_tb": round(usable_after_rf_tb, 2),
            "effective_usable_storage_tb": round(effective_usable_tb, 2),
            "n_plus_one_safe_usable_storage_tb": round(n_plus_one_usable_tb, 2)
        }
    }

def main():
    parser = argparse.ArgumentParser(description="NC2 Cluster Sizing Calculator")
    parser.add_argument("--cloud", choices=["aws", "azure", "gcp"], default="aws", help="Target Cloud Provider")
    parser.add_argument("--node-type", default="i4i.metal", help="Bare-metal instance SKU")
    parser.add_argument("--nodes", type=int, default=3, help="Number of bare-metal nodes")
    parser.add_argument("--rf", type=int, choices=[2, 3], default=2, help="Replication Factor (2 or 3)")
    parser.add_argument("--dedup-ratio", type=float, default=1.5, help="Estimated data reduction ratio (e.g. 1.5 for Compression+EC-X)")
    parser.add_argument("--cvm-ram", type=int, default=32, help="CVM RAM in GB (32 standard, 64 for heavy dedup/Files)")
    parser.add_argument("--json", action="store_true", help="Output result as JSON")
    
    args = parser.parse_args()
    
    try:
        results = calculate_sizing(args.cloud, args.node_type, args.nodes, args.rf, args.dedup_ratio, args.cvm_ram)
        if args.json:
            print(json.dumps(results, indent=2))
        else:
            print("\n=================================================================")
            print(f"       NUTANIX CLOUD CLUSTERS (NC2) SIZING ASSESSMENT")
            print("=================================================================")
            print(f" Cloud Provider       : {results['cloud']}")
            print(f" Node Type            : {results['node_type']}")
            print(f" Node Count           : {results['nodes']} Nodes ({results['replication_factor']})")
            print(f" Data Reduction Factor: {results['data_reduction_ratio']}")
            print("-----------------------------------------------------------------")
            print(" COMPUTE & MEMORY CAPACITY:")
            print(f"   • Total Physical vCPUs      : {results['compute']['total_physical_vcpus']} vCPUs")
            print(f"   • CVM & Hypervisor Overhead : {results['compute']['cvm_and_ahv_overhead_vcpus']} vCPUs")
            print(f"   • Usable User VM vCPUs      : {results['compute']['usable_uvm_vcpus']} vCPUs")
            print(f"   • Total Physical RAM        : {results['compute']['total_physical_ram_gb']} GB")
            print(f"   • CVM & Hypervisor Overhead : {results['compute']['cvm_and_ahv_overhead_ram_gb']} GB")
            print(f"   • Usable User VM RAM        : {results['compute']['usable_uvm_ram_gb']} GB")
            print("-----------------------------------------------------------------")
            print(" STORAGE CAPACITY (TB):")
            print(f"   • Total Raw NVMe Storage    : {results['storage']['total_raw_storage_tb']} TB")
            print(f"   • System Partitions Reserve : {results['storage']['system_reserve_tb']} TB")
            print(f"   • Usable (Raw / RF)         : {results['storage']['usable_storage_pre_reduction_tb']} TB")
            print(f"   • Effective Usable (With DR): {results['storage']['effective_usable_storage_tb']} TB")
            print(f"   • N+1 Resilient Usable Cap  : {results['storage']['n_plus_one_safe_usable_storage_tb']} TB")
            print("=================================================================\n")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
