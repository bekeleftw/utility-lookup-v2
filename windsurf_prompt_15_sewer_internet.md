# Windsurf Prompt 15: Sewer Lookup + Internet (FCC BDC) Integration
## February 6, 2026

Add sewer and internet as utility types. Sewer inherits from water in most cases. Internet queries the existing Railway Postgres database with FCC Broadband Data Collection data, plus imports the additional technology CSV files that aren't loaded yet.

```
## PART 1: SEWER LOOKUP

Sewer is the simplest utility — in ~80% of cases the sewer provider IS the water provider (same municipal department). The exceptions are county sanitary districts and special sewer authorities.

### Approach

1. Check if the water provider also appears in the sewer catalog (UtilityTypeId 6)
2. If yes → return the sewer catalog entry (same entity, different ID)
3. If no → fuzzy search the sewer catalog for the municipality name
4. If still no → return water provider name as sewer provider with lower confidence

### Implementation

Add to `lookup_engine/engine.py`:

```python
def _lookup_sewer(self, lat, lon, state, zip_code, city, water_result):
    """
    Sewer lookup — inherits from water, then checks sewer-specific sources.
    
    Args:
        water_result: The water lookup result (if any) — sewer often = water provider
    """
    candidates = []
    
    # Priority 1: Check if water provider has a sewer catalog entry
    if water_result and water_result.get("provider"):
        sewer_match = self.id_matcher.match(
            water_result["provider"], "sewer", state
        )
        if sewer_match and sewer_match["match_score"] >= 80:
            candidates.append({
                "provider": sewer_match["title"],
                "confidence": min(water_result.get("confidence", 0.80), 0.88),
                "source": "water_inheritance",
                "catalog_id": sewer_match["id"],
            })
    
    # Priority 2: City/municipality match against sewer catalog
    if city:
        city_variants = [
            f"City of {city}",
            f"{city} Sewer",
            f"{city} Utilities",
            f"{city} Public Works",
            city,
        ]
        for variant in city_variants:
            match = self.id_matcher.match(variant, "sewer", state)
            if match and match["match_score"] >= 75:
                candidates.append({
                    "provider": match["title"],
                    "confidence": 0.70,
                    "source": "sewer_city_match",
                    "catalog_id": match["id"],
                })
                break  # Take first good city match
    
    # Priority 3: County sanitary district match
    if hasattr(self, 'county') and self.county:
        county_match = self.id_matcher.match(
            f"{self.county} County Sanitary", "sewer", state
        )
        if county_match and county_match["match_score"] >= 70:
            candidates.append({
                "provider": county_match["title"],
                "confidence": 0.65,
                "source": "sewer_county_match",
                "catalog_id": county_match["id"],
            })
    
    # Priority 4: Fall back to water provider name (no catalog ID)
    if not candidates and water_result and water_result.get("provider"):
        candidates.append({
            "provider": water_result["provider"],
            "confidence": 0.50,
            "source": "water_fallback_no_sewer_id",
            "catalog_id": None,
        })
    
    # Deduplicate and return
    candidates = self._deduplicate_and_boost(candidates)
    candidates.sort(key=lambda c: c["confidence"], reverse=True)
    
    if not candidates:
        return None
    
    primary = candidates[0]
    return {
        "provider": primary["provider"],
        "confidence": primary["confidence"],
        "source": primary["source"],
        "catalog_id": primary.get("catalog_id"),
        "needs_review": primary["confidence"] < 0.80,
        "alternatives": candidates[1:5],
    }
```

### Update ProviderIDMatcher

The TYPE_MAP in `provider_id_matcher.py` needs sewer added:

```python
TYPE_MAP = {
    "electric": "2",
    "gas": "4",
    "water": "3",
    "sewer": "6",
    "trash": "5",  # for future use
    "internet": "8",  # if we add internet to catalog later
}
```

### Update batch_validate.py

- Add sewer to the utility types processed
- Sewer comparison logic: same as water (MATCH, MISMATCH, etc.)
- Note: the 91K tenant CSV may not have sewer data for every address — treat missing tenant sewer as BOTH_EMPTY or ENGINE_ONLY
- Check what column name the tenant CSV uses for sewer (might be "sewer_provider" or similar)

### Update batch output

Add sewer columns to batch_results.csv and the review spreadsheets.


## PART 2: INTERNET LOOKUP (FCC BDC + POSTGRES)

### Existing Setup

Railway Postgres database:
- Project: prolific-presence / production
- Table: `internet_providers`
- Schema:
  ```
  block_geoid VARCHAR (15-digit Census block GEOID)
  providers JSONB (array of provider objects)
  ```
- Provider object format:
  ```json
  {"up": 250, "down": 2000, "name": "Xfinity", "tech": "40", "low_lat": 1}
  ```
- Tech codes (FCC standard):
  - 10 = Copper/DSL
  - 40 = Cable (DOCSIS 3.0/3.1)
  - 50 = Fiber (FTTH)
  - 60 = Satellite (GSO)
  - 61 = Satellite (NGSO, e.g., Starlink)
  - 70 = Licensed Fixed Wireless
  - 71 = Unlicensed Fixed Wireless
  - 72 = Licensed-by-Rule Fixed Wireless (CBRS)
  - 0 = Other

### Step 1: Import Missing CSV Files into Postgres

The CSVs in `/CascadeProjects/bdc_downloads/` need to be loaded. These are the technology types NOT currently in Postgres (Cable and Fiber are missing — they're the most important).

First, check what's already loaded:
```sql
SELECT DISTINCT jsonb_array_elements(providers)->>'tech' as tech, COUNT(*) 
FROM internet_providers 
GROUP BY tech;
```

Then download the missing technology files from the FCC BDC website (https://broadbandmap.fcc.gov/data-download/fixed) for ALL states:
- Cable: `bdc_XX_Cable_fixed_broadband_*.csv`
- Fiber: `bdc_XX_Fiber_fixed_broadband_*.csv`

FCC BDC CSV columns (standard format):
```
frn, provider_id, brand_name, location_id, technology, max_advertised_download_speed, 
max_advertised_upload_speed, low_latency, business_residential_code, state_usps, block_geoid
```

Build an import script: `import_bdc_to_postgres.py`

```python
"""
Import FCC BDC CSV files into the internet_providers Postgres table.

Usage:
  python import_bdc_to_postgres.py --csv-dir /CascadeProjects/bdc_downloads/ --db-url postgresql://user:pass@host:port/db

What it does:
1. Reads all CSV files in the directory
2. Groups providers by block_geoid
3. For each block_geoid:
   - If row exists: merge new providers into existing JSON array (deduplicate by name+tech)
   - If row doesn't exist: insert new row
4. Commits in batches of 10,000 for performance
"""

import csv
import json
import os
import sys
import psycopg2
from collections import defaultdict

def import_csvs(csv_dir, db_url):
    conn = psycopg2.connect(db_url)
    
    # Process each CSV file
    csv_files = sorted([f for f in os.listdir(csv_dir) if f.endswith('.csv')])
    
    for csv_file in csv_files:
        print(f"Processing {csv_file}...")
        filepath = os.path.join(csv_dir, csv_file)
        
        # Group by block_geoid
        blocks = defaultdict(list)
        
        with open(filepath, encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Skip business-only entries (code 'B')
                if row.get('business_residential_code') == 'B':
                    continue
                
                block_geoid = row['block_geoid']
                provider = {
                    "name": row['brand_name'],
                    "tech": row['technology'],
                    "down": float(row['max_advertised_download_speed']),
                    "up": float(row['max_advertised_upload_speed']),
                    "low_lat": int(row.get('low_latency', 0)),
                }
                blocks[block_geoid].append(provider)
        
        # Upsert into Postgres
        cursor = conn.cursor()
        batch_count = 0
        
        for block_geoid, providers in blocks.items():
            # Deduplicate providers (same name + tech = keep highest speed)
            seen = {}
            for p in providers:
                key = f"{p['name']}|{p['tech']}"
                if key not in seen or p['down'] > seen[key]['down']:
                    seen[key] = p
            deduped = list(seen.values())
            
            cursor.execute("""
                INSERT INTO internet_providers (block_geoid, providers)
                VALUES (%s, %s::jsonb)
                ON CONFLICT (block_geoid) DO UPDATE
                SET providers = (
                    SELECT jsonb_agg(DISTINCT elem)
                    FROM (
                        SELECT jsonb_array_elements(internet_providers.providers) AS elem
                        UNION ALL
                        SELECT jsonb_array_elements(%s::jsonb) AS elem
                    ) combined
                )
            """, (block_geoid, json.dumps(deduped), json.dumps(deduped)))
            
            batch_count += 1
            if batch_count % 10000 == 0:
                conn.commit()
                print(f"  Committed {batch_count} blocks...")
        
        conn.commit()
        print(f"  Done: {batch_count} blocks from {csv_file}")
    
    conn.close()
```

NOTE: The deduplication in the ON CONFLICT upsert may need refinement — the DISTINCT on jsonb may not work perfectly for deduping provider objects with the same name+tech. Test with a small CSV first. A safer approach:

```python
# Fetch existing, merge in Python, update
cursor.execute("SELECT providers FROM internet_providers WHERE block_geoid = %s", (block_geoid,))
existing = cursor.fetchone()
if existing:
    existing_providers = json.loads(existing[0]) if isinstance(existing[0], str) else existing[0]
    # Merge: existing + new, dedupe by name+tech
    merged = merge_providers(existing_providers, deduped)
    cursor.execute("UPDATE internet_providers SET providers = %s::jsonb WHERE block_geoid = %s",
                   (json.dumps(merged), block_geoid))
else:
    cursor.execute("INSERT INTO internet_providers (block_geoid, providers) VALUES (%s, %s::jsonb)",
                   (block_geoid, json.dumps(deduped)))
```

### Step 2: Get Census Block GEOID from Geocoder

The internet lookup needs the 15-digit Census block GEOID. The Census geocoder returns this info.

Check if the Census geocoder response includes FIPS components. The Census batch geocoder returns:
```
state_fips (2 digits) + county_fips (3 digits) + tract (6 digits) + block (4 digits) = 15-digit GEOID
```

If the geocoder already returns these, concatenate them. If not, use the Census TIGERweb API as a fallback:

```
https://geocoding.geo.census.gov/geocoder/geographies/coordinates?x={lon}&y={lat}&benchmark=Public_AR_Current&vintage=Current_Current&format=json
```

This returns `GEOID` directly from coordinates.

For Google geocoder fallback: Google doesn't return Census blocks. When Google is used, we need a separate call to TIGERweb to get the block GEOID from the lat/lon.

Add to geocoder.py:
```python
def get_census_block_geoid(self, lat: float, lon: float) -> Optional[str]:
    """Get 15-digit Census block GEOID from coordinates via TIGERweb."""
    url = "https://geocoding.geo.census.gov/geocoder/geographies/coordinates"
    params = {
        "x": lon,
        "y": lat,
        "benchmark": "Public_AR_Current",
        "vintage": "Current_Current",
        "format": "json",
    }
    resp = requests.get(url, params=params, timeout=10)
    data = resp.json()
    
    geographies = data.get("result", {}).get("geographies", {})
    blocks = geographies.get("2020 Census Blocks", [])
    if blocks:
        return blocks[0].get("GEOID")
    
    # Fallback: try 2010 blocks
    blocks_2010 = geographies.get("Census Blocks", [])
    if blocks_2010:
        return blocks_2010[0].get("GEOID")
    
    return None
```

### Step 3: Internet Lookup Module

Create: `lookup_engine/internet_lookup.py`

```python
import json
import psycopg2
from typing import Optional, List, Dict

# FCC technology code labels
TECH_LABELS = {
    "10": "DSL",
    "40": "Cable",
    "50": "Fiber",
    "60": "Satellite (GSO)",
    "61": "Satellite (NGSO)",
    "70": "Fixed Wireless (Licensed)",
    "71": "Fixed Wireless (Unlicensed)",
    "72": "Fixed Wireless (CBRS)",
    "0": "Other",
}

class InternetLookup:
    """Query FCC BDC data in Postgres for internet providers at a Census block."""
    
    def __init__(self, db_url: str):
        self.db_url = db_url
        self.conn = None
    
    def _get_conn(self):
        if self.conn is None or self.conn.closed:
            self.conn = psycopg2.connect(self.db_url)
        return self.conn
    
    def lookup(self, block_geoid: str) -> Optional[Dict]:
        """
        Look up internet providers for a Census block.
        
        Returns: {
            "providers": [
                {
                    "name": "Xfinity",
                    "technology": "Cable",
                    "tech_code": "40",
                    "max_down": 2000,
                    "max_up": 250,
                    "low_latency": True,
                },
                ...
            ],
            "provider_count": 4,
            "has_fiber": True,
            "has_cable": True,
            "max_download_speed": 5000,
            "source": "fcc_bdc",
            "confidence": 0.95,
        }
        """
        if not block_geoid:
            return None
        
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute(
            "SELECT providers FROM internet_providers WHERE block_geoid = %s",
            (block_geoid,)
        )
        row = cursor.fetchone()
        
        if not row:
            return None
        
        raw_providers = row[0] if isinstance(row[0], list) else json.loads(row[0])
        
        # Process and enrich
        providers = []
        for p in raw_providers:
            tech_code = str(p.get("tech", "0"))
            providers.append({
                "name": p["name"],
                "technology": TECH_LABELS.get(tech_code, f"Unknown ({tech_code})"),
                "tech_code": tech_code,
                "max_down": p.get("down", 0),
                "max_up": p.get("up", 0),
                "low_latency": bool(p.get("low_lat", 0)),
            })
        
        # Sort: Fiber first, then Cable, then by download speed
        tech_priority = {"50": 0, "40": 1, "10": 2, "70": 3, "71": 4, "72": 5, "60": 6, "61": 7, "0": 8}
        providers.sort(key=lambda p: (tech_priority.get(p["tech_code"], 99), -p["max_down"]))
        
        # Deduplicate: same provider with multiple tech types = keep both (different services)
        # Same provider with same tech = keep highest speed (already handled in import)
        
        return {
            "providers": providers,
            "provider_count": len(set(p["name"] for p in providers)),
            "has_fiber": any(p["tech_code"] == "50" for p in providers),
            "has_cable": any(p["tech_code"] == "40" for p in providers),
            "max_download_speed": max((p["max_down"] for p in providers), default=0),
            "source": "fcc_bdc",
            "confidence": 0.95,  # FCC data is authoritative
        }
    
    def close(self):
        if self.conn and not self.conn.closed:
            self.conn.close()
```

### Step 4: Integration into Engine

```python
# In engine.py __init__:
db_url = os.environ.get("DATABASE_URL")  # Railway Postgres connection string
if db_url:
    from .internet_lookup import InternetLookup
    self.internet = InternetLookup(db_url)
else:
    self.internet = None

# In the main lookup method:
def lookup(self, address: str) -> Dict:
    # ... existing geocoding ...
    
    # Get Census block GEOID for internet lookup
    block_geoid = None
    if self.internet:
        # Try to get from Census geocoder response first
        block_geoid = geo_result.get("block_geoid")
        if not block_geoid and geo_result.get("lat") and geo_result.get("lon"):
            block_geoid = self.geocoder.get_census_block_geoid(
                geo_result["lat"], geo_result["lon"]
            )
    
    # ... existing electric/gas/water/sewer lookups ...
    
    # Internet lookup
    internet_result = None
    if self.internet and block_geoid:
        internet_result = self.internet.lookup(block_geoid)
    
    return {
        "electric": electric_result,
        "gas": gas_result,
        "water": water_result,
        "sewer": sewer_result,
        "internet": internet_result,
    }
```

### Step 5: Internet in Batch Output

Internet is different from other utilities — it returns MULTIPLE providers, not one. The batch output format:

In `batch_results.csv`, add for internet:
- `internet_providers` — pipe-separated: "Xfinity (Cable, 2000/250) | Frontier (Fiber, 5000/5000) | Frontier (DSL, 10/1)"
- `internet_provider_count` — number of unique ISPs
- `internet_has_fiber` — True/False
- `internet_has_cable` — True/False
- `internet_max_download` — highest available download speed (Mbps)
- `internet_block_geoid` — the Census block used for lookup
- `internet_source` — "fcc_bdc" or empty

For the review spreadsheet, add an "Internet" sheet with:
| Address | State | ZIP | Block GEOID | Provider Count | Has Fiber | Has Cable | Max Download | All Providers |

Internet doesn't need accuracy comparison against tenant data (tenants choose one ISP, but multiple are available). It's informational.

### Step 6: Environment Setup

The engine needs the Postgres connection string. Add to `.env`:
```
DATABASE_URL=postgresql://user:password@host:port/railway
```

For batch runs, read from environment:
```python
import os
from dotenv import load_dotenv
load_dotenv()
db_url = os.environ.get("DATABASE_URL")
```

Install psycopg2: `pip install psycopg2-binary`

## PART 3: IMPORT MISSING BDC FILES

Before the batch run, the Cable and Fiber CSVs need to be downloaded and imported.

1. Download from https://broadbandmap.fcc.gov/data-download/fixed
   - Select "Fixed Broadband" → Technology: Cable → All States → Download
   - Select "Fixed Broadband" → Technology: Fiber → All States → Download
   - Save to `/CascadeProjects/bdc_downloads/`

2. Run import script:
```bash
python import_bdc_to_postgres.py \
  --csv-dir /CascadeProjects/bdc_downloads/ \
  --db-url $DATABASE_URL
```

3. Verify:
```sql
-- Check tech distribution after import
SELECT 
  jsonb_array_elements(providers)->>'tech' as tech,
  COUNT(*) 
FROM internet_providers 
GROUP BY 1 
ORDER BY 2 DESC;

-- Should now see tech codes 40 (Cable) and 50 (Fiber) with large counts
```

## VERIFICATION

1. Test sewer lookup:
   - Address with known municipal water → should return same entity as sewer
   - Address with county sanitary district → should find it in sewer catalog
   - Address with no water result → sewer should return None gracefully

2. Test internet lookup:
   - Pick a block_geoid from the Postgres table
   - Verify the lookup returns all providers with correct tech labels
   - Verify sorting (fiber first, then cable, then by speed)

3. Test Census block GEOID extraction:
   - Run geocoder on a known address
   - Verify 15-digit GEOID is returned
   - Verify it matches when queried against Postgres

4. Run --limit 100 with all 5 utility types:
   - Electric, gas, water, sewer, internet
   - Verify sewer results appear
   - Verify internet results appear (requires DATABASE_URL set)
   - Check that adding sewer/internet didn't break electric/gas/water accuracy

5. Run full 91K batch with all 5 utility types
```
