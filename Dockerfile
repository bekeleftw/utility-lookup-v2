FROM python:3.11-slim

# System deps for geopandas/shapely + curl for downloading shapefiles
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgdal-dev \
    libgeos-dev \
    libproj-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Download shapefiles from GitHub Release (avoids LFS issues)
ARG SHAPEFILE_RELEASE=https://github.com/bekeleftw/utility-lookup-v2/releases/download/v1.0-shapefiles
RUN curl -L -o /tmp/electric.tar.gz ${SHAPEFILE_RELEASE}/electric-shapefile.tar.gz \
    && curl -L -o /tmp/gas.tar.gz ${SHAPEFILE_RELEASE}/gas-shapefile.tar.gz \
    && curl -L -o /tmp/water.tar.gz ${SHAPEFILE_RELEASE}/water-shapefile.tar.gz \
    && tar xzf /tmp/electric.tar.gz -C /app/ \
    && tar xzf /tmp/gas.tar.gz -C /app/ \
    && tar xzf /tmp/water.tar.gz -C /app/ \
    && rm -f /tmp/*.tar.gz

# Copy data files from build context
COPY data/ data/

# Copy application code (changes most often — last layer)
COPY lookup_engine/ lookup_engine/
COPY api.py .
COPY run_engine.py .
COPY provider_normalizer.py .

# Railway sets PORT env var
ENV PORT=8080
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=300s --retries=3 \
    CMD python -c "import requests; r = requests.get('http://localhost:${PORT}/health'); exit(0 if r.status_code == 200 else 1)"

CMD ["sh", "-c", "uvicorn api:app --host 0.0.0.0 --port $PORT --workers 1 --timeout-keep-alive 120"]
