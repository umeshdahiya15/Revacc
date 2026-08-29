FROM python:3.13-slim

# Runtime libraries used by Biopython and XML-based tool clients.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libxml2-dev \
        libxslt1-dev \
        zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install into the image's system interpreter; never depend on a local .venv.
COPY backend/requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir -r /app/requirements.txt

# Only the backend application is needed at runtime. Tests, frontend assets,
# local datasets, and development environments are excluded by .dockerignore.
COPY backend/app /app/app

# Keep the API process unprivileged in Railway.
RUN addgroup --system app && adduser --system --ingroup app app \
    && chown -R app:app /app
USER app

EXPOSE 8000

# Railway supplies PORT at runtime. The fallback keeps local `docker run`
# behavior convenient without changing the production (no --reload) command.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
