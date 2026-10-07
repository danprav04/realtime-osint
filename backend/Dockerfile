# ==============================================================================
# Stage 1: Build Dependencies (Builder)
# ==============================================================================
FROM python:3.11-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ==============================================================================
# Stage 2: Production Runtime (Runner)
# ==============================================================================
FROM python:3.11-slim AS runner

WORKDIR /app

# Install runtime utilities: curl (health checks) and dumb-init (signal forwarding)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    dumb-init \
    && rm -rf /var/lib/apt/lists/*

# Create unprivileged application user & group (UID/GID 10001)
RUN groupadd --gid 10001 appgroup && \
    useradd --uid 10001 --gid 10001 --shell /bin/sh --no-create-home appuser

# Copy installed Python packages from builder stage
COPY --from=builder /install /usr/local

# Copy backend source directory
COPY --chown=appuser:appgroup . /app/backend/

# Link /app/app to /app/backend/app so both `uvicorn app.main:app` and `from backend.app...` resolve
RUN ln -s /app/backend/app /app/app

# Set execution environment variables
ENV PYTHONPATH="/app:/app/backend"
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV ENVIRONMENT=production
ENV LOG_FORMAT=json

# Switch to non-root user
USER appuser

EXPOSE 8000

# Use dumb-init to ensure proper PID 1 signal handling for SIGTERM/SIGINT
ENTRYPOINT ["/usr/bin/dumb-init", "--"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
