# Revacc Pipeline - Quick Start

## Option 1: Docker Download (Easiest)

### macOS/Linux
```bash
# Download and run
curl -fsSL https://raw.githubusercontent.com/umeshdahiya15/Revacc/main/start-revacc.sh | bash
```

### Windows (PowerShell)
```powershell
# Clone and run
git clone https://github.com/umeshdahiya15/Revacc.git
cd Revacc
docker compose up -d
```

---

## Option 2: Manual Docker Setup

### 1. Clone & Enter Directory
```bash
git clone https://github.com/umeshdahiya15/Revacc.git
cd Revacc
```

### 2. Start with Docker
```bash
docker compose up -d
```

### 3. Access the Pipeline
- **Frontend**: http://localhost:3000
- **Backend**: http://localhost:8000
- **API Docs**: http://localhost:8000/docs

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

---

## Useful Commands

```bash
# Check status
docker compose ps

# View logs
docker compose logs -f

# Stop services
docker compose down

# Fresh start (remove data)
docker compose down -v
```

---

## Need Help?

See [START.md](START.md) for complete setup guide.
