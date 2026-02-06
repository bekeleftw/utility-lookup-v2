# Windsurf Prompt 8: Point-in-Polygon Lookup Engine Build
## February 6, 2026

Run this AFTER prompts 6, 7 are complete and OpenEI additions have been reviewed/approved.

**This is the core of the rebuild.** It replaces the current 60+ ArcGIS API calls, 3-4 OpenAI round trips, BrightData proxy, and Playwright scraping with a local spatial lookup targeting sub-2-second response times.

---

```
Build a new utility provider lookup engine that takes a property address and returns the correct electric, gas, and water providers using local data and spatial lookups. No external API calls at query time except geocoding.

## Architecture Overview

The lookup flow is:

1. INPUT: Street address string
2. GEOCODE: Address → (lat, lon) via geocoding API (only external call)
3. SPATIAL LOOKUP: Point-in-polygon against local shapefiles
4. NORMALIZE: Map shapefile provider names → canonical provider names
5. SCORE: Ensemble confidence scoring from multiple data sources
6. OUTPUT: Provider results with confidence scores

Target: sub-2-second total response time (geocoding ~200-500ms, spatial lookup ~10-50ms, normalize+score ~10ms).

## File Structure

Create these files in the project:

```
lookup_engine/
  __init__.py
  engine.py           # Main LookupEngine class
  geocoder.py         # Geocoding wrapper (pluggable: Google, Census, Nominatim)
  spatial_index.py    # Shapefile loading and point-in-polygon queries
  scorer.py           # Ensemble confidence scoring
  cache.py            # Address → result cache (SQLite)
  models.py           # Data models / result types
  config.py           # Configuration (file paths, thresholds, etc.)
  tests/
    test_engine.py
    test_spatial.py
    test_scorer.py
```

## Step 1: Spatial Index (spatial_index.py)

Load three shapefiles into memory at startup using geopandas + spatial indexing (rtree/pygeos):

### Electric
- Source: `electric-retail-service-territories-shapefile/Electric_Retail_Service_Territories.shp`
- 2,931 records nationwide
- Key fields: NAME, STATE, TYPE, HOLDING_CO, CNTRL_AREA, CUSTOMERS, geometry

### Gas  
- Source: `240245-V1/` (natural gas service territories — find the .shp or .geojson inside)
- Key fields: NAME, STATE, geometry

### Water
- Source: `CWS_Boundaries_Latest/CWS_2_1.gpkg` (GeoPackage, 552 MB)
- This is an EPA Community Water System boundary file
- Key fields: PWSID, PWS_NAME, geometry

### Loading Rules

1. Load ALL records nationwide — do NOT filter by STATE (AEP Texas is stored under STATE=OK, see HIFLD_TX_TDU_REPORT.md)
2. Build an R-tree spatial index for each layer at startup for fast point-in-polygon queries
3. Store the loaded GeoDataFrames in memory — they're the lookup tables
4. Log load time and record counts at startup
5. Water file is 552 MB — may need to optimize loading (consider filtering to relevant states or converting to a more efficient format)

### Point-in-Polygon Query

```python
def query_point(self, lat: float, lon: float, utility_type: str) -> list[dict]:
    """
    Returns all polygons containing the point, sorted by area (smallest first).
    Each result includes: name, state, type, area_km2, geometry_id, raw_attributes
    """
```

**CRITICAL: Sort results by polygon area ascending (smallest first).** This implements the "smallest-polygon-wins" disambiguation for overlapping territories. For Texas TDU overlaps:
- Co-op/municipal polygons are smallest → highest priority (correct for non-deregulated areas)
- TNMP sub-polygons are small → second priority  
- CenterPoint is medium → third
- AEP territories are large → fourth
- Oncor is largest → default/fallback

## Step 2: Geocoder (geocoder.py)

Create a pluggable geocoder interface:

```python
class Geocoder:
    def geocode(self, address: str) -> GeocodedAddress:
        """Returns lat, lon, confidence, components (city, state, zip)"""
```

Implement at minimum:
- **CensusGeocoder** — free, no API key, US addresses only, ~200-500ms (use geocoding.geo.census.gov/geocoder/)
- **GoogleGeocoder** — faster, more accurate, requires API key (placeholder for later)

The geocoder should also extract structured address components (state, city, ZIP) from the response. These are used by the scorer for validation.

## Step 3: Normalization Bridge

After the spatial query returns a shapefile provider name (e.g., "ONCOR ELECTRIC DELIVERY COMPANY LLC"), we need to map it to our canonical provider.

```python
def resolve_provider(self, shapefile_name: str, eia_id: int = None, state: str = None) -> ProviderResult:
```

Resolution order:
1. **EIA ID match** — if the shapefile record has an EIA ID and it matches a canonical provider's eia_id field, use that (highest confidence)
2. **Exact name match** — run shapefile_name through normalize_provider() 
3. **Fuzzy name match** — use the fuzzy matcher from prompt 6
4. **Pass-through** — if no canonical match, return the shapefile name cleaned up (strip "INC", "LLC", "CO", "CORP" suffixes, title case)

For each resolution, record the match method used (eia_id, exact, fuzzy, passthrough) — this feeds into the confidence score.

## Step 4: Ensemble Scorer (scorer.py)

Each lookup can produce evidence from multiple sources. The scorer combines them into a final confidence score.

```python
class EnsembleScorer:
    def score(self, evidence: list[Evidence]) -> ScoredResult:
```

### Evidence Sources and Weights

| Source | Base Confidence | Description |
|--------|----------------|-------------|
| Tenant-verified | 0.95 | Address exists in our 87K/384K verified data with a provider match |
| Boundary polygon (EIA ID match) | 0.90 | Point falls in shapefile polygon, matched to canonical by EIA ID |
| Boundary polygon (name match) | 0.85 | Point falls in shapefile polygon, matched to canonical by name |
| Boundary polygon (fuzzy match) | 0.75 | Point falls in shapefile polygon, matched to canonical by fuzzy name |
| County-level EIA-861 | 0.70 | EIA-861 Service_Territory says this utility serves this county |
| ZIP-level default | 0.50 | Historical lookup data suggests this provider for this ZIP |
| No match | 0.00 | No provider found |

### Scoring Rules

1. If multiple polygons contain the point (overlap zones), score each one and return the highest-scoring
2. If a polygon match is also tenant-verified, boost confidence to 0.98 (multiple independent sources agree)
3. For Texas deregulated addresses (polygon TYPE is TDU or CNTRL_AREA is ERCOT), flag the result as deregulated and return the TDU name, not a REP
4. Lubbock P&L special case: it's tagged MUNICIPAL in the shapefile but is now a TDU (deregulated since Jan 2024). Treat as deregulated.
5. Never return confidence > 0.98 — nothing is 100% certain

### Result Model

```python
@dataclass
class ProviderResult:
    provider_name: str          # Consumer-facing display name
    canonical_id: str           # Key in canonical_providers.json (if matched)
    eia_id: int | None          # EIA ID (if known)
    utility_type: str           # electric, gas, water, sewer, trash
    confidence: float           # 0.0 - 0.98
    match_method: str           # eia_id, exact, fuzzy, passthrough, tenant_verified
    is_deregulated: bool        # True for ERCOT TDU areas
    deregulated_note: str | None  # e.g., "Address is in Oncor TDU territory. Tenant chooses REP."
    polygon_source: str | None  # e.g., "HIFLD Electric Retail Service Territories"

@dataclass  
class LookupResult:
    address: str
    lat: float
    lon: float
    geocode_confidence: float
    electric: ProviderResult | None
    gas: ProviderResult | None
    water: ProviderResult | None
    sewer: ProviderResult | None
    trash: ProviderResult | None
    lookup_time_ms: int
    timestamp: str
```

## Step 5: Cache (cache.py)

SQLite-based cache keyed on normalized address string (lowercase, standardized abbreviations).

```python
class LookupCache:
    def get(self, address: str) -> LookupResult | None
    def put(self, address: str, result: LookupResult, ttl_days: int = 90)
    def invalidate(self, address: str)
```

Cache hit should bypass geocoding and spatial lookup entirely — return in <5ms.

## Step 6: Main Engine (engine.py)

```python
class LookupEngine:
    def __init__(self, config: Config):
        # Load shapefiles into spatial index (one-time, ~30-60 seconds)
        # Load canonical_providers.json
        # Load deregulated_reps.json
        # Initialize geocoder
        # Initialize cache
    
    def lookup(self, address: str) -> LookupResult:
        # 1. Check cache
        # 2. Geocode
        # 3. Spatial query (electric, gas, water in parallel if possible)
        # 4. Normalize each result
        # 5. Score each result
        # 6. Check for tenant-verified data match
        # 7. Handle deregulated market logic
        # 8. Cache result
        # 9. Return
    
    def lookup_batch(self, addresses: list[str]) -> list[LookupResult]:
        # Batch processing with progress logging
```

## Step 7: Tests

Write tests using known addresses:

```python
# Texas deregulated - should return TDU, not REP
("1600 Pennsylvania Ave, Dallas, TX 75201", "electric", "Oncor")

# Texas municipal - should return CPS Energy directly  
("100 Military Plaza, San Antonio, TX 78205", "electric", "CPS Energy")

# Texas co-op - should return co-op directly
("100 Main St, Johnson City, TX 78636", "electric", "Pedernales Electric Cooperative")

# Standard regulated utility
("233 S Wacker Dr, Chicago, IL 60606", "electric", "Commonwealth Edison")

# Gas lookup
("233 S Wacker Dr, Chicago, IL 60606", "gas", "Peoples Gas")

# Multiple utility types
("100 Main St, Columbus, OH 43215", "electric", "AEP Ohio")
("100 Main St, Columbus, OH 43215", "gas", "Columbia Gas of Ohio")
```

Test that:
- Lookup returns in under 2 seconds (after initial shapefile load)
- Cache hit returns in under 10ms  
- Texas deregulated addresses return TDU with is_deregulated=True
- Texas municipal/co-op addresses return the utility with is_deregulated=False
- Overlapping polygon zones return the most specific (smallest) match
- Gas and water lookups work independently
- Unknown addresses return confidence=0 gracefully (no crash)
- Batch lookups work and show progress

## Step 8: Startup Script

Create `run_engine.py` — a simple CLI that:
1. Loads the engine (prints load time)
2. Accepts an address as argument or reads from stdin
3. Prints the result as formatted JSON
4. Optionally runs in batch mode with a CSV input

```bash
python run_engine.py "1600 Pennsylvania Ave, Dallas, TX 75201"
python run_engine.py --batch addresses.csv --output results.csv
```

## Important Notes

1. **No external APIs at query time except geocoding.** Everything else is local shapefiles.
2. **Do NOT use AI/LLM calls anywhere in this engine.** Pure deterministic logic.
3. **The Census geocoder is free but rate-limited.** For batch processing, add a small delay between requests or implement the Google geocoder option.
4. **Shapefile loading is slow (~30-60 sec) but only happens once at startup.** This is acceptable.
5. **Water boundaries are 552 MB.** Consider loading only the states where we have properties, or converting the GeoPackage to a spatial database format (SpatiaLite/PostGIS) if memory is an issue.
6. **EIA ID is the join key.** The spatial index returns a shapefile NAME; the normalization bridge uses the EIA ID from the shapefile record to link to canonical_providers.json. This is why prompt 7's EIA ID backfill must be done first.
7. **Sewer and trash are NOT in any federal shapefile.** For now, return null for these utility types. They'll be populated later from tenant data and municipal lookups.
8. **This engine replaces the current pipeline entirely.** It does not call any of the existing ArcGIS REST APIs, OpenAI endpoints, BrightData proxies, or Playwright scrapers.

## Dependencies

Install these before running:
```
pip install geopandas shapely rtree pyproj requests fiona
```

For the Census geocoder: no API key needed (free US Census Bureau service).
For Google geocoder: will need GOOGLE_MAPS_API_KEY in environment (implement as placeholder).
```
