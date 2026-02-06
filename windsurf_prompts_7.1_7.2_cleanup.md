# Windsurf Prompts 7.1 & 7.2: Normalizer Cleanup
## February 6, 2026

Run both before Prompt 8 (engine build). Run 7.1 first, then 7.2, then re-run tenant coverage check.

---

## Prompt 7.1: Alias Additions, Missing Providers, and Null Filtering

```
The updated TENANT_COVERAGE_REPORT.md shows 1,914 unmatched Electric+Gas entries. Most fall into three categories: missing abbreviation aliases, genuinely missing providers, and null/placeholder values. Fix all three.

## Part A: Add Abbreviation Aliases to Existing Canonical Providers

These unmatched strings are abbreviations or minor variants of providers that ARE already in canonical_providers.json. Add them as aliases to the correct existing entry. Check that the canonical entry exists first — if it doesn't, add it as a new provider in Part B instead.

| Unmatched String | Likely Canonical Provider | Notes |
|---|---|---|
| Alliant Energy (693 mentions) | Alliant Energy | May already be canonical — check. If it is, find why it's not matching. If not, add as new provider. This is a major Iowa/Wisconsin IOU serving 1M+ customers. |
| GVEC (83) | Guadalupe Valley Electric Cooperative | Common abbreviation |
| SMECO (8) + SMECO - MD (48) | Southern Maryland Electric Cooperative | Abbreviation + state-qualified variant |
| NOVEC (21) + Novec (7) | Northern Virginia Electric Cooperative | Abbreviation, case variant |
| Eversource-NH (17) | Eversource NH | Dash variant |
| KUB (16) | Knoxville Utilities Board | Abbreviation |
| OGE (9) + Oge (4) | Oklahoma Gas and Electric | Common abbreviation |
| DEMCO (11) | Dixie Electric Membership Corporation | Louisiana co-op abbreviation |
| JOEMC (10) | Jones-Onslow Electric Membership Corporation | NC co-op abbreviation |
| LCEC (10) | Lee County Electric Cooperative | FL co-op abbreviation |
| PSO (3) | Public Service Company of Oklahoma | Common abbreviation |
| NGEMC (4) | North Georgia Electric Membership Corporation | Co-op abbreviation |
| Chelco (19) | Choctawhatchee Electric Cooperative | FL co-op abbreviation |
| Constellation (13) | Constellation Energy | May need to check if this is already canonical |
| Irving (10) | City of Irving (TX) | Municipal utility, may need new entry |
| SiEnergy (9) | SiEnergy (TX gas utility) | May need new entry |
| Embridge (4) | Enbridge Gas (likely misspelling) | Add as fuzzy alias or typo alias |
| DPU (2) + DPU Orangeburg (3) | Department of Public Utilities, Orangeburg SC | Municipal |

For each one:
1. Check if the canonical provider already exists
2. If yes: add the abbreviation/variant as a new alias
3. If no: add as a new canonical provider with the abbreviation as the primary alias
4. Log every change made

## Part B: Add Missing Providers

These are genuinely missing from canonical_providers.json. Add them as new canonical providers:

| Provider | Mentions | Type | State | Notes |
|---|---|---|---|---|
| CDE Lightband | 100 | Municipal | TN | Clarksville Dept of Electricity |
| Brightridge | 73 | Municipal | TN | Johnson City, formerly BrightRidge |
| Canoochee EMC | 65 | Co-op | GA | Canoochee Electric Membership Corp |
| Flint Energies | 20 | Co-op | GA | Flint Electric Membership Corp |
| Joe Wheeler EMC (4) + Okefenoke REMC (6) + Amicalola EMC (3) + Northeastern REMC (3) | Various | Co-op | GA/AL | Small co-ops but real utilities |
| Naperville IL (24) | 24 | Municipal | IL | City of Naperville electric utility |
| Waynesville MO (5) | 5 | Municipal | MO | City of Waynesville utilities |

For each new provider:
- Set display_name to the consumer-facing brand name
- Add common abbreviations and formal names as aliases
- Add EIA ID if you can find it in openei_utilities.json by name match
- Set utility_type appropriately

## Part C: Filter Null/Placeholder Values

Add these strings to a SKIP_LIST in provider_normalizer.py so they return a special result like {"match_type": "null_value", "display_name": null} instead of showing up as unmatched:

Exact strings to skip (case-insensitive):
- N/A, Na, NA, N/a, n/a
- None, none, NONE
- Not needed, Not applicable, Not required
- Landlord, landlord
- Included, included, Included in rent, included in rent
- Unknown, unknown
- Choose your electric here (this is placeholder text from your system)

Also skip any string that is ONLY whitespace or empty after stripping.

These should NOT count as "unmatched" in the coverage report — they're not provider names at all.

## Part D: Handle Propane Entries

These are propane delivery companies, not regulated utilities. Add them to a separate category:

- Amerigas Propane (31), Amerigas (6)
- Suburban Propane (23)
- Direct Propane (3)
- Superior Plus Propane (3)
- McPhails (3) — propane delivery

Add a PROPANE_COMPANIES set in provider_normalizer.py. When detected, return {"match_type": "propane", "display_name": "[name]", "note": "Propane delivery service, not a regulated utility"}.

These should be reported separately in the coverage report, not as "unmatched."

## Verification

After all changes, print:
- Total canonical providers (should be ~435-445)
- Total new aliases added
- Total new providers added
- Total null/placeholder strings added to skip list
- Total propane companies added

Do NOT re-run the full tenant coverage check yet — that happens after Prompt 7.2.
```

---

## Prompt 7.2: Fix REP Detection False Positives

```
The TENANT_COVERAGE_REPORT.md shows the REP detection system is producing false positives. Several legitimate utilities are being flagged as Texas REPs when they're not:

| Incorrectly Flagged | Mentions | What It Actually Is |
|---|---|---|
| City of Tallahassee Utilities | 211 | Florida municipal utility |
| Florence Utilities Department | 124 | Alabama municipal utility |
| Middle Tennessee Electric | 123 | Tennessee co-op |
| Withlacoochee River Electric Cooperative - FL | 103 | Florida co-op |
| Bryan Texas Utilities | 81 | TX municipal, NOT deregulated (own generation) |
| GRAYSON-COLLIN ELEC COOP | 77 | TX co-op, NOT deregulated |
| Singing River Electric Cooperative | 72 | Mississippi co-op |

That's ~591 entries incorrectly REP-flagged. These should be matching as canonical providers instead.

## Root Cause Analysis

First, diagnose WHY these are being flagged as REPs. The likely causes:

1. **Overly broad REP detection**: The is_deregulated_rep() function may be flagging any unrecognized Texas electric provider as a potential REP, rather than only flagging known REP names from deregulated_reps.json
2. **Non-TX utilities being flagged**: Tallahassee (FL), Florence (AL), Middle Tennessee, Singing River (MS) are not in Texas at all — REP detection should only apply to Texas addresses or at minimum only flag names that are in the deregulated_reps.json list
3. **TX co-ops and municipals being flagged**: Bryan TX Utilities and Grayson-Collin are Texas utilities but they're NOT in the deregulated ERCOT market. Co-ops and municipals in TX are exempt from deregulation.

## Required Fixes

1. **REP detection must be STRICT, not inferential.** is_deregulated_rep() should ONLY return true if the provider name exactly matches (or fuzzy-matches) a name in deregulated_reps.json. It should NOT flag unknown names as potential REPs. Unknown names should stay as "unmatched," not get reclassified as REPs.

2. **Add these legitimate utilities to canonical_providers.json** if they're not already there:
   - City of Tallahassee Utilities (FL municipal)
   - Florence Utilities Department (AL municipal) 
   - Middle Tennessee Electric (TN co-op)
   - Withlacoochee River Electric Cooperative (FL co-op)
   - Bryan Texas Utilities (TX municipal, non-ERCOT)
   - Grayson-Collin Electric Cooperative (TX co-op)
   - Singing River Electric Cooperative (MS co-op)

3. **Add a TX non-deregulated list** to tx_deregulated.py or config: a list of Texas utilities that are NOT in the deregulated ERCOT market. This includes:
   - All TX co-ops (they are exempt from deregulation)
   - All TX municipal utilities (CPS Energy, Austin Energy, Bryan TX Utilities, etc.)
   - Entergy Texas (East TX, separate grid)
   - El Paso Electric (West TX, separate grid)
   - Southwestern Public Service Co (Panhandle, separate grid)
   
   When a lookup returns one of these for a TX address, it is the FINAL answer — no TDU lookup needed.

4. **Review the full deregulated_reps.json** for any other legitimate utilities that got miscategorized as REPs. A REP is a company that ONLY sells electricity plans — it doesn't own wires, doesn't generate power, and the customer can switch to a different REP. If a company owns infrastructure and delivers power, it's NOT a REP.

## Verification

After fixing:
1. Re-run the tenant coverage check (full re-run of the coverage report)
2. Confirm the 7 false-positive utilities now match as canonical providers
3. Confirm REP-flagged count drops (should drop by at least 591)
4. Confirm Electric match rate increases (recovering ~591 entries from REP-flagged to matched should push Electric from 89.5% toward 90.2%+)
5. Print the updated REP-flagged top 20 — every entry should be an actual TX REP (TXU, Reliant, Gexa, Energy Texas, etc.)

Save updated report to TENANT_COVERAGE_REPORT.md (overwrite).
```

---

## Expected Results After Both Prompts

| Metric | Before 7.1/7.2 | After (estimated) |
|---|---|---|
| Electric match % | 89.5% | ~93-94% |
| Gas match % | 97.1% | ~97.5% |
| Electric+Gas combined | 92.3% | ~95% |
| REP-flagged (electric) | 7,770 | ~5,500 (true REPs only) |
| Unmatched (electric) | 1,249 | ~400-500 |
| Null/placeholder (new category) | 0 | ~100 |
| Propane (new category) | 0 | ~65 |

After these two prompts, re-run the tenant coverage check to get final numbers, then proceed to Prompt 8 (engine build).
