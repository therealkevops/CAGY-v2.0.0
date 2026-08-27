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

# Detect compose command
if command -v docker-compose &>/dev/null; then
    COMPOSE_CMD="docker-compose"
else
    COMPOSE_CMD="docker compose"
fi

# Ensure docker CLI & daemon are reachable
if ! command -v docker &>/dev/null; then
    echo "Error: 'docker' command not found. Please install Docker."
    exit 1
fi

# Ensure container data & SSL cert bundle exist
mkdir -p ./container_data ./container_data/gemini ./container_data/config ./container_data/webui ./workspace
if [ ! -f ./container_data/system_certs.pem ]; then
    if [ -f "$HOME/.gemini/system_certs.pem" ]; then
        cp "$HOME/.gemini/system_certs.pem" ./container_data/system_certs.pem
    elif command -v security &>/dev/null; then
        security find-certificate -a -p /System/Library/Keychains/SystemRootCertificates.keychain /Library/Keychains/System.keychain > ./container_data/system_certs.pem 2>/dev/null || true
    elif [ -f "/etc/ssl/certs/ca-certificates.crt" ]; then
        cp "/etc/ssl/certs/ca-certificates.crt" ./container_data/system_certs.pem
    elif [ -f "/etc/pki/tls/certs/ca-bundle.crt" ]; then
        cp "/etc/pki/tls/certs/ca-bundle.crt" ./container_data/system_certs.pem
    else
        touch ./container_data/system_certs.pem
    fi
fi
if [ -d "$HOME/.gemini/antigravity-cli" ]; then
    mkdir -p ./container_data/gemini/antigravity-cli ./container_data/gemini/config
    rsync -a --update --exclude="brain" --exclude="log" --exclude="*.log" "$HOME/.gemini/antigravity-cli/" ./container_data/gemini/antigravity-cli/ 2>/dev/null || true
    [ -d "$HOME/.gemini/config" ] && rsync -a --update "$HOME/.gemini/config/" ./container_data/gemini/config/ 2>/dev/null || true
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
        echo "Starting Unified AGY Container daemon with WebUI..."
        ./run-webui.sh stop >/dev/null 2>&1 || true
        $COMPOSE_CMD up -d --remove-orphans
        echo "✓ AGY container daemon running in background."
        echo "🌐 Antigravity WebUI is available at: http://localhost:8989"
        ;;
    web|ui)
        echo "Starting Antigravity WebUI..."
        ./run-webui.sh start
        ;;
    down|stop)
        echo "Stopping all AGY services..."
        ./run-webui.sh stop >/dev/null 2>&1 || true
        $COMPOSE_CMD down --remove-orphans
        echo "✓ All AGY services stopped cleanly."
        ;;
    update|update-cli)
        if $COMPOSE_CMD ps --status running --format '{{.Names}}' 2>/dev/null | grep -q "agy-unified"; then
            echo "Checking and updating Antigravity CLI inside running container..."
            $COMPOSE_CMD exec agy-unified agy update
            echo "Restarting WebUI inside container..."
            $COMPOSE_CMD exec agy-unified supervisorctl restart hermes-webui
            echo "✓ AGY CLI and WebUI updated successfully!"
        else
            echo "Container is not running. Performing full update & rebuild..."
            ./setup.sh
            $COMPOSE_CMD up -d --force-recreate --remove-orphans
            echo "✓ AGY container rebuilt and started successfully!"
        fi
        ;;
    rebuild|upgrade)
        echo "Performing full AGY container rebuild..."
        ./setup.sh
        $COMPOSE_CMD build --pull --no-cache
        echo "Recreating container services..."
        $COMPOSE_CMD up -d --force-recreate --remove-orphans
        echo "✓ Full AGY container rebuild complete!"
        ;;
    logs)
        $COMPOSE_CMD logs -f
        ;;
    ps|status)
        echo "=== Container Status ==="
        $COMPOSE_CMD ps
        echo ""
        echo "=== Host WebUI Status ==="
        ./run-webui.sh status
        ;;
    *)
        echo "Usage: $0 {setup|agy|cli|web|up|down|update|rebuild|logs|status}"
        echo ""
        echo "Commands:"
        echo "  setup       Run initial container setup and certificate export"
        echo "  agy         Launch Antigravity (AGY) Agent interactive session"
        echo "  cli         Run interactive shell session inside container"
        echo "  web|ui      Start Antigravity WebUI natively (http://localhost:8989)"
        echo "  up          Start AGY background daemon & WebUI in container"
        echo "  down        Stop all AGY container & host WebUI services"
        echo "  update      In-place update of AGY CLI inside running container"
        echo "  rebuild     Full clean rebuild of container image and dependencies"
        echo "  logs        Tail container logs"
        echo "  status      Show container and WebUI status"
        exit 1
        ;;
esac
