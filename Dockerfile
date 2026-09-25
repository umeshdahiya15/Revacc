# ============================================================================
# Revacc Pipeline - Unified Docker Image
# Frontend (Next.js) + Backend (FastAPI) + BLAST+
# ============================================================================

# Stage 1: Build Next.js frontend
# NOTE: node:20-slim (Debian/glibc) matches python:3.13-slim runtime so
# native bindings built here work in the final image.
FROM node:20-slim AS frontend-builder

WORKDIR /app

# Copy package files
COPY package.json package-lock.json ./

# Install dependencies
RUN npm ci

# Copy source code
COPY src ./src
COPY public ./public
COPY next.config.mjs postcss.config.mjs tsconfig.json ./
COPY package.json package-lock.json ./

# Build Next.js
RUN npm run build

# Stage 2: Production frontend
FROM node:20-slim AS frontend-production

WORKDIR /app

# Copy built assets
COPY --from=frontend-builder /app/.next/standalone ./
COPY --from=frontend-builder /app/.next/static ./.next/static
COPY --from=frontend-builder /app/public ./public

# Stage: PSORTb real binary (step 2-2 surface localization)
# Statically linked C++ build + HMM data; copied verbatim so the pipeline has
# a real, offline-capable subcellular-localization tool inside the container.
FROM brinkmanlab/psortb_commandline:1.0.2 AS psortb

# Stage 3: Backend with BLAST+ and PSORTb
FROM python:3.13-slim AS backend

# Install system dependencies
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libxml2-dev \
        libxslt1-dev \
        zlib1g-dev \
        wget \
        curl \
        procps \
    && rm -rf /var/lib/apt/lists/*

# Install BLAST+ (ncbi-blast) via apt
RUN apt-get update \
    && apt-get install -y --no-install-recommends ncbi-blast+ \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
WORKDIR /app

COPY backend/requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir -r /app/requirements.txt

# Reference database caches (baked in below at build time, read at runtime)
ENV MEV_BLAST_DB_CACHE=/opt/mev-blastdb \
    MEV_VFDB_CACHE=/opt/mev-vfdb

# Copy backend application
COPY backend/app /app/app

# NOTE: the PSORTb runtime transplant lives AFTER the adduser block below —
# `adduser` is a Perl script, so it must run before /usr/bin/perl is replaced.

# Bake reference BLAST databases (DEG10, reviewed human proteome, VFDB core)
# so pipeline steps 2-1 / 3-3 / 3-4 never depend on runtime downloads.
RUN mkdir -p /opt/mev-blastdb /opt/mev-vfdb \
    && PYTHONPATH=/app python3 -m app.tools.prefetch_dbs

# Copy Node.js runtime (needed to run Next.js standalone server.js)
# The Python base image has no node binary - copy it from node:20-slim.
COPY --from=node:20-slim /usr/local/bin/node /usr/local/bin/node
RUN ln -s /usr/local/bin/node /usr/bin/node

# Copy built frontend from stage 2
COPY --from=frontend-production /app /app/frontend

# Copy start script and make executable
COPY start-services.sh /app/start-services.sh
RUN chmod +x /app/start-services.sh

# Create non-root user
RUN addgroup --system app && adduser --system --ingroup app app \
    && chown -R app:app /app \
    && chown -R app:app /opt/mev-blastdb /opt/mev-vfdb

# ---------------------------------------------------------------------------
# PSORTb 3.0 (pipeline step 2-2, subcellular localization) — full offline
# runtime transplanted from brinkmanlab/psortb_commandline:1.0.2.
# That image ships a Perl-based PSORTb whose SCLBLAST homology search needs
# the legacy `blastall` binary, so we copy the complete self-consistent stack:
#   * perl 5.22 interpreter (replaces the Debian build at /usr/bin/perl; the
#     app itself is Python/Node — perl here exists only for PSORTb scripts,
#     which is why this block sits after the adduser/addgroup RUN above)
#   * BioPerl + PSORTb Perl modules (XS parts are perl-5.22 ABI — cannot be
#     rebuilt against modern perl, hence the interpreter transplant)
#   * psortb3.pl + HMM/motif/signal conf data at the hardcoded /usr/local/psortb
#   * static pfscan (pftools) and legacy blastall + its NCBI .so.6 closure
# `psortb`/`psort` on PATH are adapters normalizing PSORTbClient's "-i FILE"
# invocation to psortb3.pl's positional form (terse results on stdout, which
# is what parse_psortb_out expects).
# The final RUN is a build-time smoke test: the image fails to build if the
# transplanted runtime cannot produce clean, parseable terse output.
# ---------------------------------------------------------------------------
COPY --from=psortb /usr/bin/perl /usr/bin/perl
COPY --from=psortb /usr/bin/perl5.22.1 /usr/bin/perl5.22.1
COPY --from=psortb /etc/perl /etc/perl
COPY --from=psortb /usr/share/perl /usr/share/perl
COPY --from=psortb /usr/share/perl5 /usr/share/perl5
COPY --from=psortb /usr/lib/x86_64-linux-gnu/perl /usr/lib/x86_64-linux-gnu/perl
COPY --from=psortb /usr/lib/x86_64-linux-gnu/perl5 /usr/lib/x86_64-linux-gnu/perl5
COPY --from=psortb /usr/local/lib/x86_64-linux-gnu/perl /usr/local/lib/x86_64-linux-gnu/perl
COPY --from=psortb /usr/local/psortb /usr/local/psortb
COPY --from=psortb /usr/local/bin/pftools /usr/local/bin/pftools
COPY --from=psortb /usr/local/lib64 /usr/local/lib64
COPY --from=psortb /usr/bin/blastall /usr/bin/blastall
COPY --from=psortb /usr/lib/x86_64-linux-gnu/libblast.so.6* /usr/lib/x86_64-linux-gnu/
COPY --from=psortb /usr/lib/x86_64-linux-gnu/libblastapi.so.6* /usr/lib/x86_64-linux-gnu/
COPY --from=psortb /usr/lib/x86_64-linux-gnu/libblastcompadj.so.6* /usr/lib/x86_64-linux-gnu/
COPY --from=psortb /usr/lib/x86_64-linux-gnu/libncbi.so.6* /usr/lib/x86_64-linux-gnu/
COPY --from=psortb /usr/lib/x86_64-linux-gnu/libncbiobj.so.6* /usr/lib/x86_64-linux-gnu/
COPY --from=psortb /usr/lib/x86_64-linux-gnu/libncbitool.so.6* /usr/lib/x86_64-linux-gnu/

COPY psortb-adapter.sh /usr/local/bin/psortb
RUN chmod +x /usr/local/bin/psortb \
    && ln -sf /usr/local/bin/psortb /usr/local/bin/psort \
    && ln -sf /usr/local/bin/pftools/pfscan /usr/local/bin/pfscan \
    && echo /usr/local/lib64 > /etc/ld.so.conf.d/psortb.conf \
    && ldconfig \
    && printf '>smoke\nMKKIGYSADVKHLKSELAAVAGCKKIWISDTGSDYQDLTQYPQINELTKLSGSSQQKIGYDSDLKIKQVLKELGGF\n' > /tmp/psortb-smoke.fa \
    && psortb -i /tmp/psortb-smoke.fa -p --output terse > /tmp/psortb-smoke.out 2>/tmp/psortb-smoke.err \
    && grep -q "SeqID" /tmp/psortb-smoke.out \
    && grep -qE "^smoke[[:space:]]" /tmp/psortb-smoke.out \
    && ! grep -qiE "can't locate|cannot find path|fatal error|version mismatch|compilation aborted" /tmp/psortb-smoke.out /tmp/psortb-smoke.err \
    && rm -f /tmp/psortb-smoke.fa /tmp/psortb-smoke.out /tmp/psortb-smoke.err

USER app

# Expose ports
EXPOSE 3000 8000

# Environment variables
ENV PYTHONPATH=/app \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MEV_STEP_TICK_MS=650 \
    MEV_CORS_ORIGINS="*"

CMD ["/app/start-services.sh"]
