#!/usr/bin/env zsh
set -e

# Ensure Rancher Desktop bin directory is in PATH
if [[ ":$PATH:" != *":$HOME/.rd/bin:"* ]]; then
    export PATH="$HOME/.rd/bin:$PATH"
fi

if command -v docker-compose &>/dev/null; then
    COMPOSE_CMD="docker-compose"
else
    COMPOSE_CMD="docker compose"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
cd "$SCRIPT_DIR"

if [ -z "$DOCKER_HOST" ] && [ -S "$HOME/.rd/docker.sock" ]; then
    export DOCKER_HOST="unix://$HOME/.rd/docker.sock"
fi

mkdir -p ./container_data/docker_config
export DOCKER_CONFIG="$SCRIPT_DIR/container_data/docker_config"

echo "=== Antigravity (AGY) Container Setup ==="

# Check docker binary
if ! command -v docker &>/dev/null; then
    echo "Error: 'docker' command not found in PATH or ~/.rd/bin."
    echo "Please ensure Docker or Rancher Desktop is running."
    exit 1
fi

echo "✓ Docker CLI detected ($(docker --version))"

# Create local persistent data and workspace directories
echo "Creating isolated local directories..."
mkdir -p ./container_data ./container_data/gemini ./container_data/config ./workspace

# Export macOS host SSL certificate bundle (Fixes SSL CERTIFICATE_VERIFY_FAILED for proxies / custom CA)
echo "Exporting host SSL certificates for container trust..."
if [ -f "$HOME/.gemini/system_certs.pem" ]; then
    cp "$HOME/.gemini/system_certs.pem" ./container_data/system_certs.pem
else
    security find-certificate -a -p /System/Library/Keychains/SystemRootCertificates.keychain /Library/Keychains/System.keychain > ./container_data/system_certs.pem 2>/dev/null || true
fi
echo "✓ SSL certificate bundle configured in ./container_data/system_certs.pem"

# Create initial .env if not present
if [ ! -f .env ]; then
    echo "Creating .env from .env.example..."
    cp .env.example .env
    echo "✓ Created .env file. Update it with API keys or proxies if needed."
fi

# Build Docker image
echo ""
echo "Building AGY container image..."
$COMPOSE_CMD build

echo ""
echo "=== Setup Complete! ==="
echo "To interactive AGY session in container:  ./agy-container.sh cli"
echo "To run container background daemon:       ./agy-container.sh up"
echo "Target workspace directory on host:       $SCRIPT_DIR"
