# Codebase Review: utility-lookup-v2

## Summary

`utility-lookup-v2` is a well-structured multi-source utility provider lookup engine that orchestrates 11+ data sources with proper fallback chains for electric, gas, water, sewer, and internet lookups. The architecture is sound — candidates are collected from ALL sources (no short-circuiting), deduplication/boosting works correctly with a 0.98 cap, and the priority chain is implemented as documented. **However, there is one critical bug** (corrections.py queries the wrong SQLite table for address lookups), **one moderate bug** (cache doesn't restore new fields like catalog_id), and **an accuracy calculation inconsistency** between batch_validate.py and generate_review_files.py. The AI resolver has a thread-safety issue with its cache dict. The system is production-viable after fixing the critical and moderate issues; the rest are polish.

---

## Critical Issues (must fix before batch run)

### 1. `corrections.py` line 135: queries wrong table name

- `lookup_by_address()` queries `FROM corrections` but the table containing mapper corrections is `address_corrections`
- The `corrections` table exists (legacy) but has 0 rows, so ALL address-level corrections silently return None
- `lookup_by_latlon()` correctly queries `address_corrections` — only `lookup_by_address` is broken
- **Impact**: Priority 0 address corrections never fire. Mapper corrections from `import_mapper_corrections.py` are written correctly but never read back.

### 2. `cache.py` lines 116-142: `_dict_to_result()` drops new fields

- Fields NOT restored from cache: `catalog_id`, `catalog_title`, `id_match_score`, `id_confident`, `needs_review`, `alternatives`
- On cache hit, all provider ID matching results are lost
- **Impact**: Cached results return with no catalog IDs, no alternatives, and `needs_review` always defaults to False. Batch runs with `--resume` or cache-warm addresses will have incomplete output.

---

## Moderate Issues (fix soon)

### 1. Accuracy calculation inconsistency between batch_validate.py and generate_review_files.py

- `batch_validate.py` counts `MATCH_PARENT` as correct: `correct = MATCH + MATCH_TDU + MATCH_PARENT`
- `generate_review_files.py` does NOT count `MATCH_PARENT`: `correct = MATCH + MATCH_TDU` only
- The XLSX Summary sheet will show lower accuracy than the BATCH_VALIDATION_REPORT.md
- **Fix**: Align both to the same definition (recommend including MATCH_PARENT)

### 2. `ai_resolver.py`: Thread-unsafe cache and counters

- `self.cache` is a plain `dict` shared across 20 ThreadPoolExecutor workers
- `self.call_count` and `self.error_count` are incremented without locks
- CPython's GIL makes dict operations atomic for simple get/set, so data corruption is unlikely, but `call_count`/`error_count` may undercount
- **Fix**: Use `threading.Lock` for counters, or `concurrent.futures` results pattern

### 3. `batch_validate.py`: Missing `--skip-internet` flag

- `--skip-water` exists but `--skip-internet` is not implemented
- Internet lookup just silently does nothing if `DATABASE_URL` is unset, so this is low-priority

### 4. `batch_validate.py` line 1107: Phase 3 mislabeled in report

- `lines.append(f"- Phase 3 (Google fallback)")` — but Phase 3 is the AI resolver, not Google fallback
- Google fallback is part of Phase 1

### 5. Sewer not included in accuracy report

- `batch_validate.py` report loop: `for utype in ["electric", "gas", "water"]` — sewer excluded
- `generate_review_files.py` same: only computes accuracy for electric/gas/water

### 6. IOU demotion doesn't filter by source reliability

- Any local utility with confidence ≥ 0.60 can dislodge a large IOU, even from unreliable sources (e.g., FindEnergy city cache at 0.65)
- Consider requiring `alt.confidence >= 0.70` or checking `alt.polygon_source not in ("findenergy_city", "state_gas_default")`

---

## Minor Issues (nice to have)

1. **`scorer.py` line 12: `sys.path.insert` hack** — imports `provider_normalizer` from parent dir via sys.path manipulation. Would be cleaner as a package import.

2. **`provider_id_matcher.py` line 46**: Filters `UtilityTypeId` for `"2","3","4","5","6","7"` but TYPE_MAP maps internet to `"8"` — internet providers can never match from catalog.

3. **`provider_id_matcher.py` line 98**: HIFLD truncation expansion `" elec$"` uses `$` as literal string, not regex — won't match end-of-string. Should use `.endswith()` or proper regex.

4. **State GIS cache is unbounded** — for 91K addresses × 3 utility types = ~273K entries. At ~200 bytes each, this is ~55MB. Not dangerous but worth clearing between batches.

5. **Postgres connection not pooled** — `internet_lookup.py` uses a single persistent connection. Fine for sequential batch but won't work for concurrent access.

6. **Tests are NOT pytest** — `tests/test_engine.py` is a custom test harness with `global` variables, not pytest-compatible. Cannot run with `python -m pytest`.

---

## Architecture Notes

### Verified Priority Chain (matches documentation)

```
Priority 0:   User corrections (address match → 0.99, ZIP match → 0.98)
Priority 1:   State GIS API (33 electric, 13 gas, 18 water endpoints → 0.90-0.95)
Priority 2:   Gas ZIP mappings (TX, CA, IL, OH, AZ, GA → 0.80-0.93)
Priority 2.5: Georgia EMC county (GA electric only → 0.72-0.87)
Priority 2.7: County gas (IL, PA, NY, TX → 0.60-0.88)
Priority 3:   HIFLD shapefiles (electric, gas, water via spatial index → 0.60-0.85)
Priority 3.5: Remaining states ZIP data (43 electric, 51 gas, 50 water states → 0.65-0.85)
Priority 3.7: Special districts water (AZ, CA, CO, FL, WA → 0.82)
Priority 4:   EIA ZIP fallback (electric only → 0.70)
Priority 5:   FindEnergy city cache (electric + gas → 0.65)
Priority 6:   State gas defaults (gas only → 0.40-0.65)
```

### Key Design Decisions Verified

- **No short-circuiting**: ALL sources are queried — correction winners still collect alternatives
- **Deduplication boost**: `min(0.98, best + 0.05 per additional source)` — capped correctly
- **`set_conf` override**: In `_add_candidate()`, correctly overrides scorer confidence (the old capping bug was fixed)
- **EIA verification**: Adjusts from -0.05 to +0.05, bounded by `max(0.0, min(1.0, ...))`
- **Census batch geocoder**: Correctly uses `/geographies/addressbatch` (returns FIPS fields for GEOID)
- **`needs_review` flag**: Set when `confidence < 0.80`
- **Sewer inheritance**: `min(water_conf + 0.05, 0.88)` ✓ — city match = 0.82 ✓, county = 0.75 ✓
- **Internet lookup**: Queries Postgres by Census block GEOID, returns sorted by tech priority (Fiber > Cable > DSL)
- **IOU demotion**: Swaps Duke/Dominion/etc for co-ops/municipals when alt confidence ≥ 0.60

### Discrepancy Found

`sewer.needs_review` threshold uses `< 0.80` (line 309) — this is correct per spec but the sewer confidence for water inheritance (`min(water_conf + 0.05, 0.88)`) means most water-inherited sewer results will pass review if the water lookup was confident.

---

## Data File Audit

| File | Exists | Valid | Structure |
|------|--------|-------|-----------|
| `state_gis_endpoints.json` | ✓ | ✓ | 33 electric, 13 gas, 18 water state endpoints |
| `gas_mappings/texas.json` | ✓ | ✓ | ZIP prefix → utility mappings |
| `gas_mappings/california.json` | ✓ | ✓ | Same structure |
| `gas_mappings/illinois.json` | ✓ | ✓ | Same |
| `gas_mappings/ohio.json` | ✓ | ✓ | Same |
| `gas_mappings/arizona.json` | ✓ | ✓ | Same |
| `gas_mappings/georgia.json` | ✓ | ✓ | Same |
| `gas_county_lookups.json` | ✓ | ✓ | IL, PA, NY, TX counties + city overrides |
| `georgia_emcs.json` | ✓ | ✓ | 39 EMCs, 148 counties |
| `remaining_states_electric.json` | ✓ | ✓ | 43 states, 931 ZIP entries |
| `remaining_states_gas.json` | ✓ | ✓ | 51 states, 6,228 ZIP entries |
| `remaining_states_water.json` | ✓ | ✓ | 50 states, 5,212 ZIP entries |
| `special_districts_water.json` | ✓ | ✓ | ZIP → water district for AZ, CA, CO, FL, WA |
| `eia_zip_utility_lookup.json` | ✓ | ✓ | ZIP → EIA utility list |
| `findenergy/city_providers.json` | ✓ | ✓ | `STATE:city:type` → providers |
| `state_gas_defaults.json` | ✓ | ✓ | State → default gas LDC |
| `corrections.db` | ✓ | ✓ | 6 tables; `address_corrections` and `id_mapping_corrections` exist (both 0 rows) |
| `corrections/electric_zip.json` | ✓ | ✓ | 11 corrections |
| `corrections/gas_zip.json` | ✓ | ✓ | 3 corrections |
| `corrections/water_zip.json` | ✓ | ✓ | 2 corrections |
| `canonical_providers.json` | ✓ | ✓ | 447 canonical provider entries |
| `deregulated_reps.json` | ✓ | ✓ | TX TDUs + REP list |
| `provider_catalog.csv` | ✓ | ✓ | 14,345 entries — columns: ID, UtilityTypeId, Title, URL, Phone, Source, Type. By type: electric=1,669, water=8,368, gas=833, trash=1,768, sewer=1,706, other=1 |

---

## Provider ID Matching Audit

- **Alias coverage**: Good — covers SCE, SDG&E, PG&E, PSE&G, ComEd, LG&E, BGE, DTE, APS, TEP, NSTAR, NYSEG, JCP&L, PEPCO, plus Enbridge/East Ohio Gas rebrand aliases
- **HIFLD truncation expansion**: Handles `elec` → `electric`, `elec member` → `electric membership`, `coop` → `cooperative`, `pwr` → `power`, `svc` → `service`
- **State-specific matching**: Yes — prefers entries with matching state string in title (threshold ≥70)
- **Fuzzy matching**: `token_sort_ratio` at cutoff 72, then `token_set_ratio` at cutoff 90
- **ID overrides**: Loaded from `id_mapping_corrections` table, checked first (priority 0)
- **Missing alias**: No alias for "SCE&G" (South Carolina Electric & Gas, now Dominion)
- **Edge case**: The `_normalize()` strips `" electric"` suffix, which could cause false matches between similarly named non-electric utilities

---

## AI Resolver Audit

- **Prompt quality**: Well-structured — provides address, state, ZIP, city, utility type, and ranked candidates with sources and confidence. Instructions are clear.
- **Error handling**: Catches `json.JSONDecodeError` and other parse errors, returns None. Error count tracked (but not thread-safe). API timeout = 15s.
- **Rate limiting**: Set to 0.0s delay (OpenRouter handles server-side). Correct for OpenRouter.
- **Caching**: Cache key is `f"{address}|{utility_type}"` — avoids re-resolving same address+utility
- **NONE handling**: Returns None when AI picks NONE, which leaves engine pick unchanged ✓
- **Confidence cap**: `min(0.90, max(chosen_conf, parsed_conf))` — AI can boost but never above 0.90 ✓
- **Cost estimate**: For 91K × 11% flagged ≈ 10K items, at 20 concurrent workers with ~2s/call = ~17 minutes. At ~$0.003/call (Sonnet via OpenRouter) ≈ $30.

---

## Test Results

- Tests are NOT pytest-compatible — custom harness at `lookup_engine/tests/test_engine.py` using global state
- **48 tests** covering: spatial layer loading, spatial queries (Chicago, San Antonio, Dallas, AEP Texas), scorer/normalization (EIA match, name match, deregulated detection, passthrough), cache (put/get/invalidate/case-insensitive), water layer, and full lookup integration
- Tests require live geocoding and all shapefile data — NOT unit tests, these are integration tests
- **No tests for**: provider_id_matcher, ai_resolver, internet_lookup, sewer, geocoder batch, generate_review_files, import_mapper_corrections, corrections lookup

---

## Performance Estimate

### Memory Footprint (~2-3GB total)

| Component | Estimated Size |
|-----------|---------------|
| Electric shapefile (~3,000 polygons + spatial index) | ~500MB |
| Gas shapefile (~1,200 polygons) | ~200MB |
| Water GeoPackage (~44,000 polygons) | ~800MB |
| JSON data files (all combined) | ~50MB |
| Provider catalog | ~2MB |
| State GIS cache (91K batch) | ~55MB |

### Expected Batch Runtime for 91K Addresses (all 5 utility types)

| Phase | Description | Estimated Time |
|-------|-------------|---------------|
| Phase 1 | Census batch geocoding (10K chunks, 5min timeout) | 5-10 min |
| Phase 2 | Spatial + all sources (~3-5ms/row × 91K) | 5-8 min |
| Phase 3 | AI resolver (~10K flagged rows, 20 concurrent) | ~17 min |
| **Total** | *Excluding Google fallback geocoding* | **~30-40 min** |

### Postgres Query Performance

Single query per address with indexed `block_geoid` column — fast (~1ms/query). No connection pooling but sequential access is fine for batch.
