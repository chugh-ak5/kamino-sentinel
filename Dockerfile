FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency files and install
COPY pyproject.toml requirements.txt ./
RUN pip install --no-cache-dir -e .

# Copy application source
COPY kamino_sentinel/ ./kamino_sentinel/

# Default entrypoint to CLI
ENTRYPOINT ["kamino-sentinel"]
CMD ["health"]
