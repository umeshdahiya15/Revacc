# MEV Pipeline API (backend)

FastAPI backend for the multi-epitope vaccine design pipeline. Consumed by the
Next.js frontend in `../src` through `NEXT_PUBLIC_API_URL` (default
`http://localhost:8000`).

## Quick start

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --reload --port 8000
```

- Interactive docs: http://localhost:8000/docs
- Health: http://localhost:8000/api/health

## What's implemented

| Piece | Notes |
| --- | --- |
| `GET/ POST /api/jobs` | List / create jobs (`POST` body = `JobCreate`) |
| `GET /api/jobs/{id}` | Full job with 14 phases / 50 steps (shape matches `src/types/index.ts`) |
| `POST /api/jobs/{id}/(start\|pause\|resume\|stop)` | Lifecycle control; broadcasts a WS event |
| `GET /api/jobs/{id}/events` | Polling fallback for the frontend hook |
| `WS /ws/pipeline/{id}` | Live `PipelineEvent` stream (replays last 20 on connect) |
| `backend/app/simulator.py` | In-memory step engine — advances running jobs and emits the exact event types the frontend `usePipelineWebSocket` hook consumes |
| `GET /api/jobs/{id}/epitopes` | Real IEDB epitope predictions stored on the job (CTL + HTL) |
| `backend/app/tools/` | Real tool integrations: `uniprot.py` (REST proteome fetch, rate-limit aware + process cache), `cdhit.py` (CD-HIT-style greedy clustering), `deg.py` (DEG essential-gene reference, S. agalactiae A909), `ncbiblast.py` (NCBI remote BLASTp URL API client), `iedb.py` (IEDB tools-cluster REST MHC-I/MHC-II predictions), `ebi_rest_client.py` (EBI Job Dispatcher REST for Phobius), `vfdb.py` (VFDB BLASTp local database), `alphafold.py` (AlphaFold DB REST API). `runner.py` maps steps → tools |

Three demo jobs are seeded on startup (`job-001` completed, `job-002` running,
`job-003` paused) mirroring the frontend mock data, so the full flow works with
zero external services. Seeded jobs run in simulated mode (`realTools: false`).

## Real tool runs

Jobs created via `POST /api/jobs` default to `realTools: true`. Steps with a
registered runner execute the real tool; the rest of the pipeline simulates
(pausing gracefully on tool failure, which the frontend error banner shows).

Currently implemented (all 50 steps have registered runners):

| Step | Tool | Status |
|------|------|--------|
| 1-1 | UniProt | REAL |
| 1-2 | CD-HIT | REAL |
| 2-1 | DEG+BLAST | REAL |
| 2-2 | PSORTb | Pause (DNS dead) |
| 2-3 | DeepTMHMM | Pause (no REST API) |
| 2-4 | Phobius | REAL (EBI REST) |
| 3-1 | AlgPred | Pause (HTML-only) |
| 3-2 | VaxiJen | Pause (Cloudflare 403) |
| 3-3 | VFDB | REAL (local BLASTp) |
| 3-4 | Human Homology | REAL (NCBI BLASTp) |
| 4-1 | ProtParam | REAL (BioPython) |
| 4-2 | SwissModel | Pause (auth-gated) |
| 4-3 | ERRAT | Pause (domain for sale) |
| 4-4 | SOPMA | Pause (no REST API) |
| 5-1 | IEDB MHC-I | REAL |
| 5-2 | VaxiJen (CTL) | Pause |
| 5-3 | AlgPred (CTL) | Pause |
| 5-4 | ToxinPred (CTL) | Pause |
| 5-5 | Immunogenicity | REAL (computation) |
| 6-1 | IEDB MHC-II | REAL |
| 6-2–6-7 | IFNepitope/IL4Pred/IL10Pred/VaxiJen/AlgPred/ToxinPred | Pause |
| 7-1–7-5 | ABCpred/VaxiJen/AlgPred/ToxinPred/Ellipro | Pause |
| 8-1 | Pop Coverage | REAL (IEDB-AR) |
| 8-2 | Epitope Overlap | REAL (computation) |
| 9-1 | Adjuvant Selection | Pause (manual) |
| 9-2 | MEV Assembly | REAL |
| 10-1 | ProtParam (MEV) | REAL (BioPython) |
| 10-2–10-5 | VaxiJen/AlgPred/ToxinPred/Protein-Sol | Pause |
| 11-1 | SOPMA | Pause |
| 11-2 | AlphaFold DB | REAL (REST API) |
| 11-3 | Ramachandran | REAL (BioPython) |
| 11-4 | ERRAT | Pause |
| 11-5 | ProSA | Pause |
| 12-1 | DbD2 | Pause |
| 13-1 | JCat | REAL (SSL workaround) |
| 13-2 | Restriction Analysis | REAL (BioPython) |
| 13-3 | Cloning Design | Pause |
| 14-1 | C-ImmSim | Pause (no anonymous API) |

The real Phase 1, 2, 5 & 6 counts are pushed into `job.funnel` and rendered
live by the frontend FilterFunnel (proteins → non-redundant → essential →
MHC-I epitopes → MHC-II epitopes).

## Wire contract

The event payloads and job JSON are defined in `app/models.py` and must stay in
sync with `src/types/index.ts` and `src/lib/mockData.ts`. The frontend:

1. reads a job via `GET /api/jobs/{id}` (falls back to mock if offline),
2. subscribes to `ws://localhost:8000/ws/pipeline/{id}` for live step events,
3. posts to `/start`, `/pause`, `/resume` for lifecycle control.

## Configuration (env vars)

| Variable | Default | Purpose |
| --- | --- | --- |
| `MEV_STEP_TICK_MS` | `650` | Simulation speed per step (demo only) |
| `MEV_MAX_STEPS_PER_TICK` | `1` | Steps advanced per engine tick |
| `MEV_DATABASE_URL` | `postgresql://…` | Reserved for Sprint 1 (SQLAlchemy/Celery swap) |
| `NCBI_EMAIL` | *(empty)* | Raised BLAST RID priority; recommended for sustained runs |
| `NCBI_API_KEY` | *(empty)* | NCBI API key (get one at https://www.ncbi.nlm.nih.gov/account) |
| `NCBI_TOOL` | `mev-pipeline` | Tool name sent to NCBI BLAST |
| `EBI_EMAIL` | *(empty)* | Required by EBI Job Dispatcher (Phobius, etc.) |
| `MEV_VFDB_CACHE` | `/tmp/mev-vfdb` | Path to VFDB core dataset cache directory |
| `ALPHAFOLD_EMAIL` | *(empty)* | Optional contact email for AlphaFold DB API requests |

## Roadmap (from Planning.md)

- **Sprint 1** — swap `repo.py` for SQLAlchemy (async, PostgreSQL); Celery +
  Redis worker running the `pipeline_chain`; keep the wire protocol unchanged.
- **Next sprints** — implement the 18 API-backed tool integrations first
  (UniProt, CD-HIT, IEDB, VaxiJen, …), then the 7 Playwright-scraped tools
  (ElliPro, ABCpred, DbD2, …). The `Engine._advance` step just becomes a call
  into the tool client library.