# REBUILD PRINCIPLES

## What this document is
Rules for any AI assistant working on this codebase. 
Read this before every task. If a proposed change 
violates these rules, stop and flag it.

## Architecture rules

1. ONE REPO. All utility lookup code lives in one 
   repository. There is no separate "analysis" repo.

2. ONE LOOKUP PATH. An address enters the system and 
   follows one code path to a result. There is no 
   fallback chain where a second system re-queries 
   the same sources if the first system fails. If a 
   source returns nothing, that's a null vote, not a 
   trigger to try a different pipeline.

3. ONE NORMALIZATION FUNCTION. There is exactly one 
   function that normalizes provider names: 
   normalize_provider() in provider_normalizer.py. 
   It reads from exactly one data file: 
   canonical_providers.json. No other file or module 
   may contain alias mappings, brand mappings, or name 
   matching logic. Any module that needs to compare 
   provider names imports from provider_normalizer.

4. NO AI IN THE DECISION LOOP. The lookup pipeline 
   does not call OpenAI, GPT, or any LLM to select 
   between competing provider results at query time. 
   AI may be used in offline batch analysis to generate 
   data files. It may not be used in the live lookup 
   path. This rule exists because AI decisions cannot 
   be audited, cached predictably, or debugged.

5. EVERY LOOKUP LOGS ITS DECISION. Every query writes 
   a structured log entry containing: all sources 
   queried, all results returned, the final selection, 
   the confidence score, and the reason for selection. 
   Format: JSON, one line per lookup.

6. CONFIDENCE SCORES MEAN SOMETHING. A confidence 
   score of 90 means the system is right 90% of the 
   time when it returns that score. Scores must be 
   calibrated against the ground truth test set, not 
   invented from intuition. Until calibrated, use 
   only three levels: high, medium, low.

## Code rules

7. NO FILE OVER 500 LINES. If a module exceeds 500 
   lines, it must be split. The current 3,950-line 
   utility_lookup_v1.py and 2,751-line api.py exist 
   because of unconstrained growth. Do not repeat this.

8. NO HARDCODED OVERRIDES IN CODE. ZIP-level 
   corrections, provider overrides, and special cases 
   go in data files (JSON), not in Python dicts or 
   if-statements. Code reads data. Code does not 
   contain data.

9. NO DUPLICATE MODULES. Before creating a new file, 
   check if an existing module already does this. If 
   the existing module is inadequate, modify it. Do 
   not create boundary_resolver.py alongside 
   boundary_lookup.py alongside 
   geographic_boundary_lookup.py alongside 
   geographic_boundary_analyzer.py.

## Data rules

10. GROUND TRUTH EXISTS BEFORE OPTIMIZATION. No 
    accuracy improvement work begins until there is a 
    verified test set of 300+ addresses with known 
    correct providers. All accuracy claims reference 
    this test set.

11. TENANT VERIFIED DATA IS THE BEST DATA. The 87K 
    tenant-verified addresses, after cleaning, are the 
    highest-confidence data source. They rank above GIS, 
    above EIA, above municipal lookups. Only manual 
    corrections from users rank higher.

12. DEREGULATED MARKETS USE TDUs ONLY. In TX, OH, PA, 
    and other deregulated states, the system stores and 
    returns the TDU/EDC, never the REP. REP data is 
    discarded during ingestion.