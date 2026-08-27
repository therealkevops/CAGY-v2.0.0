#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Ensure common desktop / CLI bin paths are in PATH
if [ -d "$HOME/.rd/bin" ] && [[ ":$PATH:" != *":$HOME/.rd/bin:"* ]]; then
    export PATH="$HOME/.rd/bin:$PATH"
fi
if [ -d "$HOME/.local/bin" ] && [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
    export PATH="$HOME/.local/bin:$PATH"
fi

if [ -z "$DOCKER_HOST" ] && [ -S "$HOME/.rd/docker.sock" ]; then
    export DOCKER_HOST="unix://$HOME/.rd/docker.sock"
fi

mkdir -p ./container_data/docker_config
export DOCKER_CONFIG="$SCRIPT_DIR/container_data/docker_config"

if command -v docker-compose &>/dev/null; then
    COMPOSE_CMD="docker-compose"
else
    COMPOSE_CMD="docker compose"
fi

echo "=== Containerized Antigravity (CAGY) Setup ==="

# Check docker CLI and daemon availability
if ! command -v docker &>/dev/null; then
    echo "Error: 'docker' command not found."
    echo "Please install and launch Docker Desktop (macOS/Windows) or Docker Engine (Linux)."
    exit 1
fi

if ! docker info >/dev/null 2>&1; then
    echo "Error: Docker daemon is not running."
    echo "Please start Docker Desktop or the Docker service and try again."
    exit 1
fi

echo "✓ Docker daemon detected ($(docker --version))"

# Create local persistent data and workspace directories
echo "Creating isolated local directories..."
mkdir -p ./container_data ./container_data/gemini ./container_data/config ./container_data/webui ./workspace

# Export SSL certificate bundle (Fixes TLS verification for corporate proxies and custom CAs)
echo "Configuring SSL root certificates for container trust..."
if [ -f "$HOME/.gemini/system_certs.pem" ]; then
    cp "$HOME/.gemini/system_certs.pem" ./container_data/system_certs.pem
elif command -v security &>/dev/null; then
    # macOS Keychain extraction
    security find-certificate -a -p /System/Library/Keychains/SystemRootCertificates.keychain /Library/Keychains/System.keychain > ./container_data/system_certs.pem 2>/dev/null || true
elif [ -f "/etc/ssl/certs/ca-certificates.crt" ]; then
    # Debian / Ubuntu / Alpine
    cp "/etc/ssl/certs/ca-certificates.crt" ./container_data/system_certs.pem
elif [ -f "/etc/pki/tls/certs/ca-bundle.crt" ]; then
    # RHEL / CentOS / Fedora
    cp "/etc/pki/tls/certs/ca-bundle.crt" ./container_data/system_certs.pem
else
    touch ./container_data/system_certs.pem
fi
echo "✓ SSL certificate bundle configured in ./container_data/system_certs.pem"

# Sync host Antigravity credentials to container volume if present
if [ -d "$HOME/.gemini/antigravity-cli" ]; then
    echo "Syncing existing Antigravity CLI credentials to container volume..."
    mkdir -p ./container_data/gemini/antigravity-cli ./container_data/gemini/config
    rsync -a --exclude="brain" --exclude="log" --exclude="*.log" "$HOME/.gemini/antigravity-cli/" ./container_data/gemini/antigravity-cli/ 2>/dev/null || true
    [ -d "$HOME/.gemini/config" ] && rsync -a "$HOME/.gemini/config/" ./container_data/gemini/config/ 2>/dev/null || true
    echo "✓ Existing Antigravity credentials synced"
fi

# Create initial .env from template if missing
if [ ! -f .env ] && [ -f .env.example ]; then
    echo "Creating .env configuration from .env.example..."
    cp .env.example .env
    echo "✓ Created .env file."
fi

# Build Docker image
echo ""
echo "Building AGY container image..."
$COMPOSE_CMD build

echo ""
echo "=== Setup Complete! ==="
echo "1. Authenticate once in container:   ./agy-container.sh cli agy"
echo "2. Start WebUI background daemon:   ./agy-container.sh up"
echo "3. Open browser:                    http://localhost:8989"
