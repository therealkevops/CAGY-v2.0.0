# 20 Speedrun Practice Challenges — Exam Simulation Study Guide

> **Official Curriculum Mapping:** Cross-Domain Mock Exam (100% of CKA competencies)  
> **Target Total Time:** 110 minutes (10 minutes buffer)  
> **Passing Benchmark:** $\ge 66\%$ score

---

## Challenge 1: Drain & Upgrade Control Plane (Domain: Cluster Architecture | 11% | Target: 8 mins)

**Task:**
Upgrade the control plane node `controlplane` from version `1.30.0` to `1.31.0`. Ensure workloads are safely drained first.

```bash
# 1. Drain node
kubectl drain controlplane --ignore-daemonsets

# 2. Upgrade kubeadm
apt-mark unhold kubeadm
apt-get update && apt-get install -y kubeadm=1.31.0-1.1
apt-mark hold kubeadm

# 3. Plan & Apply
kubeadm upgrade plan
kubeadm upgrade apply v1.31.0 -y

# 4. Upgrade kubelet & kubectl
apt-mark unhold kubelet kubectl
apt-get install -y kubelet=1.31.0-1.1 kubectl=1.31.0-1.1
apt-mark hold kubelet kubectl

# 5. Restart & Uncordon
systemctl daemon-reload && systemctl restart kubelet
kubectl uncordon controlplane

# Verification:
kubectl get nodes
```

---

## Challenge 2: ETCD Snapshot Backup & Verification (Domain: Cluster Architecture | 8% | Target: 4 mins)

**Task:**
Take a snapshot of the active ETCD datastore running on `https://127.0.0.1:2379`. Save it to `/opt/etcd-backup.db`.

```bash
ETCDCTL_API=3 etcdctl \
  --endpoints=https://127.0.0.1:2379 \
  --cacert=/etc/kubernetes/pki/etcd/ca.crt \
  --cert=/etc/kubernetes/pki/etcd/server.crt \
  --key=/etc/kubernetes/pki/etcd/server.key \
  snapshot save /opt/etcd-backup.db

# Verification:
ETCDCTL_API=3 etcdctl snapshot status /opt/etcd-backup.db -w table
```

---

## Challenge 3: RBAC ServiceAccount & RoleBinding (Domain: Cluster Architecture | 6% | Target: 3 mins)

**Task:**
In namespace `engineering`, create a ServiceAccount named `builder-sa`. Create a Role named `pod-editor` allowing `get`, `list`, `update` on `pods`. Bind this Role to `builder-sa`.

```bash
kubectl create namespace engineering
kubectl create sa builder-sa -n engineering
kubectl create role pod-editor --verb=get,list,update --resource=pods -n engineering
kubectl create rolebinding builder-sa-binding --role=pod-editor --serviceaccount=engineering:builder-sa -n engineering

# Verification:
kubectl auth can-i update pods --as=system:serviceaccount:engineering:builder-sa -n engineering
# Expected: yes
```

---

## Challenge 4: Deploy Gateway API HTTPRoute (Domain: Services & Networking | 8% | Target: 5 mins)

**Task:**
Create an `HTTPRoute` named `orders-route` in namespace `default` attached to existing Gateway `prod-gateway` in namespace `infra`. Route `/orders` to Service `orders-svc` on port 8080.

```yaml
cat <<EOF | kubectl apply -f -
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: orders-route
  namespace: default
spec:
  parentRefs:
  - name: prod-gateway
    namespace: infra
  rules:
  - matches:
    - path:
        type: PathPrefix
        value: /orders
    backendRefs:
    - name: orders-svc
      port: 8080
EOF

# Verification:
kubectl get httproute orders-route
```

---

## Challenge 5: NetworkPolicy Ingress Isolation (Domain: Services & Networking | 7% | Target: 5 mins)

**Task:**
In namespace `production`, isolate pods labeled `app=database` so they only accept TCP traffic on port `5432` from pods labeled `app=backend` in the same namespace.

```yaml
cat <<EOF | kubectl apply -f -
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-backend-to-db
  namespace: production
spec:
  podSelector:
    matchLabels:
      app: database
  policyTypes:
  - Ingress
  ingress:
  - from:
    - podSelector:
        matchLabels:
          app: backend
    ports:
    - protocol: TCP
      port: 5432
EOF

# Verification:
kubectl describe networkpolicy allow-backend-to-db -n production
```

---

## Challenge 6: Troubleshoot Dead Kubelet on Worker (Domain: Troubleshooting | 10% | Target: 6 mins)

**Task:**
Node `worker-2` is in `NotReady` state. Resolve the problem and restore the node to `Ready`.

```bash
ssh worker-2
sudo -i
systemctl status kubelet
journalctl -u kubelet -e --no-pager -n 30
# If swap enabled:
swapoff -a && sed -i '/swap/d' /etc/fstab
systemctl restart kubelet
exit

# Verification:
kubectl get nodes worker-2
```

---

## Challenge 7: Multi-Container Pod with Volume Sharing (Domain: Workloads | 5% | Target: 4 mins)

**Task:**
Create a Pod named `shared-logs` in namespace `default` with two containers:
1. `writer`: Image `busybox`, command writing `date` every 5 seconds to `/var/log/app.log`.
2. `reader`: Image `busybox`, command `tail -f /var/log/app.log`.
Use an `emptyDir` volume mounted at `/var/log` in both containers.

```yaml
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: shared-logs
spec:
  volumes:
  - name: log-dir
    emptyDir: {}
  containers:
  - name: writer
    image: busybox
    command: ["sh", "-c", "while true; do date >> /var/log/app.log; sleep 5; done"]
    volumeMounts:
    - name: log-dir
      mountPath: /var/log
  - name: reader
    image: busybox
    command: ["sh", "-c", "tail -f /var/log/app.log"]
    volumeMounts:
    - name: log-dir
      mountPath: /var/log
EOF

# Verification:
kubectl logs shared-logs -c reader
```

---

## Challenge 8: StorageClass with WaitForFirstConsumer (Domain: Storage | 5% | Target: 4 mins)

**Task:**
Create a StorageClass named `delayed-storage` using provisioner `kubernetes.io/no-provisioner` with `volumeBindingMode: WaitForFirstConsumer` and `reclaimPolicy: Retain`.

```yaml
cat <<EOF | kubectl apply -f -
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: delayed-storage
provisioner: kubernetes.io/no-provisioner
volumeBindingMode: WaitForFirstConsumer
reclaimPolicy: Retain
EOF

# Verification:
kubectl get sc delayed-storage
```

---

## Challenge 9: PersistentVolume & PersistentVolumeClaim (Domain: Storage | 6% | Target: 5 mins)

**Task:**
Create a PersistentVolume named `app-pv` with capacity `2Gi`, accessMode `ReadWriteOnce`, storageClassName `manual`, hostPath `/data/app`. Create a PVC named `app-pvc` requesting `1Gi` using storageClass `manual`.

```yaml
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: PersistentVolume
metadata:
  name: app-pv
spec:
  capacity:
    storage: 2Gi
  accessModes:
    - ReadWriteOnce
  storageClassName: manual
  hostPath:
    path: /data/app
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: app-pvc
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: manual
  resources:
    requests:
      storage: 1Gi
EOF

# Verification:
kubectl get pvc app-pvc
# Status must be Bound
```

---

## Challenge 10: Node Affinity Scheduling (Domain: Scheduling | 5% | Target: 4 mins)

**Task:**
Create a Deployment named `zoned-deploy` with image `nginx:alpine` and 3 replicas. The pods must be scheduled only on nodes with label `zone=east`.

```bash
kubectl create deploy zoned-deploy --image=nginx:alpine --replicas=3 --dry-run=client -o yaml > zoned.yaml
```
Add affinity under `spec.template.spec`:
```yaml
      affinity:
        nodeAffinity:
          requiredDuringSchedulingIgnoredDuringExecution:
            nodeSelectorTerms:
            - matchExpressions:
              - key: zone
                operator: In
                values:
                - east
```
```bash
kubectl apply -f zoned.yaml

# Verification:
kubectl get pods -o wide -l app=zoned-deploy
```

---

## Challenge 11: Taints & Tolerations (Domain: Scheduling | 5% | Target: 4 mins)

**Task:**
Taint node `worker-1` with `tier=critical:NoSchedule`. Deploy a pod named `critical-pod` with image `redis` that tolerates this taint.

```bash
kubectl taint nodes worker-1 tier=critical:NoSchedule

cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: critical-pod
spec:
  tolerations:
  - key: "tier"
    operator: "Equal"
    value: "critical"
    effect: "NoSchedule"
  containers:
  - name: redis
    image: redis
EOF

# Verification:
kubectl get pod critical-pod -o wide
```

---

## Challenge 12: Deployment Rolling Update & Rollback (Domain: Workloads | 4% | Target: 3 mins)

**Task:**
Deployment `frontend` is running `nginx:1.24`. Update it to `nginx:1.25` and record the change. Verify the rollout, then rollback to the initial revision.

```bash
kubectl set image deployment/frontend nginx=nginx:1.25 --record
kubectl rollout status deployment/frontend
kubectl rollout undo deployment/frontend --to-revision=1

# Verification:
kubectl get deployment frontend -o jsonpath='{.spec.template.spec.containers[*].image}'
```

---

## Challenge 13: Horizontal Pod Autoscaler (HPA) (Domain: Workloads | 5% | Target: 3 mins)

**Task:**
Autoscale deployment `auth-service` to maintain average CPU utilization of 60%. Min replicas: 2, Max replicas: 8.

```bash
kubectl autoscale deployment auth-service --min=2 --max=8 --cpu-percent=60

# Verification:
kubectl get hpa auth-service
```

---

## Challenge 14: Top Resource Consumers to File (Domain: Troubleshooting | 4% | Target: 2 mins)

**Task:**
Identify the pod in namespace `analytics` consuming the highest CPU and output its name to `/opt/top_cpu_pod.txt`.

```bash
kubectl top pods -n analytics --sort-by=cpu --no-headers | head -n 1 | awk '{print $1}' > /opt/top_cpu_pod.txt

# Verification:
cat /opt/top_cpu_pod.txt
```

---

## Challenge 15: Create Static Pod (Domain: Workloads | 5% | Target: 3 mins)

**Task:**
On node `controlplane`, create a static pod named `static-busybox` running `image: busybox` with command `sleep 3600`.

```bash
ssh controlplane
sudo -i
kubectl run static-busybox --image=busybox --restart=Never --dry-run=client -o yaml -- /bin/sh -c "sleep 3600" > /etc/kubernetes/manifests/static-busybox.yaml
exit

# Verification:
kubectl get pods -A | grep static-busybox
```

---

## Challenge 16: Ingress Path Routing with TLS (Domain: Services | 7% | Target: 5 mins)

**Task:**
Create an Ingress named `web-ingress` for host `test.k8s.local` routing `/app` to `app-svc:80` using `ingressClassName: nginx` and TLS secret `tls-secret`.

```bash
kubectl create ingress web-ingress \
  --rule="test.k8s.local/app*=app-svc:80,tls=tls-secret" \
  --class=nginx

# Verification:
kubectl describe ingress web-ingress
```

---

## Challenge 17: Service NodePort Exposure (Domain: Services | 4% | Target: 2 mins)

**Task:**
Expose deployment `nginx-dep` as a NodePort Service named `nginx-svc` on port 80 forwarding to container port 80 with nodePort 30200.

```bash
kubectl create service nodeport nginx-svc --tcp=80:80 --node-port=30200 --dry-run=client -o yaml > svc.yaml
# Ensure selector matches deployment labels:
sed -i 's/app: nginx-svc/app: nginx-dep/' svc.yaml
kubectl apply -f svc.yaml

# Verification:
kubectl get svc nginx-svc
```

---

## Challenge 18: Helm Chart Deployment (Domain: Cluster Architecture | 5% | Target: 3 mins)

**Task:**
Install chart `bitnami/apache` with release name `my-apache` into namespace `web-servers` (create if needed) setting `replicaCount=2`.

```bash
helm repo add bitnami https://charts.bitnami.com/bitnami
helm repo update
helm install my-apache bitnami/apache --namespace web-servers --create-namespace --set replicaCount=2

# Verification:
helm list -n web-servers
```

---

## Challenge 19: Kustomize Overlay Deployment (Domain: Cluster Architecture | 5% | Target: 3 mins)

**Task:**
Deploy the kustomize overlay located at `/opt/kustomize/overlays/staging`.

```bash
kubectl apply -k /opt/kustomize/overlays/staging

# Verification:
kubectl get all -l env=staging
```

---

## Challenge 20: Fix CrashLooping Pod (Wrong CMD) (Domain: Troubleshooting | 6% | Target: 3 mins)

**Task:**
Pod `failing-logger` in namespace `default` is in `CrashLoopBackOff`. Fix the pod definition so it stays running.

```bash
# 1. Check previous logs
kubectl logs failing-logger --previous
# 2. Extract and fix YAML
kubectl get pod failing-logger -o yaml > pod-fix.yaml
# Edit command from "non-existent-binary" to ["sh", "-c", "sleep 3600"]
sed -i 's/non-existent-cmd/sleep 3600/' pod-fix.yaml
kubectl replace --force -f pod-fix.yaml

# Verification:
kubectl get pod failing-logger
# Status must be Running
```
