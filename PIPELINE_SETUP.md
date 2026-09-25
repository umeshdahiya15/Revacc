# Revacc Pipeline — Detailed Setup Guide (Docker)

The detailed companion to [START.md](START.md): what's inside the image, how to
start it three different ways, configuration, API reference and troubleshooting.

> **Quick path?** Use [START.md](START.md) (one-liner) or [QUICKSTART.md](QUICKSTART.md).

---

## 1. Prerequisites

- **Docker** 20.10+ — Docker Desktop (macOS/Windows) or Docker Engine (Linux)
- **RAM**: 8 GB recommended while a pipeline is running
- **Disk**: ~6 GB for the image (tools + baked reference databases)
- **Git + Docker Compose**: only for the clone-and-run / build-from-source
  methods. Docker Desktop ships Compose; on Linux see the install note in
  `START.md`. The prebuilt-image methods need nothing but Docker.

No Python, Node.js, PostgreSQL or Redis to install — everything runs inside the
single container.

---

## 2. Starting the Pipeline

### Method 1 — Prebuilt image (recommended)

```bash
docker pull umeshdahiya01/revacc:latest
docker run -d --name revacc-pipeline -p 3000:3000 -p 8000:8000 umeshdahiya01/revacc:latest

# Verify (~10 s after start)
curl http://localhost:8000/api/health     # → {"status":"ok",...}
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:3000   # → 200
```

### Method 2 — Clone + Docker Compose

```bash
git clone https://github.com/umeshdahiya15/Revacc.git
cd Revacc
docker compose up -d
```

`docker-compose.yml` starts the same single `revacc` service with ports 3000
and 8000 mapped.

### Method 3 — Build the image from source

```bash
git clone https://github.com/umeshdahiya15/Revacc.git
cd Revacc

# Either with plain Docker:
docker build -t revacc:local .
docker run -d --name revacc-pipeline -p 3000:3000 -p 8000:8000 revacc:local

# Or with Compose (build + run):
docker compose up -d --build
```

The build installs BLAST+, the complete PSORTb runtime (perl 5.22, BioPerl,
PSORTb modules, `pfscan`, legacy `blastall`) and bakes the DEG10, human and
VFDB BLAST databases via `app/tools/prefetch_dbs.py`. A smoke-test layer runs
PSORTb on real sequences during the build and **fails the build** if results
regress, so a successful build guarantees a working PSORTb.

---

## 3. Access Points

| Service | URL | Notes |
|---------|-----|-------|
| **Frontend** | http://localhost:3000 | Next.js dashboard (job creation, live progress, export) |
| **Backend API** | http://localhost:8000 | FastAPI |
| **API docs** | http://localhost:8000/docs | Interactive Swagger UI |
| **Health** | http://localhost:8000/api/health | Returns `{"status":"ok",...}` |
| **WebSocket** | ws://localhost:8000/ws/pipeline/{job_id} | Live step updates |

---

## 4. What's Inside the Image

| Component | Details |
|-----------|---------|
| **Frontend** | Next.js 14 production build, served on port 3000 |
| **Backend** | FastAPI / Python 3.13, in-memory job store, port 8000 |
| **BLAST+** | System `blastp`, `makeblastdb` |
| **PSORTb 3.0** | Full offline runtime: perl 5.22 + BioPerl + PSORTb modules + static `pfscan` + legacy `blastall` and its NCBI lib closure; `psortb-adapter.sh` normalizes the CLI invocation |
| **Reference DBs** | `/opt/mev-blastdb` (DEG10, reviewed human proteome) and `/opt/mev-vfdb` (VFDB) baked at build time |
| **Clustering** | Local greedy identity clustering (cd-hit-equivalent algorithm in Python; a provenance note records when the native binary is absent) |
| **Structure tools** | ESMFold / AlphaFold DB clients with local fallbacks |

### Offline vs. internet

| Needs internet | Runs fully offline |
|----------------|--------------------|
| Phase 1: UniProt / NCBI proteome download | BLAST searches vs baked DEG10 / human / VFDB DBs |
| Steps 5-1, 6-1: IEDB MHC binding (tools.iedb.org) | PSORTb localization (2-2) |
| Step 2-4: EBI Phobius (bounded by a hard timeout; PSORTb positives retained regardless) | Clustering, VaxiJen, B-cell/T-cell epitope scoring, MEV assembly, population-coverage logic |
| Phase 11: AlphaFold DB / SwissModel lookups (local fallbacks keep the run moving) | C-ImmSim local ODE immune model, toxicity/allergenicity filters |

A degraded external service can pause a step — the UI's **Resume** / **Retry**
buttons continue the run when the service recovers (see START.md tips).

---

## 5. Using the Pipeline

### Via the Web UI
1. Open http://localhost:3000 → **Create Job**
2. Enter pathogen name and taxon ID (e.g. `208435` for *S. agalactiae*) and
   start the run
3. Watch live progress (50 steps across 14 phases; a full run takes ~45–90 min)
4. When finished: funnel, epitopes, MEV candidates; **Export report** downloads
   the PDF (generated in the browser)

### Via the API

```bash
# Create a job
curl -X POST http://localhost:8000/api/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "name": "My Pipeline Run",
    "pathogenName": "Streptococcus agalactiae",
    "taxonId": 208435,
    "source": "pathogen",
    "realTools": true
  }'

# Start it (use the returned id)
curl -X POST http://localhost:8000/api/jobs/{job_id}/start

# Status + progress
curl http://localhost:8000/api/jobs/{job_id}

# Results
curl http://localhost:8000/api/jobs/{job_id}/epitopes

# After a pause: resume the job, or retry/skip a single step
curl -X POST http://localhost:8000/api/jobs/{job_id}/resume
curl -X POST http://localhost:8000/api/jobs/{job_id}/steps/{step_id}/retry
```

---

## 6. Configuration

Set variables with `docker run -e NAME=value ...` or in the `environment:`
block of `docker-compose.yml`, then recreate the container.

### Common

| Variable | Default | Purpose |
|----------|---------|---------|
| `MEV_STEP_TICK_MS` | `650` | UI/tick pacing (lower = snappier) |
| `MEV_MAX_STEPS_PER_TICK` | `1` | Steps processed per tick |
| `MEV_CORS_ORIGINS` | *(local only)* | Extra allowed browser origins (comma-separated) |
| `NCBI_EMAIL` | example address | Contact for NCBI/UniProt requests |
| `NCBI_API_KEY` | *(empty)* | Optional NCBI rate-limit key |
| `EBI_EMAIL` | example address | Contact for EBI/Phobius requests |
| `MEV_STRUCTURE_PROVIDER` | `esmfold` | `esmfold` \| `alphafold_db` \| `swissmodel` (structure source for phase 11) |
| `MEV_PHOBIUS_CAP` | `0` | Cap on candidates sent to Phobius in 2-4 (`0` = full pool) |

### Advanced (threshold overrides)

`DEG_IDENTITY_THRESHOLD` (40), `DEG_EVALUE_THRESHOLD` (1e-5),
`VFDB_IDENTITY_THRESHOLD` (30), `VFDB_EVALUE_THRESHOLD` (1e-4),
`VFDB_BITSCORE_THRESHOLD` (100), `HUMAN_HOMOLOGY_IDENTITY` (0.30),
`VAXIJEN_THRESHOLD` (0.50), `ALGPRED_THRESHOLD` (0.321),
`BCELL_THRESHOLD` (0.5), `IFN_GAMMA_THRESHOLD` (0.45)

Cache directories (`MEV_BLAST_DB_CACHE`, `MEV_VFDB_CACHE`, `MEV_API_CACHE`)
default to the baked `/opt/...` locations inside the image; override only if
you mount your own databases.

> The repository's job store is **in-memory** — jobs survive a container
> restart only while the process keeps them in memory; removing the container
> starts clean. That is by design for local analysis (no database to install).

---

## 7. Common Commands

```bash
# Status
docker ps --filter name=revacc-pipeline     # or: docker compose ps

# Logs (follow)
docker logs -f revacc-pipeline              # or: docker compose logs -f

# Restart (recreates the API process; jobs in memory are lost)
docker restart revacc-pipeline

# Stop and remove (image stays for the next run)
docker rm -f revacc-pipeline                # or: docker compose down

# Fresh pull + start
docker pull umeshdahiya01/revacc:latest
docker run -d --name revacc-pipeline -p 3000:3000 -p 8000:8000 umeshdahiya01/revacc:latest

# Rebuild after source changes
docker compose up -d --build                # or: docker build -t revacc:local .
```

---

## 8. Pipeline Thresholds

Calibrated to Barazesh et al. 2024:

| Parameter | Value | Description |
|-----------|-------|-------------|
| CD-HIT Identity | 0.80 | Redundancy removal threshold |
| VaxiJen Antigenicity | 0.50 | Antigenicity cutoff |
| MHC-I Percentile | ≤ 2.0 | CTL epitope binding threshold |
| MHC-II Percentile | ≤ 2.0 | HTL epitope binding threshold |
| DEG Identity | 40% | Essential gene identification |
| VFDB Identity | ≥ 30% | Virulence factor detection |
| Human Homology | 30% | Self-antigen exclusion |

---

## 9. API Reference

```http
GET    /api/health                                   # Health check
GET    /api/activity                                 # Recent activity
GET    /api/jobs                                     # List jobs
POST   /api/jobs                                     # Create job
GET    /api/jobs/{id}                                # Job status, phases, results
POST   /api/jobs/{id}/start                          # Start pipeline
POST   /api/jobs/{id}/pause                          # Pause pipeline
POST   /api/jobs/{id}/resume                         # Resume after pause
POST   /api/jobs/{id}/stop                           # Stop pipeline
POST   /api/jobs/{id}/steps/{step}/retry             # Retry one step
POST   /api/jobs/{id}/steps/{step}/skip              # Skip one step
GET    /api/jobs/{id}/events                         # Event log
GET    /api/jobs/{id}/epitopes                       # Predicted epitopes
GET    /api/jobs/compare                             # Compare runs
GET    /api/jobs/{id}/structure/requirements         # Structure needs
POST   /api/jobs/{id}/structure                      # Provide a structure
```

WebSocket: `ws://localhost:8000/ws/pipeline/{job_id}` streams step updates.

> The PDF report is produced by the frontend (**Export report** button) —
> there is no `/report` endpoint.

---

## 10. Development Mode (contributors)

```bash
# Backend (hot reload)
cd backend
pip install -r requirements.txt
python3 -m uvicorn app.main:app --reload --port 8000

# Frontend (another terminal)
npm install
npm run dev        # → http://localhost:3000
```

Run tests:

```bash
cd backend && pytest      # 219 tests
npm test                  # frontend suite
```

---

## 11. Troubleshooting

### Port already in use
```bash
lsof -i :3000
lsof -i :8000
# Stop the conflicting process, or map different host ports:
docker run -d --name revacc-pipeline -p 3001:3000 -p 8001:8000 umeshdahiya01/revacc:latest
```

### Docker daemon not running
```bash
docker info            # if this fails: start Docker Desktop, or:
sudo systemctl start docker
```

### Container exits immediately
```bash
docker logs revacc-pipeline
```

### Backend not healthy yet
Wait ~10 seconds after start, then `curl http://localhost:8000/api/health`.
Still down? `docker logs revacc-pipeline`.

### A step paused during the run
Usually an external service (IEDB, EBI Phobius, UniProt) was unreachable or
rate-limited. Check `docker logs revacc-pipeline`, then use **Resume** (job) or
**Retry** (single step) in the UI once the service is back.

### Build fails (Method 3)
```bash
docker compose down
docker compose build --no-cache
# The build includes a PSORTb smoke test — read the failing layer's output.
```

### Want a clean slate
```bash
docker rm -f revacc-pipeline
docker run -d --name revacc-pipeline -p 3000:3000 -p 8000:8000 umeshdahiya01/revacc:latest
```

---

## 12. Support

- **Setup guide**: [START.md](START.md)
- **Repository**: https://github.com/umeshdahiya15/Revacc
- **Issues**: https://github.com/umeshdahiya15/Revacc/issues
- **Technical background**: [HANDOVER.md](HANDOVER.md)
