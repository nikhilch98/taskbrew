FROM python:3.12-slim AS builder

WORKDIR /app

# Install git for worktree support (builder stage only for setup)
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# Build metadata and source needed by hatchling at install time.
# README.md and LICENSE are referenced by pyproject.toml.
COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN pip install --no-cache-dir .

# Copy remaining runtime assets after install so the image carries configs,
# pipelines and plugin templates.
COPY config ./config
COPY pipelines ./pipelines
COPY plugins ./plugins

# ---- Production stage ----
FROM python:3.12-slim

WORKDIR /app

# Install only git (needed at runtime for worktree support)
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd -m -r -s /bin/bash appuser

# Copy installed packages and app code from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --from=builder /app /app

# Create data and artifact directories with correct ownership
RUN mkdir -p /app/data /app/artifacts /app/config \
    && chown -R appuser:appuser /app

# Initialize git repo for worktree support
RUN git init && git add -A && git commit -m "init" --allow-empty 2>/dev/null || true

USER appuser

EXPOSE 8420

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8420/api/health')" || exit 1

CMD ["python", "-m", "taskbrew.main", "serve"]
