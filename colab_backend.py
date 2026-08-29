## ======================================================================
## REVACC — Colab T4 Backend + Ngrok (frontend-controlled)
## ======================================================================
## Reads NGROK_AUTHTOKEN from Colab Secrets.
## You control the pipeline from the Vercel frontend.
##
## HOW TO USE:
##   1. Open Google Colab → Runtime → Change runtime type → T4 GPU
##   2. Paste this ENTIRE script into ONE code cell
##   3. Ensure secrets: GITHUB_TOKEN + NGROK_AUTHTOKEN
##   4. Run the cell
##   5. Copy ngrok URL → set in Vercel as NEXT_PUBLIC_API_URL → redeploy
##   6. Run pipeline from the Vercel UI
## ======================================================================

import os, sys, subprocess, time, json, urllib.request

PYTHON = sys.executable
REPO_URL = "https://github.com/umeshdahiya15/Revacc.git"
WORKDIR = "/content/Revacc"
BACKEND_PORT = 8000

print(f"Using Python: {PYTHON}")

# ─── READ SECRETS ─────────────────────────────────────────────────────────────
try:
    from google.colab import userdata
    GITHUB_TOKEN = userdata.get('GITHUB_TOKEN')
    NGROK_AUTHTOKEN = userdata.get('NGROK_AUTHTOKEN')
except Exception:
    GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN', '')
    NGROK_AUTHTOKEN = os.environ.get('NGROK_AUTHTOKEN', '')

if not GITHUB_TOKEN:
    print("ERROR: Set GITHUB_TOKEN in Colab Secrets (left panel → key icon)")
    sys.exit(1)
if not NGROK_AUTHTOKEN:
    print("ERROR: Set NGROK_AUTHTOKEN in Colab Secrets (left panel → key icon)")
    sys.exit(1)
print(f"GITHUB_TOKEN loaded ({len(GITHUB_TOKEN)} chars)")
print(f"NGROK_AUTHTOKEN loaded ({len(NGROK_AUTHTOKEN)} chars)")

def sh(cmd):
    print(f"\n>>> {cmd}")
    r = subprocess.run(cmd, shell=True)
    if r.returncode != 0:
        print(f"  [exit {r.returncode}]")
    return r.returncode

# ─── STEP 1: BLAST+ ──────────────────────────────────────────────────────────
print("\n" + "="*70)
print("STEP 1/5 — Verifying BLAST+")
print("="*70)

if sh("which blastp") != 0:
    sh("apt-get update -qq")
    sh("apt-get install -y -qq ncbi-blast+")
sh("blastp -version 2>&1 | head -1")

# ─── STEP 2: CLONE REPO ──────────────────────────────────────────────────────
print("\n" + "="*70)
print("STEP 2/5 — Cloning Revacc repository")
print("="*70)

if os.path.exists(f"{WORKDIR}/.git"):
    print("Repository already cloned, pulling latest...")
    sh(f"cd {WORKDIR} && git pull origin main")
else:
    auth_url = REPO_URL.replace("https://", f"https://{GITHUB_TOKEN}@")
    sh(f"git clone {auth_url} {WORKDIR}")
    if not os.path.exists(f"{WORKDIR}/.git"):
        print("ERROR: git clone failed — check GITHUB_TOKEN has repo access")
        sys.exit(1)

# ─── STEP 3: PYTHON DEPS ────────────────────────────────────────────────────
print("\n" + "="*70)
print("STEP 3/5 — Installing Python dependencies")
print("="*70)

sh(f"{PYTHON} -m pip install --upgrade pip 'setuptools<81' wheel -q")
sh(f"{PYTHON} -m pip install -r {WORKDIR}/backend/requirements.txt -q")
sh(f"{PYTHON} -m pip install pyngrok -q")

sh(f"{PYTHON} -c \"import torch; print(f'PyTorch {{torch.__version__}}, CUDA={{torch.cuda.is_available()}}, GPU={{torch.cuda.get_device_name(0) if torch.cuda.is_available() else \\\"N/A\\\"}}')\"")

# ─── STEP 4: ESMFOLD WEIGHTS ─────────────────────────────────────────────────
print("\n" + "="*70)
print("STEP 4/5 — ESMFold weights")
print("="*70)

WEIGHTS_DIR = os.path.expanduser("~/.cache/torch/hub/checkpoints")
WEIGHTS_FILE = f"{WEIGHTS_DIR}/esmfold_3B_v1.pt"

if not os.path.exists(WEIGHTS_FILE):
    os.makedirs(WEIGHTS_DIR, exist_ok=True)
    sh(f"wget -q --show-progress -O {WEIGHTS_FILE} "
       "https://dl.fbaipublicfiles.com/fair-esm/models/esmfold_3B_v1.pt")

size_gb = os.path.getsize(WEIGHTS_FILE) / (1024**3)
print(f"Weights: {size_gb:.1f} GB ready")

# ─── STEP 5: START BACKEND + NGROK ───────────────────────────────────────────
print("\n" + "="*70)
print("STEP 5/5 — Starting backend + ngrok")
print("="*70)

sh(f"pkill -9 -f 'uvicorn.*{BACKEND_PORT}' 2>/dev/null; true")
sh(f"fuser -k {BACKEND_PORT}/tcp 2>/dev/null; true")
time.sleep(2)

env = os.environ.copy()
env.update({
    "PYTHONPATH": WORKDIR,
    "CDHIT_THRESHOLD": "0.80",
    "DEG_IDENTITY_THRESHOLD": "20",
    "DEG_EVALUE_THRESHOLD": "1e-5",
    "DEG_SCOPE": "all",
    "ALGPRED_THRESHOLD": "0.321",
    "VAXIJEN_THRESHOLD": "0.50",
    "VFDB_EVALUE_THRESHOLD": "1e-4",
    "VFDB_BITSCORE_THRESHOLD": "100",
    "VFDB_IDENTITY_THRESHOLD": "30",
    "HUMAN_HOMOLOGY_IDENTITY": "0.30",
    "HOMOLOGY_EVALUE": "1e-4",
    "MHC1_PERCENTILE": "2.0",
    "MHC2_PERCENTILE": "2.0",
    "IL4_THRESHOLD": "0.2",
    "IL10_THRESHOLD": "-0.3",
    "BCELL_THRESHOLD": "0.5",
    "BCELL_WINDOW": "7",
    "MEV_STRUCTURE_PROVIDER": "esmfold",
    "MEV_CORS_ORIGINS": "*",
    "MEV_STEP_TICK_MS": "300",
    "MEV_MAX_STEPS_PER_TICK": "1",
    "MEV_BLAST_DB_CACHE": "/content/blast_dbs",
    "MEV_VFDB_CACHE": "/content/blast_dbs/vfdb",
    "MEV_PHOBIUS_CAP": "0",
    "ESMFOLD_TIMEOUT": "600",
})
os.makedirs("/content/blast_dbs", exist_ok=True)

backend_proc = subprocess.Popen(
    [PYTHON, "-m", "uvicorn", "app.main:app",
     "--host", "0.0.0.0", "--port", str(BACKEND_PORT)],
    cwd=f"{WORKDIR}/backend",
    env=env,
)

# Wait for backend
for i in range(40):
    time.sleep(1)
    # Check if process died
    if backend_proc.poll() is not None:
        print(f"Backend process exited with code {backend_proc.returncode}")
        sys.exit(1)
    try:
        urllib.request.urlopen(f"http://localhost:{BACKEND_PORT}/api/health", timeout=2)
        print(f"Backend started (PID: {backend_proc.pid})")
        break
    except Exception:
        if i == 39:
            print("BACKEND FAILED TO START (timeout)")
            sys.exit(1)

# Start ngrok
from pyngrok import ngrok
ngrok.set_auth_token(NGROK_AUTHTOKEN)
ngrok.kill()
tunnel = ngrok.connect(BACKEND_PORT)
public_url = tunnel.public_url

print(f"\n{'='*70}")
print(f"  NGROK TUNNEL: {public_url}")
print(f"{'='*70}")
print(f"\n  1. Copy the URL above")
print(f"  2. Go to Vercel → Settings → Environment Variables")
print(f"  3. Set: NEXT_PUBLIC_API_URL = {public_url}")
print(f"  4. Redeploy the frontend")
print(f"  5. Run pipeline from the Vercel UI")
print(f"\n  Keep this cell running! Backend stops when you interrupt.")

# Keep alive
print("\nKeeping backend alive... (Interrupt to stop)")
while True:
    time.sleep(30)
    if backend_proc.poll() is not None:
        print(f"Backend died (exit code {backend_proc.returncode}), restarting...")
        # Kill any leftover process on the port
        subprocess.run(f"fuser -k {BACKEND_PORT}/tcp 2>/dev/null; true", shell=True)
        time.sleep(2)
        backend_proc = subprocess.Popen(
            [PYTHON, "-m", "uvicorn", "app.main:app",
             "--host", "0.0.0.0", "--port", str(BACKEND_PORT)],
            cwd=f"{WORKDIR}/backend",
            env=env,
        )
        time.sleep(5)
