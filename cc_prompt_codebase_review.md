# Claude Code: Full Codebase Review of utility-lookup-v2

Review the entire `utility-lookup-v2/` project. This is a utility provider lookup engine that takes a US address and returns the electric, gas, water, sewer, and internet providers serving that address. It uses a multi-source fallback chain with 11+ data sources, AI post-processing, provider ID matching against an internal catalog, and outputs human review files.

Produce a structured review I can paste into another Claude conversation for evaluation. Be thorough but concise — flag real issues, not style preferences.

## What to review

### 1. Architecture & Data Flow
- Read `engine.py` and trace the full lookup flow for ALL 5 utility types (electric, gas, water, sewer, internet)
- Document the actual priority chain as implemented (not as documented — verify the code matches the reports)
- Check: does every lookup path collect candidates from ALL sources, or does it short-circuit?
- Check: is deduplication and confidence boosting working correctly?
- Check: is the `needs_review` flag set at the right threshold?
- Check: does the IOU demotion logic work correctly? (When Duke/Dominion/AEP is primary and a co-op/municipal alternative exists with confidence ≥ 0.60, they should swap)
- Check: does sewer correctly inherit from water and fall back through city/county matching?
- Check: does internet correctly get Census block GEOID from geocoder and query Postgres?

### 2. Module-by-Module Review
For each module in `lookup_engine/`, review:
- `engine.py` — main orchestrator, all 5 utility types, IOU demotion, water/sewer overlap resolution
- `state_gis.py` — state GIS API queries, circuit breaker, caching
- `spatial.py` — HIFLD shapefile point-in-polygon queries
- `gas_mappings.py` — gas ZIP prefix lookups
- `county_gas.py` — county-based gas lookups (IL, PA, NY, TX)
- `georgia_emc.py` — Georgia EMC county lookup
- `eia_verification.py` — EIA ZIP verification + fallback
- `findenergy_lookup.py` — FindEnergy city cache
- `remaining_states.py` — remaining states ZIP data
- `special_districts.py` — water special districts (AZ, CA, CO, FL, WA)
- `corrections.py` — user corrections (Priority 0) + mapper address corrections + ID mapping corrections (SQLite tables)
- `provider_normalizer.py` — name normalization, canonical matching
- `provider_id_matcher.py` — fuzzy matching engine names to internal catalog (14,345 entries), alias expansion, HIFLD truncation expansion, state-specific matching, mapper ID overrides
- `ai_resolver.py` — Sonnet-based post-processing for low-confidence results, concurrent batch resolution with ThreadPoolExecutor
- `internet_lookup.py` — FCC BDC Postgres queries, tech code labeling, speed sorting
- `geocoder.py` — Census batch geocoder (geographies endpoint for GEOID), Google Places fallback, TIGERweb GEOID for Google results, `get_census_block_geoid()`
- `scorer.py` — confidence scoring, check that water confidence is 0.82 (not the old 0.60)
- `models.py` — data models including `catalog_id`, `catalog_title`, `id_match_score`, `id_confident`, `block_geoid`

For each: flag bugs, error handling gaps, edge cases, and performance concerns.

### 3. Data Files Integrity
Check these data files exist and are valid JSON/SQLite:
- `data/state_gis_endpoints.json` — are all URLs well-formed? Any obvious bad endpoints?
- `data/gas_mappings/*.json` (texas, california, illinois, ohio, arizona, georgia)
- `data/gas_county_lookups.json`
- `data/georgia_emcs.json`
- `data/remaining_states_electric.json`, `remaining_states_gas.json`, `remaining_states_water.json`
- `data/special_districts_water.json`
- `data/eia_zip_utility_lookup.json`
- `data/findenergy/city_providers.json`
- `data/state_gas_defaults.json`
- `data/corrections.db` — check both tables exist: `address_corrections` and `id_mapping_corrections`
- `data/corrections/*.json` (electric_zip.json, gas_zip.json, water_zip.json)
- `data/canonical_providers.json`
- `data/deregulated_reps.json`
- `data/provider_catalog.csv` — 14,345 entries, check it loads and has expected columns (Id, Title, UtilityTypeId, etc.)

For each: does the file exist? Is it loadable? Does the structure match what the code expects?

### 4. Batch Validation Pipeline
- Read `batch_validate.py` — this has 3 phases:
  - **Phase 1:** Geocoding (Census batch → Google fallback → TIGERweb GEOID for Google results)
  - **Phase 2:** Lookup all 5 utility types, provider ID matching, comparison against tenant data
  - **Phase 3:** AI resolver on flagged rows (Sonnet via OpenRouter, 20 concurrent workers)
- Check: does it correctly handle all comparison categories (MATCH, MISMATCH, MATCH_TDU, MATCH_PARENT, MATCH_ALT, TENANT_ONLY, ENGINE_ONLY, BOTH_EMPTY, TENANT_PROPANE)?
- Check: is the accuracy calculation using the right denominator (scoreable rows only)?
- Check: does it output all ID columns? (`engine_catalog_id`, `engine_catalog_title`, `engine_id_match_score`, `engine_id_confident`, `alt_catalog_ids`)
- Check: does the internet output format work? (pipe-separated providers with tech/speed)
- Check: does sewer output include catalog IDs?
- Check: does `--skip-ai` correctly disable Phase 3?
- Check: does `--skip-water` and `--skip-internet` work?
- Check: is the `.env` loading working for DATABASE_URL, GOOGLE_API_KEY, OPENROUTER_API_KEY?

### 5. Review File Generation
- Read `generate_review_files.py`
- Check: does it create `mapper_review_queue.xlsx` with correct formatting (frozen panes, color coding, dropdown validation)?
- Check: review criteria — rows flagged when ANY of: MISMATCH, MATCH_ALT, needs_review=True, catalog_id=None, id_confident=False
- Check: does `batch_results_full.xlsx` include a Summary sheet with accuracy/ID/review stats?
- Check: does `mapper_review_queue.csv` match the XLSX content?

### 6. Mapper Feedback Loop
- Read `import_mapper_corrections.py`
- Check: does it correctly parse all 4 decision types (Correct, Wrong - Use Alternative, Wrong - Manual Entry, Skip)?
- Check: does "Wrong - Use Alternative" insert into `address_corrections` table?
- Check: does "Wrong - Manual Entry" insert into BOTH `address_corrections` and `id_mapping_corrections`?
- Check: does `corrections.py` → `lookup_by_latlon()` correctly search within ~100m radius?
- Check: do mapper corrections actually override at Priority 0 on next engine run?

### 7. Provider ID Matching
- Read `provider_id_matcher.py`
- Check: alias expansion (SCE → Southern California Edison, SDG&E → San Diego Gas & Electric, etc.)
- Check: HIFLD truncation expansion ("Elec Member" → "Electric Membership", etc.)
- Check: does state-specific matching prefer entries with matching state suffix?
- Check: is fuzzy_set matching (token_set_ratio, cutoff 90) correctly catching partial name matches?
- Check: do mapper ID overrides from `id_mapping_corrections` table load and take priority?
- Check: TYPE_MAP includes all utility types: electric=2, water=3, gas=4, trash=5, sewer=6

### 8. AI Resolver
- Read `ai_resolver.py`
- Check: does `resolve_batch()` correctly use ThreadPoolExecutor with 20 workers?
- Check: is rate limiting set to 0.0s (OpenRouter handles it server-side)?
- Check: does it correctly handle NONE responses (leave engine pick unchanged)?
- Check: does it only modify when confident? (Should change ~14% of flagged rows)
- Check: does it support both OpenRouter and Anthropic API backends?
- Check: is caching working (avoid re-resolving same address+utility)?

### 9. Error Handling & Resilience
- What happens when a state GIS API is down? Does the circuit breaker work?
- What happens when geocoding fails? Does the engine gracefully return nothing?
- What happens with malformed addresses?
- What happens when Postgres (DATABASE_URL) is unreachable? Does internet lookup fail gracefully?
- What happens when OpenRouter API is unreachable? Does Phase 3 skip gracefully?
- Are there bare `except:` blocks swallowing errors silently?
- Are timeouts configured appropriately?

### 10. Performance Concerns
- How many files are loaded into memory at startup? Estimate total memory footprint.
- Are shapefiles loaded lazily or eagerly?
- Is the state GIS cache implementation correct? Could it leak memory on large batches?
- Any N+1 query patterns or unnecessary repeated work?
- Is the Postgres connection pooled or opened/closed per query?
- Is the Census GEOID correctly parsed from the batch geocoder response (no extra HTTP calls)?
- Does the AI resolver's concurrent execution actually work without race conditions?

### 11. Test Coverage
- Read `tests/` directory
- List what's tested and what's NOT tested
- Are tests still passing? Run them: `cd utility-lookup-v2 && python -m pytest tests/ -v`
- Flag any tests that are testing the wrong thing or have stale assertions
- Note: there likely are NO tests for the new modules (provider_id_matcher, ai_resolver, internet_lookup, sewer, review file generation). Flag this.

### 12. Potential Bugs (check these specifically)
- Does the WI electric multi-layer query correctly substitute `{layer}` with 0, 1, 2?
- Does the gas state-match scoring correctly penalize cross-state results?
- Does the water name normalizer handle all the formats: "City Of X", "X, City Of", "X Water Dept", "X (2310001)"?
- Does the TX deregulated market logic correctly identify REPs vs TDUs?
- Does the confidence boosting cap at 0.98 or could it exceed 1.0?
- Are ZIP codes always 5-digit strings, or could int conversion strip leading zeros (NJ: 07xxx)?
- **Does the `set_conf` parameter in `_add_candidate()` correctly override scorer confidence?** (There was a bug where correction confidence was capped by passthrough confidence of 0.60 — verify this is fixed)
- **Does IOU demotion avoid swapping when the alternative is from an unreliable source?** (e.g., don't swap Duke for a FindEnergy result with 0.65 confidence)
- **Does the Census batch geocoder endpoint use `/geographies/addressbatch` (not `/locations/addressbatch`)?** The geographies endpoint returns FIPS fields needed for block GEOID.
- **Does `internet_lookup.py` handle Postgres JSONB correctly?** (row[0] could be a dict or a string depending on psycopg2 version)
- **Does `provider_id_matcher.py` handle the Enbridge/East Ohio Gas rebrand alias?**
- **Are sewer confidence thresholds correct?** City match should be 0.82, county 0.75, water inheritance min(water_conf + 0.05, 0.88)

## Output Format

Structure your review as:

```
## Summary
One paragraph: overall assessment, biggest risks, ready-for-production or not

## Critical Issues (must fix before batch run)
- Issue, file, line if possible, why it matters

## Moderate Issues (fix soon)
- Issue, file, why it matters

## Minor Issues (nice to have)
- Issue, file

## Architecture Notes
- How the system actually works (verified from code, not docs)
- Any discrepancies between reports and actual implementation
- Verify the complete priority chain matches what's documented

## Data File Audit
- File: exists? valid? structure matches code expectations?

## Provider ID Matching Audit
- Alias coverage: any common abbreviations missing?
- Fuzzy match edge cases: any false positives or missed matches?
- Catalog duplicate analysis: how many providers have multiple entries?

## AI Resolver Audit
- Prompt quality: is the LLM prompt well-structured?
- Error handling: what happens on API failures mid-batch?
- Cost estimate: at 20 concurrent workers, how fast for 91K × 11% flagged?

## Test Results
- pytest output
- Coverage gaps — especially for new modules

## Performance Estimate
- Memory footprint
- Expected batch runtime for 91K addresses (all 5 utility types)
- Postgres query performance for internet lookups at scale
```
