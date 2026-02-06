# Prompt 13: AI Resolver + Google Geocoder Fallback Report

**Date:** 2026-02-06

---

## 1. Google Places Geocoder Fallback

### Problem
Census batch geocoder fails ~10% of addresses (apartments, units, unusual formatting). Those addresses get zero utility results.

### Solution
Added automatic Google Places API fallback in `batch_validate.py` Phase 1. When Census batch returns no match, each failed address is retried via Google's geocoding API.

### Results (--limit 100)

| Metric | Before (Census only) | After (Census + Google) |
|--------|---------------------|------------------------|
| Geocoding rate | 90/100 (90.0%) | **100/100 (100.0%)** |
| Electric accuracy | 70/77 (90.9%) | **79/86 (91.9%)** |
| Gas accuracy | 32/35 (91.4%) | **39/42 (92.9%)** |

Google recovered **10/10 Census failures** — every single one.

### Implementation
- `batch_validate.py`: Phase 1 now checks for `GOOGLE_API_KEY` env var; if set and Census has failures, iterates failed addresses through `GoogleGeocoder`
- `GoogleGeocoder` already existed in `lookup_engine/geocoder.py` — returns lat/lon, city, state, ZIP, and **county** (useful for county-based gas lookups)
- API key stored in `.env`, added to `.gitignore`

---

## 2. AI Resolver Module

### Purpose
Post-processing step for low-confidence results. Sends candidates to an LLM to pick the most likely correct provider. Runs on `batch_results.csv` after the main batch — not inline during lookups.

### Module: `lookup_engine/ai_resolver.py`
- Supports OpenRouter and Anthropic APIs
- Rate-limited (0.5s between calls)
- Caches results by address+utility_type
- Structured JSON prompt → structured JSON response
- Graceful error handling (returns None on parse failures)

### Script: `ai_resolve_batch.py`
- `--dry-run`: Show how many rows need review without calling API
- `--compare`: Head-to-head model comparison on mismatch samples
- `--max-calls`: Cost control
- `--model`: Override default model
- Reads `batch_results.csv`, writes `batch_results_ai.csv` with `ai_reasoning` and `ai_resolved` columns

---

## 3. Model Comparison: GPT-5.2 vs Claude Sonnet

### Setup
- 18 mismatch/MATCH_ALT samples from the 100-row batch
- Both models called via OpenRouter with identical prompts
- Scored against tenant-reported provider (ground truth)

### Results

| Metric | GPT-5.2 | Claude Sonnet |
|--------|---------|---------------|
| **Correct** | **3/18 (17%)** | **11/18 (61%)** |
| Unique wins | 1 | 9 |
| Both correct | 2 | 2 |
| Neither correct | 6 | 6 |
| Errors | 0 | 0 |

### Key Findings

**Sonnet wins decisively (61% vs 17%).**

**GPT-5.2's failure mode:** Returns `"NONE"` too aggressively — refused to pick a candidate in 13/18 cases. Even when the correct answer was in the candidate list, GPT-5.2 declined to commit.

**Sonnet's strengths:**
- Correctly identified local co-ops and municipals the engine missed:
  - Pedernales Electric Cooperative (Austin TX suburb)
  - Horry Electric Cooperative (rural SC)
  - OUC / Orlando Utilities Commission (FL)
  - City of Troy Utilities (AL)
  - Laurens Electric Cooperative (SC)
  - Roseville Electric (CA municipal)
- Understood that tenant-reported providers are often correct for local utilities

**GPT-5.2's one unique win:** Santee Cooper (SC) — Sonnet picked the formal name "South Carolina Public Service Authority" which didn't fuzzy-match against the tenant's "Santee Cooper" shorthand.

### Sample Highlights

| Address | Tenant | Engine | GPT-5.2 | Sonnet |
|---------|--------|--------|---------|--------|
| Loxley Ln, Austin TX | Pedernales Electric | Austin Energy | Austin Energy ✗ | **Pedernales Electric ✓** |
| Fl Ave C, St Cloud FL | OUC | Duke Energy | NONE ✗ | **OUC ✓** |
| Evergreen Dr, Galivants Ferry SC | Horry Electric | Duke Energy | NONE ✗ | **Horry Electric ✓** |
| Henry Pl Blvd, Clarksville TN | CDE Lightband | Cumberland Elec | **CDE Lightband ✓** | **CDE Lightband ✓** |
| Moen Lp, Conway SC | Santee Cooper | Duke Energy | **Santee Cooper ✓** | SC Public Service Auth ✗ |

---

## 4. Recommendation

**Use Claude Sonnet as the default AI resolver.** It's 3.6x more accurate than GPT-5.2 on this task.

### Cost Estimate (Full 91K Batch)

| Scenario | Est. needs_review rows | API calls | Cost @ $0.003/call |
|----------|----------------------|-----------|-------------------|
| Conservative (conf < 0.80) | ~15,000 | 15,000 | ~$45 |
| With mismatches | ~18,000 | 18,000 | ~$54 |
| OpenRouter markup (+20%) | ~18,000 | 18,000 | ~$65 |

### Expected Impact
- Sonnet resolves ~61% of low-confidence cases correctly
- Reduces manual review queue from ~15K to ~6K
- Net accuracy gain: estimated +2-3% on electric and gas

---

## 5. Files Created/Modified

### Created
| File | Purpose |
|------|---------|
| `lookup_engine/ai_resolver.py` | AI resolver module (OpenRouter + Anthropic) |
| `ai_resolve_batch.py` | Batch post-processor with `--compare` mode |
| `.env` | API keys (Google Places + OpenRouter) |
| `ai_comparison_results.csv` | Detailed 18-sample comparison results |

### Modified
| File | Change |
|------|--------|
| `batch_validate.py` | Google geocoder fallback in Phase 1 |
| `.gitignore` | Added `.env` |

---

## 6. Usage

```bash
# Batch with Google geocoder fallback
GOOGLE_API_KEY=... python batch_validate.py --skip-water

# AI resolver dry run
python ai_resolve_batch.py --input batch_results.csv --dry-run

# Compare models (20 samples)
python ai_resolve_batch.py --input batch_results.csv --compare --compare-size 20

# Full AI resolve with Sonnet
python ai_resolve_batch.py --input batch_results.csv --output batch_results_ai.csv \
  --model anthropic/claude-sonnet-4-5

# Full AI resolve with GPT-5.2 (not recommended)
python ai_resolve_batch.py --input batch_results.csv --output batch_results_gpt.csv \
  --model openai/gpt-5.2
```

---

## 7. Updated Priority Chain (Complete)

```
Priority 0:   User Corrections        (0.98-0.99)
Priority 1:   State GIS API           (0.90-0.95)
Priority 2:   Gas ZIP Mapping          (gas only, 0.85-0.93)
Priority 2.5: Georgia EMC             (GA electric only, 0.72-0.87)
Priority 2.7: County Gas Lookup        (IL/PA/NY/TX, 0.60-0.88)
Priority 3:   HIFLD Shapefile          (0.75-0.85)
Priority 3.5: Remaining States ZIP     (0.65-0.85)
Priority 3.7: Special Districts Water  (AZ/CA/CO/FL/WA, 0.82)
Priority 4:   EIA ZIP Fallback         (electric only, 0.70)
Priority 5:   FindEnergy City Cache    (electric + gas, 0.65)
Priority 6:   State Default Gas LDC    (gas only, 0.40-0.65)

Post-processing:
  AI Resolver (Sonnet)    Resolves needs_review flagged results
  
Geocoding:
  Census Batch → Google Places fallback (100% geocoding rate)
```
