# Revacc Pipeline - Complete Setup Guide

A complete reverse-vaccinology pipeline for vaccine candidate prediction. This guide will help you download, install, and run the Revacc pipeline on your computer.

---

## 🚀 Quick Start (3 Easy Steps)

### Step 1: Install Docker

Docker is required to run the pipeline. Choose your operating system:

#### macOS
1. Download Docker Desktop: https://www.docker.com/products/docker-desktop/
2. Install and open Docker Desktop
3. Wait for the whale icon in your menu bar to become stable

#### Windows
1. Download Docker Desktop: https://www.docker.com/products/docker-desktop/
2. Install with WSL 2 backend enabled (recommended)
3. Restart your computer if prompted
4. Open Docker Desktop and wait for it to start

#### Linux (Ubuntu/Debian)
```bash
# Update package index
sudo apt-get update

# Install Docker
sudo apt-get install docker.io

# Add your user to docker group (logout and login after this)
sudo usermod -aG docker $USER

# Start Docker
sudo systemctl start docker
sudo systemctl enable docker
```

#### Verify Docker Installation
```bash
docker --version
# Should show: Docker version 20.10 or later
```

> Docker Compose is only needed for the optional "clone repository" method (Options A and B need only Docker itself). If your distro ships it: `sudo apt-get install docker-compose-v2`; otherwise follow https://docs.docker.com/compose/install/ — or simply use Option A/B, which don't need Compose.

---

### Step 2: Download Revacc

#### Option A: One Command (Easiest — Recommended)

**For macOS/Linux:**
```bash
curl -fsSL https://raw.githubusercontent.com/umeshdahiya15/Revacc/main/start-revacc.sh | bash
```

The script automatically downloads the image, starts both services, and waits until they are healthy. If anything fails, it prints the container logs so you can see exactly what went wrong.

**For Windows (PowerShell):**
```powershell
docker pull umeshdahiya01/revacc:latest
docker run -d --name revacc-pipeline -p 3000:3000 -p 8000:8000 umeshdahiya01/revacc:latest
```

#### Option B: Manual Docker Run (No clone needed)

```bash
# Pull the image
docker pull umeshdahiya01/revacc:latest

# Run the container
docker run -d \
  --name revacc-pipeline \
  -p 3000:3000 \
  -p 8000:8000 \
  umeshdahiya01/revacc:latest

# Wait ~10 seconds, then verify
curl http://localhost:8000/api/health   # should return: {"status":"ok"}
```

#### Option C: Clone Repository (for development)

```bash
# Clone the repository
git clone https://github.com/umeshdahiya15/Revacc.git

# Navigate to the directory
cd Revacc

# Start the pipeline
docker compose up -d
```

---

### Step 3: Access the Pipeline

1. Open your web browser
2. Go to: **http://localhost:3000**
3. You should see the Revacc dashboard

That's it! The pipeline is ready to use.

---

## 📋 What You Get

### Services Running

| Service | URL | Description |
|---------|-----|-------------|
| **Frontend** | http://localhost:3000 | Web dashboard for managing jobs |
| **Backend API** | http://localhost:8000 | REST API for pipeline operations |
| **API Documentation** | http://localhost:8000/docs | Interactive Swagger UI |

### Pipeline Features

- ✅ **UniProt Integration**: Fetch protein data from UniProt
- ✅ **CD-HIT Clustering**: Remove redundant sequences
- ✅ **DEG Analysis**: Identify essential proteins
- ✅ **PSORTb/Phobius**: Surface-exposed protein detection
- ✅ **VaxiJen**: Antigenicity prediction
- ✅ **IEDB NetMHCpan**: MHC binding prediction
- ✅ **Population Coverage**: HLA allele frequency analysis
- ✅ **MEV Construction**: Multi-epitope vaccine design
- ✅ **Immune Simulation**: ODE-based immune response modeling

> **Everything is baked into the image**: BLAST+, PSORTb (with its Perl runtime
> and legacy-BLAST homology search), CD-HIT, plus the DEG10, VFDB and reviewed
> human-proteome reference databases. No tool or database downloads happen at
> run time — only a few prediction steps (Phobius, IEDB MHC) call external
> REST services and need internet access for those steps.

---

## 🎯 How to Use

### Create Your First Job

1. Open http://localhost:3000
2. Click **"Create New Job"**
3. Enter a pathogen name (e.g., "Streptococcus agalactiae")
4. Enter the taxon ID (e.g., 208435 for S. agalactiae)
5. Click **"Start Pipeline"**

### Monitor Progress

- Watch the real-time progress bar
- View step-by-step results as they complete
- Check the funnel visualization for protein counts

### View Results

- **Funnel Chart**: See how proteins are filtered at each step
- **Epitope Table**: View predicted CTL and HTL epitopes
- **MEV Sequence**: Get the final vaccine construct sequence
- **PDF Report**: Download a complete analysis report

---

## 🔧 Configuration

### Environment Variables

You can customize the pipeline by setting environment variables:

```bash
# Create a .env file
cat > .env << EOF
MEV_STEP_TICK_MS=650
MEV_CORS_ORIGINS=*
EOF
```

### Common Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `MEV_STEP_TICK_MS` | 650 | Pipeline speed (lower = faster) |
| `MEV_CORS_ORIGINS` | `*` | Allowed frontend origins |

---

## 🛠️ Troubleshooting

### Issue: "Port already in use"

```bash
# Stop existing container
docker stop revacc-pipeline && docker rm revacc-pipeline

# Or use different ports
docker run -d -p 3001:3000 -p 8001:8000 --name revacc-pipeline umeshdahiya01/revacc:latest
```

### Issue: "Cannot connect to Docker"

```bash
# Check Docker status
docker info

# Restart Docker (macOS)
# Click Docker Desktop icon → Restart

# Restart Docker (Linux)
sudo systemctl restart docker
```

### Issue: "Image pull failed" or "access denied"

```bash
# Retry the pull
docker pull umeshdahiya01/revacc:latest

# If it still fails, ensure you are not logged into a different account
docker logout
docker pull umeshdahiya01/revacc:latest
```

### Issue: "Container won't start" or "node: command not found"

```bash
# Check container logs
docker logs revacc-pipeline

# Pull the latest fixed image and restart
docker pull umeshdahiya01/revacc:latest
docker stop revacc-pipeline && docker rm revacc-pipeline
docker run -d --name revacc-pipeline -p 3000:3000 -p 8000:8000 umeshdahiya01/revacc:latest
```

### Issue: "Frontend not loading"

```bash
# Check if container is running
docker ps --filter name=revacc-pipeline

# View logs (should show "Revacc Pipeline is READY!")
docker logs revacc-pipeline

# Wait a few seconds and refresh browser
```

---

## 📊 API Reference

### Health Check
```bash
curl http://localhost:8000/api/health
```

### Create Job
```bash
curl -X POST http://localhost:8000/api/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "name": "My Pipeline Run",
    "pathogenName": "Streptococcus agalactiae",
    "taxonId": 208435,
    "source": "pathogen",
    "realTools": true
  }'
```

### Start Pipeline
```bash
curl -X POST http://localhost:8000/api/jobs/{job_id}/start
```

### Get Job Status
```bash
curl http://localhost:8000/api/jobs/{job_id}
```

### Get Epitopes
```bash
curl http://localhost:8000/api/jobs/{job_id}/epitopes
```

### Download Report
Open the finished job in the UI and click **Export report** — the PDF is
generated in your browser (no backend endpoint involved).

---

## 🐳 Docker Commands

### Start Services
```bash
docker run -d --name revacc-pipeline -p 3000:3000 -p 8000:8000 umeshdahiya01/revacc:latest
```

### Stop Services
```bash
docker stop revacc-pipeline && docker rm revacc-pipeline
```

### View Logs
```bash
docker logs -f revacc-pipeline
```

### Restart Services
```bash
docker restart revacc-pipeline
```

### Update to Latest Image
```bash
docker pull umeshdahiya01/revacc:latest
docker stop revacc-pipeline && docker rm revacc-pipeline
docker run -d --name revacc-pipeline -p 3000:3000 -p 8000:8000 umeshdahiya01/revacc:latest
```

### Remove Everything
```bash
docker stop revacc-pipeline && docker rm revacc-pipeline
docker rmi umeshdahiya01/revacc:latest
```

---

## 📁 Project Structure

```
Revacc/
├── backend/
│   ├── app/
│   │   ├── main.py          # FastAPI entry point
│   │   ├── routes.py        # API endpoints
│   │   ├── simulator.py     # Pipeline engine
│   │   └── tools/           # Bioinformatics tools
│   └── requirements.txt     # Python dependencies
├── src/                     # Next.js frontend
├── Dockerfile               # Docker build file
├── docker-compose.yml       # Docker Compose config
├── start-revacc.sh          # Quick start script
├── START.md                 # This file
└── PIPELINE_SETUP.md        # Detailed setup guide
```

---

## 🔄 Updating

To get the latest version:

```bash
# Pull new image
docker pull umeshdahiya01/revacc:latest

# Replace old container with new image
docker stop revacc-pipeline && docker rm revacc-pipeline
docker run -d --name revacc-pipeline -p 3000:3000 -p 8000:8000 umeshdahiya01/revacc:latest
```

---

## 💡 Tips

1. **First run may take 2-3 minutes** to download the Docker image
2. **Pipeline runs locally** - no data is sent to external servers
3. **Results are stored in memory** - restart clears all data
4. **Use real tools** for accurate results (enable in job settings)
5. **Check API docs** at http://localhost:8000/docs for advanced usage
6. **A full 50-step run takes roughly 45-90 minutes** - most of the time is
   surface-localization (PSORTb) and external prediction services
7. **If a step shows "paused"**, an external prediction service was
   unreachable; start the job again from the dashboard and it resumes where
   it left off

---

## 🆘 Getting Help

- **GitHub Issues**: https://github.com/umeshdahiya15/Revacc/issues
- **Documentation**: See `PIPELINE_SETUP.md` for detailed setup
- **API Docs**: Visit http://localhost:8000/docs when running

---

## 📄 License

See LICENSE file for details.

---

**Enjoy using Revacc! 🧬**
