# Revacc Pipeline - Quick Start

> **Prerequisite**: Docker (macOS/Windows: Docker Desktop, Linux: Docker Engine).
> Nothing else — no database, no Node.js, no Python needed.

## Option 1: One Command (Easiest)

### macOS/Linux
```bash
curl -fsSL https://raw.githubusercontent.com/umeshdahiya15/Revacc/main/start-revacc.sh | bash
```

### Windows (PowerShell)
```powershell
docker pull umeshdahiya01/revacc:latest
docker run -d --name revacc-pipeline -p 3000:3000 -p 8000:8000 umeshdahiya01/revacc:latest
```

---

## Option 2: Manual Docker Run (any OS)

```bash
# Download the image
docker pull umeshdahiya01/revacc:latest

# Start it (frontend :3000, backend :8000)
docker run -d --name revacc-pipeline -p 3000:3000 -p 8000:8000 umeshdahiya01/revacc:latest

# Verify (wait ~10 seconds after start)
curl http://localhost:8000/api/health   # → {"status":"ok",...}
```

---

## Option 3: Clone Repository (optional)

Requires Docker Compose (bundled with Docker Desktop; on Linux see START.md):

```bash
git clone https://github.com/umeshdahiya15/Revacc.git
cd Revacc
docker compose up -d
```

---

## What's Running

| Service | URL | Description |
|---------|-----|-------------|
| Frontend | http://localhost:3000 | Web dashboard |
| Backend | http://localhost:8000 | Backend API |
| API Docs | http://localhost:8000/docs | Interactive API docs |

---

## Create Your First Job

### Via Web UI
1. Open http://localhost:3000
2. Click "Create Job"
3. Enter pathogen name and taxonomy ID (e.g., 208435 for *S. agalactiae*)
4. Click "Start Pipeline"

### Via API
```bash
# Create job
curl -X POST http://localhost:8000/api/jobs \
  -H "Content-Type: application/json" \
  -d '{"name": "Test", "taxonId": 208435, "source": "pathogen"}'

# Start pipeline (replace {job_id} with actual ID)
curl -X POST http://localhost:8000/api/jobs/{job_id}/start
```

A full run takes roughly **45–90 minutes** (steps 5-1/6-1 call the IEDB web
service; everything else runs locally in the container).

---

## Useful Commands

```bash
# Check status
docker ps --filter name=revacc-pipeline

# View logs
docker logs -f revacc-pipeline

# Restart
docker restart revacc-pipeline

# Stop and remove (image stays downloaded for the next run)
docker rm -f revacc-pipeline
```

<details>
<summary>Compose equivalents (if you used Option 3)</summary>

```bash
docker compose ps
docker compose logs -f
docker compose down
```
</details>

---

## Need Help?

See [START.md](START.md) for the complete setup guide and troubleshooting.
