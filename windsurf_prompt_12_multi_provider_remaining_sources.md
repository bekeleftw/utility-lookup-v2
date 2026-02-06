# Windsurf Prompt 12: Multi-Provider Results + Remaining Data Sources
## February 6, 2026

Two goals: (1) return multiple providers for low-confidence results instead of forcing a single answer, and (2) port the remaining data sources from the old codebase.

```
## PART A: Multi-Provider Results for Low Confidence

### Current behavior:
The engine picks ONE winner from overlapping polygons and returns only that. Low-confidence results (ZIP fallbacks, state defaults) return a single provider that might be wrong.

### New behavior:
Return ALL candidate providers with their source and confidence score. Let the consumer decide.

#### Response format change:

For HIGH confidence results (>= 0.80 from a single authoritative source):
```json
{
  "electric": {
    "provider": "Duke Energy Carolinas",
    "confidence": 0.92,
    "source": "state_gis_NC",
    "eia_id": "5416",
    "is_deregulated": false,
    "alternatives": []
  }
}
```

For LOW confidence results (< 0.80 OR multiple conflicting sources):
```json
{
  "electric": {
    "provider": "Duke Energy Carolinas",
    "confidence": 0.72,
    "source": "eia_zip",
    "eia_id": "5416",
    "is_deregulated": false,
    "needs_review": true,
    "alternatives": [
      {"provider": "Jones-Onslow EMC", "confidence": 0.68, "source": "hifld"},
      {"provider": "Pee Dee Electric Coop", "confidence": 0.55, "source": "findenergy_city"}
    ]
  }
}
```

#### Implementation:

1. In `engine.py`, change the lookup methods to COLLECT results from all sources rather than short-circuiting at the first hit:

```python
def _lookup_electric(self, lat, lon, state, zip_code, city):
    candidates = []
    
    # Priority 1: State GIS
    state_result = self.state_gis.query(lat, lon, state, "electric")
    if state_result:
        candidates.append({
            "provider": state_result["name"],
            "confidence": 0.92,
            "source": f"state_gis_{state.lower()}"
        })
    
    # Priority 2: HIFLD shapefile
    hifld_results = self.spatial.query_point(lat, lon, "electric")
    if hifld_results:
        resolved = self._resolve_overlap(hifld_results, state, "electric")
        for i, r in enumerate(resolved[:3]):  # top 3
            candidates.append({
                "provider": r["name"],
                "confidence": 0.82 - (i * 0.05),  # 0.82, 0.77, 0.72
                "source": "hifld"
            })
    
    # Priority 3: EIA ZIP
    eia_result = self.eia_lookup.lookup_by_zip(zip_code)
    if eia_result:
        candidates.append({
            "provider": eia_result,
            "confidence": 0.70,
            "source": "eia_zip"
        })
    
    # Priority 4: FindEnergy
    fe_result = self.findenergy.lookup(city, state, "electric") if city else None
    if fe_result:
        candidates.append({
            "provider": fe_result,
            "confidence": 0.65,
            "source": "findenergy_city"
        })
    
    # Normalize all candidates
    for c in candidates:
        normalized = self.normalizer.normalize(c["provider"], "electric")
        c["provider"] = normalized.display_name
        c["eia_id"] = normalized.eia_id
    
    # Deduplicate: if multiple sources agree, boost confidence
    candidates = self._deduplicate_and_boost(candidates)
    
    # Sort by confidence descending
    candidates.sort(key=lambda c: c["confidence"], reverse=True)
    
    if not candidates:
        return None
    
    # Build result
    primary = candidates[0]
    result = {
        "provider": primary["provider"],
        "confidence": primary["confidence"],
        "source": primary["source"],
        "eia_id": primary.get("eia_id"),
        "needs_review": primary["confidence"] < 0.80,
        "alternatives": candidates[1:5]  # up to 4 alternatives
    }
    
    return result
```

2. Add a `_deduplicate_and_boost()` method:
```python
def _deduplicate_and_boost(self, candidates):
    """If multiple sources agree on the same provider, boost confidence."""
    from collections import defaultdict
    groups = defaultdict(list)
    
    for c in candidates:
        # Group by normalized provider name
        key = c["provider"].lower().strip()
        groups[key].append(c)
    
    deduped = []
    for key, group in groups.items():
        best = max(group, key=lambda c: c["confidence"])
        if len(group) > 1:
            # Multiple sources agree — boost confidence by 0.05 per additional source
            boost = min(0.10, 0.05 * (len(group) - 1))
            best["confidence"] = min(0.98, best["confidence"] + boost)
            best["source"] += f" (+{len(group)-1} sources agree)"
        deduped.append(best)
    
    return deduped
```

3. IMPORTANT: For the batch validation, the comparison logic should use the PRIMARY provider (first in list) for accuracy scoring, but also check if the tenant answer appears ANYWHERE in the alternatives. If the correct answer is in alternatives but not primary, that's a new category: **MATCH_ALT** — the engine found the right answer but didn't rank it first.

Add to batch_validate.py comparison categories:
```
MATCH_ALT: tenant provider matches an alternative (not primary) — engine found it but ranked it wrong
```

4. Update batch_results.csv to include:
- `engine_source` (primary source)
- `engine_confidence` (primary confidence)
- `engine_needs_review` (true/false)
- `engine_alternatives` (pipe-separated: "Provider1|Provider2|Provider3")

5. Update BATCH_VALIDATION_REPORT.md to include:
- Count of `needs_review` flags per utility type
- MATCH_ALT count (how often the right answer was in alternatives but not primary)
- Source distribution table (what % of results come from each layer)
- Source accuracy table (accuracy broken down by source — state GIS vs HIFLD vs ZIP fallback)

## PART B: Port Remaining Data Sources

### SOURCE 1: User Corrections Database

Copy from old codebase:
- `/CascadeProjects/Utility Provider scrape/data/corrections.db` → `utility-lookup-v2/data/corrections.db`
- `/CascadeProjects/Utility Provider scrape/data/electric_zip_corrections.json` → `utility-lookup-v2/data/corrections/electric_zip.json`
- `/CascadeProjects/Utility Provider scrape/data/gas_zip_corrections.json` → `utility-lookup-v2/data/corrections/gas_zip.json`
- `/CascadeProjects/Utility Provider scrape/data/water_zip_corrections.json` → `utility-lookup-v2/data/corrections/water_zip.json`

Build `lookup_engine/corrections.py`:
```python
class CorrectionsLookup:
    """Highest-priority source — human-verified corrections."""
    
    def lookup_by_address(self, address: str, utility_type: str) -> Optional[str]:
        """Exact address match in corrections DB."""
        
    def lookup_by_zip(self, zip_code: str, utility_type: str) -> Optional[str]:
        """ZIP-level correction override."""
```

Insert at PRIORITY 0 — before everything else:
```
Priority 0: User corrections (confidence 0.99) ← NEW
Priority 1: State GIS API (0.90-0.95)
Priority 2: Gas ZIP Mapping (gas only, 0.85-0.93)
Priority 3: HIFLD Shapefile (0.75-0.85)
...
```

### SOURCE 2: Remaining States ZIP Data

Copy from old codebase:
- `/CascadeProjects/Utility Provider scrape/data/remaining_states_water.json` → `utility-lookup-v2/data/remaining_states_water.json`
- `/CascadeProjects/Utility Provider scrape/data/remaining_states_electric.json` → `utility-lookup-v2/data/remaining_states_electric.json`
- `/CascadeProjects/Utility Provider scrape/data/remaining_states_gas.json` → `utility-lookup-v2/data/remaining_states_gas.json`

These have ZIP-to-provider mappings with metadata (dominance %, sample count, confidence). Use as fallback between HIFLD and EIA/FindEnergy.

Read the files to understand the format, then build a loader. Example expected format:
```json
{
  "28078": {
    "provider": "Duke Energy Carolinas",
    "dominance": 0.94,
    "sample_count": 47,
    "confidence": 0.85
  }
}
```

### SOURCE 3: Georgia EMC Handling

Copy: `/CascadeProjects/Utility Provider scrape/georgia_emc.py`

GA has 872 electric mismatches and no state GIS endpoint. The Georgia EMC module has specific co-op territory logic. Read the file, understand the lookup method, and integrate as a state-specific handler for GA electric (similar to TX deregulated handling).

### SOURCE 4: Special Districts (if time allows)

The special districts data (TX MUDs, FL CDDs, CO metro districts) primarily affects WATER lookups. These are high-priority for water accuracy but complex to port.

Check the size and format of:
- `/CascadeProjects/Utility Provider scrape/data/special_districts/processed/`

If the files are simple JSON with coordinates/ZIP mappings, port them. If they require complex shapely geometry, defer to a later prompt.

## VERIFICATION

After all changes:

1. Test multi-provider output:
   - Query an address in an overlap area (e.g., Jacksonville FL)
   - Verify result has primary + alternatives with different sources
   - Verify `needs_review: true` when primary confidence < 0.80

2. Test corrections override:
   - If corrections.db has any entries, verify they win over all other sources
   
3. Test source tracking:
   - Run --limit 100
   - Verify every row in batch_results.csv has engine_source filled in
   - Verify source distribution table in report

4. Full 91K batch run:
   - Include all new sources + fallbacks
   - Compare to previous run (83.9% electric, 79.1% gas, 85.5% water)
   - Report MATCH_ALT count
   - Report needs_review count
   - Report source distribution
   - Report per-source accuracy
```
