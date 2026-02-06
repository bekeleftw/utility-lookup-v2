# Prompt 14: Provider ID Matching, Human Review Output & Mapper Feedback

**Date:** 2026-02-06

---

## Part 1: Provider ID Matching

### Module: `lookup_engine/provider_id_matcher.py`

Fuzzy-matches engine provider names to the internal catalog (14,345 entries) to get provider IDs.

### Matching Pipeline

1. **Alias expansion** — common abbreviations (SCE → Southern California Edison, SDG&E → San Diego Gas & Electric, etc.)
2. **HIFLD truncation expansion** — "Elec Member" → "Electric Membership", "Pwr" → "Power", etc.
3. **Exact match** on normalized name (score 100)
4. **State-specific match** — prefer catalog entries containing the state code (score ≥ 70)
5. **Fuzzy match** via `token_sort_ratio` (cutoff 72)
6. **Fuzzy set match** via `token_set_ratio` (cutoff 90) — catches "Duke Energy Carolinas" → "Duke Energy"
7. **Mapper ID overrides** from `id_mapping_corrections` table (score 100)

### Results (--limit 100)

| Metric | Value |
|--------|-------|
| Providers with results | 199 |
| **ID matched** | **199 (100%)** |
| **ID confident (score ≥ 85)** | **175 (87.9%)** |
| No ID match | 0 |

### Test Cases

| Engine Provider | Catalog Match | ID | Score | Method |
|----------------|---------------|-----|-------|--------|
| Duke Energy Carolinas | Duke Energy Corporation | 13746 | 100 | fuzzy_set |
| Oncor | Oncor Electric-TX | 16065 | 100 | exact |
| Atmos Energy | Atmos Energy | 29 | 100 | exact |
| CenterPoint Energy | Centerpoint Energy | 26 | 100 | exact |
| Harris County MUD 457 | Harris County MUD 457 | 545 | 100 | exact |
| SCE | Southern California Edison | 245 | 100 | exact (alias) |
| SoCalGas | So Cal Gas | 5943 | 100 | exact (alias) |
| SDG&E | San Diego Gas & Electric | 4065 | 100 | exact (alias) |
| Cumberland Elec Member | CEMC (Cumberland Electric...) | 2021 | 71 | state_specific |
| Con Edison | Con Edison | 1627 | 100 | exact |
| PG&E | PG&E | 422 | 100 | exact |
| Xyzzy Fake Utility | — | — | — | NO MATCH |

### Integration

ID matching runs automatically in `engine._lookup_with_state_gis()` after candidate selection. The `ProviderResult` dataclass now includes:
- `catalog_id` — matched internal ID
- `catalog_title` — matched catalog title
- `id_match_score` — fuzzy match score (0-100)
- `id_confident` — True if score ≥ 85

Alternatives also get `catalog_id` and `catalog_title` attached.

---

## Part 2: Human Review Output

### Generated Files

| File | Rows | Purpose |
|------|------|---------|
| `mapper_review_queue.xlsx` | 65 | Formatted review queue for mappers |
| `mapper_review_queue.csv` | 65 | Plain CSV version for import |
| `batch_results_full.xlsx` | 300 | All rows + Summary sheet |

### Review Queue Criteria

A row needs review if ANY of:
- `comparison` is MISMATCH or MATCH_ALT
- `needs_review` is True (confidence < 0.80)
- `catalog_id` is None (no ID match)
- `id_confident` is False (low ID match score)

### XLSX Formatting

- **Frozen** header row + first 3 columns (address, state, ZIP)
- **Color coding:**
  - MISMATCH → light red
  - MATCH_ALT → light yellow
  - No catalog ID → light orange
  - Low confidence → light blue
- **Dropdown validation** on "Mapper Decision" column: Correct, Wrong - Use Alternative, Wrong - Manual Entry, Skip
- **Auto-filter** on all columns
- **Sorted** by: MISMATCH first, then state, then utility type

### Review Queue Breakdown (--limit 100)

| Utility | Review Queue | Accuracy | ID Match Rate |
|---------|-------------|----------|---------------|
| Electric | 29 | 84.9% (79/93) | 100/100 (100%) |
| Gas | 36 | 90.7% (39/43) | 99/99 (100%) |
| Water | 0 | N/A | N/A |

### Summary Sheet (batch_results_full.xlsx)

Contains:
- Overall accuracy by utility type
- ID match rate by utility type (total, matched, confident, no match)
- Review queue size by reason × utility type

---

## Part 3: Mapper Feedback Loop

### Script: `import_mapper_corrections.py`

Reads a filled-in `mapper_review_queue.xlsx` (or `.csv`) and imports mapper decisions into `corrections.db`.

### Decision Types

| Decision | Action |
|----------|--------|
| Correct | Skip (engine was right) |
| Wrong - Use Alternative | Insert address correction with alternative provider |
| Wrong - Manual Entry | Insert address correction + ID mapping correction |
| Skip | Skip |

### Database Tables (auto-created in corrections.db)

**`address_corrections`** — provider corrections for future lookups:
- address, lat, lon, zip_code, state, utility_type
- corrected_provider, corrected_catalog_id
- original_provider, original_source
- corrected_by, corrected_at, notes

**`id_mapping_corrections`** — name→ID mapping corrections:
- engine_provider_name, utility_type, correct_catalog_id
- corrected_by, corrected_at

### Feedback Integration

- `corrections.py` now creates these tables on init
- `lookup_by_latlon()` method checks `address_corrections` within ~100m radius
- `ProviderIDMatcher` loads `id_mapping_corrections` as priority-0 overrides
- Mapper corrections feed back as Priority 0 in the lookup chain

### Usage

```bash
# After mappers fill in the review queue:
python import_mapper_corrections.py --input mapper_review_queue_FILLED.xlsx

# Re-run engine — corrections are now active at Priority 0
python batch_validate.py --limit 100 --skip-water
```

---

## Part 4: Batch CSV Updates

### New Columns in `batch_results.csv`

| Column | Description |
|--------|-------------|
| `engine_catalog_id` | Matched internal catalog ID |
| `engine_catalog_title` | Matched catalog title |
| `engine_id_match_score` | Fuzzy match score (0-100) |
| `engine_id_confident` | True if score ≥ 85 |
| `alt_catalog_ids` | Pipe-separated catalog IDs for alternatives |

---

## Files Created

| File | Purpose |
|------|---------|
| `lookup_engine/provider_id_matcher.py` | Fuzzy ID matching with aliases + HIFLD expansion |
| `generate_review_files.py` | Generates XLSX/CSV review files from batch results |
| `import_mapper_corrections.py` | Imports mapper decisions into corrections.db |
| `data/provider_catalog.csv` | Provider catalog (14,345 entries) |

## Files Modified

| File | Changes |
|------|---------|
| `lookup_engine/models.py` | Added `catalog_id`, `catalog_title`, `id_match_score`, `id_confident` to ProviderResult |
| `lookup_engine/engine.py` | Added ProviderIDMatcher init + ID matching after candidate selection |
| `lookup_engine/corrections.py` | Added `address_corrections` + `id_mapping_corrections` tables, `lookup_by_latlon()` |
| `batch_validate.py` | Added 5 new ID columns to CSV output |

---

## Cumulative Accuracy (--limit 100)

| Metric | Value |
|--------|-------|
| **Electric** | **79/86 (91.9%)** |
| **Gas** | **39/42 (92.9%)** |
| **Geocoding** | **100/100 (100%)** |
| **ID match rate** | **199/199 (100%)** |
| **ID confident** | **175/199 (87.9%)** |
| **Review queue** | **65 rows (21.7%)** |
