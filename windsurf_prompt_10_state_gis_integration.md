# Windsurf Prompt 10: Integrate State GIS APIs from Old Codebase
## February 6, 2026

The rebuilt engine currently uses only HIFLD national shapefiles for point-in-polygon lookups. The old codebase at `/CascadeProjects/Utility Provider scrape/` has state-level GIS API endpoints that are MORE AUTHORITATIVE than HIFLD. Integrating these is the #1 accuracy improvement available.

```
## Context

The rebuilt utility lookup engine at utility-lookup-v2/ uses HIFLD shapefiles as its only spatial data source. Accuracy is:
- Electric: 83.9%
- Gas: 79.1%
- Water: 85.5%

The old codebase at /CascadeProjects/Utility Provider scrape/ has state-level GIS API integrations that are more accurate than HIFLD (state boundaries are authoritative, HIFLD polygons are overgeneralized). These need to be ported into the new engine.

## Step 1: Extract All State GIS Endpoints

Read these files from the OLD codebase and extract every ArcGIS/GIS endpoint URL:

1. `/CascadeProjects/Utility Provider scrape/gis_utility_lookup.py` — Main file with state GIS API endpoints for electric, gas, and water
2. `/CascadeProjects/Utility Provider scrape/state_utility_verification.py` — State-specific verification logic, gas LDC mappings
3. `/CascadeProjects/Utility Provider scrape/pipeline/sources/` — All source files in this directory
4. `/CascadeProjects/Utility Provider scrape/deregulated_markets.py` — Deregulated state handling
5. `/CascadeProjects/Utility Provider scrape/georgia_emc.py` — Georgia EMC-specific logic
6. `/CascadeProjects/Utility Provider scrape/special_districts.py` — MUD/CDD/special district logic
7. `/CascadeProjects/Utility Provider scrape/municipal_utilities.py` — Municipal utility databases

Also check for any JSON data files:
8. `/CascadeProjects/Utility Provider scrape/data/gas_mappings/` — State gas JSON files (arizona.json, california.json, georgia.json, illinois.json, ohio.json)
9. `/CascadeProjects/Utility Provider scrape/data/findenergy/` — FindEnergy cache files
10. `/CascadeProjects/Utility Provider scrape/data/eia_zip_utility_lookup.json` — EIA Form 861 ZIP mapping

Create a file: `utility-lookup-v2/data/state_gis_endpoints.json` with this structure:

```json
{
  "electric": {
    "NC": {
      "url": "https://..../FeatureServer/0/query",
      "source": "NC Utilities Commission",
      "name_field": "UTILITY_NA",
      "type_field": "UTILITY_TY",
      "confidence": 0.95,
      "notes": "From gis_utility_lookup.py line XXX"
    },
    "WA": { ... },
    ...
  },
  "gas": {
    "CA": { ... },
    "OH": { ... },
    ...
  },
  "water": {
    "TX": { ... },
    "CA": { ... },
    ...
  }
}
```

For EACH endpoint, extract:
- The full URL (including /query path)
- Which field contains the utility name
- Which field contains the utility type (if available)
- Any query parameters used (outFields, where clauses, spatial reference)
- The state it covers
- Any notes about coverage or reliability from comments in the code

Expected counts based on the old codebase documentation:
- Electric: 33 states
- Gas: 13 states + 4 county-based
- Water: 18 states

## Step 2: Build State GIS Query Module

Create `utility-lookup-v2/lookup_engine/state_gis.py`:

```python
import requests
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

class StateGISLookup:
    """Query state-level GIS APIs for utility provider at a point."""
    
    def __init__(self, endpoints_file: str = "data/state_gis_endpoints.json"):
        with open(endpoints_file) as f:
            self.endpoints = json.load(f)
    
    def query(self, lat: float, lon: float, state: str, utility_type: str) -> Optional[dict]:
        """
        Query the state GIS API for this state/utility_type.
        
        Returns:
            dict with keys: name, source, confidence, raw_response
            None if no state GIS available or query fails
        """
        type_endpoints = self.endpoints.get(utility_type, {})
        state_config = type_endpoints.get(state.upper())
        
        if not state_config:
            return None  # No state GIS for this state/type combo
        
        try:
            result = self._query_arcgis(
                url=state_config["url"],
                lat=lat,
                lon=lon,
                name_field=state_config["name_field"],
                out_fields=state_config.get("out_fields", "*"),
                timeout=5
            )
            if result:
                return {
                    "name": result,
                    "source": f"state_gis_{state.lower()}",
                    "confidence": state_config.get("confidence", 0.90),
                    "state": state
                }
        except Exception as e:
            logger.warning(f"State GIS query failed for {state}/{utility_type}: {e}")
        
        return None
    
    def _query_arcgis(self, url, lat, lon, name_field, out_fields="*", timeout=5):
        """Execute an ArcGIS point-in-polygon query."""
        params = {
            "geometry": f"{lon},{lat}",
            "geometryType": "esriGeometryPoint",
            "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": out_fields,
            "returnGeometry": "false",
            "f": "json"
        }
        
        response = requests.get(url, params=params, timeout=timeout)
        response.raise_for_status()
        data = response.json()
        
        features = data.get("features", [])
        if not features:
            return None
        
        # Return the name from the first matching feature
        attributes = features[0].get("attributes", {})
        return attributes.get(name_field)
    
    def has_state_source(self, state: str, utility_type: str) -> bool:
        """Check if a state GIS source exists for this state/type."""
        return state.upper() in self.endpoints.get(utility_type, {})
```

## Step 3: Integrate into Engine

Modify `utility-lookup-v2/lookup_engine/engine.py` to use state GIS as the PRIMARY source, with HIFLD as fallback:

```python
# In the lookup flow, AFTER geocoding, BEFORE HIFLD spatial:

def lookup_utilities(self, address: str) -> dict:
    # 1. Geocode
    coords = self.geocoder.geocode(address)
    if not coords:
        return {"error": "geocoding_failed"}
    
    lat, lon = coords.lat, coords.lon
    state = coords.state  # Need state from geocoder
    
    results = {}
    
    for utility_type in ["electric", "gas", "water"]:
        # Priority 1: State GIS API (authoritative, ~0.90-0.95 confidence)
        state_result = self.state_gis.query(lat, lon, state, utility_type)
        
        if state_result:
            # Normalize the state GIS result through canonical providers
            normalized = self.normalizer.normalize(state_result["name"], utility_type)
            results[utility_type] = {
                "provider": normalized.display_name,
                "source": state_result["source"],
                "confidence": state_result["confidence"],
                "eia_id": normalized.eia_id
            }
        else:
            # Priority 2: HIFLD shapefile (fallback, ~0.75-0.85 confidence)
            hifld_results = self.spatial.query_point(lat, lon, utility_type)
            if hifld_results:
                # Existing overlap resolution + scoring logic
                resolved = self._resolve_overlap(hifld_results, state, utility_type)
                results[utility_type] = self._score_result(resolved[0], utility_type)
    
    return results
```

### CRITICAL: State from Geocoder

The Census geocoder returns state information. Make sure the geocoded result includes the state abbreviation. Check what the Census batch endpoint returns — it includes the matched address which contains the state. Parse it.

If the geocoder doesn't currently return state, add it:
- Census single: parse from `matchedAddress` field
- Census batch: parse from the matched address CSV column
- Google: parse from `address_components` with type `administrative_area_level_1`
- Fallback: parse state from the input address string (last 2-letter uppercase before ZIP)

### CRITICAL: API Call Timeout and Caching

State GIS APIs are external HTTP calls. For the batch validation run:
- Cache state GIS results in the same SQLite cache as geocoding
- Set 5-second timeout per request (some state servers are slow)
- If a state GIS endpoint fails 3 times in a row, disable it for the rest of the batch run (circuit breaker)
- Log: "State GIS hit: {state}/{utility_type} → {provider}" for debugging

For production (single lookups): the state GIS call adds ~200-500ms but gives higher confidence. This is acceptable since the total lookup is already ~400ms for geocoding.

### Batch Validation Impact

For the batch run, state GIS adds an HTTP call per address per utility type. At 3 utility types × 91K addresses = 273K API calls. With caching (many addresses share the same state), actual unique calls will be much lower. But it WILL slow down the batch run significantly.

Options:
A) Run state GIS only for addresses that are currently MISMATCH (reprocess ~12K electric + ~8K gas mismatches only)
B) Run state GIS for all addresses but with aggressive caching
C) Run state GIS for the top 10 mismatch states only (covers 80% of mismatches)

Recommendation: Start with option C — enable state GIS for these states only:

**Electric:** NC, WA, GA, FL, SC, CO, MI (covers ~5,200 of 12,143 mismatches)
**Gas:** TX (county-based), CA, OH, VA, GA (covers ~3,600 of 8,352 mismatches)
**Water:** TX, CA, NJ, WA, AZ, PA (covers high-mismatch states with state GIS)

## Step 4: Also Extract State Gas Mappings

Copy these JSON files from the old codebase to the new engine:

```
/CascadeProjects/Utility Provider scrape/data/gas_mappings/arizona.json    → utility-lookup-v2/data/gas_mappings/arizona.json
/CascadeProjects/Utility Provider scrape/data/gas_mappings/california.json → utility-lookup-v2/data/gas_mappings/california.json
/CascadeProjects/Utility Provider scrape/data/gas_mappings/georgia.json    → utility-lookup-v2/data/gas_mappings/georgia.json
/CascadeProjects/Utility Provider scrape/data/gas_mappings/illinois.json   → utility-lookup-v2/data/gas_mappings/illinois.json
/CascadeProjects/Utility Provider scrape/data/gas_mappings/ohio.json       → utility-lookup-v2/data/gas_mappings/ohio.json
```

Build a ZIP-to-gas lookup that uses these as a source between state GIS and HIFLD:

```
Gas priority: State GIS API → State gas JSON mapping → HIFLD shapefile
```

Also copy the Texas gas LDC mapping logic (ZIP prefix 750-799 with 5-digit overrides) from `state_utility_verification.py`. This alone would fix the CenterPoint vs Texas Gas Service mismatch (1,013 rows).

## Step 5: Also Extract EIA ZIP Mapping

Copy: `/CascadeProjects/Utility Provider scrape/data/eia_zip_utility_lookup.json` → `utility-lookup-v2/data/eia_zip_utility_lookup.json`

Use EIA as a verification layer for electric:
```
Electric priority: State GIS API → HIFLD shapefile → EIA ZIP mapping (verification)
```

If HIFLD and EIA agree → high confidence
If HIFLD and EIA disagree → flag for review, prefer HIFLD (address-level) but lower confidence

## Verification

After integration:

1. Test state GIS queries for known addresses:
   - Raleigh, NC → should return Duke Energy Progress (from NC state GIS, not HIFLD)
   - Seattle, WA → should return Seattle City Light (from WA state GIS)
   - Houston, TX (gas) → should return CenterPoint Energy (from TX gas mapping)
   - San Antonio, TX (water) → should return SAWS (from TX TWDB)

2. Rerun batch_validate.py --recompare-only won't work here because state GIS changes the ENGINE result, not just the comparison. Need to rerun full spatial for affected states.

3. Run batch_validate.py --limit 1000 first to verify state GIS integration works, then full run.

4. Print accuracy comparison:
   | Utility | HIFLD-only | + State GIS | Change |
   | Electric | 83.9% | X% | +Xpp |
   | Gas | 79.1% | X% | +Xpp |
   | Water | 85.5% | X% | +Xpp |

## Expected Outcomes

- Electric: 83.9% → ~88-90% (state GIS resolves most overlap disputes)
- Gas: 79.1% → ~84-86% (TX gas mapping alone fixes 1,013 rows = +2.5pp)
- Water: 85.5% → ~88-90% (state water GIS for TX, CA, NJ should help significantly)
```
