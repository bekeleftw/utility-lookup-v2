# Windsurf Prompt 9.1: Pre-Batch Fixes
## February 6, 2026

The 1,000-row test revealed three issues that need fixing before the full 87K batch run.

```
## FIX 1 (CRITICAL): Water Layer Attribute Bug

"Austin Energy" is appearing as a water provider for addresses in Manhattan KS, Durham NC, and other cities far from Austin TX. Austin Energy is an electric utility — it should never appear as a water result.

Debug steps:
1. Open spatial_index.py and find the water layer query method
2. Check which column/attribute is being returned as the water provider name
3. The CWS GeoPackage (CWS_2_1.gpkg) has specific fields — print the schema:
   ```python
   import geopandas as gpd
   gdf = gpd.read_file("CWS_Boundaries_Latest/CWS_2_1.gpkg", rows=5)
   print(gdf.columns.tolist())
   print(gdf.head())
   ```
4. The water provider name should come from a field like PWS_NAME, SYSTEM_NAME, or similar — NOT from a field that contains electric utility names
5. Check if the water query is accidentally querying the electric layer or mixing up layer references

Fix the attribute extraction so it returns the correct water system name. Verify with these test cases:
- Manhattan, KS → should NOT return "Austin Energy"
- Durham, NC → should NOT return "Austin Energy"  
- Austin, TX → should return something like "City of Austin Water" or similar
- Chicago, IL → should return "CHICAGO" or "City of Chicago" (from the water layer, pop ~2.7M)
- Phoenix, AZ → should return "PHOENIX CITY OF" (from the water layer)

## FIX 2 (HIGH): Nationwide Overlap Resolution

The smallest-polygon-wins heuristic is wrong outside of Texas too. The 1,000-row test shows the same pattern everywhere:

| Engine (wrong) | Tenant (correct) | State |
|---|---|---|
| Pud No 1 Of Whatcom County | Puget Sound Energy | WA |
| Osage Valley Elec Coop | City of Harrisonville | MO |
| Beaches Energy Services | JEA | FL |
| City Of Mesa | Salt River Project | AZ |
| Shenandoah Valley Elec Coop | Dominion Energy | VA |
| Flint Hills Rural E C A | Evergy | KS |
| Reedy Creek Improvement Dist | OUC | FL |
| Oncor | Pedernales Electric Coop | TX |

Root cause: HIFLD co-op and municipal polygons are overgeneralized — their boundaries extend beyond the actual service territory, overlapping with the IOU that truly serves most addresses in that area.

The current approach: "smallest polygon wins" → picks the co-op/municipal every time since they have smaller territories.

The CORRECT approach: When multiple polygons overlap, prefer the provider with MORE CUSTOMERS in the overlap area — which is almost always the large IOU, not the small co-op whose generalized boundary spills into IOU territory.

### Implementation: Customer-Weighted Overlap Resolution

The HIFLD electric shapefile has a CUSTOMERS field on every record. Use it:

```python
def _resolve_overlap(self, candidates: list[dict], state: str, utility_type: str) -> list[dict]:
    """Resolve overlapping polygons using customer-weighted priority."""
    
    if not candidates or len(candidates) == 1:
        return candidates
    
    # Texas electric has its own special logic
    if state == "TX" and utility_type == "electric":
        return self._resolve_texas_overlap(candidates)
    
    # For all other states: 
    # 1. If a municipal with <5000 km² overlaps a larger utility,
    #    the municipal MAY be correct (genuine small municipal territory)
    #    BUT only if it has significant customers (>10,000)
    # 2. Otherwise, prefer the utility with more customers
    
    # Sort by CUSTOMERS descending — the utility serving more people 
    # in the region is more likely to be correct for any given address
    candidates.sort(key=lambda c: c.get("customers", 0), reverse=True)
    
    return candidates
```

Wait — this is too aggressive. Some small municipals genuinely serve specific neighborhoods. The problem isn't that small utilities exist in the overlap, it's that HIFLD's polygon BOUNDARIES are wrong (overgeneralized).

Better approach — **hybrid scoring**:

For each candidate polygon at an overlap point, compute a priority score:
- Start with `customers` count as base score
- If polygon area < 1,000 km² (genuine local utility), multiply score by 2.0
- If polygon area > 50,000 km² (probably overgeneralized), multiply score by 0.5
- If TYPE == "MUNICIPAL" and customers > 50,000, multiply by 1.5 (real city utility)
- If TYPE == "COOPERATIVE" and area > 10,000 km², multiply by 0.3 (overgeneralized rural co-op)

Sort by score descending. This way:
- Large IOUs with millions of customers win over small rural co-ops with overgeneralized boundaries
- Genuine municipal utilities (Austin Energy 533K customers, CPS Energy 918K) still win in their real territory
- Small co-ops (Flint Hills 5K customers, 20K km² area) get deprioritized

### Important: Don't break what already works

The existing Texas TDU priority logic (`_resolve_texas_overlap`) should be PRESERVED and called first for TX electric. The new hybrid scoring is for all other states and utility types.

Test the fix against the 1,000-row test results — rerun and compare. The 243 electric MISMATCHes should drop significantly.

## FIX 3 (MEDIUM): Batch Geocoding in batch_validate.py

The 1,000-row test ran at ~420ms/address, suggesting it's using single-call geocoding, not the batch endpoint.

Update batch_validate.py to use this flow:

```
Phase 1: Batch Geocode (minutes)
  - Read all addresses from CSV
  - Check SQLite cache for each — separate into cached vs uncached
  - Send uncached addresses through CensusGeocoder.geocode_batch() in 10K chunks
  - Store all results in SQLite cache
  - Log: "Geocoded X addresses: Y cached, Z batch-geocoded, W failed"

Phase 2: Spatial Lookup (minutes)  
  - For each address with coordinates (from cache):
    - Point-in-polygon for electric, gas, water
    - Normalize + score
    - Compare to tenant
    - Write to results CSV
  - This should be ~10-50ms per address = ~75 minutes for 91K
  
Phase 3: Google Fallback (optional, minutes)
  - If --geocoder chained and GOOGLE_API_KEY is set:
    - Collect all addresses that failed Census geocoding
    - Send through Google one at a time
    - Rerun spatial + compare for those addresses
    - Update results CSV
```

The key change: geocoding happens in a SEPARATE upfront phase, not interleaved with spatial queries. This lets the batch endpoint work efficiently.

Add timing to each phase:
```
Phase 1: Geocoding — 91,000 addresses, 84,200 cached, 6,800 batch-geocoded in 4m 12s
Phase 2: Spatial lookup — 91,000 addresses in 68m 30s (avg 45ms/address)
Phase 3: Google fallback — 312 addresses in 2m 15s
Total: 74m 57s
```

## Verification

After all three fixes:
1. Rerun --limit 1000 with same first 1000 rows
2. Water results should no longer contain "Austin Energy" for non-Austin addresses
3. Electric accuracy should improve from 62.7% — target 75%+ on the 1K sample
4. Runtime for 1K rows should be under 2 minutes (not 7+ minutes)
5. Print before/after comparison:
   - Electric: 62.7% → X%
   - Gas: 64.9% → X%  
   - Water: 64.8% → X%
```
