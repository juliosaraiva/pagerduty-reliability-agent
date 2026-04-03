FROM python:3.11-slim AS base

WORKDIR /app

# Install system dependencies for Copilot CLI
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY pyproject.toml requirements.txt ./
COPY reliability_agent/__init__.py reliability_agent/__init__.py
RUN pip install --no-cache-dir .

# Copy application code
COPY main.py ./
COPY reliability_agent/ ./reliability_agent/

ENTRYPOINT ["reliability-digest"]
CMD ["generate"]
