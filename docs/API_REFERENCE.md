# Utility Provider Lookup API — Reference

## Base URL

```
https://your-app.up.railway.app
```

> Replace with your actual Railway deployment URL.

---

## Authentication

All lookup endpoints require an API key. Pass it via **either**:

| Method | Example |
|--------|---------|
| **Header** (recommended) | `X-API-Key: YOUR_API_KEY` |
| **Query parameter** | `?api_key=YOUR_API_KEY` |

The `/health` endpoint does **not** require authentication.

**401 response** if the key is missing or invalid:
```json
{
  "detail": "Invalid or missing API key. Pass via X-API-Key header or ?api_key= query param."
}
```

---

## Endpoints

### 1. Health Check

```
GET /health
```

No authentication required. Use this to verify the service is running and the engine has finished loading shapefiles (~60-90s after cold start).

**Response:**
```json
{
  "status": "ok",
  "engine_loaded": true,
  "uptime_seconds": 142.3
}
```

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | `"ok"` if engine is loaded, `"loading"` if still starting |
| `engine_loaded` | boolean | Whether the lookup engine is ready |
| `uptime_seconds` | float | Seconds since server started |

---

### 2. Single Address Lookup

```
GET /lookup?address={address}
```

or

```
POST /lookup?address={address}
```

**Parameters:**

| Parameter | Location | Required | Description |
|-----------|----------|----------|-------------|
| `address` | query | yes | Full US street address (min 5 chars) |
| `X-API-Key` | header | yes | Your API key |

**Example Request (curl):**
```bash
curl -s "https://your-app.up.railway.app/lookup?address=10812+Watchful+Fox+Drive,+Austin,+TX+78748" \
  -H "X-API-Key: YOUR_API_KEY" | python3 -m json.tool
```

**Example Request (JavaScript fetch):**
```javascript
const response = await fetch(
  `https://your-app.up.railway.app/lookup?address=${encodeURIComponent("10812 Watchful Fox Drive, Austin, TX 78748")}`,
  { headers: { "X-API-Key": "YOUR_API_KEY" } }
);
const data = await response.json();
```

**Example Request (Python requests):**
```python
import requests

resp = requests.get(
    "https://your-app.up.railway.app/lookup",
    params={"address": "10812 Watchful Fox Drive, Austin, TX 78748"},
    headers={"X-API-Key": "YOUR_API_KEY"},
)
data = resp.json()
```

**Example Response:**
```json
{
  "address": "10812 Watchful Fox Dr, Austin, TX 78748",
  "lat": 30.189847,
  "lon": -97.824591,
  "geocode_confidence": 0.95,
  "electric": {
    "provider_name": "Austin Energy",
    "canonical_id": "Austin Energy",
    "eia_id": 1015,
    "utility_type": "electric",
    "confidence": 1.0,
    "match_method": "eia_id",
    "is_deregulated": false,
    "deregulated_note": null,
    "polygon_source": "state_gis_tx (+3 agree)",
    "needs_review": false,
    "alternatives": [],
    "catalog_id": 1234,
    "catalog_title": "Austin Energy",
    "id_match_score": 100,
    "id_confident": true
  },
  "gas": {
    "provider_name": "Texas Gas Service",
    "confidence": 0.95,
    "...": "..."
  },
  "water": {
    "provider_name": "City Of Austin Water & Wastewater",
    "confidence": 0.87,
    "...": "..."
  },
  "sewer": {
    "provider_name": "City of Austin Sewer - TX",
    "confidence": 0.82,
    "...": "..."
  },
  "trash": null,
  "internet": {
    "providers": [
      {
        "name": "Google Fiber",
        "technology": "Fiber",
        "tech_code": "50",
        "max_down": 8000.0,
        "max_up": 8000.0,
        "low_latency": true
      },
      {
        "name": "AT&T",
        "technology": "Fiber",
        "tech_code": "50",
        "max_down": 5000.0,
        "max_up": 5000.0,
        "low_latency": true
      },
      {
        "name": "Spectrum",
        "technology": "Cable",
        "tech_code": "40",
        "max_down": 1000.0,
        "max_up": 35.0,
        "low_latency": true
      }
    ],
    "provider_count": 3,
    "has_fiber": true,
    "has_cable": true,
    "max_download_speed": 8000.0,
    "source": "fcc_bdc",
    "confidence": 0.95
  },
  "lookup_time_ms": 85,
  "timestamp": "2026-02-10T21:00:00.000000"
}
```

---

### 3. Batch Lookup

```
POST /lookup/batch
```

Look up up to **100 addresses** in a single request.

**Headers:**

| Header | Value |
|--------|-------|
| `Content-Type` | `application/json` |
| `X-API-Key` | Your API key |

**Request Body:**
```json
{
  "addresses": [
    "10812 Watchful Fox Drive, Austin, TX 78748",
    "1619 Cinnabar Dr, Raymore, MO 64083",
    "1902 Karen Ct, Champaign, IL 61821"
  ]
}
```

**Example Request (curl):**
```bash
curl -s -X POST "https://your-app.up.railway.app/lookup/batch" \
  -H "X-API-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "addresses": [
      "10812 Watchful Fox Drive, Austin, TX 78748",
      "1619 Cinnabar Dr, Raymore, MO 64083"
    ]
  }' | python3 -m json.tool
```

**Response:**
```json
{
  "results": [
    { "address": "...", "electric": {...}, "gas": {...}, "...": "..." },
    { "address": "...", "electric": {...}, "gas": {...}, "...": "..." }
  ],
  "total": 2,
  "lookup_time_ms": 450
}
```

Each item in `results` has the same schema as the single lookup response.

---

## Response Field Reference

### Top-Level Fields

| Field | Type | Description |
|-------|------|-------------|
| `address` | string | Geocoded/normalized address |
| `lat` | float | Latitude |
| `lon` | float | Longitude |
| `geocode_confidence` | float | 0.0–1.0 geocoding confidence |
| `electric` | object \| null | Electric provider result |
| `gas` | object \| null | Gas provider result |
| `water` | object \| null | Water provider result |
| `sewer` | object \| null | Sewer provider result |
| `trash` | object \| null | Trash provider result (not yet implemented) |
| `internet` | object \| null | Internet/ISP providers from FCC BDC data |
| `lookup_time_ms` | int | Total lookup time in milliseconds |
| `timestamp` | string | ISO 8601 timestamp |

### Provider Fields (electric, gas, water, sewer)

| Field | Type | Description |
|-------|------|-------------|
| `provider_name` | string | Display name of the utility provider |
| `canonical_id` | string \| null | Internal canonical identifier |
| `eia_id` | int \| null | EIA utility ID (electric/gas only) |
| `utility_type` | string | `"electric"`, `"gas"`, `"water"`, or `"sewer"` |
| `confidence` | float | 0.0–1.0 match confidence |
| `match_method` | string | How the match was made (see below) |
| `is_deregulated` | boolean | Whether the market is deregulated (electric only) |
| `deregulated_note` | string \| null | TDU/market info for deregulated areas |
| `polygon_source` | string | Data source(s) used for the match |
| `needs_review` | boolean | True if confidence < 0.70 (may need manual verification) |
| `alternatives` | array | Other candidate providers found for this address |
| `catalog_id` | int \| null | Internal provider catalog ID |
| `catalog_title` | string \| null | Provider name in catalog |
| `id_match_score` | int | 0–100 fuzzy match score against catalog |
| `id_confident` | boolean | Whether the catalog ID match is confident |

### Match Methods

| Method | Description |
|--------|-------------|
| `eia_id` | Matched via EIA utility ID from shapefile |
| `exact` | Exact name match to canonical provider |
| `fuzzy` | Fuzzy name match (similarity ≥ 90) |
| `substring` | Substring match |
| `passthrough` | No canonical match found; cleaned raw name |

### Internet Fields

| Field | Type | Description |
|-------|------|-------------|
| `providers` | array | List of ISPs available at the address |
| `providers[].name` | string | ISP name |
| `providers[].technology` | string | Connection type: Fiber, Cable, DSL, Fixed Wireless, Satellite |
| `providers[].tech_code` | string | FCC technology code |
| `providers[].max_down` | float | Max download speed (Mbps) |
| `providers[].max_up` | float | Max upload speed (Mbps) |
| `providers[].low_latency` | boolean | Whether the connection is low-latency |
| `provider_count` | int | Number of unique ISPs |
| `has_fiber` | boolean | Whether fiber is available |
| `has_cable` | boolean | Whether cable is available |
| `max_download_speed` | float | Fastest available download speed (Mbps) |
| `source` | string | Always `"fcc_bdc"` (FCC Broadband Data Collection) |
| `confidence` | float | Always `0.95` |

### Alternatives Array

Each item in `alternatives`:

| Field | Type | Description |
|-------|------|-------------|
| `provider` | string | Alternative provider name |
| `confidence` | float | Confidence score |
| `source` | string | Data source |

---

## Error Responses

| Status | Meaning |
|--------|---------|
| `401` | Missing or invalid API key |
| `400` | Bad request (e.g., empty batch) |
| `422` | Validation error (e.g., address too short) |
| `500` | Internal server error |
| `503` | Engine still loading (~60-90s after cold start) |

---

## Rate Limits

- Single lookups: No hard rate limit, but geocoding calls are throttled internally
- Batch lookups: Max 100 addresses per request
- Cold start: Engine takes ~60-90 seconds to load shapefiles on first boot

---

## Interactive Docs

FastAPI auto-generates interactive API documentation:

- **Swagger UI:** `https://your-app.up.railway.app/docs`
- **ReDoc:** `https://your-app.up.railway.app/redoc`

You can test endpoints directly from the Swagger UI by clicking "Authorize" and entering your API key.

---

## Data Sources

| Utility | Primary Source | Coverage |
|---------|---------------|----------|
| Electric | HIFLD Electric Retail Service Territories | National |
| Gas | HIFLD Natural Gas Service Territories + State GIS | National |
| Water | EPA CWS Boundaries (44K+ systems) | National |
| Sewer | Derived from water + city/county matching | National |
| Internet | FCC Broadband Data Collection (BDC) | National (Census block level) |

Supplemental sources: EIA ZIP mappings, State GIS layers (TX, OH, NC, WI, IA, etc.), Georgia EMC boundaries, FindEnergy city data, manual corrections.
