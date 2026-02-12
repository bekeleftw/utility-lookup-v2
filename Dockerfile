FROM python:3.11-slim

# System deps (libgdal needed for geopandas fallback if PostGIS unavailable)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgdal-dev \
    libgeos-dev \
    libproj-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy data files from build context
COPY data/ data/

# Copy application code
COPY lookup_engine/ lookup_engine/
COPY public/ public/
COPY api.py .
COPY run_engine.py .
COPY provider_normalizer.py .

# Railway sets PORT env var
ENV PORT=8080
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import requests; r = requests.get('http://localhost:${PORT}/health'); exit(0 if r.status_code == 200 else 1)"

CMD ["sh", "-c", "uvicorn api:app --host 0.0.0.0 --port $PORT --workers 1 --timeout-keep-alive 120"]
