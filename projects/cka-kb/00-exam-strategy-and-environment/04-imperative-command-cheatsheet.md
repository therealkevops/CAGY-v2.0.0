# Imperative Command Master Cheat Sheet

Imperative commands save precious minutes during the CKA exam. Memorize these patterns so you rarely write YAML by hand.

---

## 1. Setup Aliases
```bash
alias k=kubectl
export do="--dry-run=client -o yaml"
export now="--grace-period=0 --force"
```

---

## 2. The Novice Flag Decoder (What Do These Weird Flags Mean?)

| Flag | What It Tells Kubernetes in Plain English |
| :--- | :--- |
| **`--dry-run=client -o yaml`** | *"Pretend you are creating this, but DON'T! Just spit out the YAML blueprint onto my screen."* |
| **`--` (Double Dash)** | Separates `kubectl` flags from the container's internal command (e.g. `k run b --image=busybox -- echo "hi"`). |
| **`--restart=Never`** | Tells Kubernetes: *"Create a plain, standalone Pod, NOT a Deployment."* |
| **`--rm -it`** | Opens an interactive terminal inside the pod and **automatically deletes the pod when you exit** (great for quick debugging!). |
| **`--grace-period=0 --force`** | Skips the normal 30-second shutdown timer and kills the pod in 0.1 seconds. |

---

## 3. Pods

```bash
# Basic pod
k run nginx-pod --image=nginx

# Pod with labels and port
k run web-pod --image=nginx:alpine --port=80 -l tier=frontend,env=prod

# Pod with dry-run YAML output
k run test-pod --image=redis $do > test-pod.yaml

# Run pod with custom command and args
k run busybox --image=busybox --restart=Never $do -- /bin/sh -c "sleep 3600" > pod.yaml

# Run temporary interactive debug pod (deletes automatically on exit)
k run debug --rm -it --image=busybox:1.28 --restart=Never -- sh

# Run curl test pod against internal service
k run curl-test --rm -it --image=curlimages/curl --restart=Never -- curl http://my-service.default.svc.cluster.local:80
```

---

## 3. Deployments & Scaling

```bash
# Create deployment
k create deploy my-deploy --image=nginx:1.25 --replicas=3

# Generate deployment YAML with custom port
k create deploy web-app --image=nginx --port=80 $do > web-deploy.yaml

# Scale deployment
k scale deploy my-deploy --replicas=5

# Change image (Rolling Update)
k set image deploy/my-deploy nginx=nginx:1.26 --record

# Rollout status, history, and rollback
k rollout status deploy/my-deploy
k rollout history deploy/my-deploy
k rollout undo deploy/my-deploy
k rollout undo deploy/my-deploy --to-revision=2

# Restart deployment (rolling restart pods)
k rollout restart deploy/my-deploy

# Autoscale deployment (HPA)
k autoscale deploy my-deploy --min=2 --max=8 --cpu-percent=80
```

---

## 4. Services & Networking

```bash
# Expose deployment as ClusterIP
k expose deploy my-deploy --name=my-service --port=80 --target-port=80

# Expose deployment as NodePort
k expose deploy my-deploy --name=my-nodeport --type=NodePort --port=80 --target-port=80

# Expose pod directly
k expose pod nginx-pod --name=pod-svc --port=8080 --target-port=80

# Create NodePort service imperatively (manual port mapping)
k create service nodeport my-np --tcp=80:8080 --node-port=31000 $do > np.yaml

# Create ClusterIP service imperatively
k create service clusterip internal-svc --tcp=3306:3306

# Create Headless Service (ClusterIP: None)
k create service clusterip headless-svc --clusterip="None" --tcp=9000:9000 $do > headless.yaml
```

---

## 5. Ingress Resources

```bash
# Create basic Ingress rule
k create ingress simple-ingress --rule="foo.example.com/bar*=my-service:80"

# Ingress with TLS and path prefix
k create ingress tls-ingress \
  --rule="app.company.org/*=app-svc:8080,tls=company-tls-cert" \
  --class=nginx $do > ingress.yaml
```

---

## 6. ConfigMaps and Secrets

```bash
# ConfigMap from literal values
k create cm app-config \
  --from-literal=ENVIRONMENT=production \
  --from-literal=LOG_LEVEL=debug

# ConfigMap from an existing file
k create cm nginx-conf --from-file=/etc/nginx/nginx.conf

# ConfigMap from env-file
k create cm db-env --from-env-file=config.env

# Secret (Generic) from literals
k create secret generic db-creds \
  --from-literal=username=admin \
  --from-literal=password=P@ssw0rd123!

# Secret from file
k create secret generic app-cert --from-file=ssh-privatekey=~/.ssh/id_rsa

# TLS Secret
k create secret tls domain-tls \
  --cert=path/to/tls.crt \
  --key=path/to/tls.key
```

---

## 7. Role-Based Access Control (RBAC)

```bash
# Create ServiceAccount
k create sa developer-sa -n dev

# Create Role (scoped to single namespace)
k create role pod-reader \
  --verb=get,list,watch \
  --resource=pods,pods/log \
  --namespace=dev

# Create Role with specific resource names
k create role secret-manager \
  --verb=get,update \
  --resource=secrets \
  --resource-name=app-secret \
  -n dev

# Create ClusterRole (cluster-wide)
k create clusterrole node-admin \
  --verb=get,list,watch,update \
  --resource=nodes,nodes/status

# Create RoleBinding (binding role to ServiceAccount)
k create rolebinding dev-pod-binding \
  --role=pod-reader \
  --serviceaccount=dev:developer-sa \
  --namespace=dev

# Create ClusterRoleBinding (binding ClusterRole to User or SA)
k create clusterrolebinding node-admin-binding \
  --clusterrole=node-admin \
  --user=john \
  --serviceaccount=kube-system:node-monitor-sa

# Test RBAC Permissions
k auth can-i create pods --as=system:serviceaccount:dev:developer-sa -n dev
k auth can-i delete nodes --as=john
k auth can-i list secrets -n dev --as=system:serviceaccount:dev:developer-sa
```

---

## 8. Jobs & CronJobs

```bash
# One-time Job
k create job one-time-task --image=busybox -- echo "Hello CKA"

# Job with YAML output
k create job compute-job --image=perl:5.34 $do -- perl -Mbignum=p -wle 'print 4*atan2(1,1)' > job.yaml

# CronJob running every 5 minutes
k create cronjob every-five --schedule="*/5 * * * *" --image=busybox -- date

# CronJob running daily at midnight
k create cronjob daily-backup --schedule="0 0 * * *" --image=busybox $do -- /bin/sh -c "echo backup done" > cron.yaml
```

---

## 9. Node Maintenance & Drain

```bash
# Make node unschedulable
k cordon node01

# Re-enable scheduling
k uncordon node01

# Drain node safely for maintenance/upgrades
k drain node01 --ignore-daemonsets --delete-emptydir-data --force
```

---

## 10. Inspection, Logs & Events

```bash
# Tail logs of a pod
k logs my-pod -f

# Logs of a previous crashed container
k logs my-pod --previous

# Logs of specific container in a multi-container pod
k logs my-pod -c app-container

# Tail logs of all pods with a label selector
k logs -l app=nginx --tail=50

# Describe with grep for events
k describe pod my-pod | grep -A 10 Events:

# View recent cluster events sorted by time
k get events --sort-by='.metadata.creationTimestamp' -A
```
