#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

MODE="${1:-quick}"

case "$MODE" in
  quick|cli|fast)
    echo "=== Updating Antigravity CLI in container ==="
    ./agy-container.sh update
    ;;
  full|rebuild|all)
    echo "=== Performing Full Container Rebuild & Dependency Update ==="
    ./agy-container.sh rebuild
    ;;
  *)
    echo "Usage: $0 [quick|full]"
    echo ""
    echo "  quick   In-place CLI update inside running container (fast)"
    echo "  full    Rebuild entire Docker image with fresh base packages"
    exit 1
    ;;
esac
