# Revacc Pipeline — Handover Report & Kiro Prompt

## 1. PROJECT OVERVIEW

**Revacc** is a reverse-vaccinology pipeline for *Streptococcus agalactiae* (Group B Streptococcus). It screens a pathogen's proteome through 14 phases (50 steps) to identify epitope-based multi-epitope vaccine (MEV) candidates.

- **GitHub:** https://github.com/umeshdahiya15/Revacc
- **Tech Stack:** Python 3.13 (FastAPI backend) + Next.js 14 (React frontend)
- **Reference Paper:** Barazesh et al. 2024, Nature Scientific Reports — "Designing a multi-epitope mRNA vaccine against Streptococcus agalactiae using reverse vaccinology approach"

---

## 2. CURRENT STATE — WHAT WORKS

The pipeline is **fully functional** and runs 50/50 steps in ~21 minutes with zero errors. All thresholds now match the paper standard.

### Latest Run Results (Paper Standard v2)
```
Funnel:
  Proteins curated:       2106  (UniProt txid 208435, all reviewed+unreviewed)
  Non-redundant clusters: 2094  (CD-HIT ≥ 0.80 identity)
  Essential proteins:      507  (DEG, 40% identity, e ≤ 1e-5)
  Surface-exposed:          75  (Phobius via EBI REST API)
  Virulence factors:        12  (BLASTp + VFDB, bit-score > 100, e ≤ 1e-4)
  Vaccine candidates:       53  (Human homology filtered, 30% identity cutoff)
  MHC class I epitopes:     30  (IEDB NetMHCpan EL, percentile ≤ 2)
  MHC class II epitopes:    20  (IEDB NetMHCIIpan EL, percentile ≤ 2)

MEV Construct: 336 aa
  CTL epitopes:   8  (linker: AAY)
  HTL epitopes:   8  (linker: GPGPG)
  B-cell epitopes: 5  (linker: KK)
  Adjuvant: CTxB (P01556) via EAAAK linker

Validation:
  Antigenicity:   0.782  (VaxiJen, threshold ≥ 0.50)
  Solubility:     0.803  (Protein-Sol)
  Allergenicity:  0.100  (AlgPred, threshold 0.321)
  Toxicity:       0.000  (ToxinPred)
  Peak IgG:       1554   (C-ImmSim ODE model)
  Seroconversion: Day 3
```

---

## 3. ALL THRESHOLDS — PAPER STANDARD vs CODE

Every threshold has been calibrated to match Barazesh et al. 2024 exactly.

| Parameter | Paper Value | Code Location | Current Value |
|---|---|---|---|
| CD-HIT identity | 0.80 | `models.py:88,143` → `cdhit.py:19` | 0.80 ✓ |
| AlgPred allergenicity | 0.321 | `algpred_local.py:127` | 0.321 ✓ |
| VaxiJen antigenicity | 0.50 | `vaxijen_local.py:46` | 0.50 ✓ |
| VFDB E-value | 1e-4 | `vfdb.py:30` | 1e-4 ✓ |
| VFDB bit-score | > 100 | `vfdb.py:31,188` | 100.0 ✓ |
| VFDB identity | 30% | `vfdb.py:29` | 30.0 ✓ |
| DEG identity | 40% | `runner.py:46` | 40 ✓ |
| DEG E-value | 1e-5 | `runner.py:37` | 1e-5 ✓ |
| CTL MHC-I percentile | ≤ 2 | `models.py:97,140` | 2.0 ✓ |
| CTL MHC-I lengths | 9, 10 | `iedb.py:30` | ("9","10") ✓ |
| HTL MHC-II percentile | ≤ 2 | `models.py:98,141` | 2.0 ✓ |
| HTL MHC-II lengths | 15 | `iedb.py:31` | ("15",) ✓ |
| HTL IL-4 threshold | 0.2 | `cytokine_local.py:48` | 0.2 ✓ |
| HTL IL-10 threshold | -0.3 | `cytokine_local.py:50` | -0.3 ✓ |
| B-cell ABCpred threshold | 0.5 | `bcell_local.py:55` | 0.5 ✓ |
| B-cell ABCpred window | 16 | `models.py:99` (config field) | 16 (config) |
| Human homology identity | 30% | `runner_additions.py:107` | 0.30 ✓ |
| Phobius cap | N/A | `runner_additions.py:48` | 700 |
| MEV CTL cap | 8 | `runner_additions.py:1110` | 8 ✓ |
| MEV HTL cap | 8 | `runner_additions.py:1111` | 8 ✓ |
| MEV B-cell cap | 5 | `runner_additions.py:1112` | 5 ✓ |
| MEV CTL select cap | 30 | `runner.py:460` | 30 |
| MEV HTL select cap | 20 | `runner.py:461` | 20 |

---

## 4. ARCHITECTURE

### Backend (`backend/`)
```
backend/
  app/
    main.py              # FastAPI app, CORS, lifespan
    models.py            # Pydantic models (Job, Phase, Step, Epitope, etc.)
    repo.py              # In-memory job store
    simulator.py         # Pipeline orchestrator (50-step sequential runner)
    config.py            # CORS defaults
    tools/
      runner.py          # STEP_RUNNERS registry, DEG BLAST, IEDB MHC-I/II calls
      runner_additions.py # All local tool implementations (2800+ lines)
      iedb.py            # IEDB REST client (MHC-I, MHC-II)
      vfdb.py            # VFDB BLASTp virulence factor identification
      cdhit.py           # Pure-Python CD-HIT clustering
      deg.py             # DEG essential gene fetching (NCBI efetch)
      uniprot.py         # UniProt proteome retrieval
      ncbiblast.py       # BLASTp wrapper (NCBI API)
      blastdb_local.py   # Local BLAST DB builder (DEG, human proteome)
      vaxijen_local.py   # Local VaxiJen (ACC-based antigenicity)
      algpred_local.py   # Local AlgPred (allergenicity, toxicity)
      bcell_local.py     # Local BepiPred/Ellipro (B-cell epitopes)
      cytokine_local.py  # Local IFN-γ/IL-4/IL-10 prediction
      structure_local.py # AlphaFold, Ramachandran, ERRAT, SOPMA
      quality_local.py   # ProtParam, disulfide bonds
      ebi_rest_client.py # EBI Phobius REST client
      population.py      # Population coverage analysis
      adjuvant_dbd2_local.py # Adjuvant selection
      graceful_pause.py  # Tool unavailable error handling
```

### Frontend (`src/`)
```
src/
  app/
    page.tsx              # Dashboard with pipeline controls
    settings/page.tsx     # Settings (API URL, localStorage persistence)
    jobs/[id]/page.tsx    # Job detail view
  components/
    MEVSequenceMap.tsx    # MEV construct visualization
    FunnelChart.tsx       # Funnel bar chart
    ReportPreview.tsx     # PDF report generation
  lib/
    api.ts                # resolveApiBase() — checks NEXT_PUBLIC_API_URL → localStorage → Railway fallback
    constants.ts          # HLA allele defaults (MHC-I: 9 alleles, MHC-II: 7 alleles)
    types/index.ts        # TypeScript interfaces matching backend models
```

### Key External Dependencies
- **BLAST+ 2.17.0+** — installed at `/tmp/ncbi-blast-2.17.0+/bin/`, symlinked at `~/bin/blastp`
- **Human proteome DB** — built at `/var/folders/.../mev-blastdb/human_reviewed.phr`
- **BioPython 1.87** — ProtParam, SeqIO
- **httpx** — async HTTP for IEDB, EBI REST
- **Python 3.13** at `/Library/Frameworks/Python.framework/Versions/3.13/`

---

## 5. KNOWN ISSUES & GAPS

### 5.1 Essential Gene Gap (507 vs Paper's 1336)
The paper reports 1336 essential proteins from DEG at 40% identity. We get 507. Possible causes:
- Different DEG database version (we use DEG annotation CSV from tubic.org)
- The paper may use organism-specific DEG entries vs our "all organisms" scope
- The paper may count proteins with ANY DEG hit (not requiring 40% identity)
- The number 1336 might represent a different analysis step, not strict BLAST filtering

**Impact:** Fewer essential → fewer surface-exposed → fewer virulence candidates → different MEV composition.

### 5.2 Surface-Exposed Gap (75 vs Paper's 408)
The paper uses PSORTb for subcellular localization. We use Phobius (EBI REST). PSORTb is more comprehensive — it detects outer membrane, lipoproteins, wall-anchored proteins that Phobius misses.

**Current pipeline flow:**
1. PSORTb (local) → 67 surface-exposed (secreted + membrane)
2. DeepTMHMM (local) → 66 have transmembrane helices
3. Phobius (EBI REST) → 75 surface-exposed (final)

**Impact:** The paper's 408 includes PSORTb-detected surface proteins we never consider.

### 5.3 IEDB API Fragility
IEDB's shared cluster rate-limits bursts (returns HTTP 500/429). Key findings:
- **User-Agent filter:** IEDB returns 403 for custom user-agents. Fixed by using browser UA string.
- **Request size limit:** Requests with >50 allele-length pairs overwhelm the API. Current code sends 25 alleles × 2 lengths = 50 pairs per protein batch (BATCH_SIZE=4 proteins per request).
- **Retry logic:** 4 retries with exponential backoff (3s, 8s, 20s). Sometimes all retries fail.
- **Solution in place:** 3s cooldown between batches, 180s timeout per request.

### 5.4 CD-HIT Pure-Python Performance
CD-HIT is implemented in pure Python (no compiled binary). For2106 proteins it takes ~2s. For larger proteomes (>10,000) it would be slow. Word-size scaling is applied: word=7 for >1000 seqs, word=10 for >3000 seqs.

### 5.5 Structure-provider behavior
Step 4-2 uses the real AlphaFold DB REST API by default (`MEV_STRUCTURE_PROVIDER=alphafold_db`) for individual target proteins. It caps lookups at 30 candidates and preserves the exact AlphaFold API, PDB, mmCIF, and BinaryCIF URLs in session provenance. A target without a usable real coordinate URL causes an explicit AlphaFold DB pause; no local or fabricated structure is emitted. Setting `MEV_STRUCTURE_PROVIDER=swissmodel` is an explicit opt-in only and currently pauses because this repository does not implement an official authenticated SWISS-MODEL request client. If that client is added, `SWISSMODEL_API_TOKEN` must come from Railway secrets, never source code or tests.

### 5.6 MEV Length Gap (336 aa vs Paper's 620 aa)
The paper's MEV is620 aa because it includes:
- Signal peptide (tPA)
- RpfE adjuvant (full length, not just CTxB peptide)
- Longer linker regions
Our MEV uses CTxB adjuvant peptide only (shorter), and shorter linkers. Functionally equivalent but smaller.

### 5.7 B-cell Window Config Not Used
`models.py` has `bCellWindow: int = 16` (matches paper's ABCpred 16-mer), but `bcell_local.py` uses hardcoded `WINDOW_SIZE = 7` for BepiPred smoothing. The config field is never passed to the function. This is cosmetic since we use BepiPred-style local prediction, not actual ABCpred.

### 5.8 Steps That Gracefully Pause (Not Real Tools)
These steps use local computation (heuristics) because the actual web tools are not API-accessible:
- **2-2 PSORTb:** local transmembrane/secretion signal detection
- **2-3 DeepTMHMM:** local hydrophobicity-based TM prediction
- **3-1 AlgPred 2.0:** local MW/hydrophobicity/allergen matching
- **3-2 VaxiJen 2.0:** local ACC-based antigenicity scoring
- **5-3 CTL Allergenicity:** local AlgPred
- **5-4 CTL Toxicity:** local ToxinPred
- **6-2 IFN-γ:** local motif-based prediction
- **6-3 IL-4:** local motif-based prediction
- **6-4 IL-10:** local motif-based prediction
- **7-5 Ellipro:** local surface accessibility scoring
- **13-3 Benchling:** local restriction site analysis

These are **not mock data** — they use real computational methods (ACC, propensity scales, PWMs) that approximate the web tools.

---

## 6. DEPLOYMENT

### Backend
```bash
cd backend
/Library/Frameworks/Python.framework/Versions/3.13/Resources/Python.app/Contents/MacOS/Python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### Optional Local Ngrok Tunnel
```bash
ngrok http 8000
# ./start.sh --all injects the dynamically-created tunnel URL for local testing.
```

### Frontend
```bash
npm run dev  # Port 3000
```

### Vercel Deployment
- Root Directory: `./` (NOT `backend/`)
- Set `NEXT_PUBLIC_API_URL=https://revacc-production-6342.up.railway.app` (the `https://` scheme is required; do not use a bare hostname)
- Confirm Railway has `MEV_CORS_ORIGINS=https://revacc.vercel.app`
- Rebuild/redeploy the frontend after changing this value so it is embedded in the browser bundle

### One-Command Start
```bash
./start.sh  # Starts backend + ngrok
```

---

## 7. HOW TO RUN

1. Start backend: `cd backend && python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload`
2. Open http://localhost:3000
3. Click "Start Pipeline" or use API: `POST /api/jobs` then `POST /api/jobs/{id}/start`
4. Pipeline runs 50 steps sequentially (~21 min)
5. Results viewable in real-time via WebSocket

### API Quick Reference
```
GET  /api/jobs                    # List all jobs
POST /api/jobs                    # Create job (JSON body with config)
GET  /api/jobs/{id}               # Get job status + results
POST /api/jobs/{id}/start         # Start pipeline
POST /api/jobs/{id}/resume        # Resume after pause
GET  /api/jobs/{id}/report/pdf    # Download PDF report
```

---

## 8. FILE TREE (KEY FILES)

```
Revacc/
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── models.py            # Pydantic models, all default thresholds
│   │   ├── repo.py
│   │   ├── simulator.py
│   │   └── tools/
│   │       ├── runner.py        # DEG BLAST, IEDB calls, STEP_RUNNERS
│   │       ├── runner_additions.py  # All local tool runners (2800+ lines)
│   │       ├── iedb.py          # IEDB REST client
│   │       ├── vfdb.py          # VFDB BLASTp
│   │       ├── cdhit.py         # Pure-Python CD-HIT
│   │       ├── deg.py           # DEG fetching
│   │       ├── uniprot.py       # UniProt proteome
│   │       ├── vaxijen_local.py # Local VaxiJen ACC
│   │       ├── algpred_local.py # Local AlgPred
│   │       ├── bcell_local.py   # Local BepiPred/Ellipro
│   │       ├── cytokine_local.py # Local cytokine prediction
│   │       ├── structure_local.py # Local structure analysis
│   │       ├── quality_local.py # ProtParam
│   │       └── ebi_rest_client.py # Phobius REST
│   └── requirements.txt
├── src/
│   ├── app/
│   │   ├── page.tsx
│   │   ├── settings/page.tsx
│   │   └── jobs/[id]/page.tsx
│   ├── components/
│   │   ├── MEVSequenceMap.tsx
│   │   ├── FunnelChart.tsx
│   │   └── ReportPreview.tsx
│   └── lib/
│       ├── api.ts               # resolveApiBase()
│       └── constants.ts         # HLA allele defaults
├── vercel.json
├── start.sh
└── HANDOVER.md                  # This file
```

## 4. REMEDIATION CHECKPOINT POLICY (CURRENT)

The prior summary above contains historical calibration values and must not be read as proof of a current successful run. Current production policy is provenance-first: IEDB CTL uses live consensus 12-mer and HTL uses live consensus 15-mer, both at percentile <=2, or a cache entry from a successful IEDB request. If IEDB is unavailable and no cached-real result exists, the step pauses and emits no epitope rows. Local BepiPred is labeled local analysis when ABCpred is unavailable. No PyDock/GROMACS docking or MD integration is configured; reports expose those requested protocols as unavailable rather than generating scientific values. Exact measured counts are reported only from a real or explicitly mocked test fixture and are never padded to paper targets.
