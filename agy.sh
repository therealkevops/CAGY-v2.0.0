#!/usr/bin/env zsh
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
cd "$SCRIPT_DIR"

# Ensure container data & SSL cert bundle exist
./agy-container.sh setup >/dev/null 2>&1 || true

HOST_AGY_BIN="/Users/kev.gorman/.local/bin/agy"

if [ ! -x "$HOST_AGY_BIN" ]; then
    echo "Error: Host AGY binary not found at $HOST_AGY_BIN"
    exit 1
fi

# Export host SSL root certificate bundle (Fixes Go x509: certificate signed by unknown authority)
CERT_PATH="$SCRIPT_DIR/container_data/system_certs.pem"
if [ -f "$CERT_PATH" ]; then
    export SSL_CERT_FILE="$CERT_PATH"
    export SSL_CERT_DIR="$(dirname "$CERT_PATH")"
    export REQUESTS_CA_BUNDLE="$CERT_PATH"
    export CURL_CA_BUNDLE="$CERT_PATH"
    export NODE_EXTRA_CA_CERTS="$CERT_PATH"
fi

echo "========================================================="
echo "   Launching Antigravity (AGY) with Container Isolation  "
echo "========================================================="
echo "Host AGY Engine : $HOST_AGY_BIN"
echo "Target Workspace: $SCRIPT_DIR"
echo "SSL Cert Bundle : $CERT_PATH"
echo "Container State : Isolated in ./container_data"
echo "========================================================="
echo ""

exec "$HOST_AGY_BIN" --add-dir "$SCRIPT_DIR" "$@"
