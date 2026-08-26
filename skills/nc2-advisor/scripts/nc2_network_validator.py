#!/usr/bin/env python3
"""
Nutanix Cloud Clusters (NC2) Network & MTU Budget Validator
Validates MTU constraints, Geneve encapsulation headers, TCP MSS clamping values,
and subnet capacity for AWS, Azure, GCP, and GC2 hybrid deployments.
"""

import argparse
import sys
import json

GENEVE_HEADER_BYTES = 50  # 14 Ethernet + 20 IP + 8 UDP + 8 Geneve
TCP_IP_HEADER_BYTES = 40  # 20 IP + 20 TCP

CLOUD_NETWORK_PROFILES = {
    "aws": {
        "underlay_mtu": 9001,
        "max_overlay_mtu": 8950,
        "recommended_tcp_mss": 8910,
        "secondary_ip_limit_per_eni": 49,
        "max_enis_per_node": 15
    },
    "azure": {
        "underlay_mtu": 1500,
        "max_overlay_mtu": 1450,
        "recommended_tcp_mss": 1410,
        "secondary_ip_limit_per_eni": 0,  # FVN mandatory
        "flow_gateway_recommended_nodes": 2
    },
    "gcp": {
        "underlay_mtu": 1460,
        "max_overlay_mtu": 1410,
        "recommended_tcp_mss": 1370,
        "secondary_ip_limit_per_eni": 0,
        "alias_ip_supported": True
    },
    "gc2": {
        "underlay_mtu": 9001,
        "max_overlay_mtu": 8950,
        "recommended_tcp_mss": 8910,
        "air_gapped": True
    }
}

def validate_network(cloud: str, requested_mtu: int, user_vms_count: int, routing_method: str):
    cloud = cloud.lower()
    if cloud not in CLOUD_NETWORK_PROFILES:
        raise ValueError(f"Unknown cloud '{cloud}'. Choose from {list(CLOUD_NETWORK_PROFILES.keys())}")

    profile = CLOUD_NETWORK_PROFILES[cloud]
    underlay_mtu = profile["underlay_mtu"]
    max_overlay_mtu = underlay_mtu - GENEVE_HEADER_BYTES
    calculated_mss = requested_mtu - TCP_IP_HEADER_BYTES
    
    warnings = []
    recommendations = []

    if requested_mtu > max_overlay_mtu:
        warnings.append(f"Requested MTU ({requested_mtu}) exceeds maximum allowable Geneve overlay MTU ({max_overlay_mtu}) for {cloud.upper()}. Packets will fragment or drop.")
        recommendations.append(f"Reduce guest VM MTU to {max_overlay_mtu} or enable Flow Gateway TCP MSS Clamping to {max_overlay_mtu - TCP_IP_HEADER_BYTES}.")
    
    if cloud == "azure":
        if routing_method == "ars":
            recommendations.append("Azure Route Server requires 2 dedicated BGP VMs. Consider upgrading to Azure Internal Load Balancer (ILB) on Prism Central 7.5+ to eliminate BGP VMs and ARS costs.")
        elif routing_method == "ilb":
            recommendations.append("Azure ILB routing active: Configure health probes on TCP Port 22 with 5s interval and enable HA Ports on load balancing rule.")
        elif routing_method == "vwan":
            recommendations.append("Azure Virtual WAN Hub routing active: Ensure eBGP peering is established with Virtual Hub Router (ASN 65515) and traffic inspection policies are applied via Secured Virtual Hub.")

    if cloud == "aws":
        enis_needed = max(1, (user_vms_count + profile["secondary_ip_limit_per_eni"] - 1) // profile["secondary_ip_limit_per_eni"])
        if enis_needed > profile["max_enis_per_node"]:
            warnings.append(f"User VM count ({user_vms_count}) will exceed maximum ENI limit per host ({profile['max_enis_per_node']} ENIs).")
            recommendations.append("Distribute VMs across more bare-metal hosts or switch to Flow Virtual Networking (FVN) overlay.")

    return {
        "cloud": cloud.upper(),
        "underlay_mtu": underlay_mtu,
        "geneve_encapsulation_overhead_bytes": GENEVE_HEADER_BYTES,
        "max_safe_overlay_mtu": max_overlay_mtu,
        "recommended_tcp_mss_clamping": max_overlay_mtu - TCP_IP_HEADER_BYTES,
        "requested_guest_mtu": requested_mtu,
        "calculated_tcp_mss": calculated_mss,
        "warnings": warnings,
        "recommendations": recommendations,
        "status": "PASS" if not warnings else "WARNING"
    }

def main():
    parser = argparse.ArgumentParser(description="NC2 Network & MTU Budget Validator")
    parser.add_argument("--cloud", choices=["aws", "azure", "gcp", "gc2"], default="azure", help="Target Cloud")
    parser.add_argument("--mtu", type=int, default=1500, help="Target Guest VM MTU")
    parser.add_argument("--vms", type=int, default=50, help="Planned User VM count per host")
    parser.add_argument("--routing-method", choices=["ilb", "ars", "vwan", "native"], default="ilb", help="Azure/AWS Routing Architecture")
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()
    
    res = validate_network(args.cloud, args.mtu, args.vms, args.routing_method)
    
    if args.json:
        print(json.dumps(res, indent=2))
    else:
        print("\n=================================================================")
        print(f"       NUTANIX CLOUD CLUSTERS (NC2) NETWORK VALIDATION")
        print("=================================================================")
        print(f" Cloud Provider        : {res['cloud']}")
        print(f" Overall Status        : {res['status']}")
        print(f" Physical Underlay MTU : {res['underlay_mtu']} Bytes")
        print(f" Geneve Header Overhead: {res['geneve_encapsulation_overhead_bytes']} Bytes")
        print(f" Maximum Safe Inner MTU: {res['max_safe_overlay_mtu']} Bytes")
        print(f" Recommended TCP MSS   : {res['recommended_tcp_mss_clamping']} Bytes")
        print("-----------------------------------------------------------------")
        if res["warnings"]:
            print(" [!] WARNINGS DETECTED:")
            for w in res["warnings"]:
                print(f"     • {w}")
            print("-----------------------------------------------------------------")
        if res["recommendations"]:
            print(" [*] ARCHITECTURAL RECOMMENDATIONS:")
            for r in res["recommendations"]:
                print(f"     • {r}")
        print("=================================================================\n")

if __name__ == "__main__":
    main()
