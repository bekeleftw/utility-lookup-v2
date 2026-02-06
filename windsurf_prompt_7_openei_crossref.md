# Windsurf Prompt 7: OpenEI Cross-Reference and Priority Ingestion
## February 6, 2026

Run this AFTER prompts 5 and 6 are complete.

---

```
We have two data assets that need to be cross-referenced:

1. canonical_providers.json — 401 canonical providers (our current normalizer database)
2. openei_utilities.json — 2,843 unique utilities with EIA IDs extracted from the USURDB rate database

And one validation source:
3. TENANT_COVERAGE_REPORT.md — contains the top 50+ unmatched provider strings from 91K tenant records

The goal: identify which OpenEI utilities are MISSING from canonical_providers.json, prioritize them by how often they appear in our tenant data, and add the highest-impact ones.

## Step 1: Build the Cross-Reference

Load both files. For each OpenEI utility (keyed by EIA ID):
- Check if ANY of its name variants match a canonical_providers.json entry (by canonical_id or alias, case-insensitive)
- If a match exists, record it and note whether the EIA ID is stored in canonical_providers.json (it probably isn't yet — we should add it)
- If no match exists, flag it as a missing provider

Output a summary:
- Total OpenEI utilities: X
- Matched to existing canonical: X (with EIA ID already stored: X, without: X)
- Unmatched (missing from canonical): X

## Step 2: Score Missing Providers by Tenant Data Impact

Load the tenant CSV: addresses_with_tenant_verification_2026-02-06T06_57_49_470044438-06_00.csv

For each MISSING OpenEI utility:
- Check if any of its name variants appear in the tenant data (Electricity, Gas, Water columns)
- Use case-insensitive matching and also try the fuzzy matcher from prompt 6 (if available) to catch near-misses
- Count total tenant mentions across all columns

Sort missing providers by tenant mention frequency, descending.

Output: top 100 missing providers with columns:
| EIA ID | OpenEI Name | Name Variants | Tenant Mentions | Utility Type | State |

## Step 3: Auto-Generate Additions

For missing providers with 10+ tenant mentions, auto-generate canonical_providers.json entries:

{
  "[Consumer-facing display name]": {
    "display_name": "[Consumer-facing display name]",
    "eia_id": [EIA ID number],
    "aliases": [all OpenEI name variants plus any tenant data spelling variants found],
    "utility_type": "[electric/gas/water/multi]"
  }
}

Rules for display names:
- Use the consumer-facing brand name, not the legal/corporate name
- Strip suffixes like "Inc", "Corp", "Company", "LLC" unless they're part of the common name
- For co-ops: keep "Electric Cooperative" or abbreviate to "Electric Co-op" based on which the OpenEI entry uses
- For municipal utilities: use "City of X" format if that's the common name
- NEVER use a holding company or parent company name as the display name

Do NOT auto-add these to canonical_providers.json yet. Write them to a new file: data/openei_additions_candidates.json

## Step 4: Add EIA IDs to Existing Canonical Providers

For the OpenEI utilities that DO match existing canonical providers, add the EIA ID to canonical_providers.json if it's not already there. The EIA ID is the universal join key that links to HIFLD shapefiles, EIA-861 data, and all federal data sources. This is critical infrastructure for the point-in-polygon rebuild.

Format: add "eia_id": [number] to each matched canonical provider entry.

Report how many canonical providers received EIA IDs.

## Step 5: Ingest EIA-861 Mergers

Load Mergers_2024.xlsx from the project. This contains utility mergers/acquisitions/name changes.

For each merger record:
- If the OLD utility name is not already an alias of the NEW utility in canonical_providers.json, add it
- If the NEW utility is not in canonical_providers.json at all, add it to openei_additions_candidates.json
- Log all alias additions

This catches cases where tenants use an old utility name that's been replaced by a merger (e.g., a tenant writes "Dayton Power & Light" but it's now "AES Ohio").

## Step 6: Summary Report

Save to OPENEI_CROSSREF_REPORT.md:

1. Cross-reference statistics (matched, unmatched, EIA IDs added)
2. Top 100 missing providers ranked by tenant impact
3. Total candidate additions generated (with 10+ mentions)
4. Total candidate additions generated (with 1-9 mentions, listed separately)
5. Merger aliases added
6. Recommended next action: "Review openei_additions_candidates.json, approve additions, then re-run tenant coverage check to measure improvement"

## Important Notes

- Do NOT modify canonical_providers.json except to add EIA IDs to existing entries and merger aliases
- All new provider additions go to openei_additions_candidates.json for review
- Water/sewer/trash providers from OpenEI are low priority — focus the tenant matching on Electricity and Gas columns
- Some OpenEI entries are wholesale generators, transmission-only companies, or federal power marketing agencies (like Bonneville Power, Western Area Power, Tennessee Valley Authority). These should be flagged but NOT added as canonical providers unless they appear in tenant data — tenants don't set up accounts with wholesale generators
- Co-ops with very few tenant mentions (under 3) are probably not worth adding yet — they'll be covered by the point-in-polygon engine using HIFLD boundaries
```

---

## What to Expect

This prompt will likely surface 100-200 missing providers worth adding, with the top 30-50 accounting for most of the unmatched tenant entries. After you review and approve the additions, re-running the tenant coverage check should push electric match rate from ~89% (matched + REP-flagged) to ~94-96%.

The EIA ID backfill is arguably the most valuable part — it creates the join key you need to link canonical_providers.json entries to HIFLD shapefile polygons for the point-in-polygon engine.
