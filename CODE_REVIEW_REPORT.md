# Utility Lookup Engine v2 — Code Review Report
**February 6, 2026**

Reviewer: Claude Code (claude-opus-4-6)
Scope: Full audit of `utility-lookup-v2/` prior to 87K address batch run

---

## 1. Spatial Index (`spatial_index.py`)

| # | Requirement | Verdict |
|---|---|---|
| 1.1 | Loads ALL three shapefiles (electric, gas, water) | ✅ PASS — `load_all()` calls `_load_electric()`, `_load_gas()`, `_load_water()` |
| 1.2 | Reprojects electric + gas to EPSG:4326 | ✅ PASS — `_load_layer()` checks CRS and calls `to_crs(config.target_crs)` for any non-4326 source |
| 1.3 | Calls `make_valid()` on geometries | ✅ PASS — Detects invalid geometries and applies `make_valid()` |
| 1.4 | Queries by geometry intersection, NOT STATE field filter | ✅ PASS — Uses `sindex.intersection()` + `geometry.contains(point)`. No STATE field filtering anywhere. AEP Texas (STATE=OK in HIFLD) will be found correctly. |
| 1.5 | Point-in-polygon uses (lon, lat) order for Shapely | ✅ PASS — `point = Point(lon, lat)` with correct comment |
| 1.6 | Multiple polygons resolved by smallest area first | ✅ PASS — `results.sort(key=lambda r: r.get("area_km2", float("inf")))` |
| 1.7 | Texas priority order: municipals/co-ops → TNMP → CenterPoint → AEP → Oncor | ❌ FAIL — **No Texas-specific priority logic exists.** Resolution is purely by smallest polygon area. For Dallas, Oncor may or may not be the smallest polygon depending on how shapefiles overlap. No code enforces the required TX priority order. |

---

## 2. Scorer (`scorer.py`)

| # | Requirement | Verdict |
|---|---|---|
| 2.1 | Matches shapefile results to canonical_providers.json using EIA ID as primary join key | ✅ PASS — Builds `_eia_to_canonical` index at init. `resolve_provider()` tries EIA ID first. |
| 2.2 | Falls back to name normalization if EIA ID fails | ✅ PASS — Calls `normalize_provider_verbose()` after EIA lookup fails |
| 2.3 | Detects deregulated ERCOT markets, flags `is_deregulated=True` for TDU results | ✅ PASS — `_is_deregulated()` checks TDU name list and ERCOT control area |
| 2.4 | TX co-ops and municipals correctly NOT deregulated | ✅ PASS — Explicitly returns `False` for COOPERATIVE/MUNICIPAL types (with Lubbock special case) |
| 2.5 | Confidence scores: boundary match (0.85), GIS polygon (0.80), name-only (0.70) | ⚠️ ISSUE — Actual values: `eia_id=0.90`, `exact=0.85`, `fuzzy=0.75`, `passthrough=0.60`. No `boundary_match=0.85` or `gis_polygon=0.80` tier. Scoring is reasonable but doesn't match the stated spec numbers. |

---

## 3. Provider Normalizer (`provider_normalizer.py`)

| # | Requirement | Verdict |
|---|---|---|
| 3.1 | `normalize_provider()` works for single strings (backward compatible) | ✅ PASS — Handles single string and comma-separated, returns display_name string |
| 3.2 | `normalize_provider_multi()` splits on commas and normalizes each segment | ✅ PASS — Splits on commas, normalizes each independently, returns list of dicts |
| 3.3 | REP detection is STRICT (exact match only, no fuzzy) | ✅ PASS — `if lookup in _REP_ALIASES` is exact dict lookup. Fuzzy matching block only searches canonical providers, NOT REPs. Comment explicitly states this. |
| 3.4 | Fuzzy thresholds: 90% for shapefile, 85% for tenant | ⚠️ ISSUE — Single 85% threshold in normalizer. Scorer applies a secondary 90% gate for shapefile names (`if match_type == "fuzzy" and similarity < 90: pass`). Both thresholds effectively exist but are split across two files — fragile. |
| 3.5 | Holding companies blocked from display names | ✅ PASS — `_HOLDING_COMPANIES` set checked before any matching |
| 3.6 | Null value skip list works | ✅ PASS — `_NULL_VALUES` set checked early in `_normalize_single()` |
| 3.7 | Propane companies handled separately | ✅ PASS — `_PROPANE_COMPANIES` set checked, returns `match_type="propane"` |

---

## 4. Canonical Providers (`data/canonical_providers.json`)

| # | Requirement | Verdict |
|---|---|---|
| 4.1 | ~446 entries | ✅ PASS — Exactly 446 entries |
| 4.2 | 0 alias collisions | ✅ PASS — No duplicate aliases mapping to different providers |
| 4.3 | EIA IDs present on ~245 entries | ✅ PASS — 270 entries have EIA IDs |
| 4.4 | No holding company names as display_name | ✅ PASS |
| 4.5 | Spot-check: NV Energy display_name = "NV Energy" | ✅ PASS |
| 4.6 | Spot-check: Columbia Gas has no bare unqualified alias | ✅ PASS — All aliases are state-qualified |
| 4.7 | Spot-check: TXU Energy NOT a canonical provider | ✅ PASS — Absent from file (correctly in deregulated_reps.json only) |

---

## 5. Deregulated REPs (`data/deregulated_reps.json`)

| # | Requirement | Verdict |
|---|---|---|
| 5.1 | ~105 TX REP entries | ✅ PASS — Exactly 105 entries |
| 5.2 | Does NOT contain legitimate utilities | ❌ FAIL — **Contains 12+ non-REP entries** (see table below) |
| 5.3 | Does NOT contain non-TX utilities | ❌ FAIL — **Contains `"Kentucky Power (AEP)"`** (regulated KY utility) plus city names and co-ops that are not REPs |

### Entries that must be removed:

| Entry | Problem | Frequency |
|---|---|---|
| `Kentucky Power (AEP)` | Out-of-state regulated utility (KY) | 8 |
| `Lockhart TX` | City name, not a REP | 7 |
| `Schulenburg TX` | City name, not a REP | 7 |
| `Seguin` | City name, not a REP | 7 |
| `CPS` | Municipal utility (CPS Energy, San Antonio) | 6 |
| `GCEC` | Likely co-op abbreviation, not a REP | 5 |
| `Castroville - TX` | City name, not a REP | 3 |
| `GRAYSON COLLINS` | Co-op (Grayson-Collin Electric) | 2 |
| `Grayson Collin Electric` | Co-op (Grayson-Collin Electric) | 2 |
| `South plains electric` | Co-op (South Plains Electric) | 2 |
| `TVEC` | Co-op (Trinity Valley Electric) | 2 |
| `Brushy Creek MUD` | Water/wastewater district | 2 |

Also suspicious: `INC - NM` (3), `Gray` (2), `Reli` (3), `SPEC` (5), `Mid South` (2) — appear to be garbled/truncated tenant data.

---

## 6. Engine Orchestration (`engine.py`)

| # | Requirement | Verdict |
|---|---|---|
| 6.1 | Order: geocode → spatial query → normalize → score | ✅ PASS — `lookup()`: cache check → geocode → spatial+score per type → build result → cache |
| 6.2 | Uses cache (check before geocoding, store after) | ✅ PASS |
| 6.3 | Handles geocoding failures gracefully | ✅ PASS — Returns empty `LookupResult` if geocode returns None |
| 6.4 | Returns results for all three utility types | ✅ PASS — Queries electric, gas, water (water skippable via flag) |
| 6.5 | Example response format for Dallas TX | ⚠️ ISSUE — Not runnable during review (requires live geocoding). `LookupResult.to_dict()` shows correct structure. |

---

## 7. Config (`config.py`)

| # | Requirement | Verdict |
|---|---|---|
| 7.1 | File paths correct and relative to project root | ✅ PASS — `_ROOT = Path(__file__).parent.parent` resolves correctly |
| 7.2 | ERCOT TDU list includes all 6 | ✅ PASS — Oncor, CenterPoint, AEP Texas Central, AEP Texas North, TNMP, Lubbock |
| 7.3 | Lubbock P&L treated as deregulated | ✅ PASS — `lubbock_deregulated: bool = True`, scorer checks this flag |

---

## 8. Tests (`test_engine.py`)

| # | Requirement | Verdict |
|---|---|---|
| 8.1 | All 47 tests pass | ⚠️ ISSUE — Only ~42 tests defined (counted from `test()` calls), not 47. Cannot run live — tests require geocoding API. |
| 8.2 | Test for AEP Texas (STATE=OK issue) | ❌ FAIL — **No test for AEP Texas.** No test point in AEP territory (Corpus Christi, McAllen, Abilene). The spatial code handles it correctly, but there is no test proving it. |
| 8.3 | Test for TX co-op returning `is_deregulated=False` | ✅ PASS — Pedernales co-op test + CPS Energy municipal test |
| 8.4 | Test for TX TDU returning `is_deregulated=True` | ✅ PASS — Oncor test |
| 8.5 | Test for overlap resolution (Dallas → Oncor, not TNMP) | ⚠️ ISSUE — Test checks Oncor *is present* in Dallas results, but does NOT assert Oncor *wins* (is `results[0]`). Test would pass even if TNMP is returned as the primary provider. |

---

## 9. General Code Quality

| # | Requirement | Verdict |
|---|---|---|
| 9.1 | No hardcoded file paths | ✅ PASS — All paths use `Path(__file__).parent` relative resolution |
| 9.2 | No external API calls at query time other than geocoding | ✅ PASS — Only `requests.get()` calls are in `geocoder.py` |
| 9.3 | No OpenAI/LLM calls anywhere | ✅ PASS — Zero LLM imports or calls |
| 9.4 | No try/except that silently swallow errors | ⚠️ ISSUE — Two locations: (1) `spatial_index.py:103-105`: bare `except Exception: continue` silently skips geometries during containment check. (2) `provider_normalizer.py:120-121`: silently swallows REP file load failure. |
| 9.5 | Memory usage | ⚠️ ISSUE — All three shapefiles loaded into memory. Electric ~100MB, gas ~17MB, water ~553MB on disk. **Estimated peak memory: 2-4 GB**, settling to ~1.5-2.5 GB resident. Requires 8GB+ RAM machine. |

---

## Final Summary

### Totals: 33 checks

| | Count |
|---|---|
| ✅ PASS | **26** |
| ⚠️ ISSUE | **5** |
| ❌ FAIL | **3** |

---

### ❌ FAIL Items — Must Fix Before Batch Run

#### FAIL 1: No Texas TDU Priority Logic (1.7)
**File:** `spatial_index.py`
**Problem:** Overlap resolution is purely area-based. No code enforces the Texas priority order (municipals/co-ops → TNMP → CenterPoint → AEP → Oncor). For Dallas, if TNMP's polygon is smaller than Oncor's at a given point, TNMP wins incorrectly.
**Fix:** Add a TX-specific priority resolver in `engine.py._lookup_type()`. When state=TX and utility_type=electric, after getting sorted results, apply the priority tiebreaker. Alternatively, validate empirically that area-based sorting produces the correct winner for all major TX metros (Dallas, Houston, Corpus Christi, Abilene, and overlap zones).

#### FAIL 2: deregulated_reps.json Contains 12+ Non-REP Entries (5.2, 5.3)
**File:** `data/deregulated_reps.json`
**Problem:** Contains co-ops (Grayson-Collin, South Plains, TVEC), a municipal (CPS), an out-of-state utility (Kentucky Power), a water district (Brushy Creek MUD), and city names (Lockhart TX, Schulenburg TX, Seguin, Castroville). These will cause false positive REP detection — if a tenant enters "CPS" or "Grayson Collin Electric", the system will incorrectly classify them as REPs instead of resolving to the actual utility.
**Fix:** Remove the 12 identified entries from the `"reps"` dict. Also review the ~5 garbled entries (`INC - NM`, `Gray`, `Reli`, `SPEC`, `Mid South`).

#### FAIL 3: No AEP Texas Integration Test (8.2)
**File:** `lookup_engine/tests/test_engine.py`
**Problem:** The critical STATE=OK edge case (AEP Texas is stored under STATE=OK in HIFLD) has no integration test. If anyone changes the spatial query logic, this regression could go undetected.
**Fix:** Add a spatial query test for a point in AEP Texas territory:
```python
# AEP Texas — stored under STATE=OK in HIFLD, must be found via geometry
aep_results = engine.spatial.query_point(27.8006, -97.3964, "electric")  # Corpus Christi
test("AEP Texas: found via geometry (not STATE filter)",
     lambda: any("AEP" in r["name"].upper() for r in aep_results))
```

---

### ⚠️ ISSUE Items — Ranked by Severity

#### 1. [HIGH] Dallas Overlap Test Doesn't Assert Oncor Wins (8.5)
The test checks Oncor is *in* the results, not that it's the *first* result. If area-based sorting puts TNMP first, this test still passes while producing wrong answers for DFW addresses.
**Fix:** Change test to `assert "ONCOR" in dal_results[0]["name"].upper()`.

#### 2. [MEDIUM] Confidence Scores Differ from Spec (2.5)
Code uses `eia_id=0.90, exact=0.85, fuzzy=0.75, passthrough=0.60`. Spec says `boundary=0.85, GIS=0.80, name-only=0.70`. Decide which is authoritative and align.

#### 3. [MEDIUM] Silent Error Swallowing (9.4)
Two locations silently eat errors: `spatial_index.py` skips geometries with topology errors during containment, and `provider_normalizer.py` silently ignores REP file load failures. During a batch run, these could hide real problems.
**Fix:** At minimum, log warnings instead of silently continuing.

#### 4. [LOW] Fuzzy Thresholds Split Across Files (3.4)
85% base threshold in `provider_normalizer.py`, 90% shapefile gate in `scorer.py`. Fragile — someone modifying one file might not know about the other.

#### 5. [LOW] Peak Memory 2-4 GB (9.5)
Functional but may cause issues on CI/smaller machines. Water layer alone is 553MB on disk.

---

### Overall Assessment

> **The engine is NOT ready for the 87K address batch run** without addressing the 3 FAIL items.
>
> **FAIL #2 (dirty REP data)** will cause real misclassifications in production. Any address where a tenant entered "CPS", a co-op name, or a city name will be incorrectly flagged as a deregulated REP, suppressing the actual utility result.
>
> **FAIL #1 (TX priority)** is a risk for every DFW-area address. Needs empirical validation or explicit priority logic.
>
> **FAIL #3 (AEP test)** is a safety net — add before batch to confirm the STATE=OK edge case works end-to-end.
>
> **Recommended order:** Fix FAIL #2 (data cleanup, 15 min) → Validate FAIL #1 (run Dallas test point, add priority if needed) → Add FAIL #3 (test, 5 min) → Proceed with batch.
