# 🧬 Revacc Pipeline

A complete reverse-vaccinology pipeline for vaccine candidate prediction against bacterial pathogens.

[![Python](https://img.shields.io/badge/Python-3.13-blue.svg)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-green.svg)](https://fastapi.tiangolo.com)
[![Next.js](https://img.shields.io/badge/Next.js-14-black.svg)](https://nextjs.org)
[![Docker](https://img.shields.io/badge/Docker-Ready-blue.svg)](https://docker.com)

---

## 🚀 Quick Start (3 Steps)

### 1. Install Docker

Download and install Docker Desktop:
- **macOS**: https://www.docker.com/products/docker-desktop/
- **Windows**: https://www.docker.com/products/docker-desktop/
- **Linux**: https://docs.docker.com/engine/install/

### 2. Run This Command

**macOS/Linux:**
```bash
curl -fsSL https://raw.githubusercontent.com/umeshdahiya15/Revacc/main/start-revacc.sh | bash
```

**Or clone and run:**
```bash
git clone https://github.com/umeshdahiya15/Revacc.git
cd Revacc
docker compose up -d
```

### 3. Open Browser

Go to: **http://localhost:3000**

That's it! 🎉

---

## 📖 Documentation

| Document | Description |
|----------|-------------|
| [START.md](START.md) | **Complete setup guide** - Start here! |
| [PIPELINE_SETUP.md](PIPELINE_SETUP.md) | Detailed Docker configuration |
| [QUICKSTART.md](QUICKSTART.md) | Quick reference card |

---

## 🎯 Features

### Bioinformatics Tools
- ✅ **UniProt**: Protein data retrieval
- ✅ **CD-HIT**: Sequence clustering
- ✅ **BLAST+**: Sequence alignment
- ✅ **DEG**: Essential gene identification
- ✅ **PSORTb/Phobius**: Subcellular localization
- ✅ **VaxiJen**: Antigenicity prediction
- ✅ **IEDB NetMHCpan**: MHC binding prediction
- ✅ **Population Coverage**: HLA frequency analysis

### Pipeline Phases
1. **Data Retrieval**: Fetch proteins from UniProt
2. **Filtering**: Remove redundancy, identify essentials
3. **Localization**: Surface-exposed protein detection
4. **Safety**: Allergenicity and human homology checks
5. **Antigenicity**: Immune response prediction
6. **Epitope Prediction**: CTL and HTL epitope mapping
7. **MEV Construction**: Multi-epitope vaccine design
8. **Immune Simulation**: ODE-based response modeling

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────┐
│                   Frontend (Next.js)                │
│                    http://localhost:3000             │
└─────────────────────┬───────────────────────────────┘
                      │ REST API
┌─────────────────────▼───────────────────────────────┐
│                  Backend (FastAPI)                   │
│                    http://localhost:8000             │
├─────────────────────────────────────────────────────┤
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐  │
│  │ BLAST+  │ │ CD-HIT  │ │ IEDB    │ │ Phobius │  │
│  └─────────┘ └─────────┘ └─────────┘ └─────────┘  │
└─────────────────────────────────────────────────────┘
```

---

## 💻 Development

### Run Locally (Without Docker)

```bash
# Backend
cd backend
pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8000

# Frontend (new terminal)
npm install
npm run dev
```

### Run Tests

```bash
# Backend tests
cd backend
pytest

# Frontend tests
npm test
```

---

## 🐳 Docker

### Build Image
```bash
docker compose build
```

### Start Services
```bash
docker compose up -d
```

### View Logs
```bash
docker compose logs -f
```

### Stop Services
```bash
docker compose down
```

---

## 📊 API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/health` | Health check |
| GET | `/api/jobs` | List all jobs |
| POST | `/api/jobs` | Create new job |
| GET | `/api/jobs/{id}` | Get job details |
| POST | `/api/jobs/{id}/start` | Start pipeline |
| POST | `/api/jobs/{id}/pause` | Pause pipeline |
| POST | `/api/jobs/{id}/resume` | Resume pipeline |
| GET | `/api/jobs/{id}/epitopes` | Get epitopes |
| GET | `/api/jobs/{id}/report/pdf` | Download report |

---

## 🔧 Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MEV_STEP_TICK_MS` | 650 | Pipeline speed (ms) |
| `MEV_CORS_ORIGINS` | `*` | Allowed origins |

### Pipeline Thresholds

| Parameter | Value | Description |
|-----------|-------|-------------|
| CD-HIT Identity | 0.80 | Redundancy removal |
| VaxiJen Antigenicity | 0.50 | Antigenicity cutoff |
| MHC-I Percentile | ≤ 2.0 | CTL binding threshold |
| MHC-II Percentile | ≤ 2.0 | HTL binding threshold |
| DEG Identity | 40% | Essential gene detection |

---

## 📚 References

- Barazesh et al. (2024). *In silico reverse vaccinology approach to identify novel vaccine candidates against Streptococcus agalactiae*. Nature Scientific Reports.
- UniProt: https://www.uniprot.org/
- IEDB: https://www.iedb.org/
- BLAST+: https://blast.ncbi.nlm.nih.gov/

---

## 🤝 Contributing

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

---

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.

---

## 🆘 Support

- **Issues**: https://github.com/umeshdahiya15/Revacc/issues
- **Documentation**: See [START.md](START.md) for setup help
- **API Docs**: Visit http://localhost:8000/docs when running

---

**Made with ❤️ for vaccine research**
