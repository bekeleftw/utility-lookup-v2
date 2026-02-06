# Windsurf Prompt 14: Provider ID Matching, Human Review Output & Mapper Feedback
## February 6, 2026

The engine returns provider NAMES but the internal tool needs provider IDs from our catalog. This prompt adds ID matching, produces a human-reviewable batch output for mappers, and builds the feedback loop for mapper corrections.

```
## CONTEXT

We have a provider catalog CSV (attached as `utility_providers_for_mark_s_automation_tool_api_2026-01-30T13_51_00_259761324-06_00.csv`) with ~14,347 entries:

Columns: ID, UtilityTypeId, Title, URL, Phone, Source, Type

UtilityTypeId mapping:
- 2 = Electric (1,669 entries)
- 3 = Water (8,368 entries)  
- 4 = Gas (833 entries)
- 5 = Trash (1,768 entries)
- 6 = Sewer (1,706 entries)

IMPORTANT naming issues in the catalog:
- Same provider has multiple entries: "Duke Energy" (ID 72), "Duke-Energy" (ID 15401), "Duke Energy Corporation" (ID 13746)
- State suffixes vary: "Centerpoint Energy" (ID 26), "Centerpoint Energy-MN" (ID 16044), "Centerpoint Energy - TX" (ID 15609)
- Format varies: "City of Celina - OH", "CITY OF TAUNTON", "City of Westerville (OH)"
- Some entries are REPs (TX retail providers): "Frontier Utilities", "BKV Energy", "Good Charlie"
- There are a couple malformed rows (phone numbers in UtilityTypeId column) — skip those on load

Copy this CSV to: `utility-lookup-v2/data/provider_catalog.csv`

## PART 1: Provider ID Matching Module

Create: `lookup_engine/provider_id_matcher.py`

This module loads the provider catalog and fuzzy-matches engine output names to catalog entries to get the internal ID.

```python
import csv
import re
from rapidfuzz import fuzz, process
from typing import Optional, List, Dict

class ProviderIDMatcher:
    """Matches engine provider names to internal catalog IDs."""
    
    # Map engine utility types to catalog UtilityTypeId
    TYPE_MAP = {
        "electric": "2",
        "gas": "4",
        "water": "3",
    }
    
    def __init__(self, catalog_path: str):
        self.catalog = self._load_catalog(catalog_path)
        self._build_index()
    
    def _load_catalog(self, path: str) -> List[Dict]:
        """Load and clean the provider catalog CSV."""
        entries = []
        with open(path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Skip malformed rows
                try:
                    int(row["ID"])
                    if row["UtilityTypeId"] not in ("2", "3", "4", "5", "6", "7"):
                        continue
                except (ValueError, KeyError):
                    continue
                
                entries.append({
                    "id": int(row["ID"]),
                    "type_id": row["UtilityTypeId"],
                    "title": row["Title"].strip(),
                    "url": row.get("URL", ""),
                    "phone": row.get("Phone", ""),
                    "source": row.get("Source", ""),
                    "normalized": self._normalize_title(row["Title"].strip()),
                })
        return entries
    
    def _normalize_title(self, title: str) -> str:
        """Normalize a catalog title for matching."""
        t = title.lower().strip()
        # Remove state suffixes: "- TX", "- OH", "(OH)", " TX", " NC"
        t = re.sub(r'\s*[-–]\s*[A-Z]{2}\s*$', '', t, flags=re.IGNORECASE)
        t = re.sub(r'\s*\([A-Z]{2}\)\s*$', '', t, flags=re.IGNORECASE)
        # Remove common suffixes
        for suffix in [' corporation', ' corp', ' inc', ' llc', ' co-op', ' co op']:
            t = t.replace(suffix, '')
        # Normalize whitespace and punctuation
        t = re.sub(r'[^\w\s]', ' ', t)
        t = re.sub(r'\s+', ' ', t).strip()
        return t
    
    def _build_index(self):
        """Build lookup indexes for fast matching."""
        self.by_type = {}  # type_id -> [(normalized, entry), ...]
        for entry in self.catalog:
            tid = entry["type_id"]
            if tid not in self.by_type:
                self.by_type[tid] = []
            self.by_type[tid].append((entry["normalized"], entry))
    
    def match(self, provider_name: str, utility_type: str, state: str = None) -> Optional[Dict]:
        """
        Match a provider name to a catalog entry.
        
        Returns: {
            "id": 72,
            "title": "Duke Energy",
            "match_score": 95,
            "match_method": "exact" | "fuzzy" | "state_specific",
            "confident": True | False
        } or None
        """
        type_id = self.TYPE_MAP.get(utility_type)
        if not type_id or type_id not in self.by_type:
            return None
        
        candidates = self.by_type[type_id]
        normalized_input = self._normalize_title(provider_name)
        
        # Step 1: Exact match on normalized name
        for norm, entry in candidates:
            if norm == normalized_input:
                return self._result(entry, 100, "exact")
        
        # Step 2: State-specific match (prefer entries with matching state suffix)
        if state:
            state_matches = []
            for norm, entry in candidates:
                title_upper = entry["title"].upper()
                if state.upper() in title_upper:
                    score = fuzz.token_sort_ratio(normalized_input, norm)
                    if score >= 70:
                        state_matches.append((score, entry))
            if state_matches:
                state_matches.sort(key=lambda x: x[0], reverse=True)
                best_score, best_entry = state_matches[0]
                return self._result(best_entry, best_score, "state_specific")
        
        # Step 3: Fuzzy match across all entries of this type
        choices = [(norm, entry) for norm, entry in candidates]
        if not choices:
            return None
        
        # Use rapidfuzz for efficient fuzzy matching
        names = [c[0] for c in choices]
        result = process.extractOne(
            normalized_input, names, 
            scorer=fuzz.token_sort_ratio,
            score_cutoff=72
        )
        
        if result:
            matched_name, score, idx = result
            entry = choices[idx][1]
            return self._result(entry, score, "fuzzy")
        
        return None
    
    def _result(self, entry, score, method):
        return {
            "id": entry["id"],
            "title": entry["title"],
            "url": entry["url"],
            "phone": entry["phone"],
            "match_score": score,
            "match_method": method,
            "confident": score >= 85,
        }

    def match_all_candidates(self, candidates: list, utility_type: str, state: str = None) -> List[Dict]:
        """Match all candidates, return list with IDs attached."""
        results = []
        for c in candidates:
            match = self.match(c["provider"], utility_type, state)
            result = {**c}
            if match:
                result["catalog_id"] = match["id"]
                result["catalog_title"] = match["title"]
                result["catalog_url"] = match["url"]
                result["catalog_phone"] = match["phone"]
                result["id_match_score"] = match["match_score"]
                result["id_match_method"] = match["match_method"]
                result["id_confident"] = match["confident"]
            else:
                result["catalog_id"] = None
                result["catalog_title"] = None
                result["catalog_url"] = None
                result["catalog_phone"] = None
                result["id_match_score"] = 0
                result["id_match_method"] = "none"
                result["id_confident"] = False
            results.append(result)
        return results
```

Install rapidfuzz if not present: `pip install rapidfuzz`

### Integration into engine.py

After the engine builds candidates and picks a primary, run ID matching:

```python
# In engine.py __init__:
self.id_matcher = ProviderIDMatcher("data/provider_catalog.csv")

# After building candidates:
primary_match = self.id_matcher.match(primary["provider"], utility_type, state)
if primary_match:
    primary["catalog_id"] = primary_match["id"]
    primary["catalog_title"] = primary_match["title"]
    primary["id_match_score"] = primary_match["match_score"]
    primary["id_confident"] = primary_match["confident"]
```

## PART 2: Human Review Batch Output

After the full batch run, generate TWO review files:

### File 1: `mapper_review_queue.xlsx` (only rows needing human review)

A row needs human review if ANY of these are true:
- `needs_review` is True (provider confidence < 0.80)
- `catalog_id` is None (no ID match found)
- `id_confident` is False (low ID match score)
- comparison is MISMATCH or MATCH_ALT

Columns for the review spreadsheet:

| Address | State | ZIP | Utility Type | Engine Provider | Engine Provider ID | Engine Confidence | Engine Source | ID Match Score | Tenant Provider | Tenant Provider ID (if known) | Match Status | Alternatives (all with IDs) | Review Reason | Mapper Decision | Mapper Corrected Provider | Mapper Corrected ID | Mapper Notes |

The last 4 columns are EMPTY — these are for the mapper to fill in manually.

Formatting:
- Freeze header row + first 3 columns (address, state, zip)
- Auto-width columns
- Color coding:
  - MISMATCH rows: light red (#FFE0E0)
  - MATCH_ALT rows: light yellow (#FFFDE0)
  - No catalog ID: light orange (#FFE8D0)
  - Low confidence: light blue (#E0E8FF)
- Add dropdown validation on "Mapper Decision" column: "Correct", "Wrong - Use Alternative", "Wrong - Manual Entry", "Skip"
- Filter on all columns
- Sort by: Review Reason (MISMATCH first), then State, then Utility Type

### File 2: `batch_results_full.xlsx` (all rows, for reference)

Same columns as above but ALL rows, including auto_accept ones. This is the complete picture for proving accuracy.

Add a Summary sheet with:
```
### Overall Accuracy
| Utility | Scoreable | Correct | Accuracy | Coverage-Adjusted Accuracy |
|---------|-----------|---------|----------|---------------------------|

### Accuracy by Source
| Source | Count | Correct | Accuracy |
|--------|-------|---------|----------|

### ID Match Rate
| Utility | Total | ID Matched | ID Confident | No ID Match |
|---------|-------|------------|--------------|-------------|

### Review Queue Size
| Review Reason | Electric | Gas | Water |
|---------------|----------|-----|-------|
| Low confidence | X | X | X |
| No catalog ID | X | X | X |
| MISMATCH | X | X | X |
| MATCH_ALT | X | X | X |
| TOTAL unique rows | X | X | X |
```

### File 3: `mapper_review_queue.csv` (same as xlsx but CSV for easy import)

Plain CSV version of the review queue for importing into other tools.

## PART 3: Mapper Feedback Loop

When a mapper reviews a row and corrects it, that correction needs to feed back into the engine. There are TWO types of corrections:

### Type 1: Provider Correction (for future lookups)
"This address should be served by Provider X, not Provider Y"

Store in `data/corrections.db` (SQLite, already exists):

```sql
CREATE TABLE IF NOT EXISTS address_corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    address TEXT NOT NULL,
    lat REAL,
    lon REAL,
    zip_code TEXT,
    state TEXT,
    utility_type TEXT NOT NULL,  -- electric, gas, water
    corrected_provider TEXT NOT NULL,
    corrected_catalog_id INTEGER,
    original_provider TEXT,
    original_source TEXT,
    corrected_by TEXT DEFAULT 'mapper',
    corrected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_corrections_zip ON address_corrections(zip_code, utility_type);
CREATE INDEX IF NOT EXISTS idx_corrections_state ON address_corrections(state, utility_type);
CREATE INDEX IF NOT EXISTS idx_corrections_latlon ON address_corrections(lat, lon);
```

### Type 2: ID Mapping Correction (for the catalog matcher)
"This engine provider name should map to catalog ID X"

Store in `data/corrections.db`:

```sql
CREATE TABLE IF NOT EXISTS id_mapping_corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    engine_provider_name TEXT NOT NULL,
    utility_type TEXT NOT NULL,
    correct_catalog_id INTEGER NOT NULL,
    corrected_by TEXT DEFAULT 'mapper',
    corrected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_id_corrections ON id_mapping_corrections(engine_provider_name, utility_type);
```

### Import Script: `import_mapper_corrections.py`

Reads the filled-in `mapper_review_queue.xlsx` (or CSV) and imports mapper decisions into corrections.db:

```bash
python import_mapper_corrections.py --input mapper_review_queue_FILLED.xlsx
```

Logic:
1. Read the file
2. For each row where "Mapper Decision" is not empty:
   - If "Correct" → skip (engine was right, no correction needed)
   - If "Wrong - Use Alternative" → 
     - Insert address_corrections with the alternative provider
     - If alternative has a catalog_id, use it
   - If "Wrong - Manual Entry" →
     - Insert address_corrections with "Mapper Corrected Provider" and "Mapper Corrected ID"
     - Insert id_mapping_corrections if this is a new name→ID mapping
   - If "Skip" → skip
3. Print summary: "Imported X corrections (Y address, Z ID mappings)"

### Update corrections.py to use the new tables

The existing `corrections.py` loads from JSON files. Update it to ALSO check the SQLite tables:

```python
def lookup_by_address(self, address, lat, lon, utility_type):
    # Check SQLite first (mapper corrections)
    # Then check ZIP JSON corrections
    # Mapper corrections win because they're more specific
```

For lat/lon matching, use a small radius (0.001 degrees ≈ ~111 meters) to catch nearby addresses:

```python
def _check_nearby_corrections(self, lat, lon, utility_type):
    """Find corrections within ~100m of this point."""
    cursor = self.db.execute("""
        SELECT corrected_provider, corrected_catalog_id
        FROM address_corrections
        WHERE utility_type = ?
        AND ABS(lat - ?) < 0.001
        AND ABS(lon - ?) < 0.001
        ORDER BY corrected_at DESC
        LIMIT 1
    """, (utility_type, lat, lon))
    row = cursor.fetchone()
    return row if row else None
```

## PART 4: Update batch_validate.py

Integrate the ID matcher into the batch validation:

1. Load the provider catalog at startup
2. After engine lookup, match provider to catalog ID  
3. Add columns to batch_results.csv:
   - `engine_catalog_id` — matched catalog ID (or empty)
   - `engine_catalog_title` — matched catalog title
   - `engine_id_match_score` — fuzzy match score (0-100)
   - `engine_id_confident` — True/False
   - `alt_catalog_ids` — pipe-separated catalog IDs for alternatives

4. Add to BATCH_VALIDATION_REPORT.md:
   - ID match rate by utility type
   - Top unmatched providers (engine returned a name that doesn't exist in catalog)
   - Accuracy broken down by id_confident vs not

5. Generate the review files:
   - `mapper_review_queue.xlsx`
   - `batch_results_full.xlsx`
   - `mapper_review_queue.csv`

## VERIFICATION

1. Test ID matching with known providers:
   - "Duke Energy Carolinas" → should match "Duke Energy" (ID 72) or state-specific variant
   - "Oncor" → should match "Oncor Electric-TX" (ID 16065)
   - "Atmos Energy" → should match "Atmos Energy" (ID 29)
   - "CenterPoint Energy" → should match CenterPoint (ID 26 or state-specific)
   - "Harris County MUD 457" → should match exact (ID 545)
   - Some nonsense name → should return None

2. Test mapper feedback:
   - Insert a test correction via import script
   - Run engine on that address → should return the corrected provider at priority 0

3. Run --limit 100:
   - Check that catalog_id is populated for most results
   - Check that mapper_review_queue.xlsx is generated with correct filtering
   - Verify the Summary sheet has accurate counts

4. Run full 91K batch:
   - Generate all output files
   - Report ID match rates
   - Report review queue size
```
