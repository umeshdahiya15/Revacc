#!/usr/bin/env python3
"""
Revacc T4 Colab Master Script
==============================
Single script to set up and run the complete reverse-vaccinology backend
on a Google Colab T4 GPU. Connects to Vercel frontend via ngrok.

Usage:
  1. Open Google Colab → Runtime → Change runtime type → T4 GPU
  2. Paste this entire script into a code cell
  3. Set NGROK_AUTHTOKEN below (get from https://dashboard.ngrok.com)
  4. Run the cell

Architecture:
  - T4 GPU = backend (FastAPI + all tools + local ESMFold)
  - Vercel = frontend (Next.js, controlled by user)
  - ngrok = tunnel connecting frontend to T4 backend

Paper: Barazesh et al. 2024, Nature Scientific Reports
All thresholds match the paper exactly.
"""
from __future__ import annotations

import os
import sys
import subprocess
import time
import json
import logging
import shutil
import urllib.request
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("revacc-setup")

# ============================================================================
# CONFIGURATION — Edit these values
# ============================================================================

NGROK_AUTHTOKEN = ""  #@param {type:"string"}  # Get from https://dashboard.ngrok.com
BACKEND_PORT = 8000
REPO_URL = "https://github.com/umeshdahiya15/Revacc.git"
WORKDIR = "/content/Revacc"
VENV_DIR = "/content/esmfold-env"
PYTHON_VERSION = "3.10"

# ============================================================================
# PAPER-ALIGNED THRESHOLDS (Barazesh et al. 2024)
# These are set as environment variables so the backend uses them.
# ============================================================================

PAPER_ENV = {
    # CD-HIT redundancy removal (Phase 1-2)
    "CDHIT_THRESHOLD": "0.80",

    # DEG essentiality (Phase 2-1) — identity threshold
    "DEG_IDENTITY_THRESHOLD": "20",
    "DEG_EVALUE_THRESHOLD": "1e-5",
    "DEG_SCOPE": "all",

    # AlgPred allergenicity (Phase 3-1)
    "ALGPRED_THRESHOLD": "0.321",

    # VaxiJen antigenicity (Phase 3-2)
    "VAXIJEN_THRESHOLD": "0.50",

    # VFDB virulence factors (Phase 3-3)
    "VFDB_EVALUE_THRESHOLD": "1e-4",
    "VFDB_BITSCORE_THRESHOLD": "100",
    "VFDB_IDENTITY_THRESHOLD": "30",

    # Human homology (Phase 3-4)
    "HUMAN_HOMOLOGY_IDENTITY": "30",
    "HOMOLOGY_EVALUE": "1e-4",

    # IEDB MHC-I CTL epitopes (Phase 5-1) — consensus 12-mer
    "MHC1_PERCENTILE": "2.0",
    "MHC1_LENGTHS": "12",
    "MHC1_METHOD": "consensus",

    # IEDB MHC-II HTL epitopes (Phase 6-1) — consensus 15-mer
    "MHC2_PERCENTILE": "2.0",
    "MHC2_LENGTHS": "15",
    "MHC2_METHOD": "consensus",

    # Cytokine thresholds (Phases 6-3, 6-4)
    "IL4_THRESHOLD": "0.2",
    "IL10_THRESHOLD": "-0.3",

    # B-cell epitopes (Phase 7-1)
    "BCELL_THRESHOLD": "0.5",
    "BCELL_WINDOW": "7",

    # ToxinPred (Phases 5-4, 6-7, 7-4)
    "TOXINPRED_THRESHOLD": "0.40",

    # MEV assembly caps (Phase 9-2) — paper composition 8/8/5
    "MEV_CTL_CAP": "8",
    "MEV_HTL_CAP": "8",
    "MEV_BCELL_CAP": "5",

    # Structure provider
    "MEV_STRUCTURE_PROVIDER": "esmfold",

    # CORS — allow Vercel frontend
    "MEV_CORS_ORIGINS": "*",

    # Pipeline speed
    "MEV_STEP_TICK_MS": "300",
    "MEV_MAX_STEPS_PER_TICK": "1",

    # BLAST database cache
    "MEV_BLAST_DB_CACHE": "/content/blast_dbs",
    "MEV_VFDB_CACHE": "/content/blast_dbs/vfdb",

    # Phobius settings
    "MEV_PHOBIUS_CAP": "0",

    # ESMFold settings
    "ESMFOLD_TIMEOUT": "600",
}


# ============================================================================
# STEP 1: System dependencies
# ============================================================================
def install_system_deps():
    """Install BLAST+, Python 3.10, and other system packages."""
    log.info("=== Installing system dependencies ===")

    # Update apt
    subprocess.run(["apt-get", "update", "-qq"], check=True)

    # Install BLAST+ and Python 3.10
    subprocess.run([
        "apt-get", "install", "-y", "-qq",
        "blast+", "ncbi-blast+",
        "python3.10", "python3.10-venv", "python3.10-dev", "python3.10-distutils",
        "git", "wget", "curl",
    ], check=True)

    # Verify BLAST+ is available
    blastp = shutil.which("blastp")
    makeblastdb = shutil.which("makeblastdb")
    if not blastp or not makeblastdb:
        raise RuntimeError(f"BLAST+ not found: blastp={blastp}, makeblastdb={makeblastdb}")
    log.info("BLAST+ found: blastp=%s, makeblastdb=%s", blastp, makeblastdb)

    # Verify Python 3.10
    result = subprocess.run(["python3.10", "--version"], capture_output=True, text=True)
    log.info("Python: %s", result.stdout.strip())


# ============================================================================
# STEP 2: Clone repository
# ============================================================================
def clone_repo():
    """Clone or update the Revacc repository."""
    log.info("=== Cloning repository ===")

    if os.path.exists(os.path.join(WORKDIR, ".git")):
        log.info("Repository exists, pulling latest...")
        subprocess.run(["git", "pull", "origin", "main"], cwd=WORKDIR, check=True)
    else:
        subprocess.run(["git", "clone", REPO_URL, WORKDIR], check=True)
        log.info("Cloned to %s", WORKDIR)


# ============================================================================
# STEP 3: Python virtual environment
# ============================================================================
def setup_venv():
    """Create Python 3.10 venv and install all dependencies."""
    log.info("=== Setting up Python 3.10 venv ===")

    # Create venv
    if not os.path.exists(VENV_DIR):
        subprocess.run(["python3.10", "-m", "venv", VENV_DIR], check=True)

    pip = os.path.join(VENV_DIR, "bin", "pip")

    # Upgrade pip/setuptools/wheel
    subprocess.run([pip, "install", "--upgrade", "pip", "setuptools<81", "wheel"], check=True)

    # Install PyTorch with CUDA for T4
    log.info("Installing PyTorch with CUDA...")
    subprocess.run([
        pip, "install", "torch", "torchvision",
        "--index-url", "https://download.pytorch.org/whl/cu121"
    ], check=True)

    # Install backend requirements
    log.info("Installing backend requirements...")
    req_file = os.path.join(WORKDIR, "backend", "requirements.txt")
    subprocess.run([pip, "install", "-r", req_file], check=True)

    # Install additional dependencies for IEDB population coverage
    subprocess.run([pip, "install", "numpy==1.24.2", "matplotlib==3.7.0"], check=True)

    # Install pyngrok for tunnel
    subprocess.run([pip, "install", "pyngrok"], check=True)

    # Verify key packages
    result = subprocess.run([
        os.path.join(VENV_DIR, "bin", "python"), "-c",
        "import torch; print(f'PyTorch: {torch.__version__}, CUDA: {torch.cuda.is_available()}, GPU: {torch.cuda.get_device_name(0) if torch.cuda.is_available() else \"N/A\"}')"
    ], capture_output=True, text=True)
    log.info(result.stdout.strip())
    if result.returncode != 0:
        log.warning("PyTorch check failed: %s", result.stderr.strip())


# ============================================================================
# STEP 4: Build OpenFold + ESMFold on T4
# ============================================================================
def setup_esmfold():
    """Build OpenFold with C++17 patch and download ESMFold weights."""
    log.info("=== Setting up ESMFold on T4 ===")

    venv_python = os.path.join(VENV_DIR, "bin", "python")
    venv_pip = os.path.join(VENV_DIR, "bin", "pip")

    # Check if ESMFold weights already exist
    weights_dir = os.path.expanduser("~/.cache/torch/hub/checkpoints")
    weights_file = os.path.join(weights_dir, "esmfold_3B_v1.pt")

    if not os.path.exists(weights_file):
        log.info("Downloading ESMFold 3B weights (2.7 GB)...")
        os.makedirs(weights_dir, exist_ok=True)
        subprocess.run([
            "wget", "-q", "--show-progress",
            "-O", weights_file,
            "https://dl.fbaipublicfiles.com/fair-esm/models/esmfold_3B_v1.pt"
        ], check=True)
        log.info("Weights downloaded: %s (%.1f GB)", weights_file,
                 os.path.getsize(weights_file) / (1024**3))
    else:
        log.info("ESMFold weights already present: %.1f GB",
                 os.path.getsize(weights_file) / (1024**3))

    # Clone OpenFold for CUDA extensions (needed by torch.hub ESMFold)
    openfold_dir = "/content/openfold"
    if not os.path.exists(openfold_dir):
        log.info("Cloning OpenFold (with blob filter for old commits)...")
        subprocess.run([
            "git", "clone", "--filter=blob:none",
            "https://github.com/aqlaboratory/openfold.git", openfold_dir
        ], check=True)
        subprocess.run(["git", "checkout", "4b41059"], cwd=openfold_dir, check=True)

    # CRITICAL: Patch C++14 -> C++17 for PyTorch 2.x compatibility
    log.info("Patching OpenFold C++14 -> C++17...")
    setup_py = os.path.join(openfold_dir, "setup.py")
    if os.path.exists(setup_py):
        with open(setup_py, "r") as f:
            content = f.read()
        content = content.replace("-std=c++14", "-std=c++17")
        with open(setup_py, "w") as f:
            f.write(content)

    # Patch all .cpp files
    for root, dirs, files in os.walk(openfold_dir):
        for fname in files:
            if fname.endswith(".cpp") or fname.endswith(".cu"):
                fpath = os.path.join(root, fname)
                try:
                    with open(fpath, "r") as f:
                        c = f.read()
                    if "-std=c++14" in c:
                        c = c.replace("-std=c++14", "-std=c++17")
                        with open(fpath, "w") as f:
                            f.write(c)
                except Exception:
                    pass

    # Install OpenFold dependencies
    log.info("Installing OpenFold requirements...")
    openfold_req = os.path.join(openfold_dir, "requirements.txt")
    if os.path.exists(openfold_req):
        subprocess.run([venv_pip, "install", "-r", openfold_req], check=True)

    # Install older PyTorch Lightning (required by this OpenFold commit)
    subprocess.run([venv_pip, "install", "pytorch-lightning==1.9.5"], check=True)

    # Build OpenFold CUDA extensions
    log.info("Building OpenFold CUDA extensions (this takes a few minutes)...")
    result = subprocess.run(
        [venv_python, "setup.py", "install"],
        cwd=openfold_dir,
        capture_output=True, text=True,
        timeout=600,
    )
    if result.returncode != 0:
        log.warning("OpenFold build output (last 20 lines): %s",
                     "\n".join(result.stdout.split("\n")[-20:]))
        log.warning("OpenFold build errors: %s",
                     "\n".join(result.stderr.split("\n")[-20:]))
        # Continue — torch.hub may still work without OpenFold CUDA extensions

    # Test ESMFold on T4
    log.info("Testing ESMFold inference on T4...")
    test_script = '''
import torch
print(f"CUDA available: {torch.cuda.is_available()}")
print(f"GPU: {torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'N/A'}")

print("Loading ESMFold model via torch.hub...")
model = torch.hub.load("facebookresearch/esm:main", "esmfold_3B_v1")
model = model.eval().cuda()
print(f"ESMFold loaded on: {next(model.parameters()).device}")

# Quick test fold
test_seq = "MKFLILLFNILCLFPVLAADNHGVSLQGFNKENYEKFDKARLENGITYDSIMYSGRDFNE"
with torch.no_grad():
    output = model.infer_pdb(test_seq)
print(f"Test fold complete: {len(output)} chars PDB")
print("ESMFOLD WORKING ON T4!")
'''
    test_file = "/tmp/test_esmfold.py"
    with open(test_file, "w") as f:
        f.write(test_script)

    result = subprocess.run(
        [venv_python, test_file],
        capture_output=True, text=True,
        timeout=300,
    )
    log.info(result.stdout)
    if result.returncode != 0:
        log.error("ESMFold test failed: %s", result.stderr[-500:])
        raise RuntimeError("ESMFold failed to load on T4")
    log.info("ESMFold verified working on T4!")


# ============================================================================
# STEP 5: Set up IEDB population coverage tool
# ============================================================================
def setup_iedb_tools():
    """Set up the IEDB Population Coverage standalone tool."""
    log.info("=== Setting up IEDB tools ===")

    iedb_dir = os.path.join(WORKDIR, "backend", ".iedb_tools")
    popcov_script = os.path.join(iedb_dir, "population_coverage", "calculate_population_coverage.py")

    if os.path.exists(popcov_script):
        log.info("IEDB population coverage tool found.")
    else:
        # Try to extract from tarball if present
        tarball = os.path.join(iedb_dir, "pop.tar.gz")
        if os.path.exists(tarball):
            log.info("Extracting IEDB population coverage tool...")
            import tarfile
            with tarfile.open(tarball, "r:gz") as tar:
                tar.extractall(path=iedb_dir)
            log.info("IEDB tool extracted.")
        else:
            log.warning("IEDB population coverage tool not found. "
                       "Population coverage (Phase 8-1) will use local fallback.")


# ============================================================================
# STEP 6: Set environment variables
# ============================================================================
def configure_env():
    """Set all paper-aligned environment variables."""
    log.info("=== Configuring environment ===")

    for key, value in PAPER_ENV.items():
        os.environ[key] = value
        log.info("  %s=%s", key, value)

    # Ensure BLAST DB cache directory exists
    blast_cache = PAPER_ENV["MEV_BLAST_DB_CACHE"]
    os.makedirs(blast_cache, exist_ok=True)
    log.info("BLAST DB cache: %s", blast_cache)


# ============================================================================
# STEP 7: Start backend server
# ============================================================================
def start_backend():
    """Start the FastAPI backend server."""
    log.info("=== Starting backend server ===")

    venv_python = os.path.join(VENV_DIR, "bin", "python")
    backend_dir = os.path.join(WORKDIR, "backend")

    # Set environment for the subprocess
    env = os.environ.copy()
    env["PATH"] = f"{VENV_DIR}/bin:{env.get('PATH', '')}"
    env["PYTHONPATH"] = WORKDIR

    # Kill any existing backend
    subprocess.run(["pkill", "-f", f"uvicorn.*{BACKEND_PORT}"], capture_output=True)
    time.sleep(1)

    # Start backend
    proc = subprocess.Popen(
        [venv_python, "-m", "uvicorn", "app.main:app",
         "--host", "0.0.0.0", "--port", str(BACKEND_PORT)],
        cwd=backend_dir,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    # Wait for backend to start
    log.info("Waiting for backend to start...")
    for i in range(30):
        time.sleep(1)
        if proc.poll() is not None:
            out = proc.stdout.read().decode() if proc.stdout else ""
            raise RuntimeError(f"Backend failed to start:\n{out[-1000:]}")
        try:
            import urllib.request
            urllib.request.urlopen(f"http://localhost:{BACKEND_PORT}/api/health", timeout=2)
            log.info("Backend started successfully (PID: %d)", proc.pid)
            return proc
        except Exception:
            continue

    raise RuntimeError("Backend did not start within 30 seconds")


# ============================================================================
# STEP 8: Start ngrok tunnel
# ============================================================================
def start_ngrok():
    """Start ngrok tunnel to expose backend to the internet."""
    log.info("=== Starting ngrok tunnel ===")

    if not NGROK_AUTHTOKEN:
        log.warning("No NGROK_AUTHTOKEN set. Backend accessible at http://localhost:%d", BACKEND_PORT)
        log.warning("To expose to Vercel, set NGROK_AUTHTOKEN and restart.")
        return None

    from pyngrok import ngrok, conf
    ngrok.set_auth_token(NGROK_AUTHTOKEN)

    # Kill existing tunnels
    ngrok.kill()

    # Start tunnel
    tunnel = ngrok.bind(BACKEND_PORT)
    public_url = tunnel.public_url

    log.info("=" * 60)
    log.info("BACKEND TUNNEL: %s", public_url)
    log.info("=" * 60)
    log.info("")
    log.info("To connect Vercel frontend:")
    log.info("  1. Go to Vercel dashboard → your Revacc project → Settings → Environment Variables")
    log.info("  2. Set NEXT_PUBLIC_API_URL = %s", public_url)
    log.info("  3. Redeploy the frontend")
    log.info("")

    return public_url


# ============================================================================
# STEP 9: Run the full pipeline
# ============================================================================
def run_pipeline(job_id: str = None):
    """Run the full 50-step pipeline on the T4 backend."""
    import urllib.request
    import urllib.parse

    log.info("=== Running full pipeline ===")
    base_url = f"http://localhost:{BACKEND_PORT}"

    # Create job if not provided
    if not job_id:
        job_data = json.dumps({
            "taxonId": "99287",
            "pathogenName": "Streptococcus agalactiae",
            "realTools": True,
        }).encode("utf-8")

        req = urllib.request.Request(
            f"{base_url}/api/jobs",
            data=job_data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            job = json.loads(resp.read())
            job_id = job["id"]
            log.info("Created job: %s", job_id)

    # Start pipeline
    req = urllib.request.Request(
        f"{base_url}/api/jobs/{job_id}/start",
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        log.info("Pipeline started: %s", resp.status)

    # Poll until complete
    log.info("Polling pipeline progress...")
    while True:
        try:
            req = urllib.request.Request(f"{base_url}/api/jobs/{job_id}")
            with urllib.request.urlopen(req) as resp:
                status = json.loads(resp.read())

            pipeline_status = status.get("status", "unknown")
            current_step = status.get("currentStep", "")
            current_phase = status.get("currentPhase", 0)

            if pipeline_status in ("completed", "failed", "error"):
                log.info("Pipeline %s!", pipeline_status)
                break

            log.info("Phase %s, Step %s [%s]", current_phase, current_step, pipeline_status)
            time.sleep(15)

        except Exception as e:
            log.warning("Poll error: %s", e)
            time.sleep(5)

    # Print summary
    log.info("\n" + "=" * 60)
    log.info("PIPELINE COMPLETE")
    log.info("=" * 60)

    funnel = status.get("funnel", {})
    if funnel:
        log.info("Funnel summary:")
        for key, value in funnel.items():
            if isinstance(value, dict):
                log.info("  %s: %s", key, value.get("count", value))
            else:
                log.info("  %s: %s", key, value)

    # Find MEV construct
    for phase in status.get("phases", []):
        for step in phase.get("steps", []):
            if step.get("id") == "9-2" and step.get("result"):
                result = step["result"]
                mev_seq = result.get("sequence", "")
                mev_len = result.get("mev_length", 0)
                log.info("\nMEV Construct: %d aa", mev_len)
                log.info("Sequence: %s...", mev_seq[:100] if mev_seq else "N/A")

            if step.get("id") == "11-2" and step.get("result"):
                result = step["result"]
                log.info("\nStep 11-2 (Structure):")
                log.info("  Provider: %s", result.get("provider"))
                log.info("  Method: %s", result.get("method"))
                log.info("  Source: %s", result.get("source"))

    return job_id, status


# ============================================================================
# MAIN
# ============================================================================
if __name__ == "__main__":
    log.info("=" * 60)
    log.info("REVACC T4 COLAB MASTER SCRIPT")
    log.info("Paper: Barazesh et al. 2024, Sci Reports")
    log.info("=" * 60)

    # Run setup steps
    install_system_deps()
    clone_repo()
    setup_venv()
    setup_esmfold()
    setup_iedb_tools()
    configure_env()

    # Start services
    backend_proc = start_backend()
    public_url = start_ngrok()

    # Summary
    log.info("")
    log.info("=" * 60)
    log.info("SETUP COMPLETE")
    log.info("=" * 60)
    log.info("Backend: http://localhost:%d", BACKEND_PORT)
    if public_url:
        log.info("Tunnel:  %s", public_url)
        log.info("")
        log.info("NEXT_PUBLIC_API_URL=%s", public_url)
    log.info("")
    log.info("To run the pipeline, call:")
    log.info("  job_id, status = run_pipeline()")
    log.info("")
    log.info("Or run manually:")
    log.info("  import urllib.request")
    log.info("  urllib.request.urlopen('http://localhost:%d/api/jobs', method='POST')", BACKEND_PORT)

    # Keep alive
    try:
        log.info("\nBackend running. Press Ctrl+C to stop.")
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        log.info("Shutting down...")
        if backend_proc:
            backend_proc.terminate()
