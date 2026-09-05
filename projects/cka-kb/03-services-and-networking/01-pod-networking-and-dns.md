# Pod Networking Model & CoreDNS — Novice-Friendly Study Guide

> **Official Curriculum Reference:** Domain: *Services and Networking (20%)*  
> **Official Documentation Links:**  
> - [Cluster Networking](https://kubernetes.io/docs/concepts/cluster-administration/networking/)  
> - [DNS for Services and Pods](https://kubernetes.io/docs/concepts/services-networking/dns-pod-service/)  
> - [Customizing DNS Service](https://kubernetes.io/docs/tasks/administer-cluster/dns-custom-nameservers/)

---

## 1. Explain Like I'm a Novice: Direct Phone Lines & Virtual Cables

In your home WiFi network, all your family members (laptops, phones, smart TVs) hide behind a single home router using **NAT (Network Address Translation)**. If your laptop wants to talk to a phone, it can be tricky.

Kubernetes rejected that complexity and created three non-negotiable networking laws:
1. **Every Pod gets its own real, unique IP address.**
2. **Every Pod can talk to every other Pod directly without NAT!** (No port-forwarding or address translation).
3. **Every Node can talk to every Pod on that node.**

```mermaid
flowchart TD
    subgraph PodA ["Pod A (Isolated Network Room)"]
        Eth0["eth0 interface (10.244.1.5)"]
    end

    subgraph HostWorker ["Physical Host Node (Linux Kernel)"]
        VethA["veth-abc (Virtual Cable End)"]
        Bridge["cni0 / cbr0 (Virtual Network Switch)"]
        PhysicalNIC["Physical NIC (192.168.1.11)"]
    end

    Eth0 <-->|Virtual Ethernet Cable (veth pair)| VethA
    VethA <--> Bridge
    Bridge <--> PhysicalNIC
```

### The Virtual Ethernet Cable (`veth` Pair)
A container lives in an isolated network room called a **Network Namespace (`netns`)**. How does it connect to the rest of the world?

Linux creates a **Virtual Ethernet Pair (`veth` pair)**, which acts just like a physical ethernet cable:
- You plug one end into the container (`eth0`).
- You plug the other end into the node's virtual switch (`cni0` bridge).
- Any packet the container sends through `eth0` instantly pops out on the host bridge!

---

## 2. Kubernetes DNS Standards: The Fully Qualified Domain Name (FQDN)

Instead of memorizing dynamic Pod IPs, Kubernetes automatically manages a local DNS phone book (**CoreDNS**).

### How to Address Services:
- **Talking to a Service in the same namespace:**
  ```text
  http://database-svc:5432
  ```
- **Talking to a Service in a different namespace:**
  ```text
  http://database-svc.production:5432
  ```
- **The Full Formal Address (FQDN):**
  ```text
  http://database-svc.production.svc.cluster.local:5432
  ```

---

## 3. Demystifying `/etc/resolv.conf` & `ndots:5`

If you open the `/etc/resolv.conf` file inside any Kubernetes pod:
```bash
kubectl exec my-pod -- cat /etc/resolv.conf
```
You will see:
```text
nameserver 10.96.0.10
search default.svc.cluster.local svc.cluster.local cluster.local
options ndots:5
```

### What on Earth is `ndots:5`?
When you type `curl google.com`, there is only **1 dot** in `google.com`.

Because 1 dot is less than 5 (`ndots:5`), the Linux DNS resolver assumes:
> *"This name might be a local service inside our Kubernetes cluster! Let me check the local search domains first before asking the internet."*

The resolver tries:
1. `google.com.default.svc.cluster.local` (fails)
2. `google.com.svc.cluster.local` (fails)
3. `google.com.cluster.local` (fails)
4. `google.com.` (finally asks public DNS and succeeds!)

> [!TIP]
> To bypass this multi-query delay for external domains, developers append a trailing dot: `google.com.`.

---

## 4. Customizing Pod DNS (`dnsPolicy`)

By default, every pod uses `dnsPolicy: ClusterFirst` (routes internal queries to CoreDNS).

If you need a pod to use external public DNS directly (e.g. Google `8.8.8.8` or Cloudflare `1.1.1.1`):
```yaml
apiVersion: v1
kind: Pod
metadata:
  name: external-dns-pod
spec:
  dnsPolicy: "None" # Ignore CoreDNS completely
  dnsConfig:
    nameservers:
      - 8.8.8.8
      - 1.1.1.1
  containers:
  - name: app
    image: nginx
```

---

## 5. Rookie Traps & Exam Gotchas

> [!CAUTION]
> 1. **Cross-Namespace Service Calls Without Namespace:** If you run `curl http://backend` from a pod in namespace `dev`, and the backend is in namespace `prod`, it will fail! You must specify `curl http://backend.prod`.
> 2. **Pods Using `hostNetwork: true`:** If a pod runs on the host network, its IP is the physical host's IP, and its default `dnsPolicy` must be `ClusterFirstWithHostNet` if it still needs to resolve internal cluster services.
> 3. **Testing DNS with `busybox:1.28`:** Always specify tag `1.28` when testing DNS with busybox. Unversioned `busybox:latest` has a resolver bug.

---

## 6. Knowledge Check Flashcards

1. **Q:** What is the full FQDN for a Service named `payments` in namespace `finance`?  
   **A:** `payments.finance.svc.cluster.local`.
2. **Q:** What virtual networking device connects a container's network namespace to the host bridge?  
   **A:** A virtual ethernet pair (`veth` pair).
3. **Q:** Can Pods on different worker nodes communicate with each other directly without NAT?  
   **A:** Yes, the Kubernetes networking model mandates direct pod-to-pod routing without NAT.
