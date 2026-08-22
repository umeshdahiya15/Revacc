#!/usr/bin/env bash
# Revacc — start backend + ngrok tunnel in one command.
# Usage: ./start.sh          (backend on :8000, ngrok tunnel for backend)
#        ./start.sh --all    (also start frontend on :3000)
set -euo pipefail

BACKEND_PORT=8000
FRONTEND_PORT=3000

cleanup() { pkill -f "ngrok http" 2>/dev/null; pkill -f "uvicorn.*$BACKEND_PORT" 2>/dev/null; pkill -f "next dev" 2>/dev/null; }
trap cleanup EXIT

echo "==> Starting backend on :$BACKEND_PORT …"
cd "$(dirname "$0")/backend"
# Development-only wildcard CORS for the temporary ngrok/local frontend.
# Production deployments must set MEV_CORS_ORIGINS to explicit origins.
MEV_CORS_ORIGINS="*" .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port "$BACKEND_PORT" &
sleep 2

echo "==> Starting ngrok tunnel for backend …"
ngrok http "$BACKEND_PORT" --log=stdout --log-format=json > /tmp/ngrok-revacc.json 2>&1 &
sleep 3

NGROK_URL=$(curl -s http://127.0.0.1:4040/api/tunnels | python3 -c "
import sys,json
d=json.load(sys.stdin)
print(d['tunnels'][0]['public_url'])
" 2>/dev/null || echo "")

echo ""
echo "============================================"
echo "  Backend:   http://localhost:$BACKEND_PORT"
echo "  Ngrok:     $NGROK_URL"
echo "============================================"
echo ""
echo "  Open frontend: http://localhost:$FRONTEND_PORT"
echo "  Or Vercel:     (set NEXT_PUBLIC_API_URL=$NGROK_URL)"
echo ""
echo "  Press Ctrl+C to stop everything."
echo ""

if [ "${1:-}" = "--all" ]; then
  echo "==> Starting frontend on :$FRONTEND_PORT …"
  cd "$(dirname "$0")"
  NEXT_PUBLIC_API_URL="$NGROK_URL" npm run dev &
  wait
else
  wait
fi
