# --- Build stage: resolve Python dependencies with uv ---
FROM python:3.12-slim-bookworm AS builder

# Copy the uv binary from the official image (not needed at runtime)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Copy python project configurations
COPY pyproject.toml uv.lock ./

# Install python dependencies into /app/.venv
RUN uv sync --frozen --no-dev --no-install-project

# --- Runtime stage ---
FROM python:3.12-slim-bookworm

# Set environment variables for python and a fixed location for the browser
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv

# Install only the headless Chromium shell (the script runs headless) and its system dependencies
RUN playwright install --with-deps --only-shell chromium \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*

# Copy src code
COPY src/ ./src/

ENTRYPOINT ["python", "/app/src/downloader.py"]
