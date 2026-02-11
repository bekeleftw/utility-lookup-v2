# Utility Provider Lookup API — Quick Start

## 1. Get Your API Key

Contact the API administrator to receive your API key.

## 2. Test the Connection

```bash
# Health check (no auth required)
curl https://your-app.up.railway.app/health
```

Expected: `{"status":"ok","engine_loaded":true,...}`

If `engine_loaded` is `false`, the server just started — wait ~60 seconds and retry.

## 3. Look Up an Address

### curl
```bash
curl -s "https://your-app.up.railway.app/lookup?address=10812+Watchful+Fox+Drive,+Austin,+TX+78748" \
  -H "X-API-Key: YOUR_API_KEY"
```

### JavaScript
```javascript
const resp = await fetch(
  `https://your-app.up.railway.app/lookup?address=${encodeURIComponent(address)}`,
  { headers: { "X-API-Key": "YOUR_API_KEY" } }
);
const data = await resp.json();
console.log(data.electric.provider_name); // "Austin Energy"
console.log(data.internet.has_fiber);     // true
```

### Python
```python
import requests
r = requests.get(
    "https://your-app.up.railway.app/lookup",
    params={"address": "10812 Watchful Fox Drive, Austin, TX 78748"},
    headers={"X-API-Key": "YOUR_API_KEY"},
)
data = r.json()
print(data["electric"]["provider_name"])  # Austin Energy
print(data["internet"]["providers"][0])   # Google Fiber, 8Gbps Fiber
```

### Google Apps Script / Sheets
```javascript
function lookupUtilities(address) {
  var url = "https://your-app.up.railway.app/lookup?address=" + encodeURIComponent(address);
  var options = {
    method: "get",
    headers: { "X-API-Key": "YOUR_API_KEY" },
    muteHttpExceptions: true
  };
  var response = UrlFetchApp.fetch(url, options);
  return JSON.parse(response.getContentText());
}
```

### Make.com / Zapier / n8n
Use an **HTTP Request** module:
- **Method:** GET
- **URL:** `https://your-app.up.railway.app/lookup?address={{address}}`
- **Header:** `X-API-Key: YOUR_API_KEY`

## 4. Batch Lookup (up to 100 addresses)

```bash
curl -s -X POST "https://your-app.up.railway.app/lookup/batch" \
  -H "X-API-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "addresses": [
      "10812 Watchful Fox Drive, Austin, TX 78748",
      "1619 Cinnabar Dr, Raymore, MO 64083",
      "1902 Karen Ct, Champaign, IL 61821"
    ]
  }'
```

## 5. What You Get Back

For each address, you receive:

| Field | Example |
|-------|---------|
| `electric.provider_name` | Austin Energy |
| `gas.provider_name` | Texas Gas Service |
| `water.provider_name` | City Of Austin Water & Wastewater |
| `sewer.provider_name` | City of Austin Sewer - TX |
| `internet.providers[0].name` | Google Fiber |
| `internet.has_fiber` | true |
| `internet.max_download_speed` | 8000.0 (Mbps) |

Each utility result includes a `confidence` score (0.0–1.0) and `needs_review` flag.

## 6. Tips

- **URL-encode the address** — spaces become `+` or `%20`
- **Include ZIP code** for best results — `"123 Main St, Austin, TX 78748"` > `"123 Main St, Austin, TX"`
- **Check `needs_review`** — if `true`, the result may need manual verification
- **Check `alternatives`** — other candidate providers found at the address
- **Internet data** is at Census block level from FCC BDC — very granular
- **503 on cold start** — the engine takes ~60-90s to load. Retry after a minute.

## 7. Interactive Docs

Visit `https://your-app.up.railway.app/docs` for Swagger UI where you can test endpoints in your browser.

## 8. Postman

Import `docs/Utility_Lookup_API.postman_collection.json` into Postman, then:
1. Set the `base_url` variable to your deployment URL
2. Set the `api_key` variable to your API key
3. Send requests

## Need Help?

See `docs/API_REFERENCE.md` for the full field reference, error codes, and data source details.
