#!/bin/bash
# ============================================================================
# Revacc Services Startup Script
# Runs both frontend and backend inside Docker container
# ============================================================================

set -e

echo "============================================"
echo "Revacc Pipeline - Starting Services"
echo "============================================"

# Start backend API
echo "[1/2] Starting Backend API on port 8000..."
cd /app
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 &
BACKEND_PID=$!

# Wait for backend to be ready
echo "      Waiting for backend to initialize..."
for i in $(seq 1 30); do
    if curl -s http://localhost:8000/api/health >/dev/null 2>&1; then
        echo "      ✓ Backend ready"
        break
    fi
    sleep 1
done

# Start frontend (Next.js)
echo "[2/2] Starting Frontend on port 3000..."
if ! command -v node >/dev/null 2>&1; then
    echo "ERROR: node runtime not found in image"
    exit 1
fi
cd /app/frontend
# HOSTNAME must be 0.0.0.0: Next.js standalone binds to $HOSTNAME, and bash
# sets HOSTNAME to the container ID, which breaks Docker port publishing.
HOSTNAME=0.0.0.0 node server.js &
FRONTEND_PID=$!

echo ""
echo "============================================"
echo "Revacc Pipeline is READY!"
echo "============================================"
echo ""
echo "Frontend:  http://localhost:3000"
echo "Backend:   http://localhost:8000"
echo "API Docs:  http://localhost:8000/docs"
echo ""
echo "Press Ctrl+C to stop all services"
echo ""

# Wait for both processes
wait $BACKEND_PID $FRONTEND_PID
