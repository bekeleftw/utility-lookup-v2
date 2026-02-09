"""
FastAPI server for the Utility Provider Lookup Engine.

Loads shapefiles on startup (60-90s), then serves lookups in <100ms.
Designed for Railway deployment as a long-lived process.
"""

import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
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
# Global engine + AI resolver (loaded once at startup)
# ---------------------------------------------------------------------------
engine: Optional[LookupEngine] = None
ai_resolver: Optional[AIResolver] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load the engine on startup, clean up on shutdown."""
    global engine
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
    google_key = os.environ.get("GOOGLE_API_KEY", "")
    if google_key:
        config.google_api_key = google_key

    skip_water = os.environ.get("SKIP_WATER", "").lower() in ("1", "true", "yes")
    engine = LookupEngine(config, skip_water=skip_water)

    # AI resolver for low-confidence results
    global ai_resolver
    openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")
    if openrouter_key:
        ai_resolver = AIResolver(openrouter_key, "openrouter", "anthropic/claude-sonnet-4-5")
        logger.info("AI resolver: enabled (Claude Sonnet)")
    else:
        logger.info("AI resolver: disabled (no OPENROUTER_API_KEY)")

    elapsed = time.time() - t0
    logger.info(f"Engine ready in {elapsed:.1f}s")

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
    description="Look up electric, gas, water, and sewer providers for any US address.",
    version="1.0.0",
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

        # Post-resolution guard: don't let AI replace protected-source primary
        if ai_result["provider"] != pr.provider_name and _is_protected_source(pr.polygon_source or ""):
            continue

        # Apply AI result
        pr.provider_name = ai_result["provider"]
        pr.confidence = ai_result["confidence"]
        pr.polygon_source = ai_result["source"]
        pr.needs_review = pr.confidence < 0.70

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

    # AI resolver pass for low-confidence results
    if ai_resolver:
        result = _try_ai_resolve(result, address)

    return JSONResponse(content=result.to_dict())


@app.post("/lookup", response_model=LookupResponse)
async def lookup_post(
    address: str = Query(..., description="Full US address to look up", min_length=5),
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
async def lookup_batch(req: BatchRequest):
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
