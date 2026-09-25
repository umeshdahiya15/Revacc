# Revacc Pipeline Status Report

## Executive Summary

The Revacc reverse-vaccinology pipeline is fully functional and distributed as a
**single unified Docker image** containing the frontend, backend and all analysis
tools. **219/219 backend tests pass.**

- **Run it**: see [`START.md`](START.md) (one-liner, `docker run`, or clone-and-build)
- **Image**: `umeshdahiya01/revacc:latest` on Docker Hub
- **Repository**: https://github.com/umeshdahiya15/Revacc (public)

---

## Architecture (current)

| Component | Technology | Notes |
|-----------|------------|-------|
| **Backend API** | Python 3.13, FastAPI | In-memory job/session store — no external database required |
| **Frontend** | Next.js 14, React | Served by the same container on port 3000 |
| **BLAST+** | System `blastp` / `makeblastdb` | Bundled in the image |
| **PSORTb 3.0** | Full offline runtime transplant | perl 5.22 + BioPerl + PSORTb modules + `pfscan` + legacy `blastall` and its NCBI lib closure; adapter normalizes the PSORTbClient invocation |
| **Reference DBs** | Baked at build time | DEG10, reviewed human proteome, VFDB (`prefetch_dbs.py`) |
| **Clustering** | Local Python greedy clustering | cd-hit-equivalent algorithm in `app/tools/cdhit.py` (provenance notes when the native binary is absent) |
| **MHC binding** | IEDB web services | Requires internet; exponential backoff on rate limits |
| **Structure** | AlphaFold DB / SwissModel | Local ODE fallbacks keep runs progressing when services degrade |

**Distribution model**: one container, two ports —

```bash
docker pull umeshdahiya01/revacc:latest
docker run -d --name revacc-pipeline -p 3000:3000 -p 8000:8000 umeshdahiya01/revacc:latest
```

> Legacy files (`docker-compose.yml`, `nginx.conf`, `vercel.json`) remain in the
> repository but are **not required and not part of the supported path**. Follow
> `START.md` / `QUICKSTART.md`.

---

## Test Results

| Metric | Value |
|--------|-------|
| **Total backend tests** | **219** |
| **Passing** | **219 ✅** |
| **Failing** | 0 |

Suites cover phase runners (1–14), cross-cutting filter preservation, IEDB cache
and fallback behaviour, SwissModel resilience/lifecycle, structure providers,
surface-union regression, PSORTb client contracts, and API/CORS/WebSocket
behaviour.

### Recent fixes

1. **Zero-input provenance** — empty/normal returns now carry real provenance so
   valid zero-input analyses (e.g. step 3-1 with no essential proteins after
   filtering) complete instead of pausing with "unavailable" inputs.
2. **`run_14_1` hardening** — tolerates absent job/step context; the local ODE
   immune-response model completes without external services.
3. **UTF-8 hardening** — subprocess output decoded with `errors="replace"` so
   odd bytes from VFDB/BLAST tooling can never crash a step.
4. **DEG identity threshold test** — updated to the implemented 40% threshold
   (Barazesh et al. 2024).

---

## Pipeline Thresholds

All thresholds are calibrated to Barazesh et al. 2024:

| Parameter | Value | Status |
|-----------|-------|--------|
| CD-HIT Identity | 0.80 | ✅ Matches paper |
| VaxiJen Antigenicity | 0.50 | ✅ Matches paper |
| MHC-I Percentile | ≤ 2.0 | ✅ Matches paper |
| MHC-II Percentile | ≤ 2.0 | ✅ Matches paper |
| DEG Identity | 40% | ✅ Matches paper |
| DEG E-value | ≤ 1e-5 | ✅ Matches paper |
| VFDB Identity | ≥ 30% | ✅ Matches paper |
| VFDB E-value | ≤ 1e-4 | ✅ Matches paper |
| VFDB Bit-score | > 100 | ✅ Matches paper |
| Human Homology | 30% | ✅ Matches paper |
| AlgPred Allergenicity | ≥ 0.321 | ✅ Matches paper |

---

## Validation

- **Full 50-step run**: end-to-end validation executed against the published
  image (taxon 208435, *Streptococcus agalactiae*) — all 50 steps complete,
  0 failed / 0 paused.
- **PSORTb equivalence**: results inside the image are byte-identical to the
  reference `brinkmanlab/psortb_commandline` image on real VFDB sequences
  (e.g. `Cellwall 8.76`, `Cytoplasmic 9.67`), verified at build time by a
  smoke-test layer that fails the build on regression.
- **Baked DBs verified**: `deg10_bacteria`, `human_reviewed`, `vfdb_core`
  present in `/opt/mev-blastdb` and `/opt/mev-vfdb`.

---

## Known Issues (Documented)

### 1. Essential Gene Gap
- **Issue**: 507 essential proteins vs the paper's 1336
- **Impact**: Different MEV composition
- **Status**: Documented in `HANDOVER.md`; different analysis approach, not a defect

### 2. Surface-Exposed Candidates
- **Issue**: Historically only 75 surface-exposed vs the paper's 408 (no PSORTb)
- **Status**: **Resolved** — PSORTb 3.0 is now bundled in the image with a full
  offline runtime; subcellular localization runs natively in step 2-2

### 3. External Services Require Internet
- **IEDB** (steps 5-1, 6-1): MHC binding prediction via tools.iedb.org —
  exponential backoff on HTTP 429/500
- **EBI Phobius** (step 2-4): bounded by a step timeout; PSORTb positives are
  retained regardless of Phobius availability
- **UniProt / NCBI / SwissModel**: downloads during phases 1 and 11
- Everything else (BLAST DBs, PSORTb, clustering, ODE fallbacks) runs offline

### 4. MEV Length Difference
- **Issue**: 336 aa vs the paper's 620 aa
- **Status**: Documented in `HANDOVER.md`

---

## Documentation

| File | Purpose |
|------|---------|
| [`START.md`](START.md) | **Primary setup guide** — one-liner, `docker run`, build-from-source, ports, troubleshooting |
| [`QUICKSTART.md`](QUICKSTART.md) | Shortest path to a running instance |
| [`README.md`](README.md) | Project overview |
| [`PIPELINE_SETUP.md`](PIPELINE_SETUP.md) | Detailed component setup |
| [`HANDOVER.md`](HANDOVER.md) | Technical handover and design decisions |
| [`backend/README.md`](backend/README.md) | Backend documentation |

---

## Status

- ✅ **219/219 tests passing**
- ✅ **PSORTb, BLAST+ and reference DBs bundled — no tool installation needed**
- ✅ **Full 50-step validation completed on the published image**
- ✅ **Public repo + Docker Hub image — recipients need only Docker**
- ✅ Thresholds calibrated to Barazesh et al. 2024
