# Windsurf Prompt 9.0: Geocoder Upgrades (Pre-Batch)
## February 6, 2026

```
Two geocoder upgrades needed before the 87K batch validation run.

## Upgrade 1: Census Batch Geocoding

The current CensusGeocoder uses the single-address endpoint:
  https://geocoding.geo.census.gov/geocoder/geographies/onelineaddress

For batch processing, switch to the batch endpoint:
  https://geocoding.geo.census.gov/geocoder/geographies/addressbatch

### How the batch endpoint works:

- POST request with a CSV file upload
- Max 10,000 addresses per request
- Each row in the CSV: unique_id, street, city, state, zip
- OR each row can be: unique_id, full_address (one-line format)
- Returns a CSV with: unique_id, input_address, match_status, match_type, matched_address, lon_lat, tiger_line_id, side

Example request:
```python
import requests
import csv
import io

url = "https://geocoding.geo.census.gov/geocoder/geographies/addressbatch"

# Build CSV in memory
buffer = io.StringIO()
writer = csv.writer(buffer)
for idx, address in enumerate(addresses):
    writer.writerow([idx, address])  # unique_id, full_address

files = {"addressFile": ("addresses.csv", buffer.getvalue(), "text/csv")}
data = {
    "benchmark": "Public_AR_Current",
    "vintage": "Current_Current"
}

response = requests.post(url, files=files, data=data, timeout=300)
```

Example response (CSV text):
```
"0","123 Main St, Dallas TX 75201","Match","Exact","123 MAIN ST, DALLAS, TX, 75201","-96.797,32.776","12345","L"
"1","456 Oak Ave, Houston TX 77002","Match","Exact","456 OAK AVE, HOUSTON, TX, 77002","-95.369,29.760","67890","R"
"2","999 Fake Blvd, Nowhere XX 00000","No_Match"
```

### Implementation:

Add a `geocode_batch()` method to CensusGeocoder:

```python
def geocode_batch(self, addresses: list[tuple[str, str]]) -> dict[str, tuple[float, float] | None]:
    """
    Geocode up to 10,000 addresses in one request.
    
    Args:
        addresses: list of (unique_id, full_address) tuples
        
    Returns:
        dict mapping unique_id -> (lat, lon) or None if no match
    """
```

Key details:
- Split input into chunks of 10,000
- Parse the response CSV — the lon_lat field is "lon,lat" (NOTE: longitude first!)
- Match status is "Match" or "No_Match" — only extract coords for "Match"
- The batch endpoint is slower per-request (~30-60 seconds for 10K addresses) but massively faster overall
- Add a 5-minute timeout per batch request
- Add retry logic (up to 3 retries with exponential backoff) — the Census server occasionally times out under load
- Log progress: "Batch geocoding: {start}-{end} of {total}..."

Also update batch_validate.py (from Prompt 9) to use geocode_batch() instead of looping single calls:

1. Read all addresses from CSV up front
2. Filter out addresses already in the SQLite cache
3. Send uncached addresses through geocode_batch() in 10K chunks
4. Store all results in the cache
5. Then run point-in-polygon + scoring for each address using cached coords

This changes the batch flow from:
  for each address: geocode → spatial → score → write (serial, 12+ hours)
to:
  batch geocode all addresses → for each address: cache lookup → spatial → score → write (~30 min)

## Upgrade 2: Census → Google Fallback Chain

When Census returns No_Match for an address, try Google before giving up.

Add a ChainedGeocoder class:

```python
class ChainedGeocoder(Geocoder):
    def __init__(self, primary: Geocoder, fallback: Geocoder):
        self.primary = primary
        self.fallback = fallback
        self.fallback_count = 0
    
    def geocode(self, address: str) -> GeocodedAddress | None:
        result = self.primary.geocode(address)
        if result is not None:
            return result
        # Primary failed, try fallback
        self.fallback_count += 1
        return self.fallback.geocode(address)
```

For batch mode, the flow is:
1. Send all addresses through Census batch
2. Collect the No_Match addresses
3. Send No_Match addresses through Google (single calls, since Google doesn't have a free batch endpoint)
4. Log: "Census matched X/Y addresses. Sending Z to Google fallback."

This should only trigger for ~2-5% of addresses (newer construction, non-standard formats, PO boxes). At 2-5K Google calls, that's $10-25 — acceptable.

Update the factory function:

```python
def create_geocoder(geocoder_type: str = "census", google_api_key: str = "") -> Geocoder:
    if geocoder_type == "chained" and google_api_key:
        return ChainedGeocoder(CensusGeocoder(), GoogleGeocoder(google_api_key))
    elif geocoder_type == "google" and google_api_key:
        return GoogleGeocoder(google_api_key)
    return CensusGeocoder()
```

Add a --geocoder flag to batch_validate.py:
  --geocoder census        (default, Census only, free)
  --geocoder chained       (Census + Google fallback, requires GOOGLE_API_KEY env var)
  --geocoder google        (Google only, expensive, not recommended for batch)

## Verification

1. Test batch geocoding with 10 known addresses — verify coords match single-call results
2. Test a deliberately bad address through the chained geocoder — verify Census returns None, Google is tried
3. Run batch_validate.py --limit 100 --geocoder census — verify it uses batch endpoint and completes in seconds, not minutes
4. Print geocoding stats: Census hit rate, Google fallback count, total failures
```
