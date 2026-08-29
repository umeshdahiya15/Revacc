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
| 4-2 | Structure Provider | REAL AlphaFold DB alternate by default; explicit SWISS-MODEL selection pauses unless an authenticated official client is configured |
| 4-3 | Local coordinate quality analysis | REAL AlphaFold DB PDB coordinates; explicitly not official ERRAT |
| 4-4 | SOPMA | Pause (no REST API) |
| 5-1 | IEDB MHC-I | REAL |
| 5-2 | VaxiJen (CTL) | Pause |
| 5-3 | AlgPred (CTL) | Pause |
| 5-4 | ToxinPred (CTL) | Pause |
| 5-5 | Immunogenicity | REAL local analysis over real IEDB allele/rank or IC50 inputs; never labeled as IEDB immunogenicity |
| 6-1 | IEDB MHC-II | REAL |
| 6-2–6-7 | IFNepitope/IL4Pred/IL10Pred/VaxiJen/AlgPred/ToxinPred | Pause |
| 7-1–7-5 | ABCpred/VaxiJen/AlgPred/ToxinPred/Ellipro | Pause |
| 8-1 | Pop Coverage | REAL (IEDB-AR) |
| 8-2 | Epitope Overlap | REAL local normalized sequence-containment analysis over selected CTL/HTL/B-cell rows |
| 9-1 | Adjuvant Selection | Pause (manual) |
| 9-2 | MEV Assembly | REAL |
| 10-1 | ProtParam (MEV) | REAL (BioPython) |
| 10-2–10-5 | VaxiJen/AlgPred/ToxinPred/Protein-Sol | Pause |
| 11-1 | SOPMA | Pause |
| 11-2 | AlphaFold DB / external model | Pauses for novel MEV unless a real exact-sequence PDB or documented provider model is attached; AlphaFold DB has no automatic prediction path for this construct |
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
| `MEV_CORS_ORIGINS` | local origins only | Comma-separated browser origins allowed to call the API; set `https://revacc.vercel.app` in Railway (never `*` in production) |
| `MEV_STEP_TICK_MS` | `650` | Simulation speed per step (demo only) |
| `MEV_MAX_STEPS_PER_TICK` | `1` | Steps advanced per engine tick |
| `MEV_DATABASE_URL` | local placeholder | Reserved for the planned SQLAlchemy/Celery swap; the current repository is in-memory |
| `NCBI_EMAIL` | `mev-pipeline@example.com` | Contact for NCBI BLAST; use a real address for sustained runs |
| `NCBI_API_KEY` | *(empty)* | Optional NCBI API key (set as a Railway secret) |
| `NCBI_TOOL` | `mev-pipeline` | Tool name sent to NCBI BLAST |
| `EBI_EMAIL` | `mev-pipeline@example.com` | Contact required by EBI Job Dispatcher (Phobius, etc.) |
| `MEV_VFDB_CACHE` | `/tmp/mev-vfdb` | Writable path for the downloadable VFDB cache |
| `MEV_BLAST_DB_CACHE` | `/tmp/mev-blastdb` | Writable path for downloaded local BLAST databases |
| `MEV_PHOBIUS_CACHE` | `/tmp/mev-phobius-cache` | Writable path for Phobius results cache |
| `PSORTB_BIN` / `PSORTB_PATH` | *(unset)* | Optional path to a PSORTb executable; missing PSORTb is reported as unavailable |
| `ALPHAFOLD_EMAIL` | *(empty)* | Optional contact email for AlphaFold DB API requests |
| `MEV_STRUCTURE_PROVIDER` | `alphafold_db` | Step 4-2 provider; `alphafold_db` is the real, explicitly labeled alternate used by default; `swissmodel` pauses unless an official authenticated client is available |
| `SWISSMODEL_API_TOKEN` | *(empty)* | Optional SWISS-MODEL credential supplied only through Railway secrets; never commit a token or place one in tests |

## Railway deployment

The repository-root `Dockerfile` is the Railway service image; no `railway.json` or `railway.toml` is required. Set the Railway service root to the repository root and let Railway use the Dockerfile build. Do not add a custom development start command.

Build and start behavior:

- The image installs `backend/requirements.txt` into the system interpreter and copies only `backend/app`.
- The production command is `uvicorn app.main:app --host 0.0.0.0 --port $PORT` (with a local-only `8000` fallback); it does not use `.venv` or `--reload`.
- Railway's built-in `PORT` is dynamic. Do not hardcode or manually override it.
- Configure the Railway health check path as `/api/health`. The full URL after a domain is assigned is `https://<railway-domain>/api/health` and should return JSON with `status: "ok"`.

Required Railway environment variables for the confirmed deployment (do not include a trailing slash):

```text
MEV_CORS_ORIGINS=https://revacc.vercel.app
```

`MEV_CORS_ORIGINS` accepts comma-separated origins for additional Vercel preview/custom domains. Use explicit origins only; do not set it to `*` in Railway. `PORT` is supplied by Railway. Optional credentials such as `NCBI_API_KEY` belong only in Railway's secret/environment-variable UI, never in this repository or Dockerfile.

Vercel handoff for the confirmed Railway service:

```text
NEXT_PUBLIC_API_URL=https://revacc-production-6342.up.railway.app
```

The `https://` scheme is required for deployed frontend and backend URLs. Set `NEXT_PUBLIC_API_URL` in the Vercel project environment and redeploy, then verify the browser can call `https://revacc-production-6342.up.railway.app/api/health` and open the WebSocket endpoint at `wss://revacc-production-6342.up.railway.app/ws/pipeline/<job-id>`. The frontend already gives this variable priority and does not require an ngrok URL.

Railway limitations and external prerequisites:

- The image does not include BLAST+ executables or DEG, VFDB, and human-proteome databases. Local BLAST-backed steps therefore need a separately provisioned persistent volume/tool image, or use a supported remote path; unavailable tools must remain marked unavailable rather than being replaced with fabricated data.
- The PSORTb Docker fallback cannot start a sibling Docker daemon from this container. A PSORTb binary must be separately installed and exposed through `PSORTB_BIN`/`PSORTB_PATH`; otherwise the application records the PSORTb residual and continues only with scientifically available localization results.
- IEDB, EBI/Phobius, UniProt, NCBI, AlphaFold, and IEDB population-coverage calls require outbound network access and may rate-limit or be unavailable. Railway does not supply these services or their datasets.
- The current repository uses an in-memory job store and `/tmp` caches. Jobs and downloaded caches are ephemeral across restarts/redeploys; `MEV_DATABASE_URL`, Redis, and Celery are not wired into the API process by this deployment change. Add and integrate persistent services separately before relying on durable production records.

## Publish and deploy the GHCR image

The workflow at `../.github/workflows/publish-backend-image.yml` builds the repository-root `Dockerfile` with GitHub Actions and publishes the image when `main` (the repository's default branch) or a version tag such as `v1.2.3` is pushed. It authenticates with the automatic `GITHUB_TOKEN`; no personal access token is stored in the repository.

The image URL pattern is:

```text
ghcr.io/<owner>/<repository>
```

Replace `<owner>` and `<repository>` with the lowercase GitHub owner and repository name after the workflow runs. Useful tags are `:latest` and `:main` for the default branch, `:sha-<short-commit>` for an exact published commit, and `:1.2.3` (plus compatible major/minor tags) for a `v1.2.3` push. Do not substitute a real project URL until GitHub Actions has published the package.

To use this image on Railway:

1. Push `main` or a version tag and wait for the **Publish backend image** workflow to finish.
2. In GitHub, open the repository's **Packages** entry, select the container package, and either change its visibility to **Public** or leave it private.
3. In Railway, create/deploy a service from a container image. Use `ghcr.io/<owner>/<repository>:latest` (or pin `:sha-<short-commit>` / a version tag). For a private package, configure Railway's private-registry credentials for `ghcr.io` using a separately managed GitHub identity/token with package read access; keep that credential in Railway, never in this repository. Public packages do not require registry credentials.
4. Keep the service health check at `/api/health` and let Railway provide `PORT`; the image already binds to `0.0.0.0` and uses that dynamic port.

Alternatively, skip GHCR entirely: connect the GitHub repository as a Railway service, set the service root to the repository root, and let Railway build the existing `Dockerfile`. This GitHub-source deployment uses the same production command and dynamic `PORT` behavior and does not require registry credentials.

## Roadmap (from Planning.md)

- **Sprint 1** — swap `repo.py` for SQLAlchemy (async, PostgreSQL); Celery +
  Redis worker running the `pipeline_chain`; keep the wire protocol unchanged.
- **Next sprints** — implement the 18 API-backed tool integrations first
  (UniProt, CD-HIT, IEDB, VaxiJen, …), then the 7 Playwright-scraped tools
  (ElliPro, ABCpred, DbD2, …). The `Engine._advance` step just becomes a call
  into the tool client library.