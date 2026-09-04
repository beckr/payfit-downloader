# Use the official Microsoft Playwright Python base image
FROM mcr.microsoft.com/playwright/python:v1.62.0-jammy

# Set environment variables for non-interactive installs and python
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Install uv via pip
RUN pip install --no-cache-dir uv

WORKDIR /app

# Copy python project configurations
COPY pyproject.toml uv.lock ./

# Install python dependencies using uv
RUN uv sync --frozen --no-dev

# Copy src code
COPY src/ ./src/

# Entrypoint running python script via uv run
ENTRYPOINT ["uv", "run", "python", "/app/src/downloader.py"]
