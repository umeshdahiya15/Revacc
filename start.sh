#!/bin/bash
# ============================================================================
# REVACC Local Startup Script
# Runs the backend locally with a cloudflared tunnel
# ============================================================================

set -e

BACKEND_PORT=8000
WORKDIR="$(cd "$(dirname "$0")" && pwd)"

p() { echo "$1"; }
header() { echo ""; echo "============================================================"; echo "$1"; echo "============================================================"; }

# ============================================================================
header "STEP 1/5: Check Dependencies"
# ============================================================================

# Check Python
if ! command -v python3 &>/dev/null; then
    echo "[ERROR] Python3 not found"
    exit 1
fi
p "[OK] Python3: $(python3 --version)"

# Check pip packages
p "  Checking Python packages..."
python3 -c "import fastapi, uvicorn, httpx, Bio" 2>/dev/null || {
    p "  Installing dependencies..."
    pip3 install -q fastapi "uvicorn[standard]" httpx biopython pydantic websockets httpx-sse
}
p "[OK] Python packages"

# Check BLAST+
if command -v blastp &>/dev/null; then
    p "[OK] blastp: $(which blastp)"
else
    p "[WARN] blastp not found - install: brew install ncbi-blast+ (Mac) or apt install ncbi-blast+ (Linux)"
fi

# Check DIAMOND (optional, 100x faster)
if command -v diamond &>/dev/null; then
    p "[OK] DIAMOND: $(which diamond)"
else
    p "[INFO] DIAMOND not found (optional). Install for 100x faster BLAST:"
    p "       brew install diamond (Mac) or wget https://github.com/bbuchfink/diamond/releases/download/v2.1.10/diamond-linux64.tar.gz"
fi

# Check Docker for PSORTb
if command -v docker &>/dev/null; then
    if docker info >/dev/null 2>&1; then
        # Check if PSORTb image is pulled
        if docker image inspect brinkmanlab/psortb_commandline:1.0.2 >/dev/null 2>&1; then
            p "[OK] PSORTb (Docker image ready)"
        else
            p "[INFO] Pulling PSORTb Docker image (one-time, ~2.5GB)..."
            docker pull brinkmanlab/psortb_commandline:1.0.2
            p "[OK] PSORTb Docker image pulled"
        fi
    else
        p "[WARN] Docker daemon not running - start Docker Desktop"
    fi
else
    p "[WARN] Docker not found - PSORTb will use fallback (install Docker for full functionality)"
fi

# Check cloudflared for tunnel
if command -v cloudflared &>/dev/null; then
    p "[OK] cloudflared: $(which cloudflared)"
else
    p "  Installing cloudflared..."
    if [[ "$OSTYPE" == "darwin"* ]]; then
        brew install cloudflared 2>/dev/null || {
            p "[ERROR] Install cloudflared: brew install cloudflared"
            exit 1
        }
    else
        wget -q "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64" -O /usr/local/bin/cloudflared
        chmod +x /usr/local/bin/cloudflared
    fi
    p "[OK] cloudflared installed"
fi

# ============================================================================
header "STEP 2/5: Configure Environment"
# ============================================================================

export PYTHONPATH="$WORKDIR"
export DEG_IDENTITY_THRESHOLD=40
export DEG_EVALUE_THRESHOLD=1e-5
export DEG_SCOPE=all
export ALGPRED_THRESHOLD=0.321
export VAXIJEN_THRESHOLD=0.50
export VFDB_EVALUE_THRESHOLD=1e-4
export VFDB_BITSCORE_THRESHOLD=100
export VFDB_IDENTITY_THRESHOLD=30
export HUMAN_HOMOLOGY_IDENTITY=30
export HOMOLOGY_EVALUE=1e-4
export MHC1_PERCENTILE=2.0
export MHC1_LENGTHS=12
export MHC1_METHOD=consensus
export MHC2_PERCENTILE=2.0
export MHC2_LENGTHS=15
export MHC2_METHOD=consensus
export IL4_THRESHOLD=0.2
export IL10_THRESHOLD=-0.3
export BCELL_THRESHOLD=0.5
export BCELL_WINDOW=7
export MEV_CTL_CAP=8
export MEV_HTL_CAP=8
export MEV_BCELL_CAP=5
export MEV_STEP_TICK_MS=300
export MEV_CORS_ORIGINS="*"

p "[OK] Environment configured"

# ============================================================================
header "STEP 3/5: Start Backend"
# ============================================================================

# Kill any existing backend
p "  Stopping any existing backend..."
lsof -ti :$BACKEND_PORT | xargs kill -9 2>/dev/null || true
sleep 1

p "  Starting FastAPI backend on port $BACKEND_PORT..."
cd "$WORKDIR/backend"
python3 -m uvicorn app.main:app --host 0.0.0.0 --port $BACKEND_PORT &
BACKEND_PID=$!
sleep 3

# Wait for backend
for i in $(seq 1 15); do
    if curl -s http://localhost:$BACKEND_PORT/api/health >/dev/null 2>&1; then
        p "[OK] Backend ready (PID: $BACKEND_PID)"
        break
    fi
    sleep 1
done

if ! curl -s http://localhost:$BACKEND_PORT/api/health >/dev/null 2>&1; then
    p "[ERROR] Backend failed to start"
    exit 1
fi

# ============================================================================
header "STEP 4/5: Start Tunnel"
# ============================================================================

# Kill existing cloudflared
pkill -f "cloudflared tunnel" 2>/dev/null || true
sleep 1

p "  Starting cloudflared tunnel..."
cloudflared tunnel --url http://localhost:$BACKEND_PORT --no-autoupdate 2>&1 &
TUNNEL_PID=$!
sleep 5

# Get tunnel URL
TUNNEL_URL=""
for i in $(seq 1 20); do
    TUNNEL_URL=$(curl -s http://localhost:$BACKEND_PORT/api/health 2>/dev/null | python3 -c "import sys; print('ok')" 2>/dev/null)
    # Try to get URL from cloudflared logs
    TUNNEL_URL=$(ps aux | grep "cloudflared tunnel" | grep -o "https://[^ ]*\.trycloudflare\.com" | head -1)
    if [ -n "$TUNNEL_URL" ]; then
        break
    fi
    sleep 1
done

# ============================================================================
header "STEP 5/5: Ready!"
# ============================================================================

if [ -n "$TUNNEL_URL" ]; then
    echo ""
    echo "TUNNEL ACTIVE"
    echo "  URL: $TUNNEL_URL"
    echo ""
    echo "CONNECT VERCEL FRONTEND:"
    echo "  1. Go to https://vercel.com/dashboard"
    echo "  2. Click 'Revacc' project"
    echo "  3. Settings -> Environment Variables"
    echo "  4. Set: NEXT_PUBLIC_API_URL = $TUNNEL_URL"
    echo "  5. Deployments -> Redeploy"
else
    echo ""
    echo "BACKEND RUNNING LOCALLY"
    echo "  URL: http://localhost:$BACKEND_PORT"
    echo ""
    echo "  Open the Colab notebook and set:"
    echo "  NEXT_PUBLIC_API_URL = http://localhost:$BACKEND_PORT"
fi

echo ""
echo "API ENDPOINTS:"
echo "  GET  /api/health          - Health check"
echo "  GET  /api/jobs             - List jobs"
echo "  POST /api/jobs             - Create job"
echo "  GET  /api/jobs/{id}        - Get job status"
echo "  POST /api/jobs/{id}/start  - Start pipeline"
echo ""
echo "Press Ctrl+C to stop"
echo ""

# Wait for backend
wait $BACKEND_PID
