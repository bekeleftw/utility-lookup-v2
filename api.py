"""
FastAPI server for the Utility Provider Lookup Engine.

Loads shapefiles on startup (60-90s), then serves lookups in <100ms.
Designed for Railway deployment as a long-lived process.
"""

import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Security, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader, APIKeyQuery
from pydantic import BaseModel, Field

from lookup_engine.config import Config
from lookup_engine.engine import LookupEngine
from lookup_engine.ai_resolver import AIResolver

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("api")

# ---------------------------------------------------------------------------
# API Key Authentication
# ---------------------------------------------------------------------------
_API_KEYS: set = set()

def _load_api_keys():
    """Load valid API keys from UTILITY_API_KEYS env var (comma-separated)."""
    raw = os.environ.get("UTILITY_API_KEYS", "")
    keys = {k.strip() for k in raw.split(",") if k.strip()}
    if not keys:
        logger.warning("No UTILITY_API_KEYS set — API authentication is DISABLED")
    else:
        logger.info(f"Loaded {len(keys)} API key(s)")
    return keys

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
_api_key_query = APIKeyQuery(name="api_key", auto_error=False)

async def require_api_key(
    header_key: Optional[str] = Security(_api_key_header),
    query_key: Optional[str] = Security(_api_key_query),
):
    """Validate API key from header or query param. Skip if no keys configured."""
    if not _API_KEYS:
        return None  # Auth disabled — no keys configured
    key = header_key or query_key
    if not key or key not in _API_KEYS:
        raise HTTPException(status_code=401, detail="Invalid or missing API key. Pass via X-API-Key header or ?api_key= query param.")
    return key

# ---------------------------------------------------------------------------
# Global engine + AI resolver (loaded once at startup)
# ---------------------------------------------------------------------------
engine: Optional[LookupEngine] = None
ai_resolver: Optional[AIResolver] = None


def _load_engine_background():
    """Load engine + AI resolver in a background thread so the server starts fast."""
    global engine, ai_resolver
    logger.info("Loading lookup engine (shapefiles)...")
    t0 = time.time()

    # Load .env if present
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ.setdefault(key.strip(), val.strip())

    config = Config()
    google_key = os.environ.get("GOOGLE_API_KEY", "") or os.environ.get("GOOGLE_MAPS_API_KEY", "")
    if google_key:
        config.google_api_key = google_key
        config.geocoder_type = "chained"
        logger.info("Geocoder: Census → Google fallback (chained)")
    else:
        logger.info("Geocoder: Census only (no GOOGLE_API_KEY or GOOGLE_MAPS_API_KEY)")

    skip_water = os.environ.get("SKIP_WATER", "").lower() in ("1", "true", "yes")
    engine = LookupEngine(config, skip_water=skip_water)

    # AI resolver for low-confidence results
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")
    if anthropic_key:
        ai_resolver = AIResolver(anthropic_key, "anthropic")
        logger.info("AI resolver: enabled (Anthropic direct)")
    elif openrouter_key:
        ai_resolver = AIResolver(openrouter_key, "openrouter", "anthropic/claude-sonnet-4-5")
        logger.info("AI resolver: enabled (OpenRouter)")
    else:
        logger.info("AI resolver: disabled (no ANTHROPIC_API_KEY or OPENROUTER_API_KEY)")

    elapsed = time.time() - t0
    logger.info(f"Engine ready in {elapsed:.1f}s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start engine loading in background, yield immediately so server accepts connections."""
    global _API_KEYS
    _API_KEYS = _load_api_keys()

    loader = threading.Thread(target=_load_engine_background, daemon=True)
    loader.start()

    yield

    # Shutdown: save caches
    if engine:
        engine.state_gis.save_disk_cache()
    logger.info("Shutdown complete.")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Utility Provider Lookup API",
    description="Look up electric, gas, water, sewer, and internet providers for any US address.\n\n"
                "**Authentication:** Pass your API key via `X-API-Key` header or `?api_key=` query parameter.",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------
class ProviderResponse(BaseModel):
    provider_name: str
    canonical_id: Optional[str] = None
    eia_id: Optional[int] = None
    utility_type: str
    confidence: float
    match_method: str = "none"
    is_deregulated: bool = False
    deregulated_note: Optional[str] = None
    polygon_source: Optional[str] = None
    needs_review: bool = False
    alternatives: list = Field(default_factory=list)
    catalog_id: Optional[int] = None
    catalog_title: Optional[str] = None
    id_match_score: int = 0
    id_confident: bool = False
    phone: Optional[str] = None
    website: Optional[str] = None


class InternetProviderResponse(BaseModel):
    name: str
    technology: str
    tech_code: str
    max_down: float
    max_up: float
    low_latency: bool


class InternetResponse(BaseModel):
    providers: list[InternetProviderResponse] = Field(default_factory=list)
    provider_count: int = 0
    has_fiber: bool = False
    has_cable: bool = False
    max_download_speed: float = 0
    source: str = "fcc_bdc"
    confidence: float = 0.95


class LookupResponse(BaseModel):
    address: str
    lat: float
    lon: float
    geocode_confidence: float
    electric: Optional[ProviderResponse] = None
    gas: Optional[ProviderResponse] = None
    water: Optional[ProviderResponse] = None
    sewer: Optional[ProviderResponse] = None
    trash: Optional[ProviderResponse] = None
    internet: Optional[InternetResponse] = None
    lookup_time_ms: int
    timestamp: str


class HealthResponse(BaseModel):
    status: str
    engine_loaded: bool
    uptime_seconds: float


_start_time = time.time()

# Protected sources that the AI resolver should NOT override
_PROTECTED_SOURCE_KEYWORDS = {"eia_zip", "eia_id", "hifld", "state_gis", "epa"}


def _is_protected_source(source_str: str) -> bool:
    if not source_str:
        return False
    s = source_str.lower()
    return any(kw in s for kw in _PROTECTED_SOURCE_KEYWORDS)


def _try_ai_resolve(result, address: str):
    """Run AI resolver on utility results that need review (low confidence)."""
    import re

    state_m = re.search(r",\s*([A-Z]{2})\s+\d{5}", address)
    state = state_m.group(1) if state_m else ""
    zip_m = re.search(r"\b(\d{5})(?:-\d{4})?\s*$", address)
    zip_code = zip_m.group(1) if zip_m else ""
    city_m = re.search(r",\s*([^,]+?)\s*,\s*[A-Z]{2}", address)
    city = city_m.group(1).strip() if city_m else ""

    for utype in ("electric", "gas", "water", "sewer"):
        pr = getattr(result, utype, None)
        if not pr or not pr.needs_review or not pr.alternatives:
            continue

        # Build candidates: primary + alternatives
        candidates = [
            {"provider": pr.provider_name, "confidence": pr.confidence,
             "source": pr.polygon_source or ""},
        ]
        for alt in pr.alternatives:
            candidates.append({
                "provider": alt.get("provider", ""),
                "confidence": alt.get("confidence", 0.5),
                "source": alt.get("source", "alternative"),
            })

        if len(candidates) < 2:
            continue

        try:
            ai_result = ai_resolver.resolve(
                address=address, state=state, utility_type=utype,
                candidates=candidates, zip_code=zip_code, city=city,
            )
        except Exception as e:
            logger.warning(f"AI resolver error for {address[:50]}/{utype}: {e}")
            continue

        if not ai_result:
            continue

        # Post-resolution guard: distinguish alternative promotion from true overrides
        if ai_result["provider"] != pr.provider_name and _is_protected_source(pr.polygon_source or ""):
            alt_names = [a.get("provider", "") for a in pr.alternatives]
            ai_is_alt = any(
                ai_result["provider"].lower() in a.lower() or a.lower() in ai_result["provider"].lower()
                for a in alt_names if len(a) >= 4 and len(ai_result["provider"]) >= 4
            )
            if not ai_is_alt:
                continue  # BLOCK — true override of authoritative source

        # Apply AI result
        pr.provider_name = ai_result["provider"]
        pr.confidence = ai_result["confidence"]
        pr.polygon_source = ai_result["source"]
        pr.needs_review = pr.confidence < 0.80

    return result


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/health", response_model=HealthResponse)
async def health():
    """Health check — Railway uses this to know the service is alive."""
    return HealthResponse(
        status="ok" if engine else "loading",
        engine_loaded=engine is not None,
        uptime_seconds=round(time.time() - _start_time, 1),
    )


@app.get("/lookup", response_model=LookupResponse)
async def lookup(
    address: str = Query(..., description="Full US address to look up", min_length=5),
    _key: str = Depends(require_api_key),
):
    """
    Look up utility providers for a US address.

    Returns electric, gas, water, and sewer providers with confidence scores.
    """
    if not engine:
        raise HTTPException(status_code=503, detail="Engine is still loading. Try again in ~60 seconds.")

    try:
        result = engine.lookup(address)
    except Exception as e:
        logger.error(f"Lookup error for '{address}': {e}")
        raise HTTPException(status_code=500, detail=f"Lookup failed: {str(e)}")

    # AI resolver disabled — relying on spatial/data lookup only
    # if ai_resolver:
    #     result = _try_ai_resolve(result, address)

    return JSONResponse(content=result.to_dict())


@app.post("/lookup", response_model=LookupResponse)
async def lookup_post(
    address: str = Query(..., description="Full US address to look up", min_length=5),
    _key: str = Depends(require_api_key),
):
    """POST variant of lookup (same behavior, for clients that prefer POST)."""
    return await lookup(address=address)


class BatchRequest(BaseModel):
    addresses: list[str] = Field(..., description="List of addresses to look up", max_length=100)


class BatchResponse(BaseModel):
    results: list[LookupResponse]
    total: int
    lookup_time_ms: int


@app.post("/lookup/batch", response_model=BatchResponse)
async def lookup_batch(req: BatchRequest, _key: str = Depends(require_api_key)):
    """
    Batch lookup — up to 100 addresses at once.

    Each address is looked up sequentially (geocoding rate limits apply).
    """
    if not engine:
        raise HTTPException(status_code=503, detail="Engine is still loading. Try again in ~60 seconds.")

    if not req.addresses:
        raise HTTPException(status_code=400, detail="No addresses provided.")

    t0 = time.time()
    results = []
    for addr in req.addresses:
        try:
            result = engine.lookup(addr.strip())
            results.append(result.to_dict())
        except Exception as e:
            logger.error(f"Batch lookup error for '{addr}': {e}")
            results.append({
                "address": addr,
                "lat": 0.0, "lon": 0.0,
                "geocode_confidence": 0.0,
                "electric": None, "gas": None, "water": None,
                "sewer": None, "trash": None,
                "lookup_time_ms": 0,
                "timestamp": "",
                "error": str(e),
            })

    total_ms = int((time.time() - t0) * 1000)
    return JSONResponse(content={
        "results": results,
        "total": len(results),
        "lookup_time_ms": total_ms,
    })
