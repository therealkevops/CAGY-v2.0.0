# 16 - API, Infrastructure-as-Code (IaC) & Automation in NC2

## 1. Automation Architecture Overview

Nutanix Cloud Clusters is built with an **API-first architecture**, allowing enterprise teams to automate cluster provisioning, Day-2 scaling, hibernation, network configuration, and VM orchestration via standard DevOps and IaC toolchains.

```
+-----------------------------------------------------------------------------------------------+
|                            NC2 AUTOMATION & ORCHESTRATION STACK                               |
+-----------------------------------------------------------------------------------------------+
| IaC & Configuration Engines     | Terraform (`nutanix/nutanix`), Ansible (`nutanix.ncp`),     |
|                                 | CloudFormation, Azure Bicep / ARM Templates.                |
+---------------------------------+-------------------------------------------------------------+
| Management Control APIs         | NC2 SaaS v2 REST APIs (Cluster lifecycle & Hibernation).    |
|                                 | Nutanix v4 REST APIs (Prism Central, VMs, Flow, Storage).   |
+---------------------------------+-------------------------------------------------------------+
| Developer SDKs                  | Nutanix Python SDK, Go SDK, PowerShell Cmdlets (`NTNX`).    |
+---------------------------------+-------------------------------------------------------------+
| Application Blueprints          | Nutanix Cloud Manager (NCM) Self-Service (formerly Calm).   |
+-----------------------------------------------------------------------------------------------+
```

---

## 2. NC2 v2 REST APIs (OpenAPI / OAS3 Standard)

*   **API Base URL**: `https://api.cloud.nutanix.com/api/v2`
*   **Authentication**: Bearer Token / API Key passed via HTTP Header: `Authorization: Bearer <JWT-TOKEN>`

### 1. Cluster Hibernation API Request (cURL Example)
```bash
curl -X POST "https://api.cloud.nutanix.com/api/v2/clusters/c4b8e612-89a1-4321-9988-123456abcdef/hibernate" \
  -H "Authorization: Bearer YOUR_API_KEY_HERE" \
  -H "Content-Type: application/json" \
  -d '{
    "reason": "Scheduled off-hours cost reduction",
    "powerOffVMs": true
  }'
```

### 2. Cluster Resume API Request
```bash
curl -X POST "https://api.cloud.nutanix.com/api/v2/clusters/c4b8e612-89a1-4321-9988-123456abcdef/resume" \
  -H "Authorization: Bearer YOUR_API_KEY_HERE" \
  -H "Content-Type: application/json" \
  -d '{
    "nodeCount": 3
  }'
```

---

## 3. Terraform Provider (`nutanix/nutanix`)

### Sample `main.tf` (Flow Overlay VPC and Subnet Provisioning)
```hcl
terraform {
  required_providers {
    nutanix = {
      source  = "nutanix/nutanix"
      version = ">= 1.9.0"
    }
  }
}

provider "nutanix" {
  username = var.nutanix_user
  password = var.nutanix_password
  endpoint = var.prism_central_ip
  insecure = true
  port     = 9440
}

# Create a Flow Overlay Virtual Private Cloud (VPC)
resource "nutanix_vpc" "app_vpc" {
  name        = "NC2-App-VPC"
  description = "Overlay VPC for multi-tier microservices"
  
  external_subnet_reference_uuid_list = [
    var.external_transit_subnet_uuid
  ]
}

# Create an Overlay Subnet with IPAM
resource "nutanix_subnet" "app_subnet" {
  name               = "NC2-App-Subnet-1"
  vpc_reference_uuid = nutanix_vpc.app_vpc.id
  subnet_type        = "OVERLAY"
  
  subnet_ip          = "172.16.10.0"
  prefix_length      = 24
  default_gateway_ip = "172.16.10.1"

  ip_config_pool_list_ranges {
    start_ip = "172.16.10.50"
    end_ip   = "172.16.10.200"
  }
}
```

---

## 4. Ansible Automation (`nutanix.ncp` Collection)

```bash
ansible-galaxy collection install nutanix.ncp
```

### Sample Snapshot Playbook
```yaml
---
- name: Automate NC2 Virtual Machine Snapshot
  hosts: localhost
  gather_facts: false
  vars:
    nutanix_host: "10.0.1.50"
    nutanix_user: "admin"
    nutanix_password: "{{ vault_nutanix_password }}"
    vm_name: "App-Server-01"

  tasks:
    - name: Create VM Recovery Snapshot
      nutanix.ncp.ntnx_snapshots:
        nutanix_host: "{{ nutanix_host }}"
        nutanix_user: "{{ nutanix_user }}"
        nutanix_password: "{{ nutanix_password }}"
        validate_certs: false
        state: present
        snapshot_name: "Pre-Maintenance-Backup-{{ ansible_date_time.iso8601 }}"
        vm_uuid: "45f8a0bc-1111-2222-3333-abcdef123456"
```
