# Revacc Pipeline - Local Docker Setup Guide

A complete guide to set up and run the Revacc reverse-vaccinology pipeline locally using Docker.

> **Looking for quick setup?** See [START.md](START.md) for the fastest way to get running!

## Prerequisites

### System Requirements

- **Docker**: 20.10+ (Docker Desktop recommended)
- **Docker Compose**: 2.0+
- **Git**: For cloning the repository
- **8GB RAM**: Recommended for running all services

### Install Docker

#### macOS
1. Download Docker Desktop from [docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop/)
2. Install and start Docker Desktop
3. Verify installation: `docker --version`

#### Linux (Ubuntu/Debian)
```bash
# Update package index
sudo apt-get update

# Install Docker
sudo apt-get install docker.io docker-compose

# Add your user to docker group
sudo usermod -aG docker $USER

# Log out and log back in, then verify
docker --version
docker-compose --version
```

#### Windows
1. Download Docker Desktop from [docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop/)
2. Install with WSL 2 backend enabled
3. Verify installation: `docker --version`

---

## Quick Start (3 Steps)

### Step 1: Clone the Repository

```bash
git clone https://github.com/umeshdahiya15/Revacc.git
cd Revacc
```

### Step 2: Configure Environment

```bash
# Copy the example environment file
cp .env.example .env

# Edit .env if needed (optional for local development)
# The defaults work for local Docker setup
```

### Step 3: Start the Pipeline

```bash
# Build and start all services
docker-compose up -d

# Check if all services are running
docker-compose ps
```

**That's it!** The pipeline is now running locally.

> **Note:** First run may take 5-10 minutes to download and build the Docker image.

---

## Access the Pipeline

### Frontend (Web UI)
- **URL**: http://localhost
- **Description**: React-based dashboard for managing pipeline jobs

### Backend API
- **URL**: http://localhost:8000
- **API Docs**: http://localhost:8000/docs (Interactive Swagger UI)
- **Health Check**: http://localhost:8000/api/health

### Services Overview

| Service | Port | Description |
|---------|------|-------------|
| **Nginx** | 80 | Reverse proxy (entry point) |
| **API** | 8000 | FastAPI backend |
| **PostgreSQL** | 5432 | Database |
| **Redis** | 6379 | Cache & task queue |
| **Worker** | - | Celery background tasks |

---

## Using the Pipeline

### 1. Open the Web UI

Navigate to http://localhost in your browser.

### 2. Create a New Job

Click "Create Job" or use the API:

```bash
# Using curl
curl -X POST http://localhost/api/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "name": "My First Pipeline Run",
    "pathogenName": "Streptococcus agalactiae",
    "taxonId": 208435,
    "source": "pathogen",
    "realTools": true
  }'
```

### 3. Start the Pipeline

Click "Start" in the UI or use the API:

```bash
# Replace {job_id} with the actual job ID
curl -X POST http://localhost/api/jobs/{job_id}/start
```

### 4. Monitor Progress

- **Web UI**: Real-time progress bar and step details
- **WebSocket**: Connect to `ws://localhost/ws/pipeline/{job_id}`
- **API**: Check status with `GET /api/jobs/{job_id}`

### 5. View Results

Once complete, view results in the UI or via API:

```bash
# Get job details
curl http://localhost/api/jobs/{job_id}

# Get epitope predictions
curl http://localhost/api/jobs/{job_id}/epitopes

# Download PDF report
curl http://localhost/api/jobs/{job_id}/report/pdf --output report.pdf
```

---

## Docker Compose Services

### What's Included

```yaml
services:
  postgres:    # PostgreSQL 16 database
  redis:       # Redis 7 cache
  api:         # FastAPI backend (Python 3.13)
  worker:      # Celery background worker
  nginx:       # Nginx 1.27 reverse proxy
```

### Service Details

#### PostgreSQL (Database)
- **Image**: postgres:16-alpine
- **Port**: 5432
- **Credentials**: mev/mev
- **Data**: Persisted in `pg_data` volume

#### Redis (Cache)
- **Image**: redis:7-alpine
- **Port**: 6379
- **Usage**: Task queue and caching

#### API (Backend)
- **Build**: From repository Dockerfile
- **Port**: 8000 (internal)
- **Features**: FastAPI, BioPython, BLAST+

#### Worker (Task Queue)
- **Build**: Same Dockerfile as API
- **Command**: Celery worker
- **Usage**: Background pipeline processing

#### Nginx (Reverse Proxy)
- **Image**: nginx:1.27-alpine
- **Port**: 80 (external)
- **Routes**: `/api` → API, `/ws` → WebSocket, `/` → Frontend

---

## Common Commands

### Start Services
```bash
# Start all services in background
docker-compose up -d

# Start with logs visible
docker-compose up

# Start specific service
docker-compose up api
```

### Stop Services
```bash
# Stop all services
docker-compose down

# Stop and remove volumes (fresh start)
docker-compose down -v
```

### View Logs
```bash
# All services
docker-compose logs

# Specific service
docker-compose logs api
docker-compose logs worker

# Follow logs in real-time
docker-compose logs -f api
```

### Check Status
```bash
# List running containers
docker-compose ps

# Check API health
curl http://localhost/api/health
```

### Rebuild Services
```bash
# Rebuild after code changes
docker-compose build

# Rebuild specific service
docker-compose build api

# Force rebuild without cache
docker-compose build --no-cache api
```

---

## Configuration

### Environment Variables

The `.env` file contains default configuration. Key variables:

```bash
# Database
POSTGRES_USER=mev
POSTGRES_PASSWORD=mev
POSTGRES_DB=mev

# API
MEV_DATABASE_URL=postgresql+asyncpg://mev:mev@postgres:5432/mev
REDIS_URL=redis://redis:6379/0

# External APIs (optional)
EBI_EMAIL=mev-pipeline@example.com
NCBI_EMAIL=mev-pipeline@example.com
```

### Custom Configuration

Edit the `.env` file to customize:

```bash
# Increase simulation speed (lower = faster)
MEV_STEP_TICK_MS=300

# Set your own contact email for external APIs
NCBI_EMAIL=your-email@example.com

# Add CORS origins for custom frontend
MEV_CORS_ORIGINS=https://your-domain.com
```

After changes, restart services:
```bash
docker-compose down
docker-compose up -d
```

---

## Pipeline Thresholds

All thresholds are calibrated to match the published paper (Barazesh et al. 2024):

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

## Troubleshooting

### Issue: Port already in use

```bash
# Check what's using port 80 or 8000
lsof -i :80
lsof -i :8000

# Stop conflicting services
sudo systemctl stop apache2  # or nginx
```

### Issue: Docker daemon not running

```bash
# macOS: Start Docker Desktop
# Linux: Start Docker service
sudo systemctl start docker

# Verify Docker is running
docker info
```

### Issue: Build fails

```bash
# Clean build
docker-compose down
docker-compose build --no-cache
docker-compose up -d
```

### Issue: API not responding

```bash
# Check API logs
docker-compose logs api

# Restart API service
docker-compose restart api

# Check health
curl http://localhost/api/health
```

### Issue: Database connection error

```bash
# Check PostgreSQL logs
docker-compose logs postgres

# Restart database
docker-compose restart postgres

# Wait a few seconds, then check API
sleep 5
curl http://localhost/api/health
```

### Issue: Worker not processing tasks

```bash
# Check worker logs
docker-compose logs worker

# Restart worker
docker-compose restart worker
```

---

## API Reference

### Health Check
```http
GET /api/health
```

### Create Job
```http
POST /api/jobs
Content-Type: application/json

{
  "name": "Job Name",
  "pathogenName": "Streptococcus agalactiae",
  "taxonId": 208435,
  "source": "pathogen",
  "realTools": true
}
```

### Get Job
```http
GET /api/jobs/{job_id}
```

### Start Pipeline
```http
POST /api/jobs/{job_id}/start
```

### Pause Pipeline
```http
POST /api/jobs/{job_id}/pause
```

### Resume Pipeline
```http
POST /api/jobs/{job_id}/resume
```

### Get Epitopes
```http
GET /api/jobs/{job_id}/epitopes
```

### Download Report
```http
GET /api/jobs/{job_id}/report/pdf
```

---

## Data Persistence

### Volumes

Docker Compose creates two persistent volumes:

1. **pg_data**: PostgreSQL database files
2. **mev_cache**: Pipeline cache data

### Backup

```bash
# Backup database
docker-compose exec postgres pg_dump -U mev mev > backup.sql

# Restore database
cat backup.sql | docker-compose exec -T postgres psql -U mev mev
```

### Fresh Start

```bash
# Remove all data and start fresh
docker-compose down -v
docker-compose up -d
```

---

## Development Mode

### Hot Reloading

For development with code changes:

```bash
# The API service mounts the backend directory
# Changes to backend/ are reflected immediately

# Edit backend code
vim backend/app/main.py

# API will auto-reload (if --reload is enabled)
```

### Frontend Development

The Docker setup includes Nginx serving static files. For frontend development:

```bash
# Stop Docker services
docker-compose down

# Start backend only
cd backend
python3 -m uvicorn app.main:app --reload --port 8000

# In another terminal, start frontend dev server
npm run dev

# Frontend will be at http://localhost:3000
```

---

## Next Steps

1. **Explore the API**: Visit http://localhost:8000/docs
2. **Run a test pipeline**: Create a job with taxonId 208435
3. **View results**: Check the funnel and epitope predictions
4. **Read the paper**: Barazesh et al. 2024, Nature Scientific Reports

---

## Support

- **GitHub**: https://github.com/umeshdahiya15/Revacc
- **Issues**: https://github.com/umeshdahiya15/Revacc/issues
- **Documentation**: See `HANDOVER.md` for technical details
