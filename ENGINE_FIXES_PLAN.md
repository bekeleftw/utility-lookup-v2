# Utility Lookup Engine — Bug Fixes & Accuracy Improvements

**Date:** February 11, 2026
**Source:** CODE_REVIEW.md findings cross-referenced with batch validation data (181,045 scoreable rows)
**Goal:** Push primary accuracy from ~91% toward 95%+

---

## Priority Order

| # | Fix | Risk | Expected Impact | Effort |
|---|-----|------|-----------------|--------|
| 1 | Dedup by canonical ID (not display name) | Low | +1-2pp (unlocks multi-source boost) | 1 hour |
| 2 | State ID matching word boundaries | Low | Fixes IN/OR/OH systematic errors | 30 min |
| 3 | Phone cross-contamination | Low | Fixes wrong phone numbers (no accuracy impact, but user-facing bug) | 30 min |
| 4 | EIA verification false positives | Low | Prevents bad confidence boosts on wrong providers | 1 hour |
| 5 | State GIS cache TTL | Low | Prevents stale None results persisting forever | 15 min |
| 6 | IOU demotion threshold tuning | Medium | Converts some MATCH_ALT → primary (7,872 electric rows in play) | 1 hour + testing |

**Do fixes 1-5 first (all low-risk), then re-run the batch to measure. Fix 6 requires careful testing.**

---

## Fix 1: Dedup by Canonical ID Instead of Display Name

**Problem:** `engine.py:624` — When the engine collects candidates from multiple sources, it deduplicates by display name. If State GIS returns "Duke Energy Carolinas" and HIFLD returns "Duke Energy", they don't dedup, so the provider misses the multi-source confidence boost (+0.05 per agreeing source). This means in overlap scenarios, a co-op could beat Duke not because it's more likely correct, but because Duke's confidence wasn't boosted.

**Impact:** 7,872 electric MATCH_ALT rows where the correct provider is in alternatives but not primary. Some fraction of these are caused by the right provider not getting boosted.

**Risk:** Low — this is purely improving dedup logic, doesn't change what sources are queried.

### Windsurf Prompt

```
Fix the candidate deduplication logic in engine.py around line 624.

CURRENT BEHAVIOR: Candidates from different sources are deduplicated by display name. If State GIS returns "Duke Energy Carolinas" and HIFLD returns "Duke Energy", they are treated as different providers and don't get the multi-source confidence boost.

DESIRED BEHAVIOR: Dedup by canonical provider ID instead of display name. Use the canonical_providers.json mapping (or the provider_id_matcher) to resolve each candidate's canonical ID before dedup. If two candidates resolve to the same canonical ID, treat them as the same provider and apply the multi-source confidence boost (+0.05 per additional source, capped at 0.98).

IMPLEMENTATION:
1. In the dedup/boost section of engine.py, after collecting all candidates for a utility type:
   - For each candidate, resolve its canonical name using the canonical_providers mapping in data/canonical_providers.json
   - Group candidates by canonical name (not raw display name)
   - When boosting, use the canonical grouping
2. Keep the original display name from the highest-priority source for the final output
3. Make sure the alternatives list also uses canonical dedup (don't show "Duke Energy" and "Duke Energy Carolinas" as separate alternatives)

TEST: Run a lookup for an address where you know HIFLD and State GIS return slightly different names for the same provider. Verify they now dedup and the confidence is boosted. Example test addresses:
- An address in NC served by Duke Energy (HIFLD says "Duke Energy" vs State GIS says "Duke Energy Carolinas")
- An address in GA where HIFLD says "Georgia Power" vs State GIS says "Georgia Power Company"

SUCCESS CRITERIA:
- Same provider from multiple sources gets confidence boost
- Display name comes from highest-priority source
- Alternatives don't show duplicates of the same provider under different names
- No regression on existing test addresses
```

---

## Fix 2: State-Specific ID Matching Word Boundaries

**Problem:** `provider_id_matcher.py:301` — The state matching logic uses `state.upper() in title_upper`, which is a substring match. Two-letter state codes like "IN" (Indiana), "OR" (Oregon), and "OH" (Ohio) match any provider title containing those letters as a substring. "IN" matches "DOMINION ENERGY VIRGINIA" because "IN" appears in "DOMINION" and "VIRGINIA". This causes cross-state provider matches.

**Impact:** Concentrated mismatches in IN, OR, OH. Looking at the batch data, Indiana has 98.3% accuracy (3,841/3,908) so the bug exists but may be partially masked by other signals. Still, fixing it removes a source of systematic error.

**Risk:** Low — strictly tightening a match condition.

### Windsurf Prompt

```
Fix the state-specific provider ID matching in provider_id_matcher.py around line 301.

BUG: The current code uses `state.upper() in title_upper` to check if a provider title is relevant to a state. This is a substring match, so state code "IN" (Indiana) matches any title containing "IN" as a substring — like "DOMINION ENERGY VIRGINIA", "CONSOLIDATED EDISON", etc. Same problem with "OR" (Oregon) matching "FLORIDA POWER", "OH" (Ohio) matching "OKLAHOMA GAS", etc.

FIX: Use word-boundary matching instead of substring matching. The state code should only match if it appears as a standalone word in the title.

IMPLEMENTATION OPTIONS (pick the cleanest):

Option A — Regex word boundary:
```python
import re
pattern = r'\b' + re.escape(state.upper()) + r'\b'
if re.search(pattern, title_upper):
```

Option B — Split and check tokens:
```python
title_words = title_upper.split()
if state.upper() in title_words:
```

Option C — More precise: only match state code at end of title or followed by punctuation/space, since provider names typically end with the state (e.g., "DUKE ENERGY INDIANA"):
```python
title_words = re.findall(r'[A-Z]+', title_upper)
if state.upper() in title_words:
```

Also check if there are similar substring matching issues elsewhere in the file for state codes. Search for patterns like `state in `, `state.upper() in`, etc.

TEST: 
- Verify "IN" (Indiana) does NOT match "DOMINION ENERGY VIRGINIA"
- Verify "IN" DOES match "DUKE ENERGY INDIANA" or "INDIANA MICHIGAN POWER"
- Verify "OR" does NOT match "FLORIDA POWER"
- Verify "OR" DOES match "PORTLAND GENERAL ELECTRIC OR"
- Verify "OH" does NOT match "OKLAHOMA GAS AND ELECTRIC"

SUCCESS CRITERIA:
- State code only matches as a whole word, not as a substring
- No regression on correct state-specific matches
```

---

## Fix 3: Phone Number Cross-Contamination

**Problem:** `scorer.py:244-258` — `_attach_contact_info()` looks up contact info by provider name but doesn't filter by utility type. If "City of Dallas" exists in the catalog as both a water provider (with a water department phone) and a general city entry (with a main line phone), whichever entry the fuzzy matcher hits first wins — regardless of whether the current lookup is for water, electric, or gas.

**Impact:** No accuracy impact (provider name is still correct), but users get wrong phone numbers. This is user-facing and damages trust.

**Risk:** Low — only changes which catalog entry is selected for contact info.

### Windsurf Prompt

```
Fix the phone number cross-contamination bug in scorer.py around lines 244-258.

BUG: The _attach_contact_info() method looks up contact info (phone, website) from the provider catalog by matching the provider name, but it doesn't filter by utility type. So if "City of Dallas" has entries for both water (water department phone) and electric (electric department phone), the wrong phone number can be attached.

FIX: Pass the utility type into _attach_contact_info() and use it to filter catalog matches.

IMPLEMENTATION:
1. Add a `utility_type` parameter to `_attach_contact_info()` (e.g., "electric", "gas", "water", "sewer")
2. When searching the provider catalog for contact info, prefer entries that match the utility type
3. Fallback logic: if no type-specific match exists, fall back to the best name match regardless of type (current behavior) — don't break lookups for providers that only have one catalog entry
4. Update all call sites to pass the utility type

The provider_catalog.json structure — check if entries have a utility_type field. If not, the catalog may need a minor schema update, or you can infer type from the catalog entry's context.

TEST:
- Look up an address where the primary water provider has the same name as an electric provider entry (e.g., a municipal utility). Verify the water result gets the water department phone, not the electric department phone.
- Look up a regular single-type provider and verify no regression.

SUCCESS CRITERIA:
- Water lookups get water-specific phone numbers
- Electric lookups get electric-specific phone numbers
- No phone numbers from wrong utility type
- Fallback still works for providers with only one catalog entry
```

---

## Fix 4: EIA Verification False Positives

**Problem:** `eia_verification.py:121` — The EIA cross-check compares the engine's provider name against EIA data using word overlap. But single common words like "ELECTRIC", "POWER", "ENERGY", "COMPANY" count as matches. This means almost any electric utility "matches" almost any other electric utility, making the EIA verification step nearly useless — it confirms everything, including wrong answers, giving them an unearned confidence bump.

**Impact:** TRUE_MISMATCH cases where the engine seemed confident but was wrong. The engine returns the wrong provider, EIA "verifies" it because both names contain "ELECTRIC", confidence goes up, and the wrong answer looks authoritative.

**Risk:** Low — tightening the verification only prevents false confirmations.

### Windsurf Prompt

```
Fix the EIA verification false positive issue in eia_verification.py around line 121.

BUG: The EIA cross-check uses word overlap between the engine's provider name and EIA data. But generic utility words like "ELECTRIC", "POWER", "ENERGY", "COMPANY", "CORPORATION", "INC", "LLC", "CO", "OF", "THE", "AND" are counted as matching words. This means nearly every electric utility "matches" nearly every other electric utility, making the verification useless.

FIX: Add a stop-word list and require meaningful word overlap.

IMPLEMENTATION:
1. Create a stop-word set for utility name comparison:
   ```python
   EIA_STOP_WORDS = {
       "ELECTRIC", "POWER", "ENERGY", "COMPANY", "CORPORATION", "CORP",
       "INC", "LLC", "CO", "OF", "THE", "AND", "UTILITY", "UTILITIES",
       "SERVICE", "SERVICES", "LIGHT", "GAS", "COOPERATIVE", "COOP",
       "ASSOCIATION", "AUTHORITY", "DEPARTMENT", "DEPT", "COMMISSION",
       "BOARD", "DISTRICT", "MUNICIPAL", "CITY", "COUNTY", "STATE",
       "PUBLIC", "RURAL"
   }
   ```

2. When comparing names, filter out stop words before calculating overlap:
   ```python
   engine_words = set(engine_name.upper().split()) - EIA_STOP_WORDS
   eia_words = set(eia_name.upper().split()) - EIA_STOP_WORDS
   overlap = engine_words & eia_words
   ```

3. Require at least 1 meaningful (non-stop-word) word overlap for a match, or require that the meaningful overlap covers at least 50% of the shorter name's meaningful words.

4. If no meaningful overlap exists, the EIA check should be neutral (no confidence boost or penalty) rather than a false positive match.

TEST:
- "Duke Energy" vs "Duke Energy Progress" → should match (both have "DUKE" after stop-word removal)
- "Duke Energy" vs "Georgia Power" → should NOT match (no meaningful overlap after removing "ENERGY" and "POWER")
- "Dominion Energy Virginia" vs "Dominion Energy South Carolina" → should match on "DOMINION"
- "Pacific Gas and Electric" vs "Southern California Edison" → should NOT match

SUCCESS CRITERIA:
- Common utility words no longer cause false positive matches
- Legitimate matches (same parent company, same provider different name) still work
- Wrong providers no longer get unearned confidence boosts from EIA verification
```

---

## Fix 5: State GIS Cache TTL

**Problem:** `state_gis.py:160` — The disk cache for State GIS API responses has no TTL. If a State GIS API returns `None` (transient error, timeout, API down), that `None` result is cached permanently. Future lookups for addresses in that area will always get `None` from cache, never retrying the API.

**Impact:** Addresses that hit a transient GIS API failure are permanently broken until the cache is manually cleared. This is a silent data loss issue that compounds over time.

**Risk:** Very low — only affects cache expiration.

### Windsurf Prompt

```
Add TTL to the State GIS disk cache in state_gis.py around line 160.

BUG: The State GIS results disk cache has no TTL (time-to-live). If a state GIS API call returns None due to a transient error (timeout, API temporarily down, rate limit), that None result is cached forever. Future lookups for the same coordinates will always get None from cache and never retry the API.

FIX: Add TTL-based cache expiration with different TTLs for successful vs failed results.

IMPLEMENTATION:
1. When writing to the disk cache, include a timestamp
2. When reading from the disk cache, check the timestamp:
   - Successful results (non-None): TTL of 90 days (these are stable — utility territories don't change often)
   - Failed results (None): TTL of 24 hours (retry the API after a day)
3. If the cached result has expired, treat it as a cache miss and re-query the API
4. Consider also adding a `--clear-stale-cache` CLI flag or startup option that purges expired entries

The cache format is likely a JSON file or SQLite — adapt the TTL logic to whatever storage format is currently used.

SUCCESS CRITERIA:
- Successful GIS results are cached for 90 days
- None/failed results are cached for only 24 hours, then retried
- Existing valid cache entries continue to work
- No performance regression on cache hits
```

---

## Fix 6: IOU Demotion Threshold Tuning (REQUIRES TESTING)

**Problem:** The IOU demotion logic in `engine.py` promotes co-ops/municipals over large IOUs (Duke, Dominion, Georgia Power, etc.) when the co-op has confidence ≥0.70. But 0.70 may be too high — some legitimate co-ops from State GIS or HIFLD come in at 0.60-0.70 and lose to the IOU.

**Impact:** Some of the 7,872 electric MATCH_ALT rows are co-ops/municipals that should have been promoted but weren't because they fell below the 0.70 threshold.

**Risk:** MEDIUM — The previous attempt at aggressive IOU demotion caused a -10pp regression (91.4% → 81.1%). That was a much more aggressive change (demoting IOUs broadly), but any change to this logic needs careful testing.

**IMPORTANT:** Do NOT deploy this without running the batch validation first. Test on the 91K-address dataset and compare accuracy before/after.

### Windsurf Prompt

```
CAUTION: This change requires batch validation before deployment. A previous aggressive IOU demotion caused a -10pp regression.

Investigate and potentially lower the IOU demotion alternative threshold in engine.py.

CURRENT BEHAVIOR: When the primary result is a large IOU (Duke, Dominion, Georgia Power, etc.) and a co-op/municipal exists in the alternatives with confidence >= 0.70, the co-op/municipal is promoted to primary. The threshold is 0.70.

INVESTIGATION:
1. First, add logging (can be temporary) to track IOU demotion decisions:
   - Log when an IOU is the primary and a co-op/municipal exists in alternatives
   - Log the co-op/municipal's confidence score
   - Log whether demotion was triggered or skipped (and why)

2. Run the batch validation with this logging enabled and collect:
   - How many addresses have an IOU primary with a co-op/municipal alternative?
   - What's the confidence distribution of those co-op/municipal alternatives?
   - Of those with confidence 0.60-0.70, how many are MATCH_ALT (correct provider in alt but not primary)?

3. Based on the data:
   - If many correct co-ops are at 0.60-0.70: lower threshold to 0.60
   - If most co-ops below 0.70 are wrong: keep threshold at 0.70
   - Consider a graduated approach: lower threshold only for sources that are typically reliable (State GIS, HIFLD) but not for low-quality sources (FindEnergy, EIA ZIP fallback)

CONSTRAINTS:
- Do NOT lower below 0.60 under any circumstances
- Do NOT change the logic that filters out low-quality sources from demotion
- Do NOT change the area filter (<5,000 km² for co-ops)
- Keep the existing guard that was added after the -10pp regression

OUTPUT: Before making any code changes, write a short analysis to STDOUT showing:
- Count of addresses affected at each threshold (0.70, 0.65, 0.60)
- Expected accuracy change based on MATCH_ALT data
- Recommendation

Only implement the threshold change if the analysis shows a clear net positive.
```

---

## Post-Fix Validation

After implementing fixes 1-5, run the full batch validation:

```
Run a full batch validation against the 91K-address dataset with all 5 fixes applied.

Compare results against the Phase 2 baseline:
- Electric: 95.0% (80,349 / 84,552)
- Gas: 96.7% (46,817 / 48,434)  
- Water: 93.9% (45,118 / 48,059)

Report:
1. New accuracy numbers per utility type
2. Change in MATCH vs MATCH_ALT counts (did any MATCH_ALT convert to MATCH?)
3. Change in MISMATCH count (did any mismatches get fixed?)
4. Any regressions (new mismatches that didn't exist before)
5. Confidence score distribution comparison (before/after)
```

---

## What These Fixes Won't Address

These fixes target the ~5% gap between current accuracy (91-95%) and the ceiling. The remaining gap is caused by:

- **Missing boundary data** — EMCs/co-ops within IOU footprints where we don't have the co-op's territory polygon (GA, SC, NC). Requires sourcing state-level cooperative territory maps.
- **Water fragmentation** — ~50,000 US water systems with approximate EPA boundaries. No quick fix; requires per-state water GIS data.
- **NH spatial bug** — HIFLD polygon for Public Service Co. of New Mexico erroneously covers NH. Requires manual shapefile correction or exclusion rule.
- **Utility rebranding** — Johnson City Power Board → Brightridge, etc. Requires an alias/rebrand lookup table.
- **AI resolver** — Currently disabled. Re-enabling with fixed guard logic could convert MATCH_ALT → MATCH for ambiguous cases, but is a larger project.

These are documented in the main performance report and are separate workstreams.
