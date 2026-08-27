# Containerized Antigravity (CAGY Docker & WebUI Bridge)

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Platform: Linux / macOS / WSL2](https://img.shields.io/badge/Platform-macOS%20%7C%20Linux%20%7C%20WSL2-teal.svg)](https://docker.com)
[![Engine: Antigravity 2.0](https://img.shields.io/badge/Engine-Antigravity%202.0-orange.svg)](https://deepmind.google)

A complete, fully containerized execution environment and browser WebUI for **Google Antigravity (AGY)**.

By encapsulating both the WebUI server and the native Linux `agy` CLI binary inside an isolated Debian container, all process spawning, bash tool executions, script runs, and file modifications remain completely shielded from host-level endpoint detection (e.g. SentinelOne, SecureWorks, or CrowdStrike on macOS/Windows) while providing a rich, responsive pair-programming web experience.

---

## Key Features

- **Full EDR & Process Shielding**: All shell executions, python scripts, file manipulations, and agent tool calls run exclusively inside the container's isolated Linux namespace.
- **Native Antigravity Engine**: Uses the official Google Antigravity Linux binary (`agy`) with automated lifecycle management.
- **Subagent Swarms Visualizer (`/swarm`)**: Real-time DAG hierarchy viewer, execution timeline, and inspector for delegated multi-agent subtasks (`invoke_subagent`).
- **Model Context Protocol (MCP) Hub (`/mcp`)**: Native manager for `mcp.json` with one-click presets for PostgreSQL, SQLite, Puppeteer (Browser), and Git servers.
- **Skill & Rule Scaffolder Wizard (`/skills`)**: Visual builder for `.gemini/rules/*.md` and `skills/<name>/SKILL.md`.
- **Integrated Artifacts & Workspace Drawer**: Collapsible right-hand pane with live file tree, code diff preview, and markdown artifact viewer.
- **Command Palette (`Cmd + K`)**: Quick access to all views, panels, and slash commands (`/goal`, `/schedule`, `/browser`, `/plan`, `/grill-me`).
- **Corporate SSL Certificate Trust**: Automatically exports host root certificates into `./container_data/system_certs.pem` to prevent corporate proxy or TLS inspection errors.
- **Persistent State**: Sessions, transcripts, tokens, and journals persist cleanly across container rebuilds in `./container_data/`.

---

## Prerequisites

Before setting up CAGY, ensure you have:
1. **Docker Desktop** (macOS / Windows) or **Docker Engine** (Linux) running.
2. **Git** and **Bash / Zsh**.
3. A **Google Account** with access to Google Antigravity (Gemini).

---

## Quick Start Guide

### Step 1: Clone the Repository
```bash
git clone https://github.com/<your-username>/cagy-docker.git
cd cagy-docker
```

### Step 2: Run Initial Setup & Certificate Generator
The setup script exports host root SSL certificates (vital for corporate TLS inspection), initializes the persistent data directories, and builds the container image:
```bash
./setup.sh
```

### Step 3: One-Time Google Authentication in the Container
Because macOS uses Keychain while Linux uses secure file-based token storage, run the interactive container CLI once to authenticate:
```bash
./agy-container.sh cli agy
```
1. Click the URL or press `Enter` to open the Google login page in your browser.
2. Complete sign-in and approve the Antigravity CLI authorization.
3. Once authenticated, press `Ctrl + C` or type `/exit` to return to your host terminal.
*(Your credentials are permanently stored in `./container_data/gemini/` and will persist across restarts and rebuilds.)*

### Step 4: Start the Background Container & WebUI
```bash
./agy-container.sh up
```
Open **[http://localhost:8989](http://localhost:8989)** in your browser.

---

## WebUI Overview & Navigation

| View / Feature | Shortcut / Route | Description |
| :--- | :--- | :--- |
| **Chat & Pairing Canvas** | `/chat` (Default) | Real-time streaming conversation, syntax highlighting, and inline tool inspection. |
| **Subagent Swarms** | `/swarm` or Left Rail | Visual DAG tree and step-by-step transcript timeline for autonomous subagents. |
| **MCP Server Hub** | `/mcp` or Left Rail | Catalog of active tools and servers configured in `mcp.json` with quick presets. |
| **Skills & Rules Scaffolder** | `/skills` or Left Rail | Domain skills manager and visual rule generator. |
| **Workspace & Artifacts** | Right Drawer Button | Inspect files in `/workspace` and render generated markdown artifacts. |
| **Command Palette** | `Cmd + K` / `Ctrl + K` | Universal search for commands, panels, settings, and workflows. |

---

## Managing & Updating Services

### Daily Operations

```bash
# Check status of container daemon & WebUI
./agy-container.sh status

# View live container logs (supervisor, webui, agy stream)
./agy-container.sh logs

# Open an interactive bash shell inside the container
./agy-container.sh cli

# Stop services gracefully
./agy-container.sh down
```

### Updating Antigravity CLI

We provide two update pathways:

* **Quick In-Place Update (Recommended)**:
  Downloads and updates the Antigravity binary directly inside the running container in seconds:
  ```bash
  ./update.sh
  ```
* **Full Clean Rebuild**:
  Re-exports host certificates, updates Linux base packages, and performs a fresh build of the Docker image:
  ```bash
  ./update.sh full
  ```

---

## Architecture & Directory Layout

```text
├── container_data/        # Persistent data (gitignored, survives container restarts)
│   ├── webui/             # WebUI database, sessions, and settings
│   │   └── sessions/      # Transcripts, turn journals, and history
│   ├── gemini/            # Antigravity CLI auth tokens, settings, and brain logs
│   │   └── antigravity-cli/brain/  # Subagent transcripts and artifacts
│   └── system_certs.pem   # Exported host SSL root certificates
├── skills/                # Antigravity domain skills (e.g. agy-webui-bridge)
├── webui/                 # WebUI frontend & backend bridge
│   ├── static/            # Static assets (HTML, CSS, JS, Katex)
│   ├── api/               # API endpoints (subagents, mcp_hub, skills, artifacts)
│   ├── run_agent.py       # Antigravity CLI event-streaming bridge adapter
│   └── server.py          # WebUI HTTP daemon entrypoint
├── .gemini/rules/         # Agent workspace confinement and design system rules
├── GEMINI.md              # Container execution namespace configuration
├── Dockerfile             # Unified Debian container with Linux agy and supervisor
├── docker-compose.yml     # Compose service specification and volume mounts
├── setup.sh               # Initial setup and certificate exporter
├── update.sh              # Update script (fast in-place or full rebuild)
└── agy-container.sh       # Unified container CLI manager
```

---

## Troubleshooting FAQ

### 1. "Authentication required" in WebUI
If the WebUI reports that the agent is not authenticated:
* Run `./agy-container.sh cli agy` in your host terminal.
* Complete the browser authentication link.
* Refresh the WebUI at `http://localhost:8989`.

### 2. Corporate Proxy or SSL Certificate Errors
If `agy` reports `x509: certificate signed by unknown authority`:
* Run `./setup.sh` on your host machine to re-export your local system keychain certificates to `container_data/system_certs.pem`.
* Restart the container: `./agy-container.sh down && ./agy-container.sh up`.

### 3. Port Conflict on 8989
If port `8989` is already bound by another service on your machine:
* Edit `docker-compose.yml` to map a different host port (e.g. `"9090:8989"`).
* Access the WebUI at `http://localhost:9090`.

### 4. Resetting Container State
To reset the container while preserving your sessions and login tokens:
```bash
./agy-container.sh down
./update.sh full
./agy-container.sh up
```

---

## License

This project is licensed under the [MIT License](LICENSE).


