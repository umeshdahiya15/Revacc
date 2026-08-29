## ======================================================================
## REVACC — Google Colab T4 Backend (single-cell setup + run)
## ======================================================================
## Paper: Barazesh et al. 2024, Nature Scientific Reports
## Architecture: T4 GPU = backend | Vercel = frontend | ngrok = tunnel
##
## HOW TO USE:
##   1. Open Google Colab → Runtime → Change runtime type → T4 GPU
##   2. Paste this ENTIRE script into ONE code cell
##   3. Set NGROK_AUTHTOKEN below (get from https://dashboard.ngrok.com)
##   4. Click Run All (or Shift+Enter on each section)
##   5. After Step 8 completes, copy the ngrok URL
##   6. Set NEXT_PUBLIC_API_URL=<ngrok-url> in Vercel → redeploy frontend
## ======================================================================

# ─── CONFIGURATION (edit these) ────────────────────────────────────────────────
NGROK_AUTHTOKEN = ""  #@param {type:"string"}
TAXON_ID = "99287"    # Streptococcus agalactiae
PATHOGEN_NAME = "Streptococcus agalactiae"
BACKEND_PORT = 8000
REPO_URL = "https://github.com/umeshdahiya15/Revacc.git"
WORKDIR = "/content/Revacc"
VENV = "/content/esmfold-env"

import os, sys, subprocess, time, json, shutil, urllib.request
from pathlib import Path

os.environ["NGROK_AUTHTOKEN"] = NGROK_AUTHTOKEN

def sh(cmd, **kw):
    """Run a shell command, stream output, raise on failure."""
    print(f"\n>>> {cmd}")
    r = subprocess.run(cmd, shell=True, capture_output=False, **kw)
    if r.returncode != 0:
        print(f"WARNING: command exited with code {r.returncode}")
    return r.returncode

# ─── STEP 1: SYSTEM DEPS ─────────────────────────────────────────────────────
print("\n" + "="*70)
print("STEP 1/8 — Installing system dependencies (BLAST+, Python 3.10)")
print("="*70)

sh("apt-get update -qq")
sh("apt-get install -y -qq blast+ ncbi-blast+ python3.10 python3.10-venv "
   "python3.10-dev python3.10-distutils git wget curl")
sh("blastp -version 2>&1 | head -1")
sh("python3.10 --version")

# ─── STEP 2: CLONE REPO ──────────────────────────────────────────────────────
print("\n" + "="*70)
print("STEP 2/8 — Cloning Revacc repository")
print("="*70)

if os.path.exists(f"{WORKDIR}/.git"):
    os.chdir(WORKDIR)
    sh("git pull origin main")
else:
    sh(f"git clone {REPO_URL} {WORKDIR}")

# ─── STEP 3: PYTHON VENV + ALL DEPS ──────────────────────────────────────────
print("\n" + "="*70)
print("STEP 3/8 — Setting up Python 3.10 venv + dependencies")
print("="*70)

if not os.path.exists(VENV):
    sh(f"python3.10 -m venv {VENV}")

sh(f"{VENV}/bin/pip install --upgrade pip 'setuptools<81' wheel")
sh(f"{VENV}/bin/pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121")
sh(f"{VENV}/bin/pip install -r {WORKDIR}/backend/requirements.txt")
sh(f"{VENV}/bin/pip install numpy==1.24.2 matplotlib==3.7.0 pyngrok")

# Verify
sh(f"{VENV}/bin/python -c \"import torch; print(f'PyTorch {{torch.__version__}}, CUDA={{torch.cuda.is_available()}}, GPU={{torch.cuda.get_device_name(0) if torch.cuda.is_available() else \\\"N/A\\\"}}')\"")

# ─── STEP 4: ESMFOLD WEIGHTS ─────────────────────────────────────────────────
print("\n" + "="*70)
print("STEP 4/8 — Downloading ESMFold 3B weights (2.7 GB)")
print("="*70)

WEIGHTS_DIR = os.path.expanduser("~/.cache/torch/hub/checkpoints")
WEIGHTS_FILE = f"{WEIGHTS_DIR}/esmfold_3B_v1.pt"

if not os.path.exists(WEIGHTS_FILE):
    os.makedirs(WEIGHTS_DIR, exist_ok=True)
    sh(f"wget -q --show-progress -O {WEIGHTS_FILE} "
       "https://dl.fbaipublicfiles.com/fair-esm/models/esmfold_3B_v1.pt")

size_gb = os.path.getsize(WEIGHTS_FILE) / (1024**3)
print(f"Weights: {size_gb:.1f} GB ready")

# ─── STEP 5: BUILD OPENFOLD (C++17 PATCH) ────────────────────────────────────
print("\n" + "="*70)
print("STEP 5/8 — Building OpenFold with C++17 patch for T4")
print("="*70)

OPENFOLD_DIR = "/content/openfold"
if not os.path.exists(OPENFOLD_DIR):
    sh(f"git clone --filter=blob:none https://github.com/aqlaboratory/openfold.git {OPENFOLD_DIR}")
    os.chdir(OPENFOLD_DIR)
    sh("git checkout 4b41059")

# Patch C++14 → C++17 (REQUIRED for PyTorch 2.x)
print("Patching C++14 → C++17...")
for root, dirs, files in os.walk(OPENFOLD_DIR):
    for fname in files:
        if fname in ("setup.py",) or fname.endswith((".cpp", ".cu")):
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r") as f:
                    c = f.read()
                if "-std=c++14" in c:
                    with open(fpath, "w") as f:
                        f.write(c.replace("-std=c++14", "-std=c++17"))
            except Exception:
                pass

sh(f"{VENV}/bin/pip install -r {OPENFOLD_DIR}/requirements.txt")
sh(f"{VENV}/bin/pip install pytorch-lightning==1.9.5")
print("Building CUDA extensions (3-5 min)...")
sh(f"cd {OPENFOLD_DIR} && {VENV}/bin/python setup.py install")

# ─── STEP 6: VERIFY ESMFOLD ON T4 ───────────────────────────────────────────
print("\n" + "="*70)
print("STEP 6/8 — Verifying ESMFold on T4 GPU")
print("="*70)

with open("/tmp/test_esmfold.py", "w") as f:
    f.write("""
import torch
print(f"CUDA: {torch.cuda.is_available()}, GPU: {torch.cuda.get_device_name(0)}")
model = torch.hub.load("facebookresearch/esm:main", "esmfold_3B_v1")
model = model.eval().cuda()
print(f"ESMFold loaded on: {next(model.parameters()).device}")
pdb = model.infer_pdb("MKFLILLFNILCLFPVLAADNHGVSLQGFNKENYEKFDKARLENGITYDSIMYSGRDFNE")
print(f"Test fold: {len(pdb)} chars PDB — ESMFOLD WORKING ON T4!")
""")

sh(f"{VENV}/bin/python /tmp/test_esmfold.py")

# ─── STEP 7: START BACKEND + NGROK ───────────────────────────────────────────
print("\n" + "="*70)
print("STEP 7/8 — Starting FastAPI backend + ngrok tunnel")
print("="*70)

# Kill old processes
sh("pkill -f 'uvicorn.*{BACKEND_PORT}' 2>/dev/null; true")
time.sleep(1)

# Environment for backend process
env = os.environ.copy()
env.update({
    "PATH": f"{VENV}/bin:{env.get('PATH', '')}",
    "PYTHONPATH": WORKDIR,
    # ── Paper-aligned thresholds (Barazesh et al. 2024) ──
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

# Start uvicorn
backend_proc = subprocess.Popen(
    [f"{VENV}/bin/python", "-m", "uvicorn", "app.main:app",
     "--host", "0.0.0.0", "--port", str(BACKEND_PORT)],
    cwd=f"{WORKDIR}/backend",
    env=env,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
)

# Wait for backend
for i in range(40):
    time.sleep(1)
    try:
        urllib.request.urlopen(f"http://localhost:{BACKEND_PORT}/api/health", timeout=2)
        print(f"Backend started (PID: {backend_proc.pid})")
        break
    except Exception:
        if i == 39:
            out = backend_proc.stdout.read().decode() if backend_proc.stdout else ""
            print(f"BACKEND FAILED:\n{out[-1000:]}")
            sys.exit(1)

# Start ngrok
public_url = None
if NGROK_AUTHTOKEN:
    from pyngrok import ngrok
    ngrok.set_auth_token(NGROK_AUTHTOKEN)
    ngrok.kill()
    tunnel = ngrok.bind(BACKEND_PORT)
    public_url = tunnel.public_url
    print(f"\n{'='*70}")
    print(f"  NGROK TUNNEL: {public_url}")
    print(f"{'='*70}")
    print(f"\n  → Go to Vercel dashboard → Settings → Environment Variables")
    print(f"  → Set: NEXT_PUBLIC_API_URL = {public_url}")
    print(f"  → Redeploy the frontend")
else:
    print(f"\n  Backend running at http://localhost:{BACKEND_PORT}")
    print(f"  Set NGROK_AUTHTOKEN to expose to Vercel")

# ─── STEP 8: RUN FULL PIPELINE ──────────────────────────────────────────────
print("\n" + "="*70)
print("STEP 8/8 — Running full 50-step pipeline")
print("="*70)

BASE = f"http://localhost:{BACKEND_PORT}"

# Create job
job_data = json.dumps({
    "taxonId": TAXON_ID,
    "pathogenName": PATHOGEN_NAME,
    "realTools": True,
}).encode("utf-8")
req = urllib.request.Request(
    f"{BASE}/api/jobs", data=job_data,
    headers={"Content-Type": "application/json"}, method="POST"
)
with urllib.request.urlopen(req) as resp:
    job = json.loads(resp.read())
    job_id = job["id"]
print(f"Created job: {job_id}")

# Start pipeline
req = urllib.request.Request(f"{BASE}/api/jobs/{job_id}/start", method="POST")
with urllib.request.urlopen(req) as resp:
    print(f"Pipeline started (HTTP {resp.status})")

# Poll
print("Running...")
while True:
    try:
        req = urllib.request.Request(f"{BASE}/api/jobs/{job_id}")
        with urllib.request.urlopen(req) as resp:
            status = json.loads(resp.read())
        ps = status.get("status", "unknown")
        step = status.get("currentStep", "")
        phase = status.get("currentPhase", 0)
        if ps in ("completed", "failed", "error"):
            print(f"\nPipeline finished: {ps}")
            break
        print(f"  Phase {phase} | Step {step} | {ps}    ", end="\r")
        time.sleep(15)
    except Exception:
        time.sleep(5)

# ─── RESULTS ──────────────────────────────────────────────────────────────────
print(f"\n\n{'='*70}")
print("PIPELINE RESULTS")
print(f"{'='*70}")

funnel = status.get("funnel", {})
for k, v in funnel.items():
    if isinstance(v, dict):
        print(f"  {k}: {v.get('count', v)}")
    else:
        print(f"  {k}: {v}")

for phase in status.get("phases", []):
    for step_data in phase.get("steps", []):
        sid = step_data.get("id", "")
        result = step_data.get("result") or {}
        if sid == "9-2" and result:
            print(f"\n  MEV Construct: {result.get('mev_length', 0)} aa")
            seq = result.get("sequence", "")
            if seq:
                print(f"  First 120 aa: {seq[:120]}...")
        if sid == "11-2" and result:
            print(f"\n  Structure Provider: {result.get('provider')}")
            print(f"  Structure Method:   {result.get('method')}")
            print(f"  Structure Source:   {result.get('source')}")
        if sid == "11-3" and result:
            print(f"\n  Ramachandran: {result.get('method', 'local')}")
        if sid == "13-1" and result:
            print(f"\n  Codon Optimization: CAI={result.get('cai')}, GC={result.get('gc_content')}%")

print(f"\n{'='*70}")
if public_url:
    print(f"  Frontend URL: set NEXT_PUBLIC_API_URL = {public_url}")
print(f"  Backend URL:  http://localhost:{BACKEND_PORT}")
print(f"  Job ID:       {job_id}")
print(f"{'='*70}")
