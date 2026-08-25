# Containerized Antigravity (AGY Docker)

This project provides a fully containerized environment for running Antigravity (AGY) independently of host IDE binaries or desktop applications. 

## Features

- **Runtime Isolation**: Completely isolates execution environment, tools, and dependencies inside Docker.
- **Rancher Desktop / Docker Desktop Integration**: Works seamlessly on macOS.
- **SSL Certificate Trust**: Automatically exports host macOS root certificates to `./container_data/system_certs.pem` to prevent corporate proxy or TLS inspection issues inside the container.
- **Persistent State**: Persists history, configuration, and state inside `./container_data` independently of the host system.
- **Docker-in-Docker Socket Mounting**: Optionally mounts `/var/run/docker.sock` so AGY can launch sub-containers or run dockerized tests.

## Quick Start

1. **Run Setup**:
   ```bash
   ./setup.sh
   ```

2. **Launch AGY Agent Session**:
   ```bash
   ./agy.sh
   # or
   ./agy-container.sh agy
   ```

3. **Launch Hermes WebUI (Browser & Mobile Interface)**:
   ```bash
   ./agy-container.sh web
   # or run directly on host:
   ./run-webui.sh
   ```
   Open `http://localhost:8989` in your web browser.

4. **Launch Interactive Container Linux Shell**:
   ```bash
   ./agy-container.sh cli
   ```

5. **Run Background Daemon & WebUI**:
   ```bash
   ./agy-container.sh up
   ```

6. **Stop Container**:
   ```bash
   ./agy-container.sh down
   ```

## Directory Structure

- `Dockerfile`: Image setup (Node.js 20, Python 3, Git, Ripgrep, Docker CLI, Supervisor).
- `docker-compose.yml`: Container service definitions and volume mappings.
- `setup.sh`: Initial bootstrap and certificate exporter.
- `agy-container.sh`: Management CLI script.
- `./container_data`: Isolated persistent container storage (`.gemini`, `.config`, certificates).
- `./workspace`: Host workspace mount point.
