# Utility Lookup Engine v2 — Technical Handoff

## Overview

A FastAPI service that takes a US street address and returns the electric, gas, water, sewer, and internet providers serving that location. Deployed on Railway. Responds in <100ms after initial startup (~60-90s shapefile loading).

**Production URL:** `https://utility-lookup-v2-production.up.railway.app`

**Repo:** `github.com/bekeleftw/utility-lookup-v2`

---

## Architecture

```
Address → Geocoder → Spatial Queries → Multi-Source Candidate Collection → Dedup & Boost → Overlap Resolution → Scoring/Normalization → Provider ID Matching → Contact Info → Response
```

### Core Flow (`lookup_engine/engine.py` → `LookupEngine.lookup()`)

1. **Cache check** — SQLite-backed, 90-day TTL. Never caches geocode failures (lat=0).
2. **Geocode** — Chained: Census Bureau (free) → Google Maps fallback. Returns lat/lon + city/state/zip/county/block_geoid.
3. **Multi-source candidate collection** — For each utility type (electric, gas, water), collects candidates from a priority chain of data sources (see below).
4. **Deduplication & confidence boost** — If multiple sources agree on the same provider, confidence is boosted (+0.05 per additional source, capped at 0.98).
5. **Overlap resolution** — Type-specific logic to pick the best candidate when polygons overlap.
6. **IOU demotion** — For electric: if primary is a large IOU (Duke, Dominion, etc.) and a co-op/municipal exists with confidence ≥0.70, the local utility is promoted.
7. **EIA verification** — Cross-checks electric provider against EIA ZIP-level data for confidence adjustment.
8. **Provider ID matching** — Maps provider name to internal catalog ID via fuzzy matching.
9. **Contact info** — Attaches phone/website from catalog.
10. **Sewer** — Inherits from water provider, checks sewer-specific catalog entries.
11. **Internet** — FCC Broadband Data Collection via Census block GEOID (PostGIS query).

### Data Source Priority Chain

For each utility type, candidates are collected from these sources in order:

| Priority | Source | Confidence | Utility Types | Notes |
|----------|--------|-----------|---------------|-------|
| 0 | User corrections (`corrections.py`) | 0.98-0.99 | All | Manual overrides by address or ZIP |
| 1 | State GIS APIs (`state_gis.py`) | 0.90 (boosted) | All | Real-time ArcGIS queries to state PUC endpoints |
| 2 | Gas ZIP mapping (`gas_mappings.py`) | 0.85-0.93 | Gas only | ZIP prefix → gas utility lookup table |
| 2.5 | Georgia EMC (`georgia_emc.py`) | 0.72-0.87 | GA electric | County → EMC mapping |
| 2.7 | County gas (`county_gas.py`) | varies | Gas (IL,PA,NY,TX) | County/city → gas utility |
| 3 | HIFLD shapefile (`spatial_index.py`) | 0.75-0.85 | Electric, Gas | Federal shapefiles, point-in-polygon |
| 3.5 | Remaining states ZIP (`remaining_states.py`) | 0.65-0.85 | All | ZIP-level fallback data |
| 3.7 | Special districts (`special_districts.py`) | varies | Water (AZ,CA,CO,FL,WA) | ZIP → water district |
| 4 | EIA ZIP fallback (`eia_verification.py`) | 0.70 | Electric only | EIA Form 861 ZIP-level data |
| 5 | FindEnergy city cache (`findenergy_lookup.py`) | 0.65 | Electric, Gas | City-level cache |
| 6 | State gas defaults (`state_gas_defaults.json`) | 0.40-0.65 | Gas only | Last resort |

### Overlap Resolution Strategies

- **Electric (general):** Customer-weighted hybrid scoring. Co-ops/municipals with area <5,000 km² beat large IOUs. Large IOUs are penalized when competing.
- **Electric (Texas):** TDU priority ranking (CenterPoint > AEP Texas > Oncor > TNMP). Co-ops/municipals win only if area <5,000 km² (filters out overgeneralized HIFLD polygons like Hilco 12K km²).
- **Gas:** Same-state preference, then smallest-area wins.
- **Water:** Smallest-area wins (city < county < regional).

### Water Name Filtering

State GIS water data (especially TX TWDB) sometimes returns subdivision/HOA names instead of actual utilities. The engine detects these by checking for water utility keywords ("water", "city of", "district", "MUD", etc.) and replaces non-utility names with "City of {city}".

---

## Key Files

```
utility-lookup-v2/
├── api.py                          # FastAPI server, all endpoints
├── lookup_engine/
│   ├── engine.py                   # Main LookupEngine class (962 lines)
│   ├── models.py                   # GeocodedAddress, ProviderResult, LookupResult dataclasses
│   ├── config.py                   # Config dataclass (shapefile paths, thresholds)
│   ├── geocoder.py                 # Census + Google geocoders, ChainedGeocoder
│   ├── spatial_index.py            # In-memory geopandas point-in-polygon
│   ├── postgis_spatial.py          # PostGIS spatial queries (used in production)
│   ├── scorer.py                   # EnsembleScorer: name normalization, confidence, contact info
│   ├── state_gis.py                # Real-time ArcGIS API queries to state PUC endpoints
│   ├── cache.py                    # SQLite lookup cache with TTL
│   ├── corrections.py              # Manual address/ZIP corrections
│   ├── gas_mappings.py             # ZIP prefix → gas utility
│   ├── county_gas.py               # County/city → gas utility (IL, PA, NY, TX)
│   ├── georgia_emc.py              # GA county → EMC mapping
│   ├── remaining_states.py         # ZIP-level fallback data
│   ├── special_districts.py        # Water districts (AZ, CA, CO, FL, WA)
│   ├── eia_verification.py         # EIA Form 861 ZIP cross-check
│   ├── findenergy_lookup.py        # City-level cache fallback
│   ├── provider_id_matcher.py      # Fuzzy match provider name → catalog ID
│   ├── internet_lookup.py          # FCC BDC broadband lookup via PostGIS
│   └── ai_resolver.py              # AI-powered disambiguation (currently disabled)
├── data/
│   ├── canonical_providers.json    # Provider name normalization map
│   ├── deregulated_reps.json       # TX REP data for deregulated markets
│   ├── state_gas_defaults.json     # Last-resort gas provider by state
│   ├── provider_catalog.json       # Internal provider catalog with IDs
│   ├── corrections_address.json    # Manual address-level corrections
│   ├── corrections_zip.json        # Manual ZIP-level corrections
│   └── ...                         # Various lookup tables
├── electric-retail-service-territories-shapefile/  # HIFLD electric shapefile
├── 240245-V1/gas_shp/              # Natural gas service territory shapefile
├── CWS_Boundaries_Latest/          # EPA Community Water System boundaries
├── Dockerfile                      # Python 3.11-slim + GDAL
├── requirements.txt                # fastapi, geopandas, shapely, rapidfuzz, etc.
└── railway.json                    # Railway deployment config
```

---

## API Endpoints

### V2 Endpoints (API key required via `X-API-Key` header or `?api_key=`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check (no auth) |
| GET | `/lookup?address=...` | Full lookup, returns v2 format |
| POST | `/lookup?address=...` | Same as GET |
| POST | `/lookup/batch` | Batch lookup, up to 100 addresses (JSON body: `{"addresses": [...]}`) |
| DELETE | `/cache` | Clear lookup cache |

### V1 Compatibility Endpoints (no auth — for Webflow UI)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/lookup?address=...&utilities=electric,gas,water,sewer,internet` | Returns old-format response |
| GET | `/api/lookup/stream?address=...&utilities=...` | SSE streaming (geocode → electric → gas → water → sewer → internet → complete) |
| POST | `/api/feedback` | Accepts feedback JSON, logs it |

### V2 Response Format

```json
{
  "address": "100 Main St, Dallas, TX 75201",
  "lat": 32.776268,
  "lon": -96.824003,
  "geocode_confidence": 0.95,
  "electric": {
    "provider_name": "Oncor",
    "confidence": 1.0,
    "polygon_source": "state_gis_tx (+3 agree)",
    "is_deregulated": true,
    "deregulated_note": "Address is in Oncor Electric Delivery TDU territory...",
    "phone": "8883136862",
    "website": "https://www.oncor.com",
    "catalog_id": 123,
    "alternatives": [{"provider": "...", "confidence": 0.8, "source": "..."}],
    "needs_review": false
  },
  "gas": { ... },
  "water": { ... },
  "sewer": { ... },
  "internet": {
    "providers": [{"name": "AT&T", "technology": "Fiber", "max_down": 5000, ...}],
    "has_fiber": true,
    "provider_count": 4
  },
  "lookup_time_ms": 45
}
```

### V1 Response Format (Webflow compatibility)

```json
{
  "address": "100 Main St, Dallas, TX 75201",
  "location": {"lat": 32.776268, "lon": -96.824003, "matched_address": "..."},
  "utilities": {
    "electric": [{
      "name": "Oncor",
      "phone": "8883136862",
      "website": "https://www.oncor.com",
      "confidence_score": 100,
      "confidence": "verified",
      "source": "state_gis_tx (+3 agree)",
      "deregulated": {"has_choice": true, "message": "...", "choice_website": ""},
      "other_providers": [{"name": "...", "confidence": 0.8}]
    }],
    "gas": [{ ... }],
    "water": [{ ... }],
    "sewer": [{ ... }],
    "internet": {"providers": [...]}
  }
}
```

---

## Deployment

- **Platform:** Railway (Docker)
- **Database:** PostGIS on Railway (spatial index + FCC broadband data)
- **Startup:** ~60-90s (loads shapefiles into PostGIS or memory). Server accepts connections immediately; returns 503 until engine is ready.
- **Environment variables:**
  - `POSTGIS_URL` — PostGIS connection string
  - `DATABASE_URL` — Same or separate DB for internet lookup
  - `GOOGLE_API_KEY` — Google Maps geocoder fallback
  - `API_KEYS` — Comma-separated API keys for v2 endpoints
  - `ANTHROPIC_API_KEY` or `OPENROUTER_API_KEY` — AI resolver (currently disabled)

---

## Recent Changes (Chronological)

1. **Chained geocoder** — Census → Google fallback when Census returns no match. Google API key stored in env.
2. **IOU demotion revert** — Aggressive IOU demotion caused -10pp regression (91.4% → 81.1%). Reverted to conservative version that only promotes co-ops/municipals with confidence ≥0.70 and non-low-quality sources.
3. **Cache improvements** — Never cache geocode failures (lat=0). Auto-retry from cache if result has lat=0. Added `DELETE /cache` endpoint.
4. **Water name filtering** — Detects subdivision/street names from state GIS water data (e.g., "CROSSBOW COURT") and replaces with "City of {city}".
5. **V1 compatibility layer** — Added `/api/lookup`, `/api/lookup/stream`, `/api/feedback` endpoints that translate v2 engine output to old format for Webflow UI.

---

## Webflow Integration

The Webflow page at `utilityprofit.com/utility-lookup-tool` has embedded JS that calls the API. To switch from the old engine to v2, change the base URL in the Tool JS:

```
OLD: https://web-production-9acc6.up.railway.app
NEW: https://utility-lookup-v2-production.up.railway.app
```

The V1 compatibility endpoints handle the format translation. No other JS changes needed.

**Note:** The site uses Webflow password protection. If CloudFront is in front of Webflow, it must forward cookies (`wf_auth`), allow POST methods, and forward `Host`/`Origin`/`Referer` headers — otherwise the password page loops.

---

## Known Issues / Pending Work

1. **Phone number cross-contamination** — Some providers show another utility's phone number (e.g., Oncor showing a water utility's phone). Likely a bug in the contact info attachment logic in `scorer.py`.
2. **MATCH_ALT ranking** — ~14,359 rows in batch validation where the correct provider is in alternatives but not primary. Needs targeted per-state approach.
3. **Full batch re-run with water** — Previous 91K-address batch was run without water. Need a fresh run with all utility types.
4. **AI resolver disabled** — The AI disambiguation system (`ai_resolver.py`) is implemented but disabled in production. It uses Claude to pick between ambiguous candidates. Guard logic prevents it from overriding authoritative sources (State GIS, HIFLD, EIA).

---

## Accuracy

Based on 91K-address batch validation:
- **Primary match rate:** ~91.4% (electric)
- **Top-3 match rate:** ~95%+ (correct provider in primary or alternatives)
- Gas and water match rates are lower due to less comprehensive shapefile coverage.

The confidence score (0-1) reflects data source quality:
- 0.90-0.98: State GIS + multi-source agreement
- 0.75-0.85: Single authoritative source (HIFLD, gas mapping)
- 0.65-0.70: Fallback sources (EIA ZIP, FindEnergy)
- <0.65: Low confidence, `needs_review=true`
