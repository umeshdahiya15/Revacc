#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# Revacc — One-command startup
# Starts backend (FastAPI :8000) + frontend (Next.js :3000)
# ═══════════════════════════════════════════════════════════════════════════════
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; NC='\033[0m'

ROOT="$(cd "$(dirname "$0")" && pwd)"
BACKEND="$ROOT/backend"
LOG_DIR="$ROOT/.logs"
PID_DIR="$ROOT/.pids"

mkdir -p "$LOG_DIR" "$PID_DIR"

log()  { echo -e "${CYAN}[revacc]${NC} $*"; }
ok()   { echo -e "${GREEN}[revacc]${NC} $*"; }
warn() { echo -e "${YELLOW}[revacc]${NC} $*"; }
fail() { echo -e "${RED}[revacc]${NC} $*" >&2; exit 1; }

# ── Cleanup ──────────────────────────────────────────────────────────────────
cleanup() {
  log "Shutting down..."
  for f in "$PID_DIR"/*.pid; do
    [ -f "$f" ] || continue
    pid=$(cat "$f" 2>/dev/null)
    [ -z "$pid" ] && continue
    kill "$pid" 2>/dev/null && ok "Stopped $(basename "$f" .pid) (PID $pid)"
    rm -f "$f"
  done
}
trap cleanup EXIT INT TERM

# ── Kill anything on ports ───────────────────────────────────────────────────
free_port() {
  local pids
  pids=$(lsof -ti:"$1" 2>/dev/null || true)
  if [ -n "$pids" ]; then
    warn "Port $1 busy — killing"
    echo "$pids" | xargs kill -9 2>/dev/null || true
    sleep 1
  fi
}
free_port 8000
free_port 3000

# ── Check deps ───────────────────────────────────────────────────────────────
command -v python3 &>/dev/null || fail "python3 not found"
command -v node    &>/dev/null || fail "node not found"
command -v npm     &>/dev/null || fail "npm not found"

# ── Backend venv + deps ─────────────────────────────────────────────────────
if [ ! -d "$BACKEND/venv" ]; then
  log "Creating Python venv..."
  python3 -m venv "$BACKEND/venv"
fi

# Install deps inside a subshell so we don't pollute this shell
(
  source "$BACKEND/venv/bin/activate"
  pip install --quiet --upgrade pip 2>/dev/null
  pip install --quiet fastapi uvicorn pydantic websockets httpx biopython 2>/dev/null
)

# ── Node modules ─────────────────────────────────────────────────────────────
if [ ! -d "$ROOT/node_modules" ]; then
  log "Installing Node dependencies..."
  cd "$ROOT" && npm install --silent
fi

# ── Start backend ────────────────────────────────────────────────────────────
log "Starting backend on :8000..."
(
  source "$BACKEND/venv/bin/activate"
  cd "$BACKEND"
  exec python3 -m uvicorn app.main:app \
    --host 0.0.0.0 --port 8000 --reload --log-level warning
) > "$LOG_DIR/backend.log" 2>&1 &
echo $! > "$PID_DIR/backend.pid"

# ── Start frontend ───────────────────────────────────────────────────────────
log "Starting frontend on :3000..."
(
  cd "$ROOT"
  exec npx next dev --port 3000 --hostname 0.0.0.0
) > "$LOG_DIR/frontend.log" 2>&1 &
echo $! > "$PID_DIR/frontend.pid"

# ── Wait for backend ─────────────────────────────────────────────────────────
log "Waiting for backend..."
for i in $(seq 1 20); do
  curl -sf http://localhost:8000/api/health >/dev/null 2>&1 && break
  sleep 1
done
curl -sf http://localhost:8000/api/health >/dev/null 2>&1 \
  && ok "Backend  → http://localhost:8000  (docs: /docs)" \
  || warn "Backend may still be starting — check $LOG_DIR/backend.log"

# ── Wait for frontend ────────────────────────────────────────────────────────
log "Waiting for frontend (first compile ~30-60s)..."
for i in $(seq 1 90); do
  curl -sf http://localhost:3000 >/dev/null 2>&1 && break
  sleep 1
done
curl -sf http://localhost:3000 >/dev/null 2>&1 \
  && ok "Frontend → http://localhost:3000" \
  || warn "Frontend may still be compiling — check $LOG_DIR/frontend.log"

echo ""
echo -e "${GREEN}══════════════════════════════════════════════${NC}"
echo -e "${GREEN}  Revacc is running!                          ${NC}"
echo -e "${GREEN}  Frontend : http://localhost:3000             ${NC}"
echo -e "${GREEN}  Backend  : http://localhost:8000             ${NC}"
echo -e "${GREEN}  API Docs : http://localhost:8000/docs        ${NC}"
echo -e "${GREEN}══════════════════════════════════════════════${NC}"
echo ""
echo "Logs: $LOG_DIR/"
echo "Stop:  Ctrl+C"
echo ""
wait
