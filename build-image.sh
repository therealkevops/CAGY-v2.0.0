#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

IMAGE_NAME="cagy"
TAG="latest"
RELEASE_TAG="v2.0-cagy"
PLATFORMS="linux/amd64,linux/arm64"
MODE="local"
PUSH_TARGET=""
DO_TEST=false

show_help() {
    echo "Usage: ./build-image.sh [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  (no args)           Build optimized image for local architecture and tag as cagy:latest"
    echo "  --multi-arch        Build multi-platform images (linux/amd64, linux/arm64)"
    echo "  --push <TARGET>     Build multi-platform images and push to registry (e.g. ghcr.io/org/cagy)"
    echo "  --tag <TAG>         Custom primary tag (default: latest)"
    echo "  --test              Run test suite inside container after build to verify image health"
    echo "  --help, -h          Show this help message"
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --multi-arch)
            MODE="multi-arch"
            shift
            ;;
        --push)
            MODE="push"
            PUSH_TARGET="$2"
            shift 2
            ;;
        --tag)
            TAG="$2"
            shift 2
            ;;
        --test)
            DO_TEST=true
            shift
            ;;
        --help|-h)
            show_help
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            show_help
            exit 1
            ;;
    esac
done

echo -e "${BLUE}======================================================${NC}"
echo -e "${BLUE} CAGY Container Image Optimization & Multi-Arch Build ${NC}"
echo -e "${BLUE}======================================================${NC}"

# Ensure Docker is accessible
if ! command -v docker >/dev/null 2>&1; then
    echo -e "${RED}Error: 'docker' CLI not found.${NC}"
    exit 1
fi

if [ "$MODE" = "push" ]; then
    if [ -z "$PUSH_TARGET" ]; then
        echo -e "${RED}Error: --push requires a target image repository (e.g. ghcr.io/owner/cagy).${NC}"
        exit 1
    fi
    echo -e "${YELLOW}[info] Building multi-arch ($PLATFORMS) and pushing to $PUSH_TARGET...${NC}"
    docker buildx build \
        --platform "$PLATFORMS" \
        -t "$PUSH_TARGET:$TAG" \
        -t "$PUSH_TARGET:$RELEASE_TAG" \
        --push \
        .
    echo -e "${GREEN}✓ Successfully published $PUSH_TARGET:$TAG and $PUSH_TARGET:$RELEASE_TAG!${NC}"
    exit 0
fi

if [ "$MODE" = "multi-arch" ]; then
    echo -e "${YELLOW}[info] Validating multi-arch build for platforms: $PLATFORMS...${NC}"
    docker buildx build \
        --platform "$PLATFORMS" \
        -t "$IMAGE_NAME:$TAG" \
        .
    echo -e "${GREEN}✓ Multi-arch build ($PLATFORMS) validated successfully!${NC}"
    exit 0
fi

# Local architecture build and load into local docker engine
echo -e "${YELLOW}[info] Building optimized local image ($IMAGE_NAME:$TAG, $IMAGE_NAME:$RELEASE_TAG)...${NC}"
docker buildx build \
    --load \
    -t "$IMAGE_NAME:$TAG" \
    -t "$IMAGE_NAME:$RELEASE_TAG" \
    .

echo -e "${GREEN}✓ Local image build complete!${NC}"
echo ""
echo -e "${BLUE}Image details:${NC}"
docker images | grep -E "^REPOSITORY|$IMAGE_NAME" || true
echo ""

if [ "$DO_TEST" = true ]; then
    echo -e "${YELLOW}[info] Running validation tests against new image...${NC}"
    docker run --rm "$IMAGE_NAME:$TAG" uv --version
    docker run --rm "$IMAGE_NAME:$TAG" uvx --version
    docker run --rm "$IMAGE_NAME:$TAG" agy --version
    echo -e "${GREEN}✓ Image toolchain checks passed!${NC}"
fi
