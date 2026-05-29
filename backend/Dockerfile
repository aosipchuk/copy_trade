FROM python:3.12-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv
RUN pip install uv --no-cache-dir

# Install Python deps (separate layer for caching)
COPY pyproject.toml .
RUN uv sync --no-dev --frozen 2>/dev/null || uv sync --no-dev

# Copy source
COPY . .

ENV PYTHONPATH=/app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

EXPOSE 8000
