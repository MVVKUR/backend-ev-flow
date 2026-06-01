# EV-FLOW API — slim runtime image (FastAPI + uvicorn + pandas/numpy). ~350 MB, no geo stack
# (vs 1.5 GB+ with geopandas/osmnx/matplotlib/jupyter).
# Build:  podman build -t ev-flow-api:latest -f Containerfile .
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install deps first for better layer caching.
COPY requirements-api.txt ./
RUN pip install --no-cache-dir -r requirements-api.txt

# App code + the committed EV catalogue (used if data/ isn't mounted).
COPY api/ ./api/
COPY ev_dataset.zip ./

# Alembic migrations and seed script (needed for `alembic upgrade head` and `python -m scripts.seed_db`).
# data/raw is mounted at runtime, not baked in.
COPY alembic/ ./alembic/
COPY alembic.ini ./
COPY scripts/ ./scripts/

# Run as a non-root user.
RUN useradd -m -u 10001 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# WEB_CONCURRENCY workers (each holds its own ~3.5k-row in-memory dataset — keep it small).
CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port 8000 --workers ${WEB_CONCURRENCY:-2}"]
