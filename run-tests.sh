#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}======================================================${NC}"
echo -e "${BLUE} Antigravity (CAGY) Automated Test Suite Runner       ${NC}"
echo -e "${BLUE}======================================================${NC}"

if [ "$1" = "--help" ] || [ "$1" = "-h" ]; then
    echo "Usage: ./run-tests.sh [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  (no args)       Run hermetic test suite on local host"
    echo "  --container     Run test suite inside running agy-unified Docker container"
    echo "  --compatibility Run CLI compatibility probe against live agy engine"
    echo "  --help          Show this help message"
    exit 0
fi

if [ "$1" = "--compatibility" ]; then
    echo -e "${YELLOW}[info] Running live agy compatibility probe inside container...${NC}"
    docker exec -t agy-unified python3 /workspace/skills/agy-webui-bridge/scripts/check_compatibility.py
    exit $?
fi

if [ "$1" = "--container" ]; then
    echo -e "${YELLOW}[info] Executing tests inside agy-unified container...${NC}"
    docker exec -t agy-unified python3 -m unittest discover -s /workspace/tests -p "test_*.py" -v
    EXIT_CODE=$?
    if [ $EXIT_CODE -eq 0 ]; then
        echo -e "${GREEN}✓ All container tests passed successfully!${NC}"
    else
        echo -e "${RED}✗ Container tests failed with code $EXIT_CODE.${NC}"
    fi
    exit $EXIT_CODE
fi

echo -e "${YELLOW}[info] Executing hermetic test suite on host...${NC}"
python3 -m unittest discover -s tests -p "test_*.py" -v
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo -e "${GREEN}✓ All tests passed successfully!${NC}"
else
    echo -e "${RED}✗ Tests failed with code $EXIT_CODE.${NC}"
fi

exit $EXIT_CODE
