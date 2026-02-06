# Windsurf Prompt 11: Fallback Layers — No Empty Results
## February 6, 2026

The engine still returns nothing for ~10% of addresses. The old system never had this problem because it had ZIP-level and county-level fallbacks after the spatial lookups. Add those now.

```
## Goal

Every geocoded address should return at least an electric provider and a gas provider (where gas service exists). The current engine gives up after HIFLD if there's no polygon hit. Add fallback layers that guarantee a result.

Current lookup chain:
1. State GIS API → result or nothing
2. HIFLD shapefile → result or nothing  
3. (nothing) ← THIS IS THE PROBLEM

New lookup chain:
1. State GIS API (confidence 0.90-0.95)
2. Gas ZIP mapping (gas only, confidence 0.85-0.90)
3. HIFLD shapefile (confidence 0.75-0.85)
4. **EIA ZIP mapping (electric only, confidence 0.70)** ← NEW
5. **FindEnergy ZIP cache (electric + gas, confidence 0.65)** ← NEW
6. **State default LDC (gas only, confidence 0.50)** ← NEW

Water has no fallback beyond the EPA layer — returning nothing for water is acceptable (well water, rural areas).

## FALLBACK 1: EIA ZIP Mapping for Electric

The file `data/eia_zip_utility_lookup.json` is already copied into the project (from Prompt 10, used for verification). Now use it as a FALLBACK source when State GIS + HIFLD both return nothing.

The file maps ZIP codes to electric utility names. It covers 33,412 ZIPs — essentially nationwide.

Integration in engine.py:

```python
def _lookup_electric(self, lat, lon, state, zip_code):
    # Priority 1: State GIS
    result = self.state_gis.query(lat, lon, state, "electric")
    if result:
        return self._finalize(result, "electric", confidence=0.92)
    
    # Priority 2: HIFLD shapefile
    hifld = self.spatial.query_point(lat, lon, "electric")
    if hifld:
        resolved = self._resolve_overlap(hifld, state, "electric")
        return self._finalize(resolved[0], "electric", confidence=0.82)
    
    # Priority 3: EIA ZIP fallback (NEW)
    eia_result = self.eia_lookup.lookup_by_zip(zip_code)
    if eia_result:
        return self._finalize_from_name(eia_result, "electric", confidence=0.70, source="eia_zip")
    
    return None
```

To use this, we need the ZIP code from the geocoded address. The Census geocoder returns the matched address which includes the ZIP. Make sure ZIP is parsed and available.

The EIA file structure (verify by reading the actual file):
- If it's a dict mapping ZIP → utility name: straightforward lookup
- If it's a dict mapping ZIP → list of utilities: return the one with the most customers or the first one
- Normalize the returned name through canonical_providers.json before returning

The EIA verification module (`eia_verification.py`) already loads this file. Either reuse that module or add a `lookup_by_zip()` method to it.

## FALLBACK 2: FindEnergy ZIP Cache

The old codebase has FindEnergy cache files:
- `/CascadeProjects/Utility Provider scrape/data/findenergy/electric_by_zip.json`
- `/CascadeProjects/Utility Provider scrape/data/findenergy/gas_by_zip.json`

Copy these into `utility-lookup-v2/data/findenergy/`.

Build a simple lookup module `lookup_engine/findenergy_lookup.py`:

```python
import json

class FindEnergyLookup:
    def __init__(self, electric_file, gas_file):
        with open(electric_file) as f:
            self.electric = json.load(f)
        with open(gas_file) as f:
            self.gas = json.load(f)
    
    def lookup(self, zip_code: str, utility_type: str) -> Optional[str]:
        data = self.electric if utility_type == "electric" else self.gas
        return data.get(zip_code)  # Returns provider name or None
```

Use as fallback AFTER EIA (for electric) or after HIFLD (for gas):

Electric chain: State GIS → HIFLD → EIA ZIP → FindEnergy ZIP
Gas chain: State GIS → Gas ZIP mappings → HIFLD → FindEnergy ZIP → State default

## FALLBACK 3: State Default Gas LDC

The old codebase has a `STATE_GAS_LDCS` dictionary in `state_utility_verification.py` that maps every state to a default gas utility. This is the absolute last resort — low confidence, but better than nothing for states with a dominant gas provider.

Extract this dictionary and save as `utility-lookup-v2/data/state_gas_defaults.json`:

```json
{
  "TX": {"provider": "Atmos Energy", "confidence": 0.45, "note": "TX has 4 LDCs, Atmos is most common by territory"},
  "CA": {"provider": "SoCalGas", "confidence": 0.40, "note": "CA has 3 major gas utilities"},
  "OH": {"provider": "Columbia Gas of Ohio", "confidence": 0.45},
  "GA": {"provider": "Atlanta Gas Light", "confidence": 0.60, "note": "AGL serves ~90% of GA"},
  "FL": {"provider": null, "confidence": 0, "note": "Limited gas infrastructure"},
  "HI": {"provider": null, "confidence": 0, "note": "No natural gas distribution"},
  ...
}
```

States with null provider (FL, HI, VT, ME, etc.) should return nothing — these states have limited/no gas infrastructure and returning a wrong default is worse than nothing.

For states with a single dominant gas utility (like GA where Atlanta Gas Light serves ~90%), confidence can be 0.55-0.60. For states with multiple large gas utilities (TX, CA, OH), confidence should be 0.40-0.45 since we're essentially guessing.

Integration:
```python
def _lookup_gas(self, lat, lon, state, zip_code):
    # Priority 1-4: State GIS → Gas ZIP mapping → HIFLD → FindEnergy
    # ... (existing chain) ...
    
    # Priority 5: State default (last resort)
    default = self.state_gas_defaults.get(state)
    if default and default.get("provider"):
        return self._finalize_from_name(
            default["provider"], "gas", 
            confidence=default["confidence"], 
            source="state_default"
        )
    
    return None
```

## FALLBACK 4: ZIP Code Extraction

All the ZIP-based fallbacks need the ZIP code. Make sure it's available:

1. Census geocoder: parse ZIP from the matched address string (last 5 digits)
2. Census batch geocoder: same — parse from matched address column
3. Google geocoder: parse from address_components with type "postal_code"
4. Input address fallback: regex extract 5-digit ZIP from the original input string

Add a `extract_zip(address: str) -> Optional[str]` utility function that tries to pull a 5-digit ZIP from any address string. This is the fallback if the geocoder doesn't return a parsed ZIP.

```python
import re

def extract_zip(address: str) -> Optional[str]:
    """Extract 5-digit ZIP code from address string."""
    match = re.search(r'\b(\d{5})(?:-\d{4})?\b', address)
    return match.group(1) if match else None
```

## IMPORTANT: Track the Source

Every result must include which source provided it. This is critical for the batch report — we need to know what % of results come from each layer.

Add a `source` field to every result:
- "state_gis_XX" (state code)
- "gas_zip_mapping"
- "hifld"
- "eia_zip"
- "findenergy_zip"
- "state_default"

In batch_validate.py, add a `engine_source` column to batch_results.csv.

In BATCH_VALIDATION_REPORT.md, add a section:

```
### Source Distribution
| Source | Electric | Gas | Water |
|--------|----------|-----|-------|
| State GIS | X% | X% | X% |
| Gas ZIP Mapping | - | X% | - |
| HIFLD Shapefile | X% | X% | X% |
| EIA ZIP | X% | - | - |
| FindEnergy ZIP | X% | X% | - |
| State Default | - | X% | - |
| No Result | X% | X% | X% |
```

## Verification

Before running the full 91K batch:

1. Test the full fallback chain with an address you know fails currently:
   - Pick 5 addresses from the TENANT_ONLY list in the current batch results
   - Run them through the updated engine
   - Verify they now return a result (even if low confidence)
   - Print: address, result, source, confidence

2. Run --limit 1000:
   - TENANT_ONLY count should drop significantly for electric
   - Every electric result should have a non-null provider (except geocoding failures)
   - Source distribution should show the fallback layers being used
   - Accuracy should not decrease (fallbacks should only fill gaps, not override better sources)

3. Then run full 91K batch

## Expected Outcomes

| Metric | Before | After | Notes |
|--------|--------|-------|-------|
| Electric TENANT_ONLY | 9,369 | <1,000 | EIA ZIP covers almost all ZIPs |
| Gas TENANT_ONLY | 8,662 | <3,000 | State defaults + FindEnergy fill gaps |
| Water TENANT_ONLY | 7,435 | ~7,000 | No new water fallbacks added |
| Electric coverage-adjusted accuracy | 73.1% | ~82% | More results, most correct |
| Gas coverage-adjusted accuracy | 62.6% | ~72% | More results from lower-confidence sources |
```
