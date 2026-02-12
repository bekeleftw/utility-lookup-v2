"""
FastAPI server for the Utility Provider Lookup Engine.

Loads shapefiles on startup (60-90s), then serves lookups in <100ms.
Designed for Railway deployment as a long-lived process.
"""

import asyncio
import hashlib
import hmac as hmac_mod
import json
import logging
import os
import random
import secrets
import string
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Query, Request, Security, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import APIKeyHeader, APIKeyQuery
from fastapi.staticfiles import StaticFiles
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

# Serve static files from /public (for leadgen JS)
_public_dir = Path(__file__).parent / "public"
if _public_dir.exists():
    app.mount("/js", StaticFiles(directory=str(_public_dir / "js")), name="static_js")


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


@app.delete("/cache", dependencies=[Depends(require_api_key)])
async def clear_cache():
    """Clear the lookup cache. Use after deploying fixes to avoid stale results."""
    if not engine:
        raise HTTPException(status_code=503, detail="Engine is still loading.")
    count = engine.cache.clear()
    logger.info(f"Cache cleared: {count} entries removed")
    return {"cleared": count}


@app.get("/lookup", response_model=LookupResponse)
async def lookup(
    address: str = Query(..., description="Full US address to look up", min_length=5),
    no_cache: bool = Query(False, description="Skip cache and force fresh lookup"),
    _key: str = Depends(require_api_key),
):
    """
    Look up utility providers for a US address.

    Returns electric, gas, water, and sewer providers with confidence scores.
    """
    if not engine:
        raise HTTPException(status_code=503, detail="Engine is still loading. Try again in ~60 seconds.")

    try:
        # First try with cache; if result is a geocode failure (lat=0), retry without cache
        result = engine.lookup(address, use_cache=not no_cache)
        if result.lat == 0.0 and result.lon == 0.0 and not no_cache:
            result = engine.lookup(address, use_cache=False)
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


# ---------------------------------------------------------------------------
# V1 Compatibility Layer (for Webflow utility-lookup-tool UI)
# ---------------------------------------------------------------------------


def _provider_to_v1(provider_dict, utility_type: str) -> list:
    """Convert new ProviderResponse dict to old v1 format array."""
    if not provider_dict:
        return []
    p = provider_dict

    # Build other_providers from alternatives
    other_providers = []
    for alt in (p.get("alternatives") or []):
        other_providers.append({
            "name": alt.get("provider", ""),
            "phone": alt.get("phone") or "NOT AVAILABLE",
            "website": alt.get("website") or "NOT AVAILABLE",
            "confidence": alt.get("confidence", 0.5),
            "is_propane": False,
        })

    # Build deregulated object
    is_dereg = p.get("is_deregulated", False)
    dereg_note = p.get("deregulated_note", "") or ""
    deregulated = {
        "has_choice": is_dereg,
        "message": dereg_note if is_dereg else "",
        "choice_website": "",
    }

    return [{
        "name": p.get("provider_name", ""),
        "phone": p.get("phone") or "NOT AVAILABLE",
        "website": p.get("website") or "NOT AVAILABLE",
        "confidence_score": round((p.get("confidence", 0) or 0) * 100),
        "confidence": "verified" if (p.get("confidence", 0) or 0) >= 0.85
                      else "high" if (p.get("confidence", 0) or 0) >= 0.70
                      else "medium" if (p.get("confidence", 0) or 0) >= 0.50
                      else "low",
        "source": p.get("polygon_source", ""),
        "_source": p.get("polygon_source", ""),
        "type": None,
        "deregulated": deregulated,
        "other_providers": other_providers,
        "service_check_url": None,
    }]


def _internet_to_v1(internet_dict) -> dict:
    """Convert new InternetResponse dict to old v1 format."""
    if not internet_dict:
        return {"providers": []}
    return internet_dict  # Already close to old format


def _result_to_v1(result_dict: dict) -> dict:
    """Convert full new LookupResponse dict to old v1 format."""
    return {
        "address": result_dict.get("address", ""),
        "location": {
            "lat": result_dict.get("lat", 0),
            "lon": result_dict.get("lon", 0),
            "matched_address": result_dict.get("address", ""),
        },
        "utilities": {
            "electric": _provider_to_v1(result_dict.get("electric"), "electric"),
            "gas": _provider_to_v1(result_dict.get("gas"), "gas"),
            "water": _provider_to_v1(result_dict.get("water"), "water"),
            "sewer": _provider_to_v1(result_dict.get("sewer"), "sewer"),
            "internet": _internet_to_v1(result_dict.get("internet")),
        },
    }


@app.get("/api/lookup")
async def v1_lookup(
    address: str = Query(..., min_length=5),
    utilities: str = Query("electric,gas,water,sewer,internet"),
):
    """V1 compatibility endpoint — returns old-format response for Webflow UI."""
    if not engine:
        raise HTTPException(status_code=503, detail="Engine is still loading.")

    try:
        result = engine.lookup(address)
        if result.lat == 0.0 and result.lon == 0.0:
            result = engine.lookup(address, use_cache=False)
    except Exception as e:
        logger.error(f"V1 lookup error for '{address}': {e}")
        return JSONResponse(content={"error": str(e)}, status_code=500)

    result_dict = result.to_dict()
    v1 = _result_to_v1(result_dict)

    # Filter to only requested utility types
    requested = set(u.strip().lower() for u in utilities.split(","))
    for utype in ["electric", "gas", "water", "sewer", "internet"]:
        if utype not in requested:
            v1["utilities"][utype] = [] if utype != "internet" else {"providers": []}

    return JSONResponse(content=v1)


@app.get("/api/lookup/stream")
async def v1_lookup_stream(
    address: str = Query(..., min_length=5),
    utilities: str = Query("electric,gas,water,sewer,internet"),
):
    """V1 SSE streaming endpoint — sends results progressively for Webflow UI."""
    if not engine:
        raise HTTPException(status_code=503, detail="Engine is still loading.")

    requested = set(u.strip().lower() for u in utilities.split(","))

    async def event_stream():
        try:
            result = engine.lookup(address)
            if result.lat == 0.0 and result.lon == 0.0:
                result = engine.lookup(address, use_cache=False)
        except Exception as e:
            yield f"data: {json.dumps({'event': 'error', 'message': str(e)})}\n\n"
            return

        result_dict = result.to_dict()

        # Send geocode event
        yield f"data: {json.dumps({'event': 'geocode', 'data': {'lat': result_dict.get('lat', 0), 'lon': result_dict.get('lon', 0), 'matched_address': result_dict.get('address', '')}})}\n\n"

        # Send each utility type
        for utype in ["electric", "gas", "water", "sewer", "internet"]:
            if utype not in requested:
                continue
            await asyncio.sleep(0.05)  # Small delay for streaming feel

            if utype == "internet":
                inet = _internet_to_v1(result_dict.get("internet"))
                yield f"data: {json.dumps({'event': 'internet', 'data': inet})}\n\n"
            else:
                providers = _provider_to_v1(result_dict.get(utype), utype)
                data = providers[0] if providers else None
                yield f"data: {json.dumps({'event': utype, 'data': data})}\n\n"

        yield f"data: {json.dumps({'event': 'complete'})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/feedback")
async def v1_feedback(request_body: dict = {}):
    """V1 feedback endpoint — accepts feedback from Webflow UI and logs it."""
    logger.info(f"V1 feedback received: {json.dumps(request_body)[:500]}")
    return JSONResponse(content={"status": "ok"})


# ---------------------------------------------------------------------------
# Batch endpoint
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Lead-Gen Endpoints (external lookup tool on utilityprofit.com)
# ---------------------------------------------------------------------------

# Airtable config
AIRTABLE_API_KEY = os.getenv("AIRTABLE_API_KEY")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID")
LEADGEN_LOOKUPS_TABLE_ID = os.getenv("LEADGEN_LOOKUPS_TABLE_ID", "LeadGen_Lookups")
LEADGEN_REFCODES_TABLE_ID = os.getenv("LEADGEN_REFCODES_TABLE_ID", "LeadGen_RefCodes")
LEADGEN_COMPANIES_TABLE_ID = os.getenv("LEADGEN_COMPANIES_TABLE_ID", "LeadGen_Companies")

# Anti-scraping: short-lived HMAC tokens
LEADGEN_TOKEN_SECRET = os.getenv("LEADGEN_TOKEN_SECRET", secrets.token_hex(32))
_used_tokens: dict = {}  # token -> expiry timestamp
_TOKEN_TTL = 60  # seconds
_TOKEN_CLEANUP_INTERVAL = 300
_last_token_cleanup = time.time()

# Whitelisted emails/IPs that bypass rate limiting
LEADGEN_WHITELIST_EMAILS = {"mark@utilityprofit.com"}
LEADGEN_WHITELIST_IPS = {"104.6.39.39"}

# Shared httpx client for Airtable calls
_http_client = httpx.AsyncClient(timeout=10.0)


def _cleanup_expired_tokens():
    global _last_token_cleanup
    now = time.time()
    if now - _last_token_cleanup < _TOKEN_CLEANUP_INTERVAL:
        return
    _last_token_cleanup = now
    expired = [t for t, exp in _used_tokens.items() if exp < now]
    for t in expired:
        del _used_tokens[t]


def _generate_leadgen_token() -> str:
    ts = str(int(time.time()))
    nonce = secrets.token_hex(8)
    payload = f"{ts}:{nonce}"
    sig = hmac_mod.new(LEADGEN_TOKEN_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{payload}:{sig}"


def _validate_leadgen_token(token: str) -> bool:
    _cleanup_expired_tokens()
    if not token:
        return False
    parts = token.split(":")
    if len(parts) != 3:
        return False
    ts_str, nonce, sig = parts
    try:
        ts = int(ts_str)
    except ValueError:
        return False
    if time.time() - ts > _TOKEN_TTL:
        return False
    payload = f"{ts_str}:{nonce}"
    expected = hmac_mod.new(LEADGEN_TOKEN_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac_mod.compare_digest(sig, expected):
        return False
    if token in _used_tokens:
        return False
    _used_tokens[token] = time.time() + _TOKEN_TTL
    return True


def _airtable_headers() -> dict:
    return {"Authorization": f"Bearer {AIRTABLE_API_KEY}", "Content-Type": "application/json"}


def _airtable_url(table_id: str) -> str:
    return f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{table_id}"


def _get_real_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def _resolve_ref_code(ref_code: str):
    """Look up ref_code in Airtable. Returns email or 'valid' or None."""
    if not ref_code or not AIRTABLE_API_KEY:
        return None
    try:
        url = _airtable_url(LEADGEN_COMPANIES_TABLE_ID)
        params = {"filterByFormula": f"{{ref_id}}='{ref_code}'", "maxRecords": "1"}
        resp = await _http_client.get(url, headers=_airtable_headers(), params=params)
        if resp.status_code == 200:
            records = resp.json().get("records", [])
            if records:
                return "valid"
        url = _airtable_url(LEADGEN_REFCODES_TABLE_ID)
        params = {"filterByFormula": f"{{ref_code}}='{ref_code}'"}
        resp = await _http_client.get(url, headers=_airtable_headers(), params=params)
        if resp.status_code == 200:
            records = resp.json().get("records", [])
            if records:
                return records[0].get("fields", {}).get("email")
    except Exception as e:
        logger.error(f"Error resolving ref_code: {e}")
    return None


async def _count_recent_lookups(email: str = None, ip_address: str = None) -> int:
    if email and email.lower() in LEADGEN_WHITELIST_EMAILS:
        return 0
    if ip_address and ip_address in LEADGEN_WHITELIST_IPS:
        return 0
    if not AIRTABLE_API_KEY:
        return 0
    try:
        url = _airtable_url(LEADGEN_LOOKUPS_TABLE_ID)
        twenty_four_hours_ago = (datetime.utcnow() - timedelta(hours=24)).isoformat()
        conditions = []
        if email:
            conditions.append(f"{{email}}='{email}'")
        if ip_address:
            conditions.append(f"{{ip_address}}='{ip_address}'")
        if not conditions:
            return 0
        formula = f"AND(IS_AFTER({{created_at}}, '{twenty_four_hours_ago}'), OR({','.join(conditions)}))"
        params = {"filterByFormula": formula}
        resp = await _http_client.get(url, headers=_airtable_headers(), params=params)
        if resp.status_code == 200:
            return len(resp.json().get("records", []))
    except Exception as e:
        logger.error(f"Error counting lookups: {e}")
    return 0


async def _log_leadgen_lookup(ref_code, email, ip_address, address, utilities, results, source="organic"):
    if not AIRTABLE_API_KEY:
        return
    try:
        url = _airtable_url(LEADGEN_LOOKUPS_TABLE_ID)
        data = {
            "fields": {
                "ref_code": ref_code or "",
                "email": email or "",
                "ip_address": ip_address,
                "address_searched": address,
                "utilities_requested": utilities,
                "results_json": json.dumps(results) if results else "",
                "cta_clicked": False,
                "created_at": datetime.utcnow().isoformat(),
                "source": "cold_email" if ref_code else source,
            }
        }
        await _http_client.post(url, headers=_airtable_headers(), json=data)
    except Exception as e:
        logger.error(f"Error logging leadgen lookup: {e}")


def _format_leadgen_provider(provider_dict) -> dict:
    """Extract name/phone/website from a v2 provider dict for leadgen response."""
    if not provider_dict:
        return None
    return {
        "name": provider_dict.get("provider_name", ""),
        "phone": provider_dict.get("phone") or "",
        "website": provider_dict.get("website") or "",
    }


# ── Leadgen Endpoints ─────────────────────────────────────────────────────

@app.get("/api/leadgen/token")
async def leadgen_get_token():
    """Issue a short-lived single-use token for the next leadgen lookup."""
    token = _generate_leadgen_token()
    return JSONResponse(content={"token": token})


@app.post("/api/leadgen/lookup")
async def leadgen_lookup(request: Request):
    """Main leadgen lookup endpoint with tracking and limits."""
    data = await request.json()

    token = data.get("token")
    if not _validate_leadgen_token(token):
        logger.warning(f"Leadgen: invalid/missing token from {_get_real_ip(request)}")
        return JSONResponse(
            content={"status": "error", "message": "Invalid or expired request token. Please refresh and try again."},
            status_code=403,
        )

    address = data.get("address")
    utilities_str = data.get("utilities", "electric,gas,water")
    email = data.get("email")
    ref_code = data.get("ref_code")

    if not address:
        return JSONResponse(content={"status": "error", "message": "Address is required"}, status_code=400)

    if not email and not ref_code:
        return JSONResponse(content={"status": "error", "message": "Email or ref_code is required"}, status_code=400)

    # Resolve ref_code to email if needed
    if ref_code and not email:
        resolved = await _resolve_ref_code(ref_code)
        if not resolved:
            return JSONResponse(content={"status": "error", "message": "Invalid ref code"}, status_code=400)
        if resolved != "valid":
            email = resolved

    ip_address = _get_real_ip(request)

    # Check limits (5 per 24 hours per email or IP)
    lookup_count = await _count_recent_lookups(email=email, ip_address=ip_address)
    if lookup_count >= 5:
        return JSONResponse(content={
            "status": "limit_exceeded",
            "message": "You've reached the search limit. Book a demo for unlimited access.",
        })

    if not engine:
        return JSONResponse(content={"status": "error", "message": "Engine is loading, try again shortly."}, status_code=503)

    try:
        utility_list = [u.strip() for u in utilities_str.split(",")]
        result = engine.lookup(address)
        result_dict = result.to_dict()

        formatted_results = {}
        for util_type in utility_list:
            provider = result_dict.get(util_type)
            if util_type == "internet":
                inet = provider or {}
                providers = inet.get("providers", []) if isinstance(inet, dict) else []
                formatted_results["internet"] = [
                    {
                        "name": p.get("name", ""),
                        "technology": p.get("technology", ""),
                        "max_download_mbps": p.get("max_download_mbps", 0),
                        "max_upload_mbps": p.get("max_upload_mbps", 0),
                    }
                    for p in providers[:5]
                ]
            elif provider:
                fmt = _format_leadgen_provider(provider)
                formatted_results[util_type] = [fmt] if fmt else []
            else:
                formatted_results[util_type] = []

        # Log to Airtable (fire and forget)
        asyncio.create_task(
            _log_leadgen_lookup(ref_code, email, ip_address, address, utilities_str, formatted_results)
        )

        searches_remaining = max(0, 5 - lookup_count - 1)

        return JSONResponse(content={
            "status": "success",
            "utilities": formatted_results,
            "searches_remaining": searches_remaining,
        })

    except Exception as e:
        logger.error(f"Leadgen lookup error: {e}")
        return JSONResponse(content={"status": "error", "message": "Lookup failed"}, status_code=500)


@app.get("/api/leadgen/check-limit")
async def leadgen_check_limit(request: Request, email: str = None, ref: str = None):
    """Check if user can search before they try."""
    if ref and not email:
        resolved = await _resolve_ref_code(ref)
        if resolved and resolved != "valid":
            email = resolved

    ip_address = _get_real_ip(request)
    lookup_count = await _count_recent_lookups(email=email, ip_address=ip_address)
    searches_remaining = max(0, 5 - lookup_count)

    return JSONResponse(content={"can_search": searches_remaining > 0, "searches_remaining": searches_remaining})


@app.get("/api/leadgen/resolve-ref")
async def leadgen_resolve_ref(ref: str = None):
    """Resolve a ref code to its personalization data."""
    if not ref or not AIRTABLE_API_KEY:
        return JSONResponse(content={"success": False, "data": None})

    try:
        # First try LeadGen_Companies table
        url = _airtable_url(LEADGEN_COMPANIES_TABLE_ID)
        params = {"filterByFormula": f"{{ref_id}}='{ref}'", "maxRecords": "1"}
        resp = await _http_client.get(url, headers=_airtable_headers(), params=params)

        if resp.status_code == 200:
            records = resp.json().get("records", [])
            if records:
                fields = records[0].get("fields", {})
                return JSONResponse(content={
                    "success": True,
                    "data": {
                        "company_name": fields.get("company_name"),
                        "company_city": fields.get("company_city"),
                        "logo_url": fields.get("logo_url"),
                        "pms_name": fields.get("pms_name"),
                        "pms_color": fields.get("pms_color"),
                        "pms_logo_url": fields.get("pms_logo_url"),
                        "address_1_street": fields.get("address_1_street"),
                        "address_1_city": fields.get("address_1_city"),
                        "address_2_street": fields.get("address_2_street"),
                        "address_2_city": fields.get("address_2_city"),
                        "address_3_street": fields.get("address_3_street"),
                        "address_3_city": fields.get("address_3_city"),
                    },
                })

        # Fallback to LeadGen_RefCodes table
        url = _airtable_url(LEADGEN_REFCODES_TABLE_ID)
        params = {"filterByFormula": f"{{ref_code}}='{ref}'", "maxRecords": "1"}
        resp = await _http_client.get(url, headers=_airtable_headers(), params=params)

        if resp.status_code == 200:
            records = resp.json().get("records", [])
            if records:
                fields = records[0].get("fields", {})
                return JSONResponse(content={
                    "success": True,
                    "data": {
                        "email": fields.get("email"),
                        "company_name": fields.get("company_name"),
                        "company_city": fields.get("company_city"),
                        "logo_url": fields.get("logo_url"),
                        "pms_name": fields.get("pms_name"),
                        "pms_color": fields.get("pms_color"),
                        "pms_logo_url": fields.get("pms_logo_url"),
                        "address_1_street": fields.get("address_1_street"),
                        "address_1_city": fields.get("address_1_city"),
                        "address_2_street": fields.get("address_2_street"),
                        "address_2_city": fields.get("address_2_city"),
                        "address_3_street": fields.get("address_3_street"),
                        "address_3_city": fields.get("address_3_city"),
                    },
                })
    except Exception as e:
        logger.error(f"Error resolving ref_code: {e}")

    return JSONResponse(content={"success": False, "data": None})


@app.post("/api/leadgen/track-cta")
async def leadgen_track_cta(request: Request):
    """Track when someone clicks the CTA button."""
    data = await request.json()
    email = data.get("email")
    ref_code = data.get("ref_code")

    if not email and not ref_code:
        return JSONResponse(content={"error": "email or ref_code required"}, status_code=400)

    if not AIRTABLE_API_KEY:
        return JSONResponse(content={"success": True})

    try:
        url = _airtable_url(LEADGEN_LOOKUPS_TABLE_ID)
        conditions = []
        if email:
            conditions.append(f"{{email}}='{email}'")
        if ref_code:
            conditions.append(f"{{ref_code}}='{ref_code}'")

        formula = f"OR({','.join(conditions)})"
        params = {
            "filterByFormula": formula,
            "sort[0][field]": "created_at",
            "sort[0][direction]": "desc",
            "maxRecords": "1",
        }

        resp = await _http_client.get(url, headers=_airtable_headers(), params=params)
        if resp.status_code == 200:
            records = resp.json().get("records", [])
            if records:
                record_id = records[0]["id"]
                update_url = f"{url}/{record_id}"
                await _http_client.patch(update_url, headers=_airtable_headers(), json={"fields": {"cta_clicked": True}})

        return JSONResponse(content={"success": True})
    except Exception as e:
        logger.error(f"Error tracking CTA: {e}")
        return JSONResponse(content={"success": True})


@app.post("/api/leadgen/generate-ref")
async def leadgen_generate_ref(request: Request):
    """Generate a new ref code for cold email campaigns."""
    data = await request.json()
    email = data.get("email")
    campaign = data.get("campaign", "")

    if not email:
        return JSONResponse(content={"error": "email required"}, status_code=400)

    if not AIRTABLE_API_KEY:
        return JSONResponse(content={"error": "Airtable not configured"}, status_code=500)

    try:
        chars = string.ascii_lowercase + string.digits
        for _ in range(10):
            ref_code = "".join(random.choice(chars) for _ in range(6))
            existing = await _resolve_ref_code(ref_code)
            if not existing:
                break
        else:
            return JSONResponse(content={"error": "Could not generate unique ref code"}, status_code=500)

        url = _airtable_url(LEADGEN_REFCODES_TABLE_ID)
        payload = {
            "fields": {
                "ref_code": ref_code,
                "email": email,
                "campaign": campaign,
                "created_at": datetime.utcnow().isoformat(),
            }
        }
        await _http_client.post(url, headers=_airtable_headers(), json=payload)

        return JSONResponse(content={"ref_code": ref_code, "url": f"https://www.utilityprofit.com/utility-lookup-tool?ref={ref_code}"})
    except Exception as e:
        logger.error(f"Error generating ref code: {e}")
        return JSONResponse(content={"error": "Failed to generate ref code"}, status_code=500)
