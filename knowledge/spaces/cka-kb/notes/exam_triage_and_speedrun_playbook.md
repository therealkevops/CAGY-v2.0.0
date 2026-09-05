# CKA Exam Triage & Speedrun Playbook

- **Space**: `cka-kb`
- **Domain**: Exam Strategy & Environment (00) & Speed Optimization
- **Tags**: #cka #exam-strategy #speedrun #terminal #vimrc #bash #triage

---

## 1. Exam Environment Mechanics & Speed Setup

The CKA exam runs inside a browser-based PSI Remote Proctor terminal (120 minutes, ~15-17 questions, 66% passing score). Every second spent typing manual YAML or scrolling through deep documentation submenus costs precious points.

### 1.1 The Golden 30-Second Shell Bootstrapping
Execute this block immediately upon session launch:

```bash
# ~/.bashrc quick injection
alias k=kubectl
export do="--dry-run=client -o yaml"
export now="--force --grace-period=0"
complete -o default -F __start_kubectl k

# ~/.vimrc 2-space YAML formatting
cat << 'EOF' > ~/.vimrc
set tabstop=2
set shiftwidth=2
set expandtab
set number
set cursorline
syntax on
EOF
```

### 1.2 Point-per-Minute Metric
- Total exam time: **120 minutes** for **100 percentage points**.
- Budget: **~1.2 minutes per percentage point**.
- A 4% task should take **under 5 minutes**.
- An 8% task (e.g. Cluster Upgrade, etcd Backup/Restore) warrants up to **10 minutes**.
- **The 6-Minute Rule**: If you hit a roadblock on any single task exceeding 6 minutes with no obvious fix in sight, flag it, write down the question number in the scratchpad, and proceed immediately. Never let one stubborn question consume 20 minutes.

---

## 2. Speedrun Imperative Generator Cheat Sheet

| Task | Fast Imperative Command |
| :--- | :--- |
| **Pod with Custom Command** | `k run nginx --image=nginx $do -- /bin/sh -c "sleep 3600" > pod.yaml` |
| **Expose Deployment (ClusterIP)** | `k expose deploy web --port=80 --target-port=8080 --name=web-svc` |
| **Expose NodePort** | `k expose deploy web --type=NodePort --port=80 --name=web-np $do > np.yaml` |
| **Multi-Container Pod Scaffold** | `k run box --image=busybox $do > box.yaml` (duplicate container block in vim) |
| **NetworkPolicy Scaffold** | Copy template from `kubernetes.io/docs` or run `k create -f - <<EOF ...` |
| **Role & RoleBinding** | `k create role dev-role --verb=get,list,watch --resource=pods,deployments -n dev`<br>`k create rolebinding dev-rb --role=dev-role --user=jane -n dev` |
| **ServiceAccount & Token** | `k create sa deploy-sa -n prod` |
| **Secret from Literal** | `k create secret generic db-pass --from-literal=password=SuperSecret -n prod` |
| **Job with Parallelism** | `k create job batch-job --image=busybox $do -- sleep 10 > job.yaml` |

---

## 3. High-Speed Documentation Search Keys

The exam permits a single browser tab to `kubernetes.io/docs` and `gateway-api.sigs.k8s.io`. Use these exact search queries to jump straight to copy-pasteable manifests:
- **`networkpolicy`** $\rightarrow$ Click *"Declare Network Policy"* $\rightarrow$ Copy the complete ingress/egress example.
- **`ingress`** $\rightarrow$ Click *"Ingress"* $\rightarrow$ Copy simple path routing block.
- **`httproute`** $\rightarrow$ Click Gateway API documentation $\rightarrow$ Copy `HTTPRoute` spec.
- **`etcd backup`** $\rightarrow$ Click *"Operating etcd clusters for Kubernetes"* $\rightarrow$ Copy snapshot save/restore commands (see [[adr_003_etcd_backup_restore_strategy|ADR 003]]).
- **`pv pvc`** $\rightarrow$ Click *"Configure a Pod to Use a PersistentVolume for Storage"*.

---

## 4. Interlinks & Related Knowledge

- **Space Hub**: [[overview|CKA Space Overview]]
- **Troubleshooting**: [[troubleshooting_decision_trees|Troubleshooting Decision Trees]]
- **Curriculum Domain Map**: [[cka_curriculum_domain_map|CKA Curriculum Domain Map]]
- **Decisions & Standards**:
  - [[adr_001_calico_ebpf_data_plane|ADR 001: Calico eBPF Data Plane]]
  - [[adr_002_gateway_api_adoption|ADR 002: Gateway API Adoption]]
  - [[adr_003_etcd_backup_restore_strategy|ADR 003: etcd Snapshot Backup & Restore]]
  - [[adr_004_cgroup_systemd_standardization|ADR 004: cgroup systemd Standardization]]
- **Study Guides**:
  - [Exam Format, Rules & UI](file:///workspace/projects/cka-kb/00-exam-strategy-and-environment/01-exam-format-rules-and-ui.md)
  - [Terminal Setup & Speedruns](file:///workspace/projects/cka-kb/00-exam-strategy-and-environment/02-terminal-setup-and-speedruns.md)
  - [Killer.sh & Simulation Strategy](file:///workspace/projects/cka-kb/00-exam-strategy-and-environment/03-killer-sh-and-simulation-strategy.md)
  - [Imperative Command Cheatsheet](file:///workspace/projects/cka-kb/00-exam-strategy-and-environment/04-imperative-command-cheatsheet.md)
