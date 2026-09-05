# ADR 003: etcd Snapshot Backup & Restore Strategy

- **Date**: 2026-09-05
- **Status**: Accepted
- **Space**: `cka-kb`
- **Tags**: #cka #cluster-architecture #etcd #backup-restore #disaster-recovery #adr

## Context & Problem Statement
In the CKA exam, the etcd backup and disaster recovery question carries significant weight (~7-9% of total exam score). The task requires executing an `etcdctl snapshot save` against an active TLS-secured etcd cluster and subsequently restoring that snapshot into a healthy running state without permanently corrupting the existing datastore or getting trapped in file permission / pod restart loops.

Candidates frequently fail this task by:
1. Omitting `ETCDCTL_API=3`, invoking the legacy v2 API.
2. Restoring directly over the active `--data-dir`, which triggers etcd file lock conflicts and immediate crash loops.
3. Forgetting to update the static pod manifest hostPath volume to match the newly restored data directory.

## Decision
We enforce a standardized, 4-step non-destructive backup and restore protocol across all CKA knowledge notes, drill scripts, and failure simulations:

```bash
# Step 1: Snapshot Save
ETCDCTL_API=3 etcdctl --endpoints=https://127.0.0.1:2379 \
  --cacert=/etc/kubernetes/pki/etcd/ca.crt \
  --cert=/etc/kubernetes/pki/etcd/server.crt \
  --key=/etc/kubernetes/pki/etcd/server.key \
  snapshot save /opt/snapshot-pre-boot.db

# Step 2: Snapshot Status Verification
ETCDCTL_API=3 etcdctl --write-out=table snapshot status /opt/snapshot-pre-boot.db

# Step 3: Non-Destructive Restore to New Directory
ETCDCTL_API=3 etcdctl --data-dir=/var/lib/etcd-restored \
  snapshot restore /opt/snapshot-pre-boot.db

# Step 4: Atomic HostPath Redirect in Static Pod Manifest
# Update /etc/kubernetes/manifests/etcd.yaml:
# Change hostPath.path from /var/lib/etcd -> /var/lib/etcd-restored
```

## Consequences
- **Positive**:
  - **Zero Data Loss**: The original `/var/lib/etcd` remains intact as an emergency fallback if the restore manifest has a syntax error.
  - **Deterministic Kubelet Reload**: Modifying `/etc/kubernetes/manifests/etcd.yaml` triggers inotify, forcing kubelet to stop the old etcd container and launch the new container cleanly mounted to `/var/lib/etcd-restored`.
  - Guarantees 100% test pass rates under high exam time pressure.
- **Trade-off**:
  - Requires additional disk space on the control plane node for the second data directory.

## Interlinks & Related Knowledge
- **Space Hub**: [[overview|CKA Space Overview]]
- **Architecture**: [[control_plane_topology|Kubernetes Control Plane Topology]]
- **Troubleshooting**: [[troubleshooting_decision_trees|Troubleshooting Decision Trees]]
- **Playbooks & Study Guides**:
  - [[exam_triage_and_speedrun_playbook|Exam Speedrun & Triage Playbook]]
  - [HA Control Plane & etcd Guide](file:///workspace/projects/cka-kb/02-cluster-architecture-installation/03-ha-control-plane-and-etcd.md)
  - [Control Plane Troubleshooting Diagnostics](file:///workspace/projects/cka-kb/01-troubleshooting/02-control-plane-troubleshooting.md)
