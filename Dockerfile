FROM python:3.12-slim

WORKDIR /app

# Install curl for container health check
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy production source code
COPY src/ ./src/
COPY run.sh .

# Ensure data directory exists for historian
RUN mkdir -p /app/data

# Default port (Railway injects $PORT at runtime)
EXPOSE 8008

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:${PORT:-8008}/health || exit 0

# Start FastAPI server binding to Railway dynamic PORT
CMD ["python", "-m", "src.api.main"]
