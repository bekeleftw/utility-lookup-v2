# Multi-Provider Results + Remaining Data Sources Report

**Date:** 2026-02-06  
**Scope:** Prompt 12 — Return multiple candidates per lookup, port remaining data sources

---

## Part A: Multi-Provider Results

### What Changed

The engine now **collects candidates from ALL sources** instead of short-circuiting at the first hit. Results include:

- **Primary provider** — highest confidence candidate after deduplication
- **Alternatives** — up to 4 other candidates with their source and confidence
- **needs_review** flag — `true` when primary confidence < 0.80
- **Confidence boosting** — when multiple sources agree on the same provider, confidence is boosted (+0.05 per additional source, max +0.10)

### New Comparison Category: MATCH_ALT

When the primary provider doesn't match the tenant but an **alternative** does, the comparison is `MATCH_ALT` instead of `MISMATCH`. This means the engine found the right answer but ranked it wrong.

### Response Format

High confidence (>= 0.80):
```json
{
  "provider": "Austin Energy",
  "confidence": 0.98,
  "source": "state_gis_tx (+4 agree)",
  "needs_review": false,
  "alternatives": []
}
```

Low confidence (< 0.80) or conflicting sources:
```json
{
  "provider": "Duke Energy",
  "confidence": 0.72,
  "source": "HIFLD Electric Retail Service Territories",
  "needs_review": true,
  "alternatives": [
    {"provider": "Energy United", "confidence": 0.75, "source": "state_gis_nc"},
    {"provider": "Duke Energy Carolinas", "confidence": 0.70, "source": "eia_zip"}
  ]
}
```

### New CSV Columns in batch_results.csv

| Column | Description |
|--------|-------------|
| `engine_needs_review` | True if primary confidence < 0.80 |
| `engine_alternatives` | Pipe-separated alternative providers |

---

## Part B: Remaining Data Sources

### Source 1: User Corrections (Priority 0)

**Module:** `lookup_engine/corrections.py`  
**Data:** `data/corrections.db` (schema ready, 0 entries currently)

- Highest priority — human-verified corrections override everything
- Supports exact address match + ZIP-level corrections
- Confidence: 0.99 (address) / 0.98 (ZIP)
- Ready to accept corrections as they're verified

### Source 2: Remaining States ZIP Data (Priority 3.5)

**Module:** `lookup_engine/remaining_states.py`  
**Data:** `data/remaining_states_{electric,gas,water}.json`

| Utility | States | ZIP Entries |
|---------|--------|-------------|
| Electric | 43 | 931 |
| Gas | 51 | 6,228 |
| Water | 50 | 5,212 |

- Tenant-verified ZIP mappings from the 87K dataset
- Includes dominance percentage and sample count
- Confidence mapped from dominance: 0.65 (low) to 0.85 (high + many samples)
- Inserted between HIFLD and EIA ZIP in the priority chain

### Source 3: Georgia EMC (Priority 2.5)

**Module:** `lookup_engine/georgia_emc.py`  
**Data:** `data/georgia_emcs.json`

- 39 EMCs covering 148 Georgia counties
- County-level lookup (requires county from geocoder)
- Fills the gap: GA has 872 electric mismatches and no state GIS endpoint
- Single-EMC county: confidence 0.87
- Multi-EMC county: confidence 0.72 (first EMC returned, others as alternatives)

### Source 4: Special Districts (Deferred)

Checked `data/special_districts/processed/` — files are ZIP-to-district-ID mappings (AZ: 176, CA: 284, CO: 132, FL: 26, WA: 90). These require resolving district IDs to actual water provider names via separate detail files. Water-only benefit, complex to port. Deferred.

---

## Updated Priority Chain

```
Priority 0:   User Corrections        (0.98-0.99)  ← NEW
Priority 1:   State GIS API           (0.90-0.95)
Priority 2:   Gas ZIP Mapping          (gas only, 0.85-0.93)
Priority 2.5: Georgia EMC             (GA electric only, 0.72-0.87)  ← NEW
Priority 3:   HIFLD Shapefile          (0.75-0.85)
Priority 3.5: Remaining States ZIP     (0.65-0.85)  ← NEW
Priority 4:   EIA ZIP Fallback         (electric only, 0.70)
Priority 5:   FindEnergy City Cache    (electric + gas, 0.65)
Priority 6:   State Default Gas LDC    (gas only, 0.40-0.65)
```

All sources are queried and candidates collected. Deduplication boosts confidence when multiple sources agree.

---

## Verification: --limit 100 Results

| Metric | Value |
|--------|-------|
| **Electric accuracy** | **90.9%** (70/77) |
| **Gas accuracy** | **91.4%** (32/35) |
| Geocoding | 90.0% (90/100) |
| Runtime | 43.7s |

### Comparison Breakdown (100 rows × 3 utility types = 300 rows)

| Category | Count | % |
|----------|-------|---|
| MATCH | 97 | 32.3% |
| TENANT_ONLY | 66 | 22.0% |
| ENGINE_ONLY | 59 | 19.7% |
| BOTH_EMPTY | 55 | 18.3% |
| MISMATCH | 10 | 3.3% |
| **MATCH_ALT** | **8** | **2.7%** |
| MATCH_TDU | 5 | 1.7% |

### MATCH_ALT Examples

| Address | Primary | Alternative (correct) |
|---------|---------|----------------------|
| Huntersville, NC 28078 | Duke Energy | Energy United (state_gis_nc) |
| Roseville, CA | PG&E | Roseville Electric |
| Cleveland, OH 44108 | East Ohio Gas/Dominion | Enbridge Gas Ohio |

### Source Distribution

| Source | Count |
|--------|-------|
| HIFLD Electric | 42 |
| HIFLD Gas | 35 |
| remaining_states_gas | 19 |
| state_gis_mn | 13 |
| state_gis_or | 12 |
| state_gis_tx | 11 |
| gas_zip_mapping_tx | 11 |
| state_gis_va | 5 |
| state_gis_wa | 4 |
| state_gis_wi | 4 |
| eia_zip | 2 |
| No result | 121 |

### needs_review Flag

| Value | Count |
|-------|-------|
| False | 146 |
| True | 33 |

---

## Files Created

| File | Purpose |
|------|---------|
| `lookup_engine/corrections.py` | User corrections lookup (Priority 0) |
| `lookup_engine/remaining_states.py` | Remaining states ZIP lookup (Priority 3.5) |
| `lookup_engine/georgia_emc.py` | Georgia EMC county lookup (Priority 2.5) |
| `data/corrections.db` | Corrections database (copied, empty) |
| `data/remaining_states_electric.json` | 931 ZIP entries across 43 states |
| `data/remaining_states_gas.json` | 6,228 ZIP entries across 51 states |
| `data/remaining_states_water.json` | 5,212 ZIP entries across 50 states |
| `data/georgia_emcs.json` | 39 EMCs, 148 county mappings |

## Files Modified

| File | Changes |
|------|---------|
| `lookup_engine/models.py` | Added `needs_review`, `alternatives` fields to ProviderResult |
| `lookup_engine/engine.py` | Multi-source candidate collection, deduplication, boosting, new module imports |
| `batch_validate.py` | MATCH_ALT category, alternatives param, needs_review + alternatives CSV columns |
