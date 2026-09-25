#!/bin/bash
# ============================================================================
# Revacc Pipeline - Quick Start Script
# Downloads and runs the complete Revacc pipeline (frontend + backend)
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/umeshdahiya15/Revacc/main/start-revacc.sh | bash
# ============================================================================

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

IMAGE="umeshdahiya01/revacc:latest"
CONTAINER="revacc-pipeline"
FRONTEND_URL="http://localhost:3000"
BACKEND_URL="http://localhost:8000/api/health"

print_status()  { echo -e "${GREEN}[✓]${NC} $1"; }
print_warning() { echo -e "${YELLOW}[!]${NC} $1"; }
print_error()   { echo -e "${RED}[✗]${NC} $1"; }
print_info()    { echo -e "${BLUE}[i]${NC} $1"; }

echo ""
echo "============================================"
echo "   Revacc Pipeline - Quick Start"
echo "============================================"
echo ""

# ----------------------------------------------------------------------------
# 1. Check Docker installation
# ----------------------------------------------------------------------------
print_info "Checking Docker installation..."
if ! command -v docker >/dev/null 2>&1; then
    print_error "Docker is not installed!"
    echo ""
    echo "Please install Docker first:"
    echo ""
    echo "  macOS:     https://docs.docker.com/desktop/install/mac-install/"
    echo "  Windows:   https://docs.docker.com/desktop/install/windows-install/"
    echo "  Linux:     https://docs.docker.com/engine/install/"
    echo ""
    exit 1
fi

# ----------------------------------------------------------------------------
# 2. Check Docker daemon is running
# ----------------------------------------------------------------------------
print_info "Checking Docker daemon..."
if ! docker info >/dev/null 2>&1; then
    print_error "Docker daemon is not running!"
    echo ""
    echo "Start Docker Desktop (macOS/Windows) or run:"
    echo "  sudo systemctl start docker   (Linux)"
    echo "  colima start                  (macOS with Colima)"
    echo ""
    exit 1
fi
print_status "Docker is ready"

# ----------------------------------------------------------------------------
# 3. Pull image (with retry - handles flaky networks)
# ----------------------------------------------------------------------------
print_info "Pulling Revacc image (may take a few minutes on first run)..."
pull_ok=0
for attempt in 1 2 3; do
    if docker pull "$IMAGE"; then
        pull_ok=1
        break
    fi
    print_warning "Pull failed (attempt $attempt/3). Retrying in 10s..."
    sleep 10
done

if [ "$pull_ok" -ne 1 ]; then
    print_error "Could not download $IMAGE"
    echo ""
    echo "Possible causes:"
    echo "  - No internet connection"
    echo "  - Docker Hub is down (https://www.dockerstatus.com)"
    echo "  - Repository is private (must be Public on Docker Hub)"
    echo ""
    exit 1
fi
print_status "Image downloaded successfully"

# ----------------------------------------------------------------------------
# 4. Remove old container (if any) and start fresh
# ----------------------------------------------------------------------------
print_info "Removing any existing Revacc container..."
docker stop "$CONTAINER" 2>/dev/null || true
docker rm "$CONTAINER" 2>/dev/null || true

print_info "Starting Revacc Pipeline..."
if ! docker run -d --name "$CONTAINER" -p 3000:3000 -p 8000:8000 "$IMAGE"; then
    print_error "Failed to start container (port 3000 or 8000 may already be in use)"
    echo ""
    echo "Find what is using the ports:"
    echo "  macOS/Linux:  lsof -i :3000 -i :8000"
    echo "  Then stop that process, or run:"
    echo "  docker rm -f $CONTAINER"
    echo ""
    exit 1
fi

# ----------------------------------------------------------------------------
# 5. Wait for backend + frontend to become healthy
# ----------------------------------------------------------------------------
print_info "Waiting for services to start (up to 60 seconds)..."
backend_ok=0
frontend_ok=0
for i in $(seq 1 60); do
    if [ "$backend_ok" -eq 0 ] && curl -fsS --max-time 2 "$BACKEND_URL" >/dev/null 2>&1; then
        backend_ok=1
        print_status "Backend ready (port 8000)"
    fi
    if [ "$frontend_ok" -eq 0 ] && curl -fsS --max-time 2 -o /dev/null "$FRONTEND_URL" 2>/dev/null; then
        frontend_ok=1
        print_status "Frontend ready (port 3000)"
    fi
    if [ "$backend_ok" -eq 1 ] && [ "$frontend_ok" -eq 1 ]; then
        break
    fi
    sleep 1
done

# ----------------------------------------------------------------------------
# 6. Final verification - fail loudly if anything is broken
# ----------------------------------------------------------------------------
if [ "$backend_ok" -ne 1 ] || [ "$frontend_ok" -ne 1 ]; then
    print_error "Services did not come up in time."
    echo ""
    echo "Container status:"
    docker ps -a --filter "name=$CONTAINER" --format "  {{.Status}}"
    echo ""
    echo "Last 30 log lines:"
    docker logs --tail 30 "$CONTAINER" 2>&1 | sed 's/^/  /'
    echo ""
    echo "To remove the broken container:  docker rm -f $CONTAINER"
    exit 1
fi

echo ""
echo "============================================"
echo -e "${GREEN}   Revacc Pipeline is READY!${NC}"
echo "============================================"
echo ""
echo -e "  Frontend:  ${BLUE}http://localhost:3000${NC}"
echo -e "  Backend:   ${BLUE}http://localhost:8000${NC}"
echo -e "  API Docs:  ${BLUE}http://localhost:8000/docs${NC}"
echo ""
echo "  Open http://localhost:3000 in your browser"
echo ""
echo "  Useful commands:"
echo "    Status:   docker ps --filter name=$CONTAINER"
echo "    Logs:     docker logs -f $CONTAINER"
echo "    Restart:  docker restart $CONTAINER"
echo "    Stop:     docker stop $CONTAINER && docker rm $CONTAINER"
echo ""
