# Windsurf Prompt 9.2: Post-Batch Accuracy Fixes
## February 6, 2026

The 91K batch validation is complete. These fixes target the highest-impact mismatches identified in the report. All are naming/alias/comparison fixes — no spatial logic changes needed.

```
Five fixes to improve accuracy based on 91K batch validation results. These are all comparison-layer fixes — do NOT rerun geocoding or spatial queries. After implementing, rerun batch_validate.py using the cached geocoding + spatial results (should take ~10 min).

## FIX 1 (HIGH): Water Name Normalizer

Water "mismatches" are almost entirely formatting differences between CWS GeoPackage names and tenant-entered names. The top 20 water mismatches alone account for 4,284 false negatives.

Pattern examples:
- "Gilbert, Town Of" vs "Town of Gilbert AZ"
- "Raleigh, City Of" vs "City of Raleigh - NC"  
- "Fairfax County Water Authority" vs "Fairfax Water"
- "Lubbock Public Water System" vs "City Of Lubbock Water Utilities Department - TX"
- "Onslow Wtr And Sewer Authority" vs "Onslow Water and Sewer Authority - NC"
- "Kansas City Pws" vs "KC Water"
- "Lees Summit Pws" vs "Lee's Summit Water Dept"

Add a `normalize_water_name(name: str) -> str` function in provider_normalizer.py that:

1. **Strip state suffixes:** Remove trailing " - NC", " - TX", " - CA", " - VA", " - SC", " - FL", " - IN", " - KS", " - OR", " - OH", " - GA", " - MO" etc. Also strip trailing bare state abbreviations: " NC", " TX", " AZ", " VA" etc. (2-letter uppercase at end of string after a space)

2. **Strip parenthetical IDs:** Remove anything in parentheses: "(2310001)", "(Sc0410012)", "(TN)", "(WA)"

3. **Normalize reversed entity format:** Convert "X, City Of" → "City Of X" and "X, Town Of" → "Town Of X" (CWS uses "Lastname, First" format for municipalities)

4. **Expand abbreviations:**
   - "Pws" → "Public Water System"
   - "Wtr" → "Water"  
   - "Co" → "County" (when followed by space or end of string, NOT inside words)
   - "Ws" → "Water System"
   - "Dept" → "Department"
   - "Auth" → "Authority"
   - "Svc" → "Service"
   - "St" → "Saint" (when at start of name, e.g. "St Louis")

5. **Strip generic suffixes:** Remove "Water Department", "Water Utilities Department", "Water Utility", "Water System", "Public Water System", "City Utilities", "Water Dept", "Pws" — these add no distinguishing info.

6. **Extract core city name:** After all normalization, the comparison should match on the core municipality name. "Raleigh" should match "Raleigh" regardless of what's wrapped around it.

The comparison for water should be:
```python
def water_names_match(engine_name: str, tenant_name: str) -> bool:
    """Lenient water name comparison — matches on core municipality name."""
    norm_engine = normalize_water_name(engine_name)
    norm_tenant = normalize_water_name(tenant_name)
    
    # Exact match after normalization
    if norm_engine.lower() == norm_tenant.lower():
        return True
    
    # Fuzzy match at 80% threshold (lower than electric/gas because water names vary more)
    ratio = fuzz.token_sort_ratio(norm_engine.lower(), norm_tenant.lower())
    if ratio >= 80:
        return True
    
    # Check if one contains the other's core city name
    # e.g., "Fort Wayne" is in both "Fort Wayne - 3 Rivers Filtration Plant" and "Fort Wayne City Utilities"
    engine_tokens = set(norm_engine.lower().split())
    tenant_tokens = set(norm_tenant.lower().split())
    # Remove generic words
    generic = {"city", "of", "the", "water", "utilities", "utility", "department", 
               "dept", "system", "public", "authority", "county", "town", "service",
               "services", "district", "plant", "filtration", "metropolitan", "regional"}
    engine_core = engine_tokens - generic
    tenant_core = tenant_tokens - generic
    
    if engine_core and tenant_core:
        # If the core non-generic words overlap significantly
        overlap = engine_core & tenant_core
        if len(overlap) >= min(len(engine_core), len(tenant_core)):
            return True
    
    return False
```

Update the comparison logic in batch_validate.py to use `water_names_match()` for water utility comparisons instead of the generic `providers_match()`.

## FIX 2 (HIGH): Add Missing Electric Aliases

Add these aliases to canonical_providers.json:

| Canonical Name | New Alias | Mismatches Fixed |
|---|---|---|
| City Of Chattanooga - (TN) | EPB | 140 |
| City Of Chattanooga - (TN) | Electric Power Board (EPB) - TN | 133 |
| City Of Chattanooga - (TN) | Electric Power Board | 10 (est.) |
| PUD No 1 Of Clark County - (WA) | Clark Public Utilities | 242 |
| Cleveland Electric Illum | The Illuminating Company | 139 |

Check what the canonical entry names actually are in canonical_providers.json — the names above are from the HIFLD shapefile. The alias should map the tenant name to whatever the canonical entry is.

## FIX 3 (HIGH): Add Missing REP

Add "Just Energy" to deregulated_reps.json.

Just Energy (also "Just Energy Texas LP") is a licensed Texas REP.
Currently 101 addresses show as MISMATCH (Oncor vs Just Energy) that should be MATCH_TDU.

Also scan the full MISMATCH list for other potential missing REPs — any TX electric mismatch where the tenant value is a company name (not a utility) selling retail electricity plans in ERCOT.

## FIX 4 (MEDIUM): Add MATCH_PARENT Groups

Add these parent company groups to the parent_company mapping used by the comparison logic:

```python
PARENT_COMPANIES = {
    # ... existing groups ...
    
    "Alliant Energy": [
        "Alliant Energy",
        "Wisconsin Power & Light", 
        "Wisconsin Power And Light",
        "Interstate Power and Light",
        "IPL"
    ],
    
    "Enbridge": [
        "Enbridge Gas",
        "Enbridge Gas Ohio",
        "Enbridge Gas North Carolina",
        "Enbridge Gas NC",
        "Public Service NC",       # PSNC Energy, acquired by Enbridge 2019
        "Public Service Company of North Carolina",
        "PSNC Energy",
        "Vectren Energy",
        "Vectren"
    ],
    
    "Gas South": [
        "Gas South",
        "Gas South Avalon",       # Gas South plan/brand name
        "Nicor Gas",              # Gas South acquired Nicor's GA retail operations
    ],
    
    # Also add if not present:
    "FirstEnergy": [
        "FirstEnergy",
        "Cleveland Electric Illuminating",
        "Cleveland Electric Illum",
        "The Illuminating Company",
        "Ohio Edison",
        "Toledo Edison",
        "Mon Power",
        "Potomac Edison",
        "West Penn Power",
        "Jersey Central Power & Light",
        "Met-Ed"
    ],
}
```

## FIX 5 (MEDIUM): Gas State-Match Scoring

The gas layer has wrong-state results:
- Dominion Energy Utah appearing for Ohio addresses (250 mismatches)
- Cheyenne Light Fuel & Power appearing for Kansas addresses (183 mismatches)

Add a state-match check in the gas overlap resolution. When multiple gas polygons overlap:

```python
def _resolve_gas_overlap(self, candidates, address_state):
    if len(candidates) <= 1:
        return candidates
    
    # Penalize cross-state gas results
    for c in candidates:
        provider_state = c.get("state", "")
        if provider_state and address_state:
            if provider_state.upper() != address_state.upper():
                c["score"] = c.get("score", 1.0) * 0.1  # 90% penalty for wrong state
    
    candidates.sort(key=lambda c: c.get("score", 1.0), reverse=True)
    return candidates
```

This is safe because unlike electric (AEP Texas under STATE=OK), gas utilities
almost never serve areas outside their listed state. The rare exceptions
(border-town gas utilities) won't be penalized enough to lose to a same-state
competitor that doesn't exist.

## VERIFICATION

After all fixes, rerun the batch comparison ONLY (skip geocoding and spatial):

```bash
python3 batch_validate.py --recompare-only   # or whatever flag skips Phase 1+2
```

If no --recompare-only flag exists, add one. It should:
1. Read batch_results.csv (which has engine_provider and tenant_raw for every row)
2. Re-normalize and re-compare using the updated logic
3. Produce a new BATCH_VALIDATION_REPORT.md

Expected improvements:
| Utility | Before | After | Change |
|---------|--------|-------|--------|
| Electric | 82.2% | ~84% | +1.8pp |
| Gas | 76.2% | ~80% | +3.8pp |
| Water | 63.7% | ~74% | +10pp |

Print a before/after comparison table showing old vs new accuracy.
```
