# Windsurf Prompt 9: Batch Validation — 87K Addresses
## February 6, 2026

```
Run ALL addresses from the tenant CSV through the lookup engine and compare results against tenant-verified providers. This is the definitive accuracy benchmark.

## Input

File: addresses_with_tenant_verification_2026-02-06T06_57_49.470044438-06_00.csv
- ~91K rows
- Columns: display (full address), Internet, Electricity, Water, Gas, Trash, Sewer, Liability Insurance
- "display" column contains the full address string (e.g., "072 Yerger Rd, Fair Lawn, NJ 07410")
- Electricity, Gas, Water columns contain tenant-verified provider names (from old tool output, confirmed by tenant bill upload)
- Many cells are empty (Gas is 53.6% filled, Water 53.5%, Electricity 93.2%)

## Process

### Step 1: Build the batch runner

Create batch_validate.py that:

1. Reads the CSV
2. For each row with a non-empty address in "display":
   a. Geocodes the address (use the engine's geocoder — Census first, Google fallback)
   b. Runs point-in-polygon for electric, gas, and water
   c. Normalizes the engine's raw shapefile result through the scorer (EIA ID match → name normalization → fuzzy)
   d. Normalizes the tenant's provider string through normalize_provider() (handles commas, fuzzy, REP detection, nulls)
   e. Compares engine result vs tenant result
3. Writes results to a CSV

### Step 2: Comparison logic

For each address + utility type (electric, gas, water), classify the result as one of:

| Category | Definition |
|---|---|
| MATCH | Engine's canonical provider == tenant's normalized canonical provider |
| MATCH_TDU | Engine returned a TDU (Oncor, CenterPoint, etc.) and tenant entered a REP (TXU, Reliant, etc.) for the same address. This is CORRECT behavior — engine returns the wire owner, tenant reported their retail provider. Count as a match. |
| MATCH_PARENT | Engine and tenant resolve to different display names but same parent company (e.g., engine says "Dominion Virginia Power", tenant says "Dominion Energy"). Count as a match. |
| MISMATCH | Engine returned a different provider than tenant. This is an error. |
| ENGINE_ONLY | Engine returned a provider but tenant cell is empty. Not scoreable. |
| TENANT_ONLY | Tenant has a provider but engine returned nothing (geocode failed or no polygon hit). This is a gap. |
| BOTH_EMPTY | Neither engine nor tenant has a result. Skip. |
| TENANT_NULL | Tenant value was a null/placeholder (N/A, None, Landlord, Included, etc.). Skip. |
| TENANT_PROPANE | Tenant value is a propane company (Amerigas, Suburban Propane, etc.). Skip for gas comparison. |

For MATCH_TDU detection:
- If the address state is TX AND utility type is electric AND tenant's value is flagged as a REP by normalize_provider() AND engine returned a TDU (Oncor, CenterPoint, AEP Texas Central, AEP Texas North, TNMP, Lubbock P&L) → MATCH_TDU
- Also handle: tenant entered comma-separated "Energy Texas, TXU Energy" — if ALL segments are REPs or nulls, and engine returned a TDU, that's MATCH_TDU

For MATCH_PARENT detection:
- Build a small parent_company map for the most common parent groups:
  - Dominion Energy: ["Dominion Virginia Power", "Dominion Energy Virginia", "Dominion NC", "Dominion Energy South Carolina", "Dominion Energy Ohio", "Questar Gas", "Virginia Natural Gas"]
  - Duke Energy: ["Duke Energy Carolinas", "Duke Energy Progress", "Duke Energy Indiana", "Duke Energy Ohio", "Duke Energy Florida", "Piedmont Natural Gas"]
  - Southern Company: ["Georgia Power", "Alabama Power", "Mississippi Power", "Gulf Power"]
  - Eversource: ["Eversource CT", "Eversource MA", "Eversource NH", "NSTAR", "Yankee Gas"]
  - WEC Energy: ["We Energies", "Wisconsin Public Service", "Peoples Gas IL", "North Shore Gas"]
  - Xcel Energy: ["Northern States Power", "Public Service Company of Colorado", "Southwestern Public Service"]
  - FirstEnergy: ["Ohio Edison", "Cleveland Illuminating", "Toledo Edison", "Mon Power", "Potomac Edison", "Jersey Central P&L", "Met-Ed", "Penelec"]
  - AEP: ["AEP Ohio", "AEP Texas Central", "AEP Texas North", "Appalachian Power", "Indiana Michigan Power", "Kentucky Power", "Public Service Company of Oklahoma", "Southwestern Electric Power"]
- If engine and tenant both resolve to names within the same parent group → MATCH_PARENT

### Step 3: Handle geocoding rate limits

The Census geocoder is free but slow. Google geocoder has API costs.

- Use the engine's SQLite cache — addresses geocoded once don't need re-geocoding
- Process in batches of 100 with progress logging every 1,000 rows
- If geocoding fails for an address, log it and continue (don't crash)
- Print estimated time remaining based on running average per-address time
- Save progress every 5,000 rows so we can resume if interrupted (write partial results CSV + a checkpoint file with the last processed row index)
- Add a --resume flag that reads the checkpoint and skips already-processed rows
- Add a --limit N flag for testing (process only first N rows)
- Add a --start N flag to start from row N

### Step 4: Output

Generate two files:

**A. batch_results.csv** — One row per address per utility type:
```
address,state,utility_type,engine_provider,engine_eia_id,engine_confidence,engine_is_deregulated,tenant_raw,tenant_normalized,comparison,match_detail
```

Where `comparison` is one of: MATCH, MATCH_TDU, MATCH_PARENT, MISMATCH, ENGINE_ONLY, TENANT_ONLY, BOTH_EMPTY, TENANT_NULL, TENANT_PROPANE

And `match_detail` has extra info for debugging mismatches:
- For MISMATCH: "engine={engine_name} vs tenant={tenant_name}"
- For MATCH_TDU: "tdu={engine_name}, rep={tenant_rep_name}"
- For TENANT_ONLY: "geocode_failed" or "no_polygon_hit"

**B. BATCH_VALIDATION_REPORT.md** — Summary statistics:

```markdown
# Batch Validation Report
## [timestamp]

### Overall
- Total addresses processed: X
- Geocoding success rate: X%
- Geocoding failures: X (list top 10 failure patterns)

### Electric Accuracy
- Scoreable rows (both engine + tenant have result): X
- MATCH: X (X%)
- MATCH_TDU: X (X%)
- MATCH_PARENT: X (X%)
- Total correct (MATCH + MATCH_TDU + MATCH_PARENT): X (X%)
- MISMATCH: X (X%)
- ENGINE_ONLY: X
- TENANT_ONLY: X
- TENANT_NULL: X
- BOTH_EMPTY: X

### Gas Accuracy
[same format]

### Water Accuracy
[same format]

### Mismatch Analysis — Electric
- Top 20 most common mismatches by (engine_provider, tenant_provider) pair
- Top 10 states with highest mismatch rate
- Top 10 mismatches where engine returned nothing (TENANT_ONLY, not geocode failure)

### Mismatch Analysis — Gas
[same format]

### TX Deregulated Market
- Total TX electric rows: X
- REP detected (MATCH_TDU): X
- TDU returned correctly: breakdown by Oncor/CenterPoint/AEP Central/AEP North/TNMP/Lubbock
- TX co-ops/municipals correctly NOT flagged as deregulated: X
- TX addresses where engine returned no result: X

### Geocoding Failures
- Total: X
- By state: [breakdown]
- Sample of 20 failed addresses (for manual review)

### Performance
- Total runtime: X
- Average per address: Xms
- Geocoding: X% of time
- Spatial query: X% of time
- Cache hit rate: X%
```

## Important Notes

1. Do NOT skip water comparisons. Water accuracy will be low (municipal fragmentation), but we need to measure it.

2. The tenant "Electricity" column for TX addresses often contains REP names (TXU Energy, Reliant, Gexa, Energy Texas). The engine should return the TDU. Classify these as MATCH_TDU, not MISMATCH.

3. Tenant values with commas (e.g., "Energy Texas, TXU Energy") should be processed through normalize_provider_multi() to split and normalize each segment. For comparison purposes, if ANY segment matches the engine's result, it's a MATCH. If ALL segments are REPs and engine returned a TDU, it's MATCH_TDU.

4. The engine's geocoder uses Census (free) as primary and Google as fallback. For 91K addresses, Census will handle most of them. Monitor the Google API usage — if it starts burning through quota, add a flag to disable Google fallback.

5. Start with --limit 100 to verify everything works, then --limit 1000 to check for edge cases, then run the full batch.

6. The engine loads ~2-4 GB into memory (three shapefiles). Make sure the machine has enough RAM. If memory is an issue, add a --skip-water flag.

## Success Criteria

- Script completes all ~91K addresses without crashing
- Checkpoint/resume works if interrupted
- Electric accuracy (MATCH + MATCH_TDU + MATCH_PARENT) / scoreable > 85%
- Gas accuracy > 85%
- Water accuracy measured but no target (expected to be low due to municipal naming)
- All TX REP entries classified as MATCH_TDU, not MISMATCH
- Geocoding success rate > 95%
- Report generated with all sections filled

Run --limit 100 first, verify the output makes sense, then run the full batch.
```
