# Dockerfile — PineScript v6 MCP Server
# ─────────────────────────────────────────────────────────────────────────────
# Multi-stage build: deps stage caches pip installs, app stage is slim.
#
# Usage:
#   docker build -t pinescript-mcp .
#   docker run -p 8080:8080 pinescript-mcp
#
# On Render: set PORT env var (Render injects it automatically).
# ChromaDB data (pinescript_db/) is baked into the image.
# ─────────────────────────────────────────────────────────────────────────────

# ── Stage 1: dependency builder ───────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Stage 2: runtime image ───────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application code
COPY server.py config.json ./
COPY core/ ./core/
COPY tools/ ./tools/
COPY formatters/ ./formatters/
COPY templates/ ./templates/

# Copy the pre-built ChromaDB vector store (baked into image)
# To use an external volume instead, mount at /app/pinescript_db
COPY pinescript_db/ ./pinescript_db/

# ── Runtime configuration ─────────────────────────────────────────────────────
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TRANSPORT=http \
    HOST=0.0.0.0 \
    PORT=8080 \
    LAZY_MODEL=true \
    PINESCRIPT_DB_PATH=/app/pinescript_db \
    PINESCRIPT_COLLECTION=pinescript_v6 \
    PINESCRIPT_EMBED_MODEL=all-MiniLM-L6-v2 \
    PINESCRIPT_MAX_RESULTS=20 \
    PINE_FACADE_TIMEOUT=20 \
    VALIDATION_CACHE_TTL=300 \
    LOG_LEVEL=INFO

EXPOSE 8080

# Health check: verify server process is responding (start-period allows model loading)
HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:${PORT}/sse')" || exit 1

CMD ["python", "server.py"]
