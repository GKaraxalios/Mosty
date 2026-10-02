# ═══════════════════════════════════════════════════════════════════
# MostyAPI — Production Dockerfile (Cloud Run)
# ═══════════════════════════════════════════════════════════════════
# Build:   docker build -t mostyapi .
# Run:     docker run -p 8080:8080 --env-file .env mostyapi
# ═══════════════════════════════════════════════════════════════════

FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# System deps (PyMySQL needs no native libs, but keep build-essential
# in case any future dependency compiles a C extension).
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

# ── Dependencies ──────────────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Application code ─────────────────────────────────────────────
COPY app/ app/
COPY static/ static/
COPY scripts/ scripts/

# Ensure data directory exists (ephemeral in Cloud Run; use GCS for
# persistence by setting CHUNK_STORE_PATH / SYNC_STATE_PATH to gs://…).
RUN mkdir -p data

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -sf http://localhost:8080/api/v1/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "2"]
