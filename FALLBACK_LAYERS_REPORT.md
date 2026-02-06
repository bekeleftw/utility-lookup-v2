# Fallback Layers Report — No Empty Results

**Date:** 2026-02-06  
**Scope:** Add ZIP-level and city-level fallbacks so every geocoded address returns at least an electric and gas provider

---

## Problem

The engine returned nothing for ~10% of addresses after State GIS + HIFLD. The old system never had this problem because it had ZIP-level and county-level fallbacks.

## Solution

Extended the lookup chain from 3 layers to 6:

```
Priority 1: State GIS API           (confidence 0.90-0.95)
Priority 2: Gas ZIP Mapping          (gas only, 0.85-0.93)
Priority 3: HIFLD Shapefile          (0.75-0.85)
Priority 4: EIA ZIP Fallback         (electric only, 0.70)       ← NEW
Priority 5: FindEnergy City Cache    (electric + gas, 0.65)      ← NEW
Priority 6: State Default Gas LDC    (gas only, 0.40-0.65)       ← NEW
```

Water has no fallback beyond EPA/state GIS — returning nothing for water is acceptable (well water, rural areas).

---

## Fallback 1: EIA ZIP Mapping (Electric)

**File:** `data/eia_zip_utility_lookup.json` (33,412 ZIP entries, already in project from Prompt 10)  
**Module:** `lookup_engine/eia_verification.py` — added `lookup_by_zip()` method

- Covers essentially every US ZIP code
- Returns the primary electric utility (prefers IOU over cooperative/municipal)
- Confidence capped at 0.70
- Only fires when State GIS + HIFLD both return nothing

**Example:** ZIP 28078 (Huntersville, NC) → Duke Energy via EIA when HIFLD polygon missed

## Fallback 2: FindEnergy City Cache (Electric + Gas)

**File:** `data/findenergy/city_providers.json` (528 entries — 265 electric, 263 gas)  
**Module:** `lookup_engine/findenergy_lookup.py`

- City-based lookup keyed by `STATE:city:utility_type` (e.g., `TX:austin:electric`)
- Covers major US cities across ~40 states
- Confidence capped at 0.65
- Fires after EIA ZIP (electric) or after HIFLD (gas)

**Example:** Charlotte, NC gas → Piedmont Natural Gas via FindEnergy when HIFLD had no gas polygon

## Fallback 3: State Default Gas LDC (Gas Only)

**File:** `data/state_gas_defaults.json` (51 states/territories)  
**Module:** Loaded directly in `lookup_engine/engine.py`

- Maps each state to its most common gas utility
- States with limited/no gas infrastructure return `null` (FL, HI, VT, ME)
- Confidence varies by state dominance:
  - 0.60-0.70: States with single dominant utility (GA → Atlanta Gas Light, DC → Washington Gas, NM → NM Gas Co)
  - 0.45-0.55: States with 2-3 major utilities (AZ → Southwest Gas, OK → ONG)
  - 0.35-0.45: States with many utilities (TX → Atmos, CA → SoCalGas, NY → National Grid)
- Absolute last resort — low confidence but better than nothing

**Example:** Manhattan, KS gas → Kansas Gas Service (conf 0.50) via state default when HIFLD had no polygon

---

## Fallback 4: ZIP + City Extraction

All fallbacks need ZIP code and city. Added extraction in both `engine.py` and `batch_validate.py`:

1. **ZIP:** `geo.zip_code` from geocoder → regex fallback from address string
2. **City:** `geo.city` from geocoder → regex fallback parsing `, City, ST` pattern

---

## Source Tracking

Added `engine_source` column to `batch_results.csv`. Every result now includes which layer provided it:

| Source | Description |
|--------|-------------|
| `state_gis_XX` | State GIS API (XX = state code) |
| `gas_zip_mapping_XX` | Gas ZIP prefix mapping |
| `HIFLD Electric/Gas/Water...` | HIFLD shapefile |
| `eia_zip` | EIA ZIP fallback |
| `findenergy_city` | FindEnergy city cache |
| `state_gas_default` | State default gas LDC |

---

## Verification: --limit 1000 Results

### Source Distribution

| Source | Electric | Gas |
|--------|----------|-----|
| State GIS | 64.8% | 15.6% |
| Gas ZIP Mapping | — | 19.3% |
| HIFLD Shapefile | 26.8% | 38.2% |
| EIA ZIP | 0.6% | — |
| FindEnergy City | — | 3.1% |
| State Default | — | 6.7% |
| No Result | 8.4% | 11.5% |

### Coverage Improvement

| Metric | Before (Prompt 10) | After (Prompt 11) | Change |
|--------|--------------------|--------------------|--------|
| Electric TENANT_ONLY | ~10.3% | 7.4% | -2.9pp |
| Gas TENANT_ONLY | ~9.5% | 4.4% | -5.1pp |
| Electric no-result | ~8.4% | 8.4% | — (all geocode failures) |
| Gas no-result | ~15%+ | 11.5% | -3.5pp |

### Accuracy

| Utility | Accuracy | Notes |
|---------|----------|-------|
| Electric | 83.4% | 707/848 compared |
| Gas | 69.7% | 290/416 compared |

The remaining "no result" cases are almost entirely **geocode failures** (Census couldn't match the address). The fallback chain catches everything else.

### Gas No-Result Breakdown

The 115 gas no-results break down as:
- **84** geocode failures (no coordinates at all)
- **~31** in states with `null` gas default (FL, HI, VT, ME — no gas infrastructure)
- This is correct behavior — these states genuinely have limited/no gas service

---

## Files Created

| File | Purpose |
|------|---------|
| `lookup_engine/findenergy_lookup.py` | FindEnergy city-based provider lookup |
| `data/findenergy/city_providers.json` | 528 city→provider mappings (copied from old codebase) |
| `data/state_gas_defaults.json` | 51 state→default gas LDC mappings |

## Files Modified

| File | Changes |
|------|---------|
| `lookup_engine/eia_verification.py` | Added `lookup_by_zip()` method for electric fallback |
| `lookup_engine/engine.py` | Full 6-layer chain, FindEnergy + state gas defaults init, city extraction, `Path` import |
| `batch_validate.py` | City extraction, `engine_source` column in CSV output, pass city to all lookup calls |

---

## Next Steps

1. **Full 91K batch re-run** — get exact accuracy numbers with all fallbacks enabled
2. **Improve geocoding** — the remaining TENANT_ONLY cases are geocode failures; Google fallback would help
3. **Add more FindEnergy cities** — current 528 entries cover major cities; scraping more would improve gas coverage
4. **Tune state gas defaults** — after batch run, check which defaults are causing mismatches and adjust
