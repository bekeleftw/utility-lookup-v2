# Windsurf Prompt 16: Full 91K Batch Run with Provider ID Comparison

Run the full 91K address batch with all 5 utility types, provider ID matching on BOTH sides (engine AND tenant), and generate Darius's review files.

## PART 1: Tenant Provider ID Matching

Before running the batch, add logic to run the tenant provider names through `ProviderIDMatcher` to get tenant catalog IDs. This enables ID-to-ID comparison — the headline metric Darius needs.

### In `batch_validate.py`, after loading the tenant CSV:

For each row, the tenant columns are provider NAMES (not IDs):
- `Electricity` → tenant electric provider name
- `Gas` → tenant gas provider name  
- `Water` → tenant water provider name
- `Internet` → tenant internet provider name (informational, skip ID matching)

For electric, gas, and water: run the tenant provider name through `ProviderIDMatcher.match()` with the appropriate utility type and state. Store:
- `tenant_catalog_id` — matched catalog ID (or None)
- `tenant_catalog_title` — matched catalog title
- `tenant_id_match_score` — fuzzy match score

### New comparison column: `id_match`

Three-bucket comparison:
```python
if engine_catalog_id and tenant_catalog_id:
    if engine_catalog_id == tenant_catalog_id:
        id_match = "ID_MATCH"  # Same catalog ID — definitive match
    elif comparison in ("MATCH", "MATCH_TDU", "MATCH_PARENT", "MATCH_ALT"):
        id_match = "NAME_MATCH_ID_MISMATCH"  # Same provider, different catalog IDs (catalog dupe)
    else:
        id_match = "TRUE_MISMATCH"  # Different provider entirely
elif engine_catalog_id and not tenant_catalog_id:
    id_match = "TENANT_ID_MISSING"  # Couldn't match tenant name to catalog
elif not engine_catalog_id and tenant_catalog_id:
    id_match = "ENGINE_ID_MISSING"  # Couldn't match engine name to catalog
else:
    id_match = "BOTH_ID_MISSING"  # Neither side matched
```

### New CSV columns

Add these to `batch_results.csv`:
- `tenant_catalog_id`
- `tenant_catalog_title`  
- `tenant_id_match_score`
- `id_match` (ID_MATCH | NAME_MATCH_ID_MISMATCH | TRUE_MISMATCH | TENANT_ID_MISSING | ENGINE_ID_MISSING | BOTH_ID_MISSING)

## PART 2: Catalog Dupe Report

After the batch completes, generate a catalog dupe analysis:

Find all cases where `id_match == "NAME_MATCH_ID_MISMATCH"` — these are rows where the name comparison says MATCH but the IDs differ. Group by engine_catalog_id + tenant_catalog_id pairs:

```
Catalog Duplicate Pairs:
  Duke Energy Corporation (ID 13746) ↔ Duke Energy (ID 72): 47 occurrences
  Centerpoint Energy (ID 26) ↔ Centerpoint Energy-TX (ID 15609): 23 occurrences
  ...
```

Write this to `catalog_dupes_report.txt` — Darius can use this to clean up the catalog.

## PART 3: Run the Full Batch

```bash
# Run all 91K addresses, all 5 utility types, with AI resolver
python batch_validate.py --skip-internet

# Then run internet separately if desired (optional, it's informational only):
# python batch_validate.py --internet-only
```

Wait — actually, run WITH internet unless it's too slow. The Census GEOID optimization should make it fast. If the batch takes more than 6 hours, kill it and re-run with `--skip-internet`.

The batch should run these phases:
1. **Phase 1**: Geocode all addresses (Census batch in 10K chunks + Google fallback)
2. **Phase 2**: Lookup electric, gas, water, sewer, internet for every address. Run ProviderIDMatcher on both engine AND tenant provider names.
3. **Phase 3**: AI resolver on flagged rows (Sonnet via OpenRouter, 20 concurrent workers). Use `--skip-ai` if you want to save the ~$30 and do it later.

### Expected output files:

| File | Contents |
|------|----------|
| `batch_results.csv` | All 91K rows with all columns including tenant IDs and id_match |
| `BATCH_VALIDATION_REPORT.md` | Accuracy report with ID match stats |

## PART 4: Updated Report Format

The `BATCH_VALIDATION_REPORT.md` should include these sections:

### Headline: Provider ID Match Rate (Darius's metric)
```
Provider ID Match Rate (engine catalog ID == tenant catalog ID):
  Electric: X/Y (Z%)
  Gas: X/Y (Z%)
  Water: X/Y (Z%)
  Sewer: N/A (no tenant data)
  Internet: N/A (informational)
  
  Overall: X/Y (Z%)
```

### Name-Based Accuracy (existing metric)
```
Name-Based Accuracy:
  Electric: X/Y (Z%)
  Gas: X/Y (Z%)
  Water: X/Y (Z%)
```

### ID Match Breakdown
```
ID Match Breakdown:
  ID_MATCH:                X (Z%)  — same catalog ID ✓
  NAME_MATCH_ID_MISMATCH:  X (Z%)  — catalog dupe issue, not engine error
  TRUE_MISMATCH:           X (Z%)  — engine got it wrong ✗
  TENANT_ID_MISSING:       X (Z%)  — couldn't ID-match tenant name
  ENGINE_ID_MISSING:       X (Z%)  — couldn't ID-match engine name
  BOTH_ID_MISSING:         X (Z%)  — neither side matched
```

### Adjusted Accuracy
```
Adjusted accuracy (treating NAME_MATCH_ID_MISMATCH as correct):
  Electric: X/Y (Z%)
  Gas: X/Y (Z%)
  Water: X/Y (Z%)
```

This is the number that accounts for catalog dupes. It answers: "if we clean up the catalog, what would the ID match rate be?"

### Other sections (keep existing):
- Accuracy by source
- Review queue breakdown
- AI resolver impact
- Geocoding stats
- Internet coverage stats
- Top mismatched providers

## PART 5: Generate Review Files

After the batch and report, run:
```bash
python generate_review_files.py
```

This should produce:
- `mapper_review_queue.xlsx` — flagged rows for mappers (NOT including internet)
- `mapper_review_queue.csv` — CSV version
- `batch_results_full.xlsx` — all rows + Summary sheet

The review files should include the new tenant ID columns and the `id_match` column so Darius can see the three-bucket breakdown per row.

## PART 6: Verification Checklist

After the batch completes, verify:
- [ ] All 91K rows processed (no rows dropped)
- [ ] Geocoding success rate (should be ~99%+)
- [ ] Electric, gas, water, sewer all have results
- [ ] Internet has results (if not skipped)
- [ ] Provider IDs populated on engine side (100% match rate on 100-row was 100%)
- [ ] Tenant provider IDs populated (check: how many TENANT_ID_MISSING?)
- [ ] id_match column populated for all scoreable rows
- [ ] AI resolver ran on flagged rows (check: how many changed?)
- [ ] Review files generated successfully
- [ ] Report contains all sections including ID match headline

## Environment Required

```
DATABASE_URL=postgresql://...   # Railway Postgres for internet
GOOGLE_API_KEY=...              # Google Places geocoder fallback
OPENROUTER_API_KEY=...          # AI resolver (Sonnet)
```

All should already be in `.env` from previous prompts.
