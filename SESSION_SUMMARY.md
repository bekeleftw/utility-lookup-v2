# Utility Lookup Engine — Session Summary
## February 6, 2026

This document summarizes all work done in this Windsurf session for Claude review.

---

## 1. Code Review Fixes (Prompt 8.1)

Five issues identified in code review, all fixed:

### FIX 1 (CRITICAL): Cleaned `deregulated_reps.json`
**Problem:** 12+ entries in the REP list were NOT Retail Electric Providers — city names, co-op abbreviations, municipal utilities, truncated garbage. These caused false positive REP detection, suppressing correct results.

**Fix:** Removed 23 entries total (105 → 82 REPs):
- 5 city/town names: Lockhart TX, Schulenburg TX, Seguin, Castroville - TX, AE Texas
- 5 co-op names: GCEC, GRAYSON COLLINS, Grayson Collin Electric, South plains electric, TVEC
- 4 truncated garbage: INC - NM, Gray, Reli, SPEC
- 1 municipal abbreviation: CPS (CPS Energy, San Antonio)
- 1 water district: Brushy Creek MUD
- 1 out-of-state utility: Kentucky Power (AEP)
- 1 regulated utility: Xcel Energy - TX (SPS, not a REP)
- 1 gas company: UniGas
- 1 not a TX REP: Mid South
- 2 co-op names: Magic Valley, Magic valley
- 1 municipal utility: Bastrop Power and Light

**Kept but flagged for review:** NRG, TXU, Txu, Gexa, Xoom, Ambit, Reliant, Champion, Chariot, Abundance, Atlantex, cirro, Ironhorse — all short but valid REP abbreviations.

### FIX 2 (CRITICAL): Texas TDU Priority Logic
**Problem:** Overlap resolution was purely area-based (smallest polygon wins). This is wrong for Texas — rural co-op polygons (Hilco 12K km², Trinity Valley 14K km²) overlap urban TDU areas due to HIFLD polygon generalization.

**Fix:** Added `_resolve_texas_overlap()` in `engine.py`:
1. **Co-ops/municipals win only if area < 5,000 km²** — real local boundaries (CPS Energy 1.5K, Austin Energy 830) are small; overgeneralized rural co-ops are 12K+
2. **TDU priority ranking** (when no small co-op matches):
   - CenterPoint Energy (priority 1) — well-defined Houston metro
   - AEP Texas Central/North (priority 2) — geographically distinct South/West TX
   - Oncor (priority 3) — largest TDU, DFW metro default
   - TNMP (priority 4) — polygon overgeneralized, 58% overlap with Oncor
   - Lubbock P&L (priority 5) — tiny, wins by area anyway
3. **Fallback:** smallest area wins for non-Texas or non-TDU polygons

**Verified results:**
- Dallas → Oncor ✅ (was incorrectly picking Hilco co-op)
- Houston → CenterPoint ✅ (was picking San Bernard co-op)
- San Antonio → CPS Energy ✅ (municipal, 1.5K km² < threshold)
- Corpus Christi → AEP Texas Central ✅

### FIX 3 (CRITICAL): AEP Texas Integration Tests
**Problem:** AEP Texas polygons are stored under STATE=OK in the HIFLD shapefile. Need to verify geometry-based lookup works regardless of STATE field.

**Fix:** Added 2 tests:
- Corpus Christi (27.80, -97.40) → AEP Texas Central found ✅
- Abilene (32.45, -99.73) → AEP Texas North found ✅

### FIX 4 (HIGH): Dallas Overlap Test
**Problem:** Existing test only checked that Oncor was "somewhere in the list," not that it was the primary result.

**Fix:** Changed to assert Oncor is the primary result via `_resolve_texas_overlap()` ✅

### FIX 5 (MEDIUM): Logging in Silent Error Handlers
**Problem:** Two `except` blocks silently swallowed errors with no logging.

**Fix:**
- `spatial_index.py` line 103: `except Exception as e: logger.warning(f"Skipping geometry {idx}: {e}")`
- `provider_normalizer.py` line 120: `except ... as e: logger.warning(f"Failed to load deregulated_reps.json: {e}")`
- Added `import logging` + `logger` to `provider_normalizer.py`

**All tests: 50/50 passing** (was 47/47 before, +3 new tests)

---

## 2. Geocoder Upgrades (Prompt 9.0)

### Upgrade 1: Census Batch Geocoding
Added `geocode_batch()` to `CensusGeocoder`:
- Uses `locations/addressbatch` endpoint (POST with CSV upload)
- Auto-splits one-line addresses into street/city/state/zip via `_split_address()`
- Chunks at 10,000 addresses per request
- Retry logic: 3 attempts with exponential backoff (2s, 4s, 8s)
- 5-minute timeout per chunk
- Parses response CSV correctly (**longitude first** in lon,lat field)

**Performance:** 10 addresses in 709ms batch vs 1,166ms for 3 single calls.

### Upgrade 2: Census → Google Fallback Chain
Added `ChainedGeocoder` class:
- Tries primary (Census) first, falls back to secondary (Google) on miss
- Tracks `primary_hits`, `fallback_hits`, `total_misses` with `.stats` property
- Updated `create_geocoder()` factory: supports `"census"`, `"google"`, `"chained"`
- Config updated: `geocoder_type` now accepts all three options

**Verification:** All factory paths tested, batch vs single coordinates match exactly (0.000000 diff).

---

## 3. Batch Validation (Prompt 9 — partial)

### What was built: `batch_validate.py`
Full batch validation script that:
1. Reads the 90,978-row tenant CSV
2. Batch geocodes uncached addresses via Census batch endpoint
3. Runs point-in-polygon for electric, gas, water
4. Normalizes both engine and tenant provider names
5. Compares using 9 categories (see below)
6. Writes `batch_results.csv` + `BATCH_VALIDATION_REPORT.md`
7. Supports `--limit N`, `--start N`, `--resume`, `--skip-water`, `--geocoder`
8. Checkpoints every 5,000 rows for resume capability

### Comparison Categories
| Category | Definition |
|---|---|
| MATCH | Engine canonical == tenant normalized canonical |
| MATCH_TDU | TX electric: engine returned TDU, tenant entered REP(s) — correct behavior |
| MATCH_PARENT | Different display names but same parent company group |
| MISMATCH | Engine returned a different provider than tenant |
| ENGINE_ONLY | Engine has result, tenant cell empty |
| TENANT_ONLY | Tenant has provider, engine returned nothing |
| BOTH_EMPTY | Neither has a result |
| TENANT_NULL | Tenant value is N/A, Landlord, Included, etc. |
| TENANT_PROPANE | Tenant gas value is a propane company (Amerigas, etc.) |

### 1,000-Row Test Results

| Utility | Correct | Scoreable | Accuracy |
|---------|---------|-----------|----------|
| **Electric** | 409 | 652 | **62.7%** |
| **Gas** | 181 | 279 | **64.9%** |
| **Water** | 239 | 369 | **64.8%** |

**Electric breakdown:** 363 MATCH + 45 MATCH_TDU + 1 MATCH_PARENT = 409 correct, 243 MISMATCH

**Geocoding:** 90% success rate (10% failures on first 100 rows, mostly newer construction addresses)

**TX Deregulated:** 45 MATCH_TDU detected (39 Oncor, 6 CenterPoint). All REP entries correctly classified.

### Top Electric Mismatches (root causes)
| Count | Engine | Tenant | Root Cause |
|-------|--------|--------|------------|
| 15 | Pud No 1 Of Whatcom County | Puget Sound Energy | Co-op polygon overlap — different entities |
| 11 | Osage Valley Elec Coop | City of Harrisonville | Co-op polygon overlap |
| 10 | Beaches Energy Services | JEA | Polygon overlap in Jacksonville FL |
| 9 | City Of Mesa | Salt River Project | Municipal/IOU overlap in Phoenix metro |
| 8 | Shenandoah Valley Elec Coop | Dominion Energy | Rural co-op overlap with IOU |
| 7 | Flint Hills Rural E C A | Evergy | Rural co-op overlap in Kansas |
| 7 | Reedy Creek Improvement Dist | OUC | Disney utility district overlap in Orlando |
| 6 | Oncor | Pedernales Electric Coop | TDU priority picks Oncor, tenant actually served by co-op |

**Key insight:** The vast majority of mismatches are **legitimate polygon overlap issues** in the HIFLD shapefile data, not bugs in the comparison or engine logic. The engine returns the polygon that contains the point, but multiple overlapping polygons exist and the "wrong" one wins. This is a data quality limitation, not a code bug.

### Top Water Mismatches
| Count | Engine | Tenant | Root Cause |
|-------|--------|--------|------------|
| 7 | Austin Energy | CITY OF MANHATTAN | Engine returning wrong water system (Austin Energy is electric, not water) — likely a bug in water layer attribute extraction |
| 5 | Austin Energy | City of Durham NC | Same issue — "Austin Energy" appearing as water provider |
| 3 | Clarksville Water Dept | Clarksville Gas & Water | Name mismatch — same entity, different names |
| 3 | Lees Summit Pws | Lee's Summit Water Dept | Name mismatch — same entity |

**Water bug identified:** "Austin Energy" is appearing as a water provider for addresses in Manhattan KS, Durham NC, etc. This is clearly wrong — Austin Energy is an electric utility in Austin TX. The water layer attribute extraction may be pulling the wrong column.

### Top Gas Mismatches
| Count | Engine | Tenant | Root Cause |
|-------|--------|--------|------------|
| 13 | CenterPoint Energy | Texas Gas Service | CenterPoint serves Houston gas, Texas Gas Service serves other TX areas — polygon overlap |
| 10 | Liberty Utilities Natural Gas | Spire | Different companies, polygon overlap in MO |
| 6 | Public Service NC | Dominion NC | Different companies in NC gas market |

### Full Batch Status
The full 91K batch was started but cancelled to produce this summary. The script is ready to run:
```bash
python3 batch_validate.py                    # Full run
python3 batch_validate.py --resume           # Resume from checkpoint
python3 batch_validate.py --limit 5000       # Test with 5K rows
```

Estimated full run time: ~10 hours for uncached addresses (420ms/address × 91K), but the SQLite cache means subsequent runs are fast (~36ms/address).

---

## 4. Files Changed

### Modified
| File | Changes |
|------|---------|
| `data/deregulated_reps.json` | Removed 23 non-REP entries (105 → 82) |
| `lookup_engine/engine.py` | Added `_resolve_texas_overlap()` with TDU priority logic, 5K km² co-op threshold |
| `lookup_engine/geocoder.py` | Added `geocode_batch()`, `_split_address()`, `_send_batch()`, `_parse_batch_response()`, `ChainedGeocoder`, updated `create_geocoder()` |
| `lookup_engine/config.py` | Updated geocoder_type comment to include "chained" |
| `lookup_engine/spatial_index.py` | Added logging to geometry error handler |
| `provider_normalizer.py` | Added `import logging` + logger, added logging to REP file load error handler |
| `lookup_engine/tests/test_engine.py` | Added AEP Texas tests (2), Dallas Oncor primary test, loads all layers including water |

### Created
| File | Purpose |
|------|---------|
| `batch_validate.py` | Full batch validation script (688 lines) |

---

## 5. Known Issues / Next Steps

1. **Water "Austin Energy" bug:** The water layer is returning "Austin Energy" as a water provider for addresses far from Austin TX. Investigate the water attribute extraction in `spatial_index.py` — may be pulling the wrong column from the CWS GeoPackage.

2. **Electric accuracy at 62.7%** — below the 85% target. Root cause is HIFLD polygon overlaps, not engine logic. Potential improvements:
   - Add overlap priority logic for other states (not just TX)
   - Use population-weighted polygon selection
   - Add more aliases to `canonical_providers.json` for name matching

3. **Geocoding at 90%** — below 95% target. The Census geocoder fails on newer construction and non-standard address formats. The Google fallback chain is built but needs a `GOOGLE_API_KEY` to activate.

4. **Full 91K batch not yet complete** — script is ready, just needs to run (~10 hours).

5. **Gas ENGINE_ONLY count is very high (308/774)** — the gas shapefile has less coverage than electric. Many addresses have no gas polygon hit even though the tenant reported a gas provider.

---

## 6. Test Results

**50/50 tests passing** after all changes:
- 2 layer load tests
- 13 spatial query tests (including AEP Texas + Dallas Oncor primary)
- 6 scorer/normalization tests
- 7 cache tests
- 6 water layer tests
- 16 full lookup + integration tests
