# Kiro Prompt — Revacc Reverse Vaccinology Pipeline

Copy-paste this entire prompt to Kiro to hand over the project.

---

## PROJECT: Revacc — Reverse Vaccinology Pipeline for Streptococcus agalactiae

### What This Project Is
A full-stack reverse-vaccinology pipeline (Python/FastAPI backend + Next.js 14 frontend) that screens a pathogen's proteome through 14 phases (50 steps) to design multi-epitope vaccine (MEV) candidates. It is calibrated against a published paper: Barazesh et al. 2024, Nature Scientific Reports.

### GitHub
https://github.com/umeshdahiya15/Revacc

### Tech Stack
- Backend: Python 3.13, FastAPI, BioPython, httpx, BLAST+ 2.17.0+
- Frontend: Next.js 14, React, Tailwind CSS v4, Zustand
- Deployment: Vercel (frontend) + Railway (backend)
- External APIs: IEDB (MHC-I/II epitope prediction), EBI (Phobius signal peptide), UniProt, DEG, VFDB, AlphaFold DB

### Pipeline Flow (14 Phases, 50 Steps)
1. **Proteome Retrieval** — UniProt txid 208435 (all reviewed + unreviewed)
2. **CD-HIT Redundancy Removal** — threshold 0.80 identity
3. **Subtractive Filtering** — DEG essential (40% identity), PSORTb/Phobius surface-exposed, DeepTMHMM transmembrane
4. **Safety Screening** — AlgPred allergenicity (threshold 0.321), VaxiJen antigenicity (threshold 0.50), VFDB virulence (bit-score >100, e-value ≤1e-4), Human homology BLASTp (30% identity cutoff)
5. **Structural Validation** — ProtParam, AlphaFold DB (capped at 30), ERRAT, Chou-Fasman
6. **CTL Epitope Prediction** — IEDB NetMHCpan EL (MHC-I, 25 alleles, lengths 9+10, percentile ≤ 2)
7. **HTL Epitope Prediction** — IEDB NetMHCIIpan EL (MHC-II, 25 alleles, length 15, percentile ≤ 2)
8. **B-cell Epitope Prediction** — Local BepiPred (threshold 0.5, window 7) + Ellipro conformational
9. **Population Coverage** — IEDB-AR v2.20 (local fallback NMDP frequencies)
10. **MEV Assembly** — CTxB adjuvant, EAAAK/AAY/GPGPG/KK linkers, 8 CTL + 8 HTL + 5 B-cell caps
11. **MEV Validation** — ProtParam, VaxiJen, AlgPred, ToxinPred, Protein-Sol
12. **3D Structure** — AlphaFold/SwissModel backbone, Ramachandran, ERRAT, ProSA
13. **Disulfide Bond Engineering** — DbD2 local
14. **Codon Optimization** — JCat, restriction sites, in silico cloning
15. **Immune Simulation** — C-ImmSim ODE model

### All Calibrated Thresholds (Matching Paper)

```python
# backend/app/models.py
cdHitThreshold: float = 0.8          # CD-HIT identity
vaxijenThreshold: float = 0.5        # VaxiJen antigenicity
mhciPercentile: float = 2.0          # MHC-I strong binder percentile
mhciiPercentile: float = 2.0         # MHC-II strong binder percentile
bCellWindow: int = 16                # ABCpred window (config field, not used by bcell_local.py)

# backend/app/tools/vfdb.py
VFDB_IDENTITY_THRESHOLD = 30.0       # BLASTp identity %
VFDB_EVALUE_THRESHOLD = 1e-4         # BLASTp e-value
VFDB_BITSCORE_THRESHOLD = 100.0      # BLASTp bit-score minimum

# backend/app/tools/runner.py
DEG_IDENTITY_THRESHOLD = 40          # DEG BLASTp identity %
DEG_EVALUE_THRESHOLD = 1e-5          # DEG BLASTp e-value

# backend/app/tools/iedb.py
DEFAULT_MHCI_LENGTHS = ("9", "10")   # CTL epitope lengths
DEFAULT_MHCII_LENGTHS = ("15",)      # HTL epitope length
BATCH_SIZE = 4                       # Proteins per IEDB request
BATCH_COOLDOWN_SEC = 3.0             # Seconds between IEDB batches

# backend/app/tools/bcell_local.py
THRESHOLD = 0.5                      # BepiPred epitope threshold
WINDOW_SIZE = 7                      # BepiPred smoothing window

# backend/app/tools/algpred_local.py (line 127)
is_allergen = score >= 0.321         # AlgPred allergenicity threshold

# backend/app/tools/cytokine_local.py
IL4_SCORE_THRESHOLD = 0.2            # IL-4 induction threshold
IL10_SCORE_THRESHOLD = -0.3          # IL-10 induction threshold
IFN_GAMMA_SCORE_THRESHOLD = 0.45     # IFN-γ induction threshold

# backend/app/tools/runner_additions.py
HUMAN_HOMOLOGY_IDENTITY_THRESHOLD = 0.30  # Human homology cutoff (30%)
PHOBIUS_CAP = 700                   # Max proteins for Phobius analysis
CTL_CAP = 8                          # MEV CTL epitope cap
HTL_CAP = 8                          # MEV HTL epitope cap
BCELL_CAP = 5                        # MEV B-cell epitope cap

# backend/app/tools/runner_additions.py (line 1119)
CTL length filter: 8 <= len(sequence) <= 12  # CTL epitope length range
HTL length filter: 13 <= len(sequence) <= 20 # HTL epitope length range
B-cell length filter: len(sequence) <= 25     # B-cell epitope max length

# HLA Alleles (defaults when config is empty)
MHC-I: 25 alleles (HLA-A*02:01, HLA-A*02:05, HLA-A*01:01, HLA-A*03:01,
  HLA-A*11:01, HLA-A*23:01, HLA-A*24:02, HLA-A*26:01, HLA-A*30:01,
  HLA-A*32:01, HLA-A*33:01, HLA-A*68:01, HLA-B*07:02, HLA-B*08:01,
  HLA-B*15:01, HLA-B*35:01, HLA-B*40:01, HLA-B*44:02, HLA-B*51:01,
  HLA-B*53:01, HLA-B*57:01, HLA-B*58:01, HLA-C*01:02, HLA-C*04:01,
  HLA-C*07:02)

MHC-II: 25 alleles (HLA-DRB1*01:01 through HLA-DQB1*05:01)
```

### Latest Run Results
```
Proteins: 2106 → CD-HIT: 2094 → Essential: 507 → Surface: 75 → Virulence: 12 → Candidates: 53
CTL epitopes: 30, HTL epitopes: 20, B-cell epitopes: 63 (22 linear + 11 conformational)
MEV: 336 aa (8 CTL + 8 HTL + 5 B-cell), antigenicity 0.78, solubility 0.80
```

### How to Run
```bash
# Backend
cd backend
/Library/Frameworks/Python.framework/Versions/3.13/Resources/Python.app/Contents/MacOS/Python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Frontend
npm run dev  # Port 3000

# Optional local tunnel for development; production uses Railway.
ngrok http 8000
```

### API Quick Reference
```
GET  /api/jobs                    # List all jobs
POST /api/jobs                    # Create job
GET  /api/jobs/{id}               # Get job status + funnel + epitopes + MEV
POST /api/jobs/{id}/start         # Start pipeline
POST /api/jobs/{id}/resume        # Resume after pause
GET  /api/jobs/{id}/epitopes      # Predicted epitopes (PDF export runs in the UI)
```

### Known Issues to Address

1. **IEDB API Fragility:** IEDB rate-limits bursts (HTTP 500/429). Current workaround: 3s cooldown between batches, browser User-Agent, 4 retries with backoff. But it can still fail on busy hours. Consider caching IEDB results or adding a local MHC binding prediction fallback.

2. **Essential Gene Gap:** We get 507 essential proteins vs paper's 1336. The DEG annotation CSV from tubic.org may have changed, or the paper uses a different organism-specific subset. Investigate DEG entry counts for S. agalactiae.

3. **Surface-Exposed Gap:** We get 75 vs paper's 408. Paper likely uses PSORTb (which detects more surface categories). Our PSORTb step is "graceful pause" (local heuristic). Consider integrating PSORTb v6.0 local binary if available.

4. **AlphaFold Timeout:** Step 4-2 can TimeoutError on slow connections. The pipeline pauses and can be resumed. Consider increasing timeout or reducing candidate count from 30.

5. **B-cell Window Config Unused:** `bCellWindow=16` in models.py is never passed to `bcell_local.py` which uses `WINDOW_SIZE=7`. Either wire the config or remove the field.

6. **MEV Length:** Our MEV is 336 aa vs paper's 620 aa. Paper uses full RpfE adjuvant + signal peptide. We use CTxB peptide only. Consider adding signal peptide option.

### Key Files to Know
```
backend/app/tools/runner.py        — STEP_RUNNERS, DEG BLAST, IEDB calls
backend/app/tools/runner_additions.py — All local tool implementations (2800+ lines)
backend/app/tools/iedb.py          — IEDB REST client (MHC-I/II)
backend/app/tools/vfdb.py          — VFDB BLASTp virulence
backend/app/tools/cdhit.py         — Pure-Python CD-HIT
backend/app/tools/deg.py           — DEG essential gene fetching
backend/app/tools/vaxijen_local.py — Local VaxiJen ACC antigenicity
backend/app/tools/bcell_local.py   — Local BepiPred/Ellipro B-cell
backend/app/tools/cytokine_local.py — Local cytokine prediction
backend/app/models.py              — All default threshold values
src/lib/api.ts                     — resolveApiBase() for frontend
src/lib/constants.ts               — HLA allele defaults
```

### What to Work On Next
1. Fix IEDB reliability (caching, local fallback, or retry improvements)
2. Investigate DEG essential gene gap (507 vs 1336)
3. Investigate surface-exposed gap (75 vs 408) — possibly integrate PSORTb local
4. Wire `bCellWindow` config to `bcell_local.py`
5. Add signal peptide option to MEV construct
6. Improve PDF report with funnel comparison table
7. Add ability to compare multiple runs
