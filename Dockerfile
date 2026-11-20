# ── Dockerfile for NFD (Nassim Fast Downloader) Headless Server ──
FROM python:3.11-slim AS builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy project definition and source
COPY pyproject.toml README.md ./
COPY src/ ./src/

# Install application and production dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir . uvicorn[standard] fastapi

# ── Runtime Stage ──
FROM python:3.11-slim

WORKDIR /app

# Create downloads and config directories
RUN mkdir -p /downloads /root/.nassimdl

# Copy installed site-packages and binaries from builder
COPY --from=builder /usr/local /usr/local
COPY pyproject.toml README.md ./
COPY src/ ./src/

# Environment variables
ENV PYTHONUNBUFFERED=1
ENV NASSIMDL_OUTPUT_DIR=/downloads

# Expose API & Chrome Interceptor Port
EXPOSE 8000

# Mount points for persistent downloads and configuration
VOLUME ["/downloads", "/root/.nassimdl"]

# Default entry point: FastAPI headless backend with REST & WebSocket
CMD ["uvicorn", "nassimdl.api.server:app", "--host", "0.0.0.0", "--port", "8000"]
