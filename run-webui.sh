#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=================================================="
echo " Starting Authentic Hermes WebUI (AGY Bridge)    "
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

export HERMES_WEBUI_AGENT_DIR="$SCRIPT_DIR/webui"
export HERMES_WEBUI_HOST="127.0.0.1"
export HERMES_WEBUI_PORT="8989"
export HERMES_WEBUI_DEFAULT_MODEL="Antigravity 2.0 (agy CLI)"
export HERMES_WORKSPACE_ROOT="$SCRIPT_DIR"
export PYTHONPATH="$SCRIPT_DIR/webui:$PYTHONPATH"

python3 webui/server.py
