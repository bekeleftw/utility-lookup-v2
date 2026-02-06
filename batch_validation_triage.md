# Batch Validation Triage — 91K Results
## February 6, 2026

## Current Accuracy
| Utility | Correct | Scoreable | Accuracy |
|---------|---------|-----------|----------|
| Electric | 61,919 | 75,367 | 82.2% |
| Gas | 30,375 | 39,884 | 76.2% |
| Water | 26,223 | 41,187 | 63.7% |

---

## TIER 1: Quick Wins (alias/naming fixes, no logic changes)

### Electric — Alias Additions (~980 mismatches fixable)

These are cases where the engine found the RIGHT utility but the comparison
fails because the tenant uses a different name for the same entity:

| Engine Name | Tenant Name | Count | Fix |
|---|---|---|---|
| City Of Chattanooga - (TN) | EPB | 140 | Add alias: EPB → City of Chattanooga |
| City Of Chattanooga - (TN) | Electric Power Board (EPB) - TN | 133 | Add alias |
| PUD No 1 Of Clark County - (WA) | Clark Public Utilities | 242 | Add alias |
| Cleveland Electric Illum | The Illuminating Company | 139 | Add alias (both are FirstEnergy subsidiaries) |
| Wisconsin Power & Light | Alliant Energy | 225 | Add to MATCH_PARENT groups |
| Oncor | Just Energy | 101 | **BUG: Just Energy is a REP — add to deregulated_reps.json** |
| **TOTAL** | | **~980** | |

**Just Energy is the only bug here** — it's a legitimate TX REP that was missed
in the original 82-entry list. The other 101 Oncor/Just Energy rows should be
MATCH_TDU, not MISMATCH.

**Projected electric after Tier 1: (61,919 + 980) / 75,367 = 83.5% (+1.3pp)**

### Gas — Alias + Parent Company (~1,100 mismatches fixable)

| Engine Name | Tenant Name | Count | Fix |
|---|---|---|---|
| Public Service NC | Enbridge Gas NC | 131 | PSNC Energy was acquired by Enbridge 2019 — add MATCH_PARENT |
| Public Service NC | Enbridge Gas North Carolina | 147 | Same acquisition |
| Public Service NC | Dominion NC | 546 | Different companies — REAL MISMATCH (polygon overlap) |
| Nicor Gas | Gas South | 238 | Gas South acquired Nicor's GA retail — add MATCH_PARENT |
| Nicor Gas | Gas South Avalon | 242 | Same — Gas South Avalon is a Gas South plan name |
| Wisconsin Power And Light | Alliant Energy | 130 | Add to MATCH_PARENT groups |
| Liberty Utilities Natural Gas | Spire Energy | 157 | Different companies — REAL MISMATCH |
| Liberty Utilities Natural Gas | Spire | 126 | Same |
| **Fixable total** | | **~888** | |

**Projected gas after Tier 1: (30,375 + 888) / 39,884 = 78.4% (+2.2pp)**

### Water — Name Format Normalization (~4,300 mismatches fixable)

Almost every top water mismatch is the SAME entity with formatting differences:

| Engine (CWS format) | Tenant (user format) | Count |
|---|---|---|
| Gilbert, Town Of | Town of Gilbert AZ | 494 |
| Lubbock Public Water System | City Of Lubbock Water Utilities Dept - TX | 410 |
| Raleigh, City Of | City of Raleigh - NC | 341 |
| Durham, City Of | City of Durham NC | 326 |
| Fairfax County Water Authority | Fairfax Water | 290 |
| Greenville Water (2310001) | Greenville Water - SC | 258 |
| San Diego, City Of | City of San Diego - CA | 256 |
| Richmond, City Of | City of Richmond VA | 198 |
| Dallas Water Utility | Dallas Water Utilities - TX | 197 |
| Fort Wayne - 3 Rivers Filtration Plant | Fort Wayne City Utilities - IN | 187 |
| Onslow Wtr And Sewer Authority | Onslow Water and Sewer Authority - NC | 180 |
| City Of Tampa Water Department | City of Tampa - FL | 153 |
| Augusta-Richmond Co Ws | Augusta GA | 133 |
| Kansas City Pws | KC Water | 128 |
| Lawrence, City Of | City of Lawrence - KS | 117 |
| Mo American St Louis St Charles Counties | Missouri American Water | 114 |
| Electric City Utilities (Sc0410012) | Anderson-Electric City Utilities | 114 |
| Bend Water Department | City of Bend - OR | 112 |
| Henrico County Water System | Henrico Dept of Public Utilities - VA | 106 |
| Lees Summit Pws | Lee's Summit Water Dept | 100 |
| **TOTAL (top 20 alone)** | | **~4,284** | |

The fix is a **water-specific name normalizer** that:
1. Strips state abbreviations and suffixes: "- NC", "- TX", "AZ", "VA"
2. Strips parenthetical IDs: "(2310001)", "(Sc0410012)"
3. Normalizes "X, City Of" ↔ "City of X" (reversed entity format)
4. Strips department suffixes: "Water Utilities Department", "Water Dept", "Pws"
5. Normalizes abbreviations: "Wtr" → "Water", "Co" → "County", "Ws" → "Water System"
6. Extracts core city name for fuzzy comparison: "Fairfax" from both sides

**Projected water after Tier 1: (26,223 + 4,284) / 41,187 = 74.1% (+10.4pp)**

---

## TIER 2: Bug Fixes (logic errors found in mismatch data)

### BUG 1: Gas layer returning electric utilities
**CoServ Electric Cooperative, Inc. → Atmos Energy (361 mismatches)**

CoServ DOES provide gas in North Texas, so this might be a legitimate polygon
overlap, not a bug. BUT — investigate whether the gas shapefile actually has a
CoServ gas entry or if the gas query is accidentally hitting the electric layer.
If the engine is returning "CoServ Electric Cooperative, Inc." (with "Electric"
in the name) as a gas provider, that's suspicious.

Check: Does the gas GeoDataFrame contain a record with "CoServ" in the name?
If no → the gas query is leaking electric results. If yes → legitimate overlap.

### BUG 2: Wrong-state gas results
**Dominion Energy Utah → Enbridge Gas Ohio (250 mismatches)**
**Cheyenne Light Fuel & Power → Kansas Gas Service (183 mismatches)**

Dominion Energy Utah should NOT appear for Ohio addresses.
Cheyenne (Wyoming) should NOT appear for Kansas addresses.

This is the same bug pattern as AEP Texas (STATE=OK) — the gas shapefile
has polygons coded to wrong states, and overgeneralized boundaries extend
across state lines.

Fix: For gas overlaps, add a state-match bonus. If the address state matches
the provider's state, boost that candidate's score. Don't hard-filter (AEP
Texas proves that's wrong), but penalize cross-state matches heavily.

**Potential impact: 250 + 183 + unknown others = 600+ mismatches**

### BUG 3: Tenant data in wrong column
**Public Service Co of New Mexico → YANKEE GAS SERVICE CO (EVERSOURCE) (132)**

NM electric utility matched against a CT gas utility. The tenant entered their
gas provider (Yankee Gas / Eversource) in the electricity column. This is
tenant data quality, not an engine error. These 132 rows should be excluded
from scoring or flagged as TENANT_WRONG_COLUMN.

Not fixable in the engine — but should not count against accuracy.

### BUG 4: Missing REP in deregulated_reps.json
**Oncor → Just Energy (101 mismatches)**

Just Energy is a legitimate TX REP. Add it to deregulated_reps.json.
These 101 rows should be MATCH_TDU.

---

## TIER 3: Structural Issues (HIFLD data quality, hard to fix)

These are genuine polygon overlap problems where the engine picks a utility
whose HIFLD polygon contains the address, but the tenant is actually served
by a different utility. The HIFLD boundaries are overgeneralized.

### Electric Overlap Issues (not easily fixable)
| Engine (wrong) | Tenant (correct) | Count | Issue |
|---|---|---|---|
| Rio Grande Electric Coop | El Paso Electric | 296 | Rural co-op polygon extends into El Paso metro |
| DTE | Consumers Energy | 275 | MI IOU boundary overlap |
| Oncor | Pedernales Electric Coop | 268 | TX co-op not caught by 5K km² threshold |
| Modern Electric Water | Avista Utilities | 255 | WA overlap |
| PSE | Snohomish County PUD | 234 | WA overlap |
| Duke Energy | Jones-Onslow EMC | 228 | NC co-op overlap |
| SRP | Arizona Public Service | 208 | AZ IOU overlap |
| Bluebonnet Electric | Pedernales Electric Coop | 187 | TX co-op vs co-op overlap |
| Oncor → Coserv | (various) | 186 | TX co-op overlap |
| FPL | Lee County Electric Coop | 123 | FL overlap |
| Bandera Electric Coop | CPS Energy | 99 | TX co-op overlap |
| Portland General Electric | PacifiCorp | 95 | OR IOU overlap |
| **TOTAL** | | **~2,654** | |

These require either:
a) Address-level override tables built from mismatch data
b) Better polygon data (state-level GIS when available)
c) Census block group level data (more precise than HIFLD polygons)

### Gas Coverage Gaps
ENGINE_ONLY: 32,195 rows — engine returned a gas provider but tenant had no gas entry.
TENANT_ONLY: 8,662 rows — tenant reported gas but engine found nothing.

The gas shapefile (1,259 polygons) has much less coverage than electric (2,931).
8,662 TENANT_ONLY means the gas layer is missing polygons for those areas entirely.

---

## Projected Accuracy After All Tier 1 + Tier 2 Fixes

| Utility | Current | After Tier 1 | After Tier 1+2 | Target |
|---------|---------|-------------|----------------|--------|
| Electric | 82.2% | 83.5% | ~84.5% | 85% |
| Gas | 76.2% | 78.4% | ~80% | 85% |
| Water | 63.7% | 74.1% | ~74.5% | N/A |

Electric gets within 0.5pp of the 85% target with Tier 1+2 fixes.
Gas needs structural improvements (better shapefile or state GIS data) to reach 85%.
Water jumps +10pp just from name normalization — the engine is finding the
right water system, it's just comparing names badly.

---

## Recommended Fix Priority

### Prompt 9.2 (do now):
1. Water name normalizer (strips state, normalizes "City Of" format) — +10pp water
2. Add missing aliases: EPB, Clark Public Utilities, Illuminating Company — +1pp electric
3. Add Just Energy to deregulated_reps.json — fixes 101 MATCH_TDU
4. Add MATCH_PARENT groups: Alliant/WPL, Enbridge/PSNC, Gas South/Nicor — +2pp gas
5. Gas state-match scoring bonus to fix wrong-state results — +1.5pp gas

### Later (requires new data or manual overrides):
6. Electric polygon overlap fixes — needs state GIS data or address-level overrides
7. Gas coverage gaps — needs more gas polygon sources
8. Geocoding 90.3% → 95% — needs Google fallback with API key

### Rerun after 9.2:
Only need to rerun comparison logic (skip geocoding + spatial — use cached results).
Should take ~10 minutes instead of 95 minutes.
