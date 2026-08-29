#!/usr/bin/env python3
"""
============================================================================
REVACC COLAB MASTER SCRIPT
============================================================================
Single-cell Colab script that sets up and runs the complete reverse-
vaccinology backend. Handles everything:
  - System dependencies
  - Python packages
  - Code fixes (DEG threshold, step labels)
  - Backend server
  - Ngrok tunnel
  - Health monitoring
  - Job creation from frontend

Usage:
  1. Open Google Colab -> Runtime -> Change runtime type -> T4 GPU
  2. Paste this ENTIRE script into ONE code cell
  3. Run the cell
  4. Open the ngrok URL to use the frontend

Paper: Barazesh et al. 2024, Nature Scientific Reports
============================================================================
"""
from __future__ import annotations

import os
import sys
import subprocess
import time
import json
import urllib.request
import signal
import glob

# ============================================================================
# HELPERS
# ============================================================================
def p(msg):
    """Print with immediate flush for Colab."""
    print(msg, flush=True)

def header(title):
    p("")
    p("=" * 60)
    p(title)
    p("=" * 60)

def run(cmd, check=False, cwd=None, capture_output=True):
    """Run a command and return result."""
    return subprocess.run(cmd, capture_output=capture_output, text=True, check=check, cwd=cwd)

PYTHON = sys.executable

# ============================================================================
# CONFIGURATION
# ============================================================================
TAXON_ID = "99287"
PATHOGEN_NAME = "Streptococcus agalactiae"
BACKEND_PORT = 8000
REPO_URL = "https://github.com/umeshdahiya15/Revacc.git"
WORKDIR = "/content/Revacc"

# Read ngrok token from Colab Secrets
NGROK_AUTHTOKEN = ""
try:
    from google.colab import userdata
    NGROK_AUTHTOKEN = userdata.get("NGROK_AUTHTOKEN")
    if NGROK_AUTHTOKEN:
        p(f"[OK] Loaded ngrok token ({len(NGROK_AUTHTOKEN)} chars)")
    else:
        p("[WARN] NGROK_AUTHTOKEN is empty in Colab Secrets")
except Exception as e:
    p(f"[WARN] Cannot access Colab Secrets: {e}")

if not NGROK_AUTHTOKEN:
    p("       Add NGROK_AUTHTOKEN in Colab Secrets (left panel, key icon)")
    p("       Get token free at: https://dashboard.ngrok.com/get-started/your-authtoken")

# ============================================================================
# STEP 1: System Dependencies
# ============================================================================
header("STEP 1/7: System Dependencies")

run(["apt-get", "update", "-qq"], check=True)

for pkg in ["ncbi-blast+", "blast+", "git", "wget"]:
    r = run(["apt-get", "install", "-y", "-qq", pkg])
    if r.returncode == 0:
        p(f"  [OK] {pkg}")

# Install cloudflared
r = run(["wget", "-q", "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64", "-O", "/usr/local/bin/cloudflared"])
run(["chmod", "+x", "/usr/local/bin/cloudflared"])
p(f"  [OK] cloudflared")

blastp = run(["which", "blastp"])
if blastp.stdout.strip():
    p(f"  [OK] blastp: {blastp.stdout.strip()}")
else:
    p("  [WARN] blastp not found - will use fallback")

# ============================================================================
# STEP 2: Clone Repository + Install PSORTb
# ============================================================================
header("STEP 2/7: Clone Repository + PSORTb")

if os.path.exists(f"{WORKDIR}/.git"):
    p("  Repository exists, pulling latest...")
    run(["git", "pull", "origin", "main"], cwd=WORKDIR)
    p("  [OK] Updated")
else:
    p("  Cloning repository...")
    run(["git", "clone", REPO_URL, WORKDIR], check=True)
    p("  [OK] Cloned")

# Install PSORTb for subcellular localization
p("  Installing PSORTb from source...")
r = run(["bash", os.path.join(WORKDIR, "install_psortb.sh")], capture_output=True)
if r.returncode == 0:
    p("  [OK] PSORTb installed")
else:
    p(f"  [WARN] PSORTb install issue: {r.stderr[:200] if r.stderr else 'unknown'}")
    p("  Pipeline will use Phobius fallback for localization")

# ============================================================================
# STEP 3: Python Dependencies
# ============================================================================
header("STEP 3/7: Python Dependencies")

# Upgrade pip
run([PYTHON, "-m", "pip", "install", "-q", "--upgrade", "pip"], check=True)

# Install required packages
packages = [
    "fastapi",
    "uvicorn[standard]",
    "httpx",
    "biopython",
    "pydantic",
    "websockets",
    "pyngrok",
]

result = run([PYTHON, "-m", "pip", "install", "-q"] + packages, check=True)
p("  [OK] All packages installed")

# Verify key imports
verify = run([PYTHON, "-c", "import fastapi, uvicorn, httpx, Bio; print('OK')"])
if "OK" in verify.stdout:
    p("  [OK] Import verification passed")

# ============================================================================
# STEP 4: Apply Code Fixes
# ============================================================================
header("STEP 4/7: Apply Code Fixes")

# Fix 1: DEG identity threshold (20% -> 40%)
runner_path = os.path.join(WORKDIR, "backend", "app", "tools", "runner.py")
if os.path.exists(runner_path):
    with open(runner_path, "r") as f:
        content = f.read()
    old = 'DEG_IDENTITY_THRESHOLD = float(os.environ.get("DEG_IDENTITY_THRESHOLD", "20"))'
    new = 'DEG_IDENTITY_THRESHOLD = float(os.environ.get("DEG_IDENTITY_THRESHOLD", "40"))'
    if old in content:
        content = content.replace(old, new)
        with open(runner_path, "w") as f:
            f.write(content)
        p("  [OK] DEG threshold: 20% -> 40%")
    else:
        p("  [OK] DEG threshold already correct")

# Fix 2: Frontend step label (SwissModel -> AlphaFold DB)
constants_path = os.path.join(WORKDIR, "src", "lib", "constants.ts")
if os.path.exists(constants_path):
    with open(constants_path, "r") as f:
        content = f.read()
    if 'tool: "SwissModel"' in content:
        content = content.replace('tool: "SwissModel"', 'tool: "AlphaFold DB"')
        with open(constants_path, "w") as f:
            f.write(content)
        p("  [OK] Step 4-2 label: SwissModel -> AlphaFold DB")
    else:
        p("  [OK] Step label already correct")

# Fix 3: Add ngrok bypass middleware for CORS
main_path = os.path.join(WORKDIR, "backend", "app", "main.py")
if os.path.exists(main_path):
    with open(main_path, "r") as f:
        content = f.read()
    if "NgrokBypassMiddleware" not in content:
        # Add import
        content = content.replace(
            "from fastapi import FastAPI",
            "from fastapi import FastAPI, Request, Response\nfrom starlette.middleware.base import BaseHTTPMiddleware"
        )
        # Add middleware class before lifespan
        ngrok_middleware = '''


class NgrokBypassMiddleware(BaseHTTPMiddleware):
    """Bypass ngrok browser protection for API requests."""
    
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["ngrok-skip-browser-warning"] = "true"
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "*"
        response.headers["Access-Control-Allow-Headers"] = "*"
        return response


'''
        content = content.replace(
            "@asynccontextmanager",
            ngrok_middleware + "@asynccontextmanager"
        )
        # Add middleware usage
        content = content.replace(
            "app.add_middleware(\n    CORSMiddleware,",
            "app.add_middleware(NgrokBypassMiddleware)\n\napp.add_middleware(\n    CORSMiddleware,"
        )
        # Fix CORS origins to allow all
        content = content.replace(
            "allow_origins=list(settings.cors_origins),",
            'allow_origins=["*"],'
        )
        with open(main_path, "w") as f:
            f.write(content)
        p("  [OK] Added ngrok CORS bypass middleware")
    else:
        p("  [OK] Ngrok middleware already present")

# ============================================================================
# STEP 5: Configure Environment
# ============================================================================
header("STEP 5/7: Configure Environment")

ENV_VARS = {
    "CDHIT_THRESHOLD": "0.80",
    "DEG_IDENTITY_THRESHOLD": "40",
    "DEG_EVALUE_THRESHOLD": "1e-5",
    "DEG_SCOPE": "all",
    "ALGPRED_THRESHOLD": "0.321",
    "VAXIJEN_THRESHOLD": "0.50",
    "VFDB_EVALUE_THRESHOLD": "1e-4",
    "VFDB_BITSCORE_THRESHOLD": "100",
    "VFDB_IDENTITY_THRESHOLD": "30",
    "HUMAN_HOMOLOGY_IDENTITY": "30",
    "HOMOLOGY_EVALUE": "1e-4",
    "MHC1_PERCENTILE": "2.0",
    "MHC1_LENGTHS": "12",
    "MHC1_METHOD": "consensus",
    "MHC2_PERCENTILE": "2.0",
    "MHC2_LENGTHS": "15",
    "MHC2_METHOD": "consensus",
    "IL4_THRESHOLD": "0.2",
    "IL10_THRESHOLD": "-0.3",
    "BCELL_THRESHOLD": "0.5",
    "BCELL_WINDOW": "7",
    "MEV_CTL_CAP": "8",
    "MEV_HTL_CAP": "8",
    "MEV_BCELL_CAP": "5",
    "MEV_STRUCTURE_PROVIDER": "esmfold",
    "MEV_CORS_ORIGINS": "*",
    "MEV_STEP_TICK_MS": "300",
    "MEV_BLAST_DB_CACHE": "/content/blast_dbs",
    "MEV_VFDB_CACHE": "/content/blast_dbs/vfdb",
    "PSORTB_BIN": "/usr/local/miniconda/bin/psortb",
}

for key, value in ENV_VARS.items():
    os.environ[key] = value

os.makedirs("/content/blast_dbs", exist_ok=True)
p(f"  [OK] Set {len(ENV_VARS)} environment variables")

# ============================================================================
# STEP 6: Start Backend + Ngrok
# ============================================================================
header("STEP 6/7: Start Backend + Tunnel")

# Kill any existing backend aggressively
p("  Stopping any existing backend...")
subprocess.run(["pkill", "-9", "-f", "uvicorn"], capture_output=True)
subprocess.run(["pkill", "-9", "-f", "cloudflared"], capture_output=True)
time.sleep(1)
# Also kill by port
subprocess.run(["fuser", "-k", f"{BACKEND_PORT}/tcp"], capture_output=True)
time.sleep(2)

# Verify port is free
import socket
port_check = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    port_check.bind(("0.0.0.0", BACKEND_PORT))
    port_check.close()
    p(f"  [OK] Port {BACKEND_PORT} is free")
except OSError:
    p(f"  [WARN] Port {BACKEND_PORT} still in use, killing harder...")
    subprocess.run(["lsof", "-ti", f":{BACKEND_PORT}"], capture_output=True)
    # Force kill anything on that port
    result = subprocess.run(["lsof", "-ti", f":{BACKEND_PORT}"], capture_output=True, text=True)
    if result.stdout.strip():
        for pid in result.stdout.strip().split("\n"):
            subprocess.run(["kill", "-9", pid], capture_output=True)
    time.sleep(2)

# Prepare environment
env = os.environ.copy()
env["PYTHONPATH"] = WORKDIR
env["PATH"] = "/usr/local/miniconda/bin:" + env.get("PATH", "")
backend_dir = os.path.join(WORKDIR, "backend")

# Start backend
p("  Starting FastAPI backend...")
backend_proc = subprocess.Popen(
    [PYTHON, "-m", "uvicorn", "app.main:app",
     "--host", "0.0.0.0", "--port", str(BACKEND_PORT)],
    cwd=backend_dir,
    env=env,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
)

# Wait for backend to be ready
p("  Waiting for backend...")
backend_ready = False
for i in range(45):
    time.sleep(1)
    if backend_proc.poll() is not None:
        out = backend_proc.stdout.read().decode() if backend_proc.stdout else ""
        p(f"  [ERROR] Backend crashed!")
        p(f"  Output: {out[-500:]}")
        sys.exit(1)
    try:
        req = urllib.request.Request(f"http://localhost:{BACKEND_PORT}/api/health")
        urllib.request.urlopen(req, timeout=2)
        backend_ready = True
        p(f"  [OK] Backend ready (PID: {backend_proc.pid})")
        break
    except Exception:
        if i > 0 and i % 10 == 0:
            p(f"  Waiting... {i}s")

if not backend_ready:
    p("  [ERROR] Backend did not start within 45 seconds")
    sys.exit(1)

# Start tunnel (using cloudflared - no browser warning, pre-installed)
public_url = None

# Kill any existing cloudflared
subprocess.run(["pkill", "-f", "cloudflared"], capture_output=True)
time.sleep(1)

try:
    p("  Starting cloudflared tunnel (no browser warning)...")
    
    # Start cloudflared in background
    cloudflared_proc = subprocess.Popen(
        ["cloudflared", "tunnel", "--url", f"http://localhost:{BACKEND_PORT}", "--no-autoupdate"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    
    # Wait for tunnel URL
    import re
    for i in range(30):
        time.sleep(1)
        line = cloudflared_proc.stdout.readline()
        # Extract URL using regex
        match = re.search(r'https://[a-zA-Z0-9-]+\.trycloudflare\.com', line)
        if match:
            public_url = match.group(0)
            p(f"  [OK] Tunnel: {public_url}")
            break
        if i == 29:
            p("  [WARN] Could not get tunnel URL")
except Exception as e:
    p(f"  [WARN] cloudflared failed: {e}")

if not public_url:
    p("  Backend accessible at http://localhost:8000")

# ============================================================================
# STEP 7: Health Monitor + Instructions
# ============================================================================
header("STEP 7/7: Health Monitor & Instructions")

# Function to check health
def check_health():
    try:
        req = urllib.request.Request(f"http://localhost:{BACKEND_PORT}/api/health")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())
    except Exception:
        return None

# Initial health check
health = check_health()
if health:
    p(f"  [OK] Health: {health.get('status')}")
    p(f"  [OK] Service: {health.get('service')}")

# Print instructions
header("SETUP COMPLETE!")
p("")
if public_url:
    p("TUNNEL ACTIVE")
    p(f"  URL: {public_url}")
    p("")
    p("TO CONNECT VERCEL FRONTEND:")
    p(f"  1. Go to https://vercel.com/dashboard")
    p(f"  2. Click your 'Revacc' project")
    p(f"  3. Settings -> Environment Variables")
    p(f"  4. Set: NEXT_PUBLIC_API_URL = {public_url}")
    p(f"  5. Deployments -> Redeploy (uncheck cache)")
    p(f"  6. Wait 1-2 minutes, then open the URL")
else:
    p("BACKEND RUNNING LOCALLY")
    p(f"  URL: http://localhost:{BACKEND_PORT}")
    p("")
    p("Note: Localhost only - not accessible from Vercel")

p("")
p("API ENDPOINTS:")
p(f"  GET  /api/health          - Health check")
p(f"  GET  /api/jobs             - List jobs")
p(f"  POST /api/jobs             - Create job")
p(f"  GET  /api/jobs/{{id}}        - Get job status")
p(f"  POST /api/jobs/{{id}}/start  - Start pipeline")

p("")
p("EXAMPLE - Create and run a job:")
p(f'  curl -X POST http://localhost:{BACKEND_PORT}/api/jobs \\')
p(f'    -H "Content-Type: application/json" \\')
p(f'    -d \'{{"taxonId": "99287", "pathogenName": "Streptococcus agalactiae", "realTools": true}}\'')
p("")

# Keep alive and monitor
p("Monitoring backend (Ctrl+C to stop)...")
p("")
try:
    while True:
        health = check_health()
        if health:
            sys.stdout.write(f"\r  [OK] Backend alive | Jobs: {health.get('jobCount', '?')} | ")
            sys.stdout.flush()
        else:
            sys.stdout.write("\r  [WARN] Backend not responding          ")
            sys.stdout.flush()
        time.sleep(10)
except KeyboardInterrupt:
    p("\n\nShutting down...")
    if backend_proc:
        backend_proc.terminate()
    p("Done!")
