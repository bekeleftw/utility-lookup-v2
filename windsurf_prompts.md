# Windsurf Prompts: Utility Lookup Rebuild — Next Steps
## February 6, 2026

Run these prompts in order. Each one builds on the previous output. Review outputs between prompts before proceeding.

---

## Prompt 1: Fix Collision #4 and Verify All Collision Resolutions

```
I need you to make two changes to canonical_providers.json based on my review of COLLISION_RESOLUTIONS.md:

**Fix #1: "columbia gas" bare alias (collision #4)**
The resolution assigned bare "columbia gas" to Columbia Gas PA, but our tenant data shows Ohio is actually the largest Columbia Gas market (686 OH vs 276 PA vs 265 VA). Rather than reassign to OH, follow the same pattern used for Peoples Gas (collision #11): REMOVE the bare "columbia gas" alias entirely. Keep only state-qualified aliases (e.g., "Columbia Gas of Ohio", "Columbia Gas of Pennsylvania", "Columbia Gas of Virginia", "Columbia Gas of Kentucky", "Columbia Gas of Maryland"). Our tenant data proves users almost always qualify with state — only 5 out of 1,270 Columbia Gas entries used the bare name.

Also add "Colombia gas of Ohio" as an alias for Columbia Gas of Ohio (misspelling found in tenant data).

**Fix #2: Verify all 15 collision resolutions were applied correctly**
After making the change above, run a verification pass:
- Confirm 0 alias collisions remain (same alias mapping to multiple providers)
- Confirm no holding company names appear as aliases (Berkshire Hathaway Energy, NiSource, AGL Resources, etc.)
- Confirm the HOLDING_COMPANIES set in provider_normalizer.py blocks holding companies from being returned as display names
- Print a summary of changes made

Do NOT touch any other aliases or providers beyond what's specified here.
```

---

## Prompt 2: Add Comma-Split Preprocessing to normalize_provider()

```
Our tenant data contains thousands of comma-separated multi-provider entries like:
- "Energy Texas, TXU Energy"
- "Reliant Energy, TXU Energy"  
- "Columbia Gas of Pennsylvania, Peoples Gas - PA"
- "Ameren MO, Spire Energy"
- "COLUMBIA GAS OF OHIO, First energy"

These all fail normalize_provider() because the full comma-separated string doesn't match any alias.

Add a comma-split preprocessing step to provider_normalizer.py:

1. If the input string contains a comma, split on comma and normalize each segment independently
2. Return ALL matched providers as a list (not just the first match)
3. Strip whitespace from each segment before matching
4. If no comma is present, behave exactly as before (single match)
5. Add a new function: normalize_provider_multi(raw_name) -> list[dict] that returns a list of {"canonical_id": ..., "display_name": ..., "original_segment": ...} for each matched segment
6. The existing normalize_provider() function should continue to work unchanged for backward compatibility — it returns a single best match
7. Log segments that don't match anything (these are candidates for alias additions)

Write tests that verify:
- "Energy Texas, TXU Energy" returns two matches
- "Columbia Gas of Pennsylvania, Peoples Gas - PA" returns two matches  
- "Duke Energy" (no comma) returns single match as before
- "Just Energy, Reliant Energy okay so we don't pay that" handles the trailing noise gracefully (match "Just Energy" and "Reliant Energy", ignore the rest)
- Empty segments after split are ignored

Do NOT modify canonical_providers.json in this prompt.
```

---

## Prompt 3: Build deregulated_reps.json and TX REP → TDU Mapping

```
Our tenant data has ~2,700 entries where Texas tenants reported their Retail Electric Provider (REP) instead of their Transmission and Distribution Utility (TDU). The lookup tool needs to return the TDU (the wire owner), not the REP (the retail provider the tenant chose). This is critical because REPs change when tenants switch plans, but the TDU is determined by physical address.

Create data/deregulated_reps.json with this structure:

{
  "metadata": {
    "description": "Retail Electric Providers in deregulated Texas (ERCOT). REPs are NOT canonical providers — they should never be returned as the 'correct' utility for an address.",
    "correct_behavior": "When a REP name is detected, the lookup should return the TDU for that address instead. TDU is determined by physical location using HIFLD boundary polygons.",
    "last_updated": "2026-02-06"
  },
  "texas_tdus": [
    {"name": "Oncor", "eia_id": null, "service_area": "Dallas-Fort Worth metro and surrounding"},
    {"name": "CenterPoint Energy Houston Electric", "eia_id": null, "service_area": "Greater Houston"},
    {"name": "AEP Texas Central", "eia_id": null, "service_area": "Corpus Christi, McAllen, Rio Grande Valley"},
    {"name": "AEP Texas North", "eia_id": null, "service_area": "Abilene, San Angelo, West Texas"},
    {"name": "Texas-New Mexico Power", "eia_id": null, "service_area": "Non-contiguous: N-Central TX, Gulf Coast, West TX"},
    {"name": "Lubbock Power & Light", "eia_id": null, "service_area": "Lubbock area"}
  ],
  "reps": {
    "TXU Energy": {"frequency_in_tenant_data": 522, "notes": "Largest REP by tenant mentions"},
    "Reliant Energy": {"frequency_in_tenant_data": 482},
    ...
  }
}

Populate the reps section using our tenant data. Extract ALL provider names from the Electricity column where the address is in Texas. Any provider name that is NOT one of the six TDUs and is NOT a known co-op or municipal utility is likely a REP.

To identify TX addresses: parse the state from the "display" column (address field). The state is typically the two-letter abbreviation before the ZIP code.

Also:
1. Remove TXU Energy from canonical_providers.json if it's still there (per AUDIT.md finding)
2. Add all TX REP names to provider_normalizer.py as a recognized-but-flagged category — when normalize_provider() encounters a known REP, it should return a result with a flag like {"is_rep": true, "market": "ERCOT", "note": "Address-based TDU lookup required"} instead of returning the REP as the provider
3. Cross-reference REPs against the PUC Texas certified REP list if accessible, otherwise use our tenant data frequency as the source

The six Texas TDUs should remain in (or be added to) canonical_providers.json as regular canonical providers. They ARE the correct answer for deregulated TX addresses.

Print a summary showing: total unique REP names found, total tenant entries affected, and the top 20 REPs by frequency.
```

---

## Prompt 4: Tenant Coverage Check with Updated Normalizer

```
Now that we've updated canonical_providers.json (collision fixes, bare alias removals) and provider_normalizer.py (comma-split, REP flagging), run the tenant coverage check.

Load the tenant verification CSV: addresses_with_tenant_verification_2026-02-06T06_57_49_470044438-06_00.csv

For each utility column (Electricity, Gas, Water, Trash, Sewer):
1. Extract all non-empty provider name strings
2. Run each through normalize_provider_multi() (to handle comma-separated entries)
3. Track: matched, unmatched, and REP-flagged

Report:
- Overall match rate by utility type (Electric, Gas, Water, Trash, Sewer)
- Overall match rate across all utility types combined
- Top 50 UNMATCHED provider strings by frequency (these are the highest-priority additions to canonical_providers.json)
- Top 20 REP-flagged entries (confirming the REP detection works)
- Total number of comma-separated entries successfully split and partially/fully matched

Save the report to TENANT_COVERAGE_REPORT.md (overwrite the previous unreviewed version).

IMPORTANT: The match rate for Water will be low — that's expected. Water providers are mostly municipal utilities with thousands of naming variants. Water accuracy depends on EPA boundary polygons, not on expanding canonical_providers.json. Focus the unmatched analysis primarily on Electricity and Gas, where name matching should work well.

Do not make any changes to canonical_providers.json based on the unmatched results. Just report them. I will review and decide which ones to add.
```

---

## Prompt 5: Verify TX TDU Boundaries in HIFLD Shapefile

```
Before we build the point-in-polygon lookup engine, I need to confirm the HIFLD electric retail service territories shapefile contains usable TX TDU boundaries.

Load the HIFLD electric shapefile: electric-retail-service-territories-shapefile/ (the .shp file inside)

1. Filter for STATE == 'TX' (or the Texas FIPS code if state is stored differently)
2. List ALL utility names found in Texas with their record count
3. Specifically confirm whether these six TDUs have boundary polygons:
   - Oncor
   - CenterPoint Energy (Houston Electric)
   - AEP Texas Central
   - AEP Texas North  
   - Texas-New Mexico Power (TNMP)
   - Lubbock Power & Light (LP&L)
4. For each TDU found, report: name as stored, number of polygon features, approximate total area (sq km or sq miles)
5. Check for overlapping boundaries between TDUs (there should be minimal overlap — each address falls in exactly one TDU territory)
6. Also list any TX co-ops and municipal utilities that have boundary polygons (these are the non-deregulated TX utilities that ARE the correct answer)

If any of the six TDUs are missing, flag which ones so I know what supplemental data to find.

Print the shapefile's metadata/attributes schema so I can see what fields are available (NAME, STATE, EIA_ID, etc.)
```

---

## Prompt 6: Add Basic Fuzzy Matching as Fallback

```
Our tenant data contains many typos and minor variants that fail exact alias matching:
- "txu engery" (should match TXU Energy)
- "Colombia gas of Ohio" (should match Columbia Gas of Ohio)  
- "Rythym Energy" (should match Rhythm Energy)
- "Txu energy", "txu", "TXU ENERGY" (case variants)
- "People's Gas" (apostrophe variant of Peoples Gas)
- "Spire - It's not called Alabama Gas Corp Anymore" (embedded name in free text)

Add a fuzzy matching fallback to provider_normalizer.py:

1. The matching order should be:
   a. Exact match on canonical_id (case-insensitive)
   b. Exact match on alias (case-insensitive) 
   c. Fuzzy match (Levenshtein distance or similar)
   d. No match

2. For fuzzy matching:
   - Use rapidfuzz or thefuzz library (install if needed)
   - Normalize both sides: lowercase, strip punctuation, collapse whitespace
   - Minimum similarity threshold: 85% (to avoid false matches)
   - If multiple candidates score above threshold, return the highest scoring one
   - Return the match with a confidence flag: {"match_type": "fuzzy", "similarity": 0.92}

3. For the "free text with embedded name" case (like "Spire - It's not called Alabama Gas Corp Anymore"):
   - After fuzzy match fails, try matching any canonical_id or alias as a SUBSTRING of the input
   - Minimum canonical_id/alias length of 4 characters to avoid spurious substring matches
   - Return with {"match_type": "substring", "matched_on": "Spire"}

4. Add a function: normalize_provider_verbose(raw_name) -> dict that returns the full match details including match_type, similarity score, and what was matched against. The standard normalize_provider() should use fuzzy matching silently.

5. Write tests covering all the examples above plus edge cases:
   - Very short inputs ("AEP") should still match
   - Completely unrelated strings ("Netflix") should return no match
   - Near-misses between different providers shouldn't cross-match (e.g., "PECO" shouldn't fuzzy-match to "Pedernales")

Do NOT lower the threshold below 85% — false matches are worse than no match.
```

---

## Notes

- Run prompts 1-4 as a batch. Review the TENANT_COVERAGE_REPORT.md output before running 5-6.
- Prompt 5 requires the HIFLD shapefile to be accessible in the project directory.
- Prompt 6 requires installing rapidfuzz or thefuzz (`pip install rapidfuzz`).
- After all six prompts, the next phase is the OpenEI cross-reference (matching 2,843 EIA-keyed utilities against canonical_providers.json) and then the actual point-in-polygon lookup engine build.
