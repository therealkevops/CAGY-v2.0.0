#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

MODE="${1:-quick}"

show_help() {
    echo -e "${BLUE}Usage: ./update.sh [MODE]${NC}"
    echo ""
    echo "Update Antigravity (CAGY) CLI, container image, or source repository."
    echo ""
    echo "Modes:"
    echo "  quick, cli, fast   (Default) In-place AGY CLI update inside running container"
    echo "  full, rebuild      Full clean Docker rebuild (--no-cache) with updated base packages"
    echo "  repo, git, pull    Pull latest CAGY commits from Git repository"
    echo "  all                Pull Git updates + full clean container rebuild + host sync"
    echo "  help, --help, -h   Display this help message"
    echo ""
    echo "Examples:"
    echo "  ./update.sh           # In-place CLI update in seconds"
    echo "  ./update.sh repo      # Pull latest code and restart WebUI"
    echo "  ./update.sh full      # Rebuild image from scratch"
    echo "  ./update.sh all       # Complete upgrade of everything"
}

update_host_cli() {
    if command -v agy >/dev/null 2>&1; then
        echo -e "${YELLOW}[info] Updating host Antigravity CLI...${NC}"
        agy update || true
    fi
}

restart_container_webui() {
    if docker compose ps --status running --format '{{.Names}}' 2>/dev/null | grep -q "agy-unified"; then
        echo -e "${YELLOW}[info] Reloading WebUI inside running container...${NC}"
        docker compose exec agy-unified supervisorctl restart agy-webui >/dev/null 2>&1 || true
        echo -e "${GREEN}✓ Container WebUI reloaded.${NC}"
    fi
}

case "$MODE" in
  quick|cli|fast)
    echo -e "${BLUE}=== Updating Antigravity CLI in container ===${NC}"
    ./agy-container.sh update
    update_host_cli
    echo -e "${GREEN}✓ In-place update complete!${NC}"
    ;;
  full|rebuild)
    echo -e "${BLUE}=== Performing Full Container Rebuild & Dependency Update ===${NC}"
    ./agy-container.sh rebuild
    update_host_cli
    echo -e "${GREEN}✓ Full container rebuild and update complete!${NC}"
    ;;
  repo|git|pull)
    echo -e "${BLUE}=== Pulling Latest CAGY Updates from Git ===${NC}"
    CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo main)"
    git pull origin "$CURRENT_BRANCH"
    restart_container_webui
    echo -e "${GREEN}✓ Repository up to date!${NC}"
    ;;
  all)
    echo -e "${BLUE}=== Performing Complete CAGY Upgrade (Git + Image + CLI) ===${NC}"
    CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo main)"
    echo -e "${YELLOW}[info] Pulling latest git commits on branch '$CURRENT_BRANCH'...${NC}"
    git pull origin "$CURRENT_BRANCH" || echo -e "${YELLOW}[warn] Git pull had non-zero exit; continuing rebuild...${NC}"
    ./agy-container.sh rebuild
    update_host_cli
    echo -e "${GREEN}✓ Complete CAGY upgrade finished successfully!${NC}"
    ;;
  help|--help|-h)
    show_help
    exit 0
    ;;
  *)
    echo -e "${RED}Unknown update mode: '$MODE'${NC}"
    echo ""
    show_help
    exit 1
    ;;
esac
