#!/usr/bin/env zsh
set -e

# Ensure Rancher Desktop bin directory is in PATH
if [[ ":$PATH:" != *":$HOME/.rd/bin:"* ]]; then
    export PATH="$HOME/.rd/bin:$PATH"
fi

if [ -z "$DOCKER_HOST" ] && [ -S "$HOME/.rd/docker.sock" ]; then
    export DOCKER_HOST="unix://$HOME/.rd/docker.sock"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
cd "$SCRIPT_DIR"

mkdir -p ./container_data/docker_config
export DOCKER_CONFIG="$SCRIPT_DIR/container_data/docker_config"

# Detect compose command
if command -v docker-compose &>/dev/null; then
    COMPOSE_CMD="docker-compose"
else
    COMPOSE_CMD="docker compose"
fi

# Ensure container data & SSL cert bundle exist
mkdir -p ./container_data ./container_data/gemini ./container_data/config ./workspace
if [ ! -f ./container_data/system_certs.pem ]; then
    security find-certificate -a -p /System/Library/Keychains/SystemRootCertificates.keychain /Library/Keychains/System.keychain > ./container_data/system_certs.pem 2>/dev/null || true
fi

COMMAND="${1:-cli}"

case "$COMMAND" in
    setup)
        echo "Running AGY container setup..."
        ./setup.sh
        ;;
    agy)
        shift 1 2>/dev/null || true
        ./agy.sh "$@"
        ;;
    cli|run)
        shift 1 2>/dev/null || true
        if [ $# -gt 0 ]; then
            $COMPOSE_CMD run --rm agy-unified "$@"
        else
            echo "Starting interactive AGY container shell..."
            echo "=== Antigravity (AGY) Container Interactive Environment ==="
            echo "Workspace directory mounted at: /workspace"
            echo ""
            $COMPOSE_CMD run --rm agy-unified bash
        fi
        ;;
    up)
        echo "Starting Unified AGY Container daemon with Hermes WebUI..."
        $COMPOSE_CMD up -d --remove-orphans
        echo "✓ AGY container daemon running in background."
        echo "🌐 Hermes WebUI is available at: http://localhost:8989"
        ;;
    web|ui)
        echo "Starting Hermes WebUI in background container..."
        $COMPOSE_CMD up -d --remove-orphans
        echo "🌐 Hermes WebUI is live: http://localhost:8989"
        ;;
    down|stop)
        echo "Stopping AGY container services..."
        $COMPOSE_CMD down --remove-orphans
        ;;
    update|upgrade)
        echo "Rebuilding AGY Container with fresh base dependencies..."
        $COMPOSE_CMD build --pull --no-cache
        echo "Recreating container..."
        $COMPOSE_CMD up -d --force-recreate --remove-orphans
        echo "✓ AGY container updated successfully!"
        ;;
    logs)
        $COMPOSE_CMD logs -f
        ;;
    ps|status)
        $COMPOSE_CMD ps
        ;;
    *)
        echo "Usage: $0 {setup|agy|cli|web|up|down|update|logs|status}"
        echo ""
        echo "Commands:"
        echo "  setup    Run initial container setup script"
        echo "  agy      Launch Antigravity (AGY) Agent interactive session"
        echo "  cli      Run interactive shell session inside container"
        echo "  web|ui   Start and access Hermes WebUI (http://localhost:8989)"
        echo "  up       Start AGY background daemon & WebUI container"
        echo "  down     Stop container daemon"
        echo "  update   Rebuild image with latest base dependencies"
        echo "  logs     Tail container logs"
        echo "  status   Show container status"
        exit 1
        ;;
esac
