# ADR 004: cgroup systemd Driver Standardization

- **Date**: 2026-09-05
- **Status**: Accepted
- **Space**: `cka-kb`
- **Tags**: #cka #troubleshooting #containerd #cgroup #systemd #kubelet #adr

## Context & Problem Statement
A notorious and frequent CKA exam scenario involves a worker node marked as `NotReady` immediately following an upgrade or initial join. When inspecting `systemctl status kubelet`, the service is actively crashing with errors such as:
`"failed to run Kubelet: misconfiguration: kubelet cgroup driver: \"systemd\" is different from docker/containerd cgroup driver: \"cgroupfs\""`

Linux cgroups manage CPU, memory, and I/O resource limits for processes. If the host init system (`systemd`) and the container runtime (`containerd` via `cgroupfs`) attempt to manage cgroups independently using different drivers, kernel race conditions occur under memory pressure, leading to spontaneous node eviction and kubelet termination.

## Decision
We enforce `SystemdCgroup = true` as the cluster-wide standard across all node configurations, runtime initialization scripts, and triage runbooks:

1. **containerd Configuration** (`/etc/containerd/config.toml`):
   ```toml
   [plugins."io.containerd.grpc.v1.cri".containerd.runtimes.runc.options]
     SystemdCgroup = true
   ```
2. **kubelet Configuration** (`/var/lib/kubelet/config.yaml`):
   ```yaml
   cgroupDriver: systemd
   ```
3. **Execution Hook**: Always restart and verify the services in sequence:
   ```bash
   sudo systemctl restart containerd
   sudo systemctl daemon-reload
   sudo systemctl restart kubelet
   ```

## Consequences
- **Positive**:
  - Unified cgroup v2 hierarchy managed solely by systemd.
  - Eliminates the primary cause of sudden `NotReady` node state in upgrade and worker recovery exam questions.
  - Complete conformance with Kubernetes v1.28+ default expectations.
- **Trade-off**:
  - Requires generating default containerd configurations via `containerd config default` if `/etc/containerd/config.toml` is absent or malformed.

## Interlinks & Related Knowledge
- **Space Hub**: [[overview|CKA Space Overview]]
- **Architecture**: [[control_plane_topology|Kubernetes Control Plane Topology]]
- **Troubleshooting**: [[troubleshooting_decision_trees|Troubleshooting Decision Trees]]
- **Playbooks & Study Guides**:
  - [[exam_triage_and_speedrun_playbook|Exam Speedrun & Triage Playbook]]
  - [Node & Kubelet Troubleshooting](file:///workspace/projects/cka-kb/01-troubleshooting/01-node-and-kubelet-troubleshooting.md)
  - [kubeadm Cluster Bootstrap](file:///workspace/projects/cka-kb/02-cluster-architecture-installation/01-kubeadm-cluster-bootstrap.md)
