FROM python:3.11-slim

# System deps for geopandas/shapely
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgdal-dev \
    libgeos-dev \
    libproj-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy data files
COPY data/ data/

# Copy shapefiles (large — these layers will be cached between builds
# as long as the shapefiles don't change)
COPY electric-retail-service-territories-shapefile/ electric-retail-service-territories-shapefile/
COPY 240245-V1/ 240245-V1/
COPY CWS_Boundaries_Latest/ CWS_Boundaries_Latest/

# Copy application code (changes most often — last layer)
COPY lookup_engine/ lookup_engine/
COPY api.py .
COPY run_engine.py .

# Railway sets PORT env var
ENV PORT=8080
EXPOSE 8080

# Health check for Railway
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
    CMD python -c "import requests; r = requests.get('http://localhost:${PORT}/health'); exit(0 if r.status_code == 200 else 1)"

CMD ["sh", "-c", "uvicorn api:app --host 0.0.0.0 --port ${PORT} --workers 1 --timeout-keep-alive 120"]
