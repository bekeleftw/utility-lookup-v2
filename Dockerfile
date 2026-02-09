FROM python:3.11-slim

# System deps for geopandas/shapely + git-lfs for fetching shapefiles
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgdal-dev \
    libgeos-dev \
    libproj-dev \
    git \
    git-lfs \
    && git lfs install \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Clone repo and pull LFS files (shapefiles are stored in LFS)
ARG GITHUB_REPO=https://github.com/bekeleftw/utility-lookup-v2.git
RUN git clone --depth 1 ${GITHUB_REPO} /tmp/repo \
    && cd /tmp/repo && git lfs pull \
    && cp -r /tmp/repo/electric-retail-service-territories-shapefile/ /app/ \
    && cp -r /tmp/repo/240245-V1/ /app/ \
    && cp -r /tmp/repo/CWS_Boundaries_Latest/ /app/ \
    && cp -r /tmp/repo/data/ /app/ \
    && rm -rf /tmp/repo

# Copy application code from build context (changes most often)
COPY lookup_engine/ lookup_engine/
COPY api.py .
COPY run_engine.py .

# Railway sets PORT env var
ENV PORT=8080
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=300s --retries=3 \
    CMD python -c "import requests; r = requests.get('http://localhost:${PORT}/health'); exit(0 if r.status_code == 200 else 1)"

CMD ["sh", "-c", "uvicorn api:app --host 0.0.0.0 --port $PORT --workers 1 --timeout-keep-alive 120"]
