#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PID_FILE="$SCRIPT_DIR/container_data/webui.pid"

# Stop competing docker container if running on port 8989
stop_docker_if_running() {
  if command -v docker >/dev/null 2>&1; then
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^agy-unified$"; then
      echo "[info] Stopping competing agy-unified docker container..."
      docker stop agy-unified >/dev/null 2>&1 || true
    fi
  fi
}

# Graceful stop function
stop_server() {
  echo "=================================================="
  echo " Shutting Down Antigravity WebUI (AGY Bridge)     "
  echo "=================================================="

  stop_docker_if_running

  local stopped=0
  if [ -f "$PID_FILE" ]; then
    local pid=$(cat "$PID_FILE" 2>/dev/null || true)
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      echo "[info] Sending graceful shutdown signal to PID $pid..."
      kill -TERM "$pid" 2>/dev/null || true
      
      # Wait up to 5 seconds for graceful exit
      for i in {1..10}; do
        if ! kill -0 "$pid" 2>/dev/null; then
          echo "✓ Antigravity WebUI server stopped cleanly."
          stopped=1
          break
        fi
        sleep 0.5
      done
      
      if [ "$stopped" -eq 0 ] && kill -0 "$pid" 2>/dev/null; then
        echo "[warn] Force-killing unresponsive process $pid..."
        kill -KILL "$pid" 2>/dev/null || true
      fi
    fi
    rm -f "$PID_FILE"
  fi

  # Also check if any rogue process is still listening on port 8989
  local port_pids=$(lsof -ti:8989 -sTCP:LISTEN 2>/dev/null || true)
  if [ -n "$port_pids" ]; then
    for p in $port_pids; do
      echo "[info] Clearing remaining process on port 8989 (PID $p)..."
      kill -TERM "$p" 2>/dev/null || true
      sleep 0.5
      kill -9 "$p" 2>/dev/null || true
    done
  fi

  echo "✓ WebUI is stopped."
}

# Status function
status_server() {
  local running=0
  if [ -f "$PID_FILE" ]; then
    local pid=$(cat "$PID_FILE" 2>/dev/null || true)
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      running=1
      echo "✓ Antigravity WebUI is running (PID: $pid)"
    fi
  fi

  if [ "$running" -eq 0 ]; then
    local port_pids=$(lsof -ti:8989 -sTCP:LISTEN 2>/dev/null || true)
    if [ -n "$port_pids" ]; then
      echo "✓ Antigravity WebUI is listening on port 8989 (PIDs: $port_pids)"
      running=1
    else
      echo "○ Antigravity WebUI is stopped."
    fi
  fi
}

ACTION="${1:-start}"

case "$ACTION" in
  stop|down)
    stop_server
    exit 0
    ;;
  status)
    status_server
    exit 0
    ;;
  restart)
    stop_server
    sleep 1
    exec "$0" start
    ;;
  start|run|"")
    ;;
  *)
    echo "Usage: $0 {start|stop|restart|status}"
    exit 1
    ;;
esac

# Pre-start: Clean up any old process on port 8989
stop_docker_if_running
OLD_PIDS=$(lsof -ti:8989 -sTCP:LISTEN 2>/dev/null || true)
if [ -n "$OLD_PIDS" ]; then
  for p in $OLD_PIDS; do
    echo "[info] Clearing previous process on port 8989 (PID $p)..."
    kill -TERM "$p" 2>/dev/null || true
  done
  sleep 0.5
fi

echo "=================================================="
echo " Starting Antigravity WebUI (AGY Bridge)          "
echo "=================================================="

# Load .env if present
if [ -f .env ]; then
  set -a
  [ -f .env ] && . ./.env
  set +a
fi

# Export certificates if present
if [ -f ./container_data/system_certs.pem ]; then
  export SSL_CERT_FILE="$SCRIPT_DIR/container_data/system_certs.pem"
  export REQUESTS_CA_BUNDLE="$SCRIPT_DIR/container_data/system_certs.pem"
fi

# Ensure state directory exists and point both host and container to container_data/webui
mkdir -p "$SCRIPT_DIR/container_data/webui/sessions"
export AGY_WEBUI_STATE_DIR="$SCRIPT_DIR/container_data/webui"
export HERMES_WEBUI_STATE_DIR="$AGY_WEBUI_STATE_DIR"  # compat alias

export AGY_WEBUI_AGENT_DIR="$SCRIPT_DIR/webui"
export HERMES_WEBUI_AGENT_DIR="$AGY_WEBUI_AGENT_DIR"  # compat alias

export AGY_WEBUI_HOST="127.0.0.1"
export HERMES_WEBUI_HOST="$AGY_WEBUI_HOST"  # compat alias

export AGY_WEBUI_PORT="8989"
export HERMES_WEBUI_PORT="$AGY_WEBUI_PORT"  # compat alias

export AGY_WEBUI_DEFAULT_MODEL="Antigravity 2.0 (agy CLI)"
export HERMES_WEBUI_DEFAULT_MODEL="$AGY_WEBUI_DEFAULT_MODEL"  # compat alias

export AGY_WEBUI_DEFAULT_WORKSPACE="$SCRIPT_DIR"
export HERMES_WEBUI_DEFAULT_WORKSPACE="$AGY_WEBUI_DEFAULT_WORKSPACE"  # compat alias

export AGY_WORKSPACE_ROOT="$SCRIPT_DIR"
export HERMES_WORKSPACE_ROOT="$AGY_WORKSPACE_ROOT"  # compat alias

export AGY_WEBUI_SKIP_ONBOARDING="1"
export HERMES_WEBUI_SKIP_ONBOARDING="$AGY_WEBUI_SKIP_ONBOARDING"  # compat alias
export PYTHONPATH="$SCRIPT_DIR/webui:$PYTHONPATH"

# Setup cleanup trap for foreground runs
cleanup() {
  rm -f "$PID_FILE"
}
trap cleanup EXIT INT TERM

# Run python server and record PID
python3 webui/server.py &
SERVER_PID=$!
echo "$SERVER_PID" > "$PID_FILE"

# Wait for server process
wait "$SERVER_PID"
