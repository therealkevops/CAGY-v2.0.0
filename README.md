# Containerized Antigravity (AGY Docker & WebUI Bridge)

This project provides a fully containerized, isolated environment for running Google Antigravity (AGY) alongside the browser WebUI. 

By encapsulating both the Web UI server and the native Linux `agy` CLI binary inside an isolated Docker/Linux container, all process spawning, bash tool executions, script runs, and file modifications remain completely shielded from host-level endpoint detection (e.g. SentinelOne and SecureWorks on macOS).

---

## Key Features

- **Full EDR Shielding**: The Web UI, the Linux Antigravity CLI binary, and all tool subprocesses execute strictly within the Linux container namespace.
- **Native Linux `agy` Engine**: Official Google Antigravity Linux binary runs directly inside the container.
- **Integrated Browser WebUI**: Connects to `http://localhost:8989` with real-time markdown streaming, tool inspection, and live terminal drawer.
- **Confinement Awareness**: Built-in rules ([`.gemini/rules/container_confinement.md`](.gemini/rules/container_confinement.md) & [`GEMINI.md`](GEMINI.md)) ensure the agent is aware of its container boundaries.
- **Dual Update Pathways**: Instant in-place CLI updates (`./update.sh`) or full clean rebuilds (`./update.sh full`).
- **Persistent State**: Chat sessions, journals, and credentials persist across restarts in `./container_data/`.
- **SSL Certificate Trust**: Automatically exports host macOS root certificates to `./container_data/system_certs.pem` to prevent corporate proxy or TLS inspection errors.

---

## Quick Start (Containerized Mode)

### Step 1: Initial Bootstrap & Certificate Setup
Run the setup script to export corporate SSL certificates, initialize volumes, and build the Docker container:
```bash
./setup.sh
```

### Step 2: One-Time Google Authentication in Container
Because macOS uses Keychain while Linux uses file-based token storage, run the interactive container CLI once to authenticate:
```bash
./agy-container.sh cli agy
```
Follow the on-screen prompt in your browser to complete Google sign-in. Once logged in, tokens are stored permanently in `./container_data/gemini/`.

### Step 3: Start the Container Daemon & WebUI
```bash
./agy-container.sh up
```
Open **`http://localhost:8989`** in your browser.

### Step 4: Stop Services Gracefully
```bash
./agy-container.sh down
```

---

## Updating Antigravity & the Container

We provide two update pathways:

### Option A: Quick In-Place Update (Fast)
Updates the Antigravity CLI binary directly inside the running container and reloads the WebUI in seconds without rebuilding:
```bash
./update.sh
# or
./agy-container.sh update
```

### Option B: Full Container Rebuild (Fresh Base Packages)
Refreshes host SSL certificates, updates base Linux packages, downloads the latest Antigravity build, and recreates the container:
```bash
./update.sh full
# or
./agy-container.sh rebuild
```

---

## Command Reference

| Command | Description |
| :--- | :--- |
| `./agy-container.sh up` | Starts the unified background container daemon with WebUI on port 8989 |
| `./agy-container.sh down` | Gracefully stops all container services and host processes |
| `./agy-container.sh cli` | Spawns an interactive bash shell inside the Docker container |
| `./agy-container.sh cli agy` | Runs an interactive Antigravity CLI session inside the container (for login) |
| `./update.sh` | Fast in-place update of AGY CLI inside the running container |
| `./update.sh full` | Rebuilds the entire Docker container with latest packages and certs |
| `./agy-container.sh logs` | Tails live container logs (supervisor, webui, agy) |
| `./agy-container.sh status` | Reports the live status of the container and WebUI |
| `./run-webui.sh` | (Optional) Starts the WebUI natively on macOS host |
| `./stop-webui.sh` | (Optional) Gracefully stops the native macOS host WebUI |

---

## Directory Structure

```text
├── container_data/        # Persistent data shared across container runs
│   ├── webui/             # Sessions, workspaces, index, and UI settings
│   │   └── sessions/      # Persistent JSON transcripts and turn journals
│   ├── gemini/            # Persistent Antigravity CLI tokens & configs
│   └── system_certs.pem   # Host SSL root certificate bundle
├── skills/                # Workspace skills (e.g. agy-webui-bridge)
├── webui/                 # WebUI frontend & API bridge layer
│   ├── static/            # Web assets (HTML, CSS, JS)
│   ├── api/               # API routes, streaming handlers, and settings
│   ├── run_agent.py       # AGY CLI streaming bridge adapter
│   └── server.py          # WebUI server entrypoint
├── .gemini/rules/         # Agent workspace confinement rules
├── GEMINI.md              # Global workspace architecture & path rules
├── Dockerfile             # Unified container image definition with Linux AGY
├── docker-compose.yml     # Compose service definitions and volume mappings
├── setup.sh               # Initial setup and certificate bundle generator
├── update.sh              # Update script (in-place CLI or full rebuild)
├── agy-container.sh       # Main container management CLI
├── run-webui.sh           # Host-native WebUI start script
└── stop-webui.sh          # Host-native WebUI stop script
```

---

## Troubleshooting

* **"Authentication required" in WebUI**:
  * Run `./agy-container.sh cli agy` once in your terminal to complete Google sign-in for the containerized Linux binary.
* **"Connection lost" banner in browser**:
  * Perform a hard refresh in the browser (`Cmd + Shift + R`).
  * Check container health with `./agy-container.sh status`.
* **Corporate Proxy / SSL Errors**:
  * Run `./setup.sh` to re-export current macOS Keychain certificates into `./container_data/system_certs.pem`.


