# Multi-stage build: wheels are compiled in the builder and only the installed
# environment is copied forward, so no compiler toolchain ships in the runtime.
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

# Dependency metadata first: this layer is cached until pyproject.toml changes,
# so ordinary source edits do not trigger a full dependency reinstall.
COPY pyproject.toml README.md ./
COPY src/ ./src/

RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install ".[loaders,chroma]"


FROM python:3.12-slim AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ASKMYDOCS_PROFILE=offline \
    ASKMYDOCS_STORAGE_DIR=/data/storage

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY src/ ./src/
COPY config/ ./config/
COPY data/ ./data/

# Run unprivileged, and give the app a writable volume for the index.
RUN useradd --create-home --shell /bin/bash askmydocs \
    && mkdir -p /data/storage \
    && chown -R askmydocs:askmydocs /app /data
USER askmydocs

VOLUME ["/data"]
EXPOSE 8000

# Readiness is the meaningful check: an empty index means the service can only
# abstain, so it should not be receiving traffic yet.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/readyz', timeout=4).status==200 else 1)"

# Index on first boot if the volume is empty, then serve. Ingestion is idempotent
# and content-hashed, so a restart with an existing index is a no-op.
CMD ["sh", "-c", "askmydocs ingest || true; exec uvicorn askmydocs.api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
