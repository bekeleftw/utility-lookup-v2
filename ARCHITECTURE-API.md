# ARCHITECTURE.md — Utility Lookup API

> Exhaustive reference for the utility-lookup-api codebase.
> Generated from a full read of every file in the repository.

---

## Table of Contents

1. [File Map](#1-file-map)
2. [Lookup Flow](#2-lookup-flow)
3. [Data Sources](#3-data-sources)
4. [Reconciliation Logic](#4-reconciliation-logic)
5. [Data Models](#5-data-models)
6. [Known Overrides and Special Cases](#6-known-overrides-and-special-cases)
7. [Configuration](#7-configuration)
8. [Pipeline Module](#8-pipeline-module)

---

## 1. File Map

### Root — Core Application

| File | Description |
|------|-------------|
| `api.py` (~2752 lines) | Flask application: all HTTP endpoints, `format_utility()`, auth, feedback, corrections admin, batch/stream, leadgen, CORS/rate-limiting setup |
| `utility_lookup_v1.py` (~3950 lines) | Core lookup engine: `geocode_address()`, `lookup_utilities_by_address()`, `lookup_electric_only()`, `lookup_gas_only()`, `lookup_water_only()`, internet/sewer/trash lookups |
| `utility_lookup.py` | Legacy lookup module (predecessor to v1), still importable but not the active code path |
| `utility_lookup_currently_deployed.py` | Snapshot of a previously deployed version, kept for rollback reference |
| `cross_validation.py` (298 lines) | Majority-vote cross-validation: `SourceResult`, `CrossValidationResult`, `AgreementLevel` enum, `providers_match()`, `PROVIDER_ALIASES` |
| `confidence_scoring.py` (344 lines) | Additive confidence scoring: `SOURCE_SCORES` (22 entries, 5 tiers), `PRECISION_SCORES`, SERP/cross-validation adjustments, state data boost |
| `brand_resolver.py` (457 lines) | Legal-to-brand name resolution: `CORPORATE_MERGERS` (13 entries), `COMMON_BRAND_MAPPINGS` (37 entries), directory lookup, OpenAI fallback formatting |
| `state_utility_verification.py` (1430 lines) | State-specific verification: Texas TDU/LDC mapping, EIA 861 ZIP verification, problem areas registry, generic ranking heuristics |
| `geocoding.py` | Standalone geocoding helpers (Census, Google, Nominatim) |
| `gis_utility_lookup.py` (~1800 lines) | 60+ ArcGIS REST API endpoint functions across 33+ states for electric, 13 for gas, 20+ for water |

### Root — Lookup Modules

| File | Description |
|------|-------------|
| `municipal_utilities.py` | Municipal utility lookups from `municipal_utilities.json` plus regional water files (Long Island, SoCal, DFW, Houston, Philly, DC, Atlanta, Florida) |
| `corrections_lookup.py` | User feedback corrections system: Airtable + local JSON, auto-verification at 3 confirmations, address/ZIP/city/county match hierarchy |
| `findenergy_lookup.py` | FindEnergy.com scraping and caching: ZIP-level electric provider data with disk cache |
| `serp_verification.py` | SERP verification via BrightData proxy + OpenAI LLM analysis; `verify_utility_via_serp()`, `normalize_utility_name()`, `is_alias()` |
| `csv_utility_lookup.py` | CSV-based electric/gas provider lookups from supplemental data files |
| `csv_water_lookup.py` | CSV-based water provider lookups from curated water data |
| `water_reconciler.py` | Water provider reconciliation: combines GIS, CSV, EPA, municipal sources and picks best match |
| `water_gis_lookup.py` | Water-specific GIS lookups using state ArcGIS endpoints |
| `sewer_lookup.py` | Sewer provider lookups: Texas TCEQ CCN, state GIS, municipal fallback |
| `special_districts.py` | Special district lookups: Texas MUDs/WCIDs, Florida CDDs, Colorado Metro Districts |
| `special_areas.py` | Detect special area types: tribal lands, military installations, unincorporated areas |
| `deregulated_markets.py` | Deregulated electricity market detection and info for 16 states |
| `deregulated_market_handler.py` | Helpers for adjusting results in deregulated markets (TDU vs REP distinction) |
| `rural_utilities.py` | Rural utility detection: electric cooperatives, propane likelihood |
| `propane_service.py` | Propane area detection for addresses without natural gas service |
| `well_septic.py` | Well water and septic system likelihood detection for rural addresses |
| `building_types.py` | Building type detection from address (apartment, condo, commercial) and metering arrangement info |
| `address_inference.py` | Infer utility from nearby verified addresses; `add_verified_address()`, `infer_utility_from_nearby()` |
| `address_normalization.py` | Address string normalization and parsing |
| `address_cache.py` | In-memory + SQLite address lookup cache with TTL and confirmation tracking |
| `geographic_boundary_lookup.py` | Geographic boundary-based utility resolution using learned lat/lon boundaries |
| `geographic_boundary_analyzer.py` | Analyze and build geographic boundaries from verified lookups |
| `ai_boundary_analyzer_concurrent.py` | Concurrent AI-powered boundary analysis using OpenAI |
| `tenant_verified_lookup.py` | Tenant bill upload verification: street-level overrides from utility bill data |
| `tenant_override_lookup.py` | Tenant hard override lookup from `tenant_hard_overrides.json` |
| `tenant_confidence_scorer.py` | Confidence scoring specific to tenant-verified data |
| `name_normalizer.py` | Utility name normalization (remove suffixes, title case, abbreviation handling) |
| `utility_name_normalizer.py` | Alternative utility name normalizer with slightly different rules |
| `utility_normalization.py` | Yet another normalization module (used by different code paths) |
| `provider_id_matcher.py` | Match utility names to provider IDs from `utility_providers_IDs.csv` |
| `utility_directory.py` | Utility directory lookups from `data/utility_directory/master.json` |
| `utility_direct_lookup.py` | Direct utility API lookups (utility company service address checkers) |
| `utility_scrapers.py` | Utility website scraper registry: ~80 utility-specific verification functions |
| `utility_website_verification.py` (~3600 lines) | 80+ utility-specific territory verification functions (Duke, FPL, ComEd, etc.) using website scraping |
| `browser_verification.py` | Playwright-based browser scraping for utility website verification and website discovery |
| `nj_utility_gis.py` | New Jersey-specific utility GIS lookups (NJ DEP, Board of Public Utilities) |
| `epa_echo_lookup.py` | EPA ECHO system lookups for water/wastewater facility data |
| `state_data_quality.py` | State-by-state data quality tier assignments and availability boost calculations |
| `ml_enhancements.py` | Machine learning enhancements: ensemble prediction, anomaly detection, source weighting |
| `utility_auth.py` | Authentication blueprint: API key management, session auth, Webflow integration |
| `logging_config.py` | Logging configuration with structured JSON logging |

### Root — Internet Lookup

| File | Description |
|------|-------------|
| `bdc_internet_lookup.py` | FCC Broadband Data Collection (BDC) lookup via PostgreSQL/SQLite |
| `broadbandnow_lookup.py` | BroadbandNow.com scraping for internet provider data |
| `allconnect_lookup.py` | AllConnect.com scraping with Playwright browser automation |
| `combined_internet_lookup.py` | Orchestrates FCC BDC + BroadbandNow + AllConnect into unified internet results |

### Root — Build & Data Scripts

| File | Description |
|------|-------------|
| `build_name_mappings.py` | Build `provider_name_mappings.json` from EIA/HIFLD data |
| `build_tenant_overrides.py` | Build `tenant_hard_overrides.json` from tenant verification data |
| `build_water_lookup.py` | Build `water_utility_lookup.json` from EPA SDWIS data |
| `generate_tenant_rules.py` | Generate `sub_zip_provider_rules_50k.json` from tenant data |
| `geocode_tenant_addresses.py` | Batch geocode tenant addresses for boundary building |
| `enrich_utility_websites.py` | Enrich utility records with website URLs via SERP |
| `findenergy_bulk_collect.py` | Bulk collect FindEnergy data for all US ZIP codes |
| `export_streamed.py` | Export lookup results via streaming to CSV/JSON |
| `export_to_postgres.py` | Export lookup data to PostgreSQL |
| `bulk_lookup.py` | Batch address lookup tool |
| `verify_provider_disputes.py` | Verify disputed provider assignments |
| `run_massive_comparison.py` | Run large-scale accuracy comparison tests |
| `run_targeted_comparison.py` | Run targeted comparison for specific areas |
| `create_aggregated_table.sql` | SQL to create aggregated internet provider table |

### Root — Test & Config

| File | Description |
|------|-------------|
| `test_addresses.py` | Test address suite with expected results for validation |
| `test_gis_apis.py` | Test GIS API endpoint connectivity |
| `requirements.txt` | Python dependencies: Flask, requests, beautifulsoup4, psycopg2, playwright, openai, etc. |
| `Dockerfile` | Docker build: Python 3.11-slim, installs Playwright Chromium, runs on port 8080 via gunicorn |
| `.gitignore` | Ignores `.env`, `__pycache__`, node_modules, etc. |
| `.railway-trigger` | Railway deployment trigger file |
| `postman_collection.json` | Postman API test collection |

### Root — Frontend Embeds

| File | Description |
|------|-------------|
| `webflow_embed.html` | Main Webflow embed widget (address lookup UI) |
| `webflow_embed_auth.html` | Webflow embed with authentication |
| `webflow_embed_pm.html` | Property manager version of embed |
| `webflow_embed_pm_slim.html` | Slim property manager embed |
| `webflow_embed_slim.html` | Slim version of main embed |
| `webflow_embed_unified.html` | Unified embed combining all features |
| `webflow_embed_with_auth.html` | Full-featured embed with auth |
| `webflow_embed_with_auth_min.html` | Minified auth embed |

### `pipeline/` — Modular Lookup Pipeline

| File | Description |
|------|-------------|
| `pipeline/__init__.py` | Package init, exports `LookupPipeline`, `UtilityType`, `LookupContext` |
| `pipeline/interfaces.py` | Abstract base classes: `DataSource` ABC, `UtilityType` enum, `LookupContext`, `SourceResult`, `PipelineResult` dataclasses, `SOURCE_CONFIDENCE` and `PRECISION_BONUS` dicts |
| `pipeline/pipeline.py` | `LookupPipeline` orchestrator: parallel source queries, cross-validation, AI/Smart selector, SERP verification, brand enrichment |
| `pipeline/ai_selector.py` | `AISelector`: OpenAI gpt-4o-mini based utility selection with state knowledge base context |
| `pipeline/smart_selector.py` | `SmartSelector`: disk-cached OpenAI selection with tenant/geographic context and rule-based fallback scoring |

### `pipeline/sources/` — Data Source Implementations

| File | Description |
|------|-------------|
| `pipeline/sources/__init__.py` | Source package init, imports all source classes |
| `pipeline/sources/electric.py` | 7 electric sources: `StateGISElectricSource` (85), `MunicipalElectricSource` (88), `CoopSource` (68), `EIASource` (70), `HIFLDElectricSource` (58), `TenantVerifiedElectricSource` (70), `CountyDefaultElectricSource` (50) |
| `pipeline/sources/gas.py` | 6 gas sources: `StateGISGasSource` (85), `MunicipalGasSource` (88), `ZIPMappingGasSource` (75), `HIFLDGasSource` (58), `CountyDefaultGasSource` (50), plus propane detection and official gas utility validation |
| `pipeline/sources/water.py` | 7 water sources: `StateGISWaterSource`, `MunicipalWaterSource`, `SpecialDistrictWaterSource`, `EPAWaterSource`, `CSVWaterSource`, `CountyDefaultWaterSource`, `TexasMUDSupplementalSource` (uses OpenAI for subdivision extraction) |
| `pipeline/sources/corrections.py` | `UserCorrectionSource`: queries Airtable corrections API + local JSON files for user-reported corrections |
| `pipeline/sources/georgia_emc.py` | `GeorgiaEMCSource`: Georgia Electric Membership Corporation county-to-cooperative mapping |
| `pipeline/sources/correction_verifier.py` | Verifies user-submitted corrections before applying them to the database |

### `guide/` — Resident Guide Module

| File | Description |
|------|-------------|
| `guide/guide_api.py` | Flask blueprint for resident guide generation endpoints |
| `guide/guide_generator.py` | Generates comprehensive resident utility guides (PDF/HTML) using OpenAI |
| `guide/guide_pdf.py` | PDF generation for resident guides using ReportLab |
| `guide/guide_templates.py` | HTML/text templates for guide output |

### `data/` — Key Data Files

| File | Description |
|------|-------------|
| `data/municipal_utilities.json` (~134K lines) | City-owned utility overrides: electric, gas, water, sewer, trash for thousands of US cities |
| `data/county_utility_defaults.json` (~16K lines) | County-level fallback utility assignments |
| `data/deregulated_markets.json` (357 lines) | 16 deregulated electricity states with market structure details |
| `data/state_utility_knowledge.json` (~3.5K lines) | AI knowledge base: per-state utility landscape info used by AISelector |
| `data/provider_name_mappings.json` (~24K lines) | Provider name disambiguation and alias mappings |
| `data/sub_zip_provider_rules_50k.json` (~45K lines) | Street-level disambiguation rules for ZIPs with multiple providers |
| `data/tenant_hard_overrides.json` (~1.4K lines) | Tenant-verified street-level provider overrides |
| `data/service_check_urls.json` | Utility service address check/verification URLs |
| `data/api_keys.json` | API key storage (file-based) |
| `data/feedback/` | Directory containing user feedback JSON files |
| `data/electric_zip_corrections.json` | User feedback ZIP corrections for electric |
| `data/gas_zip_corrections.json` | User feedback ZIP corrections for gas |
| `data/water_zip_corrections.json` | User feedback ZIP corrections for water |
| `data/cross_validation_disagreements.json` | Logged cross-validation disagreements for manual review |
| `data/utility_directory/master.json` | Master utility directory with brand names, aliases, contact info |
| `eia_zip_utility_lookup.json` (~10MB) | EIA Form 861 ZIP-to-electric-utility mapping for all US ZIPs |
| `water_utility_lookup.json` (~6.8MB) | EPA SDWIS water system data indexed by ZIP |
| `tenant_verified_lookup.json` (~310KB) | Tenant-verified utility assignments from bill upload data |
| `utility_providers_IDs.csv` (~1.5MB) | Master provider ID table (name, state, type, ID) |
| `water_utilities_supplemental.json` | Supplemental water utility data |
| `water_missing_cities.json` | Cities missing from primary water data |
| `water_utility_cache.json` | Cached water utility lookup results |

### `schemas/` — JSON Schemas

| File | Description |
|------|-------------|
| `schemas/lookup_response.json` | JSON schema for the lookup API response |
| `schemas/feedback_request.json` | JSON schema for feedback submission |

### `scripts/` — Utility Scripts

Contains ~16 scripts for data management, HubSpot CRM sync, Smartlead push, lead scoring, bulk operations, and comparison testing.

### `tests/` — Test Suite

Contains ~7 test files covering API endpoints, pipeline sources, confidence scoring, and cross-validation.

### `docs/` — Documentation

Contains ~11 files covering GIS API inventory, gas utility expansion notes, sewer API status, provider verification guides, and phase implementation summaries.

### `monitoring/` — Monitoring

| File | Description |
|------|-------------|
| `monitoring/accuracy_tracker.py` | Track lookup accuracy metrics over time |
| `monitoring/dashboard.py` | Simple monitoring dashboard |

---

## 2. Lookup Flow

End-to-end trace of a single address lookup from HTTP request to JSON response.

### Entry Point

```
POST /api/lookup  (api.py:~310)
  → Body: { "address": "1100 Congress Ave, Austin, TX 78701", "utilities": ["electric","gas","water","internet"] }
  → api.py calls lookup_utilities_by_address() from utility_lookup_v1.py
```

### Step-by-step trace

#### 2.1 Geocoding — `geocode_address()` (utility_lookup_v1.py:356)

Four-tier fallback:

1. **Census Geocoder** — `geocode_with_census()` (utility_lookup_v1.py) → `https://geocoding.geo.census.gov/geocoder/geographies/onelineaddress` — Returns lat, lon, city, county, state, zip_code, block_geoid. Validates returned ZIP against input ZIP (rejects if 3-digit prefix mismatch).
2. **Google Maps** — `geocode_with_google()` (utility_lookup_v1.py) → Google Geocoding API with `GOOGLE_MAPS_API_KEY`. Adds Census block_geoid via `_get_census_block_geoid()` if missing.
3. **Nominatim/OSM** — `geocode_with_nominatim()` (utility_lookup_v1.py) → `https://nominatim.openstreetmap.org/search`
4. **City Centroid** — `geocode_city_centroid()` (utility_lookup_v1.py) → Parses city/state/zip from address, uses Nominatim to get city center coordinates. Warning flag set.

Returns: `{ lat, lon, city, county, state, zip_code, matched_address, block_geoid }`

#### 2.2 Pre-processing — `lookup_utilities_by_address()` (utility_lookup_v1.py:2200)

After geocoding, before per-utility lookups:

1. **Special areas detection** — `get_special_area_info()` (special_areas.py) → Checks tribal land, military installation, unincorporated status
2. **Building type detection** — `detect_building_type_from_address()` (building_types.py) → apartment, condo, commercial, single-family
3. **Deregulated market check** — `is_deregulated_state()` / `get_deregulated_market_info()` (deregulated_markets.py)
4. **Corrections check** — `check_correction()` (corrections_lookup.py) → Checks Airtable + local JSON for user-reported corrections for each utility type

#### 2.3 Electric Lookup — Priority Chain (utility_lookup_v1.py:2290-2470)

Each step is tried in order; first non-None result wins:

| Priority | Source | Function/Module | Confidence |
|----------|--------|-----------------|------------|
| 0 | User corrections | `check_correction()` (corrections_lookup.py) | user_reported |
| 0.5 | Tenant hard overrides | `check_tenant_override_for_address()` (tenant_override_lookup.py) | 90%+ required |
| 0.6 | Geographic boundary | `check_geographic_boundary()` (geographic_boundary_lookup.py) | 15%+ threshold |
| 0.6b | Nearby consensus | `get_utility_from_nearby_consensus()` (geographic_boundary_lookup.py) | 80%+ threshold |
| 1 | Pipeline (AI selector) | `_pipeline_lookup()` → `LookupPipeline.lookup()` (pipeline/pipeline.py) | varies |
| 2 | State GIS APIs | `lookup_electric_utility_gis()` (gis_utility_lookup.py) | high |
| 3 | Municipal database | `lookup_municipal_electric()` (municipal_utilities.py) | 88 |
| 4 | HIFLD polygon | ArcGIS query to HIFLD FeatureServer | 58 |
| 5 | State verification | `verify_electric_provider()` (state_utility_verification.py) | verified |
| 6 | FindEnergy | `lookup_findenergy()` (findenergy_lookup.py) | 78 |
| 7 | County defaults | `county_utility_defaults.json` lookup | 50 |

After selection: `adjust_electric_result_for_deregulation()` applied if state is deregulated and utility is not municipal/co-op.

#### 2.4 Gas Lookup — Priority Chain (utility_lookup_v1.py:~2470-2650)

| Priority | Source | Function/Module | Confidence |
|----------|--------|-----------------|------------|
| 0 | User corrections | `check_correction()` (corrections_lookup.py) | user_reported |
| 0.5 | Tenant hard overrides | `check_tenant_override_for_address()` (tenant_override_lookup.py) | 90%+ |
| 0.6 | Geographic boundary | `check_geographic_boundary()` (geographic_boundary_lookup.py) | 15%+ |
| 1 | NJ-specific | `nj_utility_gis.py` for New Jersey | high |
| 2 | Pipeline (AI selector) | `_pipeline_lookup()` (pipeline/pipeline.py) | varies |
| 3 | State GIS APIs | `lookup_gas_utility_gis()` (gis_utility_lookup.py) | high |
| 4 | Municipal database | `lookup_municipal_gas()` (municipal_utilities.py) | 88 |
| 5 | HIFLD gas polygon | ArcGIS query to HIFLD gas FeatureServer | 58 |
| 6 | State LDC mapping | `STATE_GAS_LDCS` in state_utility_verification.py | 65 |
| 7 | County defaults | `county_utility_defaults.json` gas lookup | 50 |
| 8 | Propane detection | `is_likely_propane_area()` (propane_service.py) | — |

Gas-specific: If no gas provider found, checks propane likelihood for rural areas.

#### 2.5 Water Lookup — Priority Chain (utility_lookup_v1.py:~2650-2850)

| Priority | Source | Function/Module | Confidence |
|----------|--------|-----------------|------------|
| 0 | User corrections | `check_correction()` (corrections_lookup.py) | user_reported |
| 1 | State GIS APIs | `lookup_water_utility_gis()` (gis_utility_lookup.py) | high |
| 2 | Geographic boundary | `check_geographic_boundary()` (geographic_boundary_lookup.py) | 15%+ |
| 3 | GIS EPA | EPA facility point-in-polygon | medium |
| 4 | Municipal database | `lookup_municipal_water()` (municipal_utilities.py) | 88 |
| 5 | CSV water data | `lookup_water_from_csv()` (csv_water_lookup.py) | supplemental |
| 6 | Special districts | `lookup_special_district()` (special_districts.py) | 85 |
| 7 | EPA SDWIS | `water_utility_lookup.json` ZIP lookup | 55 |
| 8 | Reconciliation | `reconcile_water_providers()` (water_reconciler.py) | varies |
| 9 | Well water check | `get_well_septic_likelihood()` (well_septic.py) | — |

#### 2.6 Internet Lookup (utility_lookup_v1.py:~2850-2950)

Orchestrated by `combined_internet_lookup.py`:

1. **FCC BDC** — `bdc_internet_lookup.py` → PostgreSQL or SQLite query by Census block_geoid
2. **BroadbandNow** — `broadbandnow_lookup.py` → Scrapes broadbandnow.com for ZIP
3. **AllConnect** — `allconnect_lookup.py` → Playwright browser scrape of allconnect.com

Returns top providers by type (fiber, cable, DSL, fixed wireless, satellite).

#### 2.7 Sewer & Trash Lookups

- **Sewer** — `sewer_lookup.py`: Texas TCEQ CCN lookup, state GIS, municipal fallback
- **Trash** — Municipal database lookup, city/county default

#### 2.8 Post-processing — Back in `lookup_utilities_by_address()`

1. **SERP Verification** — If `verify_with_serp=True` and confidence is below threshold, `verify_utility_via_serp()` (serp_verification.py) queries BrightData Google SERP proxy, then uses OpenAI to analyze results
2. **Confidence Scoring** — `calculate_confidence()` (confidence_scoring.py) applied to each utility result
3. **Brand Resolution** — `resolve_brand_name_with_fallback()` (brand_resolver.py) converts legal names to consumer brands
4. **Address caching** — Result cached via `address_cache.py`
5. **Address inference** — Verified result added to inference DB via `add_verified_address()` (address_inference.py)

#### 2.9 Response Formatting — `format_utility()` (api.py:648)

Each utility result passes through `format_utility()` which:

1. Normalizes the name via `normalize_utility_name()` (name_normalizer.py)
2. Filters blocked website domains (social media, directories, etc.)
3. Discovers utility website via `find_utility_website()` (browser_verification.py) if missing
4. Matches to `provider_id` via `get_provider_id()` (provider_id_matcher.py)
5. Looks up `service_check_url` via `get_service_check_url()` (api.py)
6. Checks `EXCLUSIVE_MUNICIPAL_UTILITIES` set for confidence override (score=98, level=verified)
7. Checks deregulated market status and adds `deregulated_info` fields
8. Returns structured dict (see Data Models section)

#### 2.10 Final API Response Assembly (api.py:~310-450)

```python
{
    "address": <input_address>,
    "matched_address": <geocoded_address>,
    "lat": <float>,
    "lon": <float>,
    "city": <string>,
    "county": <string>,
    "state": <string>,
    "zip": <string>,
    "electric": { ... },           # format_utility() output
    "gas": { ... },                # or gas_no_service object
    "water": { ... },
    "sewer": { ... },
    "trash": { ... },
    "internet": [ ... ],           # array of providers
    "special_areas": { ... },      # tribal, military, unincorporated
    "building_type": { ... },      # type and metering info
    "deregulated_info": { ... },   # if applicable
    "lookup_time_ms": <int>
}
```

---

## 3. Data Sources

### 3.1 External APIs (Live Queries)

| Source | URL/Endpoint | What It Returns | Called From |
|--------|-------------|-----------------|------------|
| **US Census Geocoder** | `geocoding.geo.census.gov/geocoder/geographies/onelineaddress` | lat, lon, city, county, state, zip, block FIPS, matched address | `geocode_with_census()` in utility_lookup_v1.py |
| **Google Maps Geocoding** | `maps.googleapis.com/maps/api/geocode/json` | lat, lon, formatted address, components | `geocode_with_google()` in utility_lookup_v1.py |
| **Nominatim/OSM** | `nominatim.openstreetmap.org/search` | lat, lon, display name | `geocode_with_nominatim()` in utility_lookup_v1.py |
| **HIFLD Electric** | `services1.arcgis.com/.../Electric_Retail_Service_Territories` | Utility name, ID, state, type for polygon containing point | `lookup_electric_only()` in utility_lookup_v1.py |
| **HIFLD Gas** | `services1.arcgis.com/.../Natural_Gas_Local_Distribution` | Gas utility name, ID, state for polygon containing point | `lookup_gas_only()` in utility_lookup_v1.py |
| **State GIS APIs (60+)** | Various ArcGIS REST endpoints per state | Utility name, type, territory boundary match | `gis_utility_lookup.py` — see functions: `lookup_electric_gis_ca()`, `lookup_electric_gis_tx()`, `lookup_water_gis_nc()`, etc. |
| **EPA SDWIS** | `enviro.epa.gov/enviro/efservice/WATER_SYSTEM` | Water system name, PWSID, population served, source type | `epa_echo_lookup.py` |
| **FCC BDC** | PostgreSQL/SQLite table `broadband_data` | ISP name, technology, speeds by Census block | `bdc_internet_lookup.py` |
| **BrightData SERP Proxy** | `brd.superproxy.io:33335` → Google Search | Search results for utility verification | `serp_verification.py` |
| **OpenAI gpt-4o-mini** | `api.openai.com/v1/chat/completions` | AI-powered utility selection, name formatting, SERP analysis | `pipeline/ai_selector.py`, `pipeline/smart_selector.py`, `brand_resolver.py`, `serp_verification.py` |
| **FindEnergy.com** | `findenergy.com/` (web scrape) | Electric utility by ZIP | `findenergy_lookup.py` |
| **BroadbandNow** | `broadbandnow.com/` (web scrape) | Internet providers by ZIP | `broadbandnow_lookup.py` |
| **AllConnect** | `allconnect.com/` (Playwright scrape) | Internet providers by address | `allconnect_lookup.py` |
| **Airtable Corrections** | Airtable API | User-submitted corrections | `pipeline/sources/corrections.py` |
| **Utility Website Scrapers** | 80+ individual utility websites | Service territory verification | `utility_website_verification.py` |
| **Texas TCEQ** | `gisweb.tceq.texas.gov/arcgis/rest/services` | MUD/WCID/FWSD boundaries, sewer CCN boundaries | `special_districts.py`, `sewer_lookup.py` |

### 3.2 Local Data Files

| File | Format | Contents | Size | Queried By |
|------|--------|----------|------|------------|
| `eia_zip_utility_lookup.json` | JSON | EIA Form 861 ZIP-to-electric-utility mapping; keys are 5-digit ZIPs, values are arrays of `{name, eiaid, state, ownership}` | ~10MB | `state_utility_verification.py:get_eia_utility_by_zip()` |
| `water_utility_lookup.json` | JSON | EPA SDWIS water systems indexed by ZIP; `{name, pwsid, population_served, source_type, state}` | ~6.8MB | `utility_lookup_v1.py` water lookup |
| `data/municipal_utilities.json` | JSON | Municipal utilities by city/state: `{ "state:city": { electric, gas, water, sewer, trash } }` | ~134K lines | `municipal_utilities.py` |
| `data/county_utility_defaults.json` | JSON | County-level fallback: `{ "STATE:COUNTY": { electric, gas } }` | ~16K lines | `utility_lookup_v1.py`, pipeline sources |
| `data/deregulated_markets.json` | JSON | 16 deregulated states with `{ structure, note, tdu_field }` | 357 lines | `deregulated_markets.py` |
| `data/state_utility_knowledge.json` | JSON | Per-state AI knowledge base: major utilities, cooperatives, municipal systems, market structure | ~3.5K lines | `pipeline/ai_selector.py` |
| `data/provider_name_mappings.json` | JSON | Name-to-canonical mappings for disambiguation | ~24K lines | Various normalizers |
| `data/sub_zip_provider_rules_50k.json` | JSON | Street-level rules for ZIPs with multiple providers: `{ "zip": { "streets": { "pattern": "utility" } } }` | ~45K lines | `tenant_override_lookup.py` |
| `data/tenant_hard_overrides.json` | JSON | High-confidence street-level overrides from tenant bills | ~1.4K lines | `tenant_override_lookup.py` |
| `tenant_verified_lookup.json` | JSON | Tenant bill verification data indexed by address/ZIP | ~310KB | `tenant_verified_lookup.py` |
| `data/service_check_urls.json` | JSON | Utility → service address verification URL | — | `api.py:get_service_check_url()` |
| `data/utility_directory/master.json` | JSON | Master utility directory: name, aliases, phone, website, states served | — | `brand_resolver.py`, `utility_directory.py` |
| `utility_providers_IDs.csv` | CSV | Provider ID mapping: name, state, utility type, unique ID | ~1.5MB | `provider_id_matcher.py` |
| `data/electric_zip_corrections.json` | JSON | User-reported ZIP-level electric corrections | — | `corrections_lookup.py` |
| `data/gas_zip_corrections.json` | JSON | User-reported ZIP-level gas corrections | — | `corrections_lookup.py` |
| `data/water_zip_corrections.json` | JSON | User-reported ZIP-level water corrections | — | `corrections_lookup.py` |

---

## 4. Reconciliation Logic

### 4.1 Cross-Validation — `cross_validation.py`

**Purpose**: Compare results from multiple data sources and assess agreement level.

**Data structures**:
```python
class AgreementLevel(Enum):
    FULL = "full"       # All sources agree        → +20 confidence
    MAJORITY = "majority" # Most sources agree      → +10 confidence
    SPLIT = "split"     # Sources disagree equally  → -10 confidence
    SINGLE = "single"   # Only one source           → +0 confidence
    NONE = "none"       # No sources returned data  → -20 confidence

@dataclass
class SourceResult:
    source_name: str
    provider_name: Optional[str]
    confidence: str      # 'high', 'medium', 'low'
    raw_data: Optional[dict] = None

@dataclass
class CrossValidationResult:
    primary_provider: Optional[str]
    agreement_level: AgreementLevel
    agreeing_sources: List[str]
    disagreeing_sources: List[str]
    all_candidates: Dict[str, List[str]]   # provider_name → [source_names]
    confidence_adjustment: int
    notes: List[str]
```

**Matching logic** — `providers_match()` (cross_validation.py:93):
Three-tier matching (returns True if any tier matches):
1. **Exact normalized** — After `normalize_provider_name()`: lowercase, strip suffixes (inc, llc, corp, electric, energy, power, delivery, service, utility, cooperative, etc.), remove punctuation
2. **Substring containment** — If both names > 3 chars, check if one contains the other
3. **Alias groups** — Check `PROVIDER_ALIASES` dict (~15 groups):
   - `pge`: pacific gas, pg&e, pacific gas and electric
   - `sce`: southern california edison, socal edison
   - `sdge`: san diego gas, sdg&e
   - `fpl`: florida power, florida power and light
   - `duke`: duke energy
   - `oncor`: oncor electric, oncor delivery
   - `centerpoint`: center point, entex
   - `atmos`: atmos energy
   - `aep`: american electric power, aep texas
   - `txu`: txu energy
   - `pec`: pedernales, pedernales electric
   - `bluebonnet`: bluebonnet electric
   - `austin energy`: austin energy, city of austin
   - `texas gas`: texas gas service

**Selection logic** — `cross_validate()` (cross_validation.py:120):
1. Filter results with non-null `provider_name`
2. If 0 results → `AgreementLevel.NONE`, adjustment = -20
3. If 1 result → `AgreementLevel.SINGLE`, adjustment = 0
4. Group by normalized name using `providers_match()` for grouping
5. Count: if top_count == total → FULL (+20); if top_count > total/2 → MAJORITY (+10); else → SPLIT (-10)
6. Primary provider = highest-confidence result from winning group (high=3, medium=2, low=1)

**No source trust weighting** — Pure count-based majority vote. All sources weighted equally.

**Disagreement logging** — `log_disagreement()` writes MAJORITY and SPLIT cases to `data/cross_validation_disagreements.json` (capped at 1000 entries).

### 4.2 Confidence Scoring — `confidence_scoring.py`

**Additive scoring system**: Final score = SOURCE_SCORE + PRECISION_BONUS + SERP_ADJUSTMENT + CV_ADJUSTMENT + PROBLEM_PENALTY + FRESHNESS_PENALTY + STATE_BOOST

**SOURCE_SCORES** (22 entries, 5 tiers):

| Tier | Sources | Score |
|------|---------|-------|
| **Tier 1: Authoritative (90+)** | `user_confirmed`=95, `utility_direct_api`=92, `franchise_agreement`=92, `parcel_data`=90, `user_feedback`=88, `municipal_utility`=88 | Skip SERP |
| **Tier 2: High Quality (80-89)** | `special_district`=85, `verified`=85, `utility_api`=85, `state_puc_map`=82, `zip_override`=80, `railroad_commission`=80 | Spot-check SERP |
| **Tier 3: Good Quality (65-79)** | `findenergy`=78, `findenergy_cache`=78, `findenergy_scrape`=76, `findenergy_serp`=72, `state_puc`=75, `address_inference`=72, `eia_861`=70, `supplemental`=70, `electric_cooperative`=68, `state_ldc_mapping`=65 | SERP recommended |
| **Tier 4: Needs Verification (50-64)** | `google_serp`=60, `hifld_polygon`=58, `epa_sdwis`=55, `serp_only`=50 | Always SERP |
| **Tier 5: Low Confidence (<50)** | `county_match`=45, `heuristic`=30, `unknown`=15 | Requires verification |

**PRECISION_SCORES** (additive bonus):

| Level | Points |
|-------|--------|
| `parcel` | +15 |
| `address` | +12 |
| `gis_point` | +10 |
| `subdivision` | +8 |
| `special_district` | +8 |
| `zip5` | +5 |
| `zip3` | +3 |
| `county` | +1 |
| `state` | +0 |

**Adjustments**:

| Factor | Adjustment | Condition |
|--------|------------|-----------|
| SERP confirmed | +20 | `serp_result.confirmed == True` |
| SERP contradicted | -25 | `serp_result.contradicted == True` |
| 3+ sources agree | +20 | `len(agreeing_sources) >= 3` |
| 2 sources agree | +10 | `len(agreeing_sources) == 2` |
| Problem area | -15 | `is_problem_area == True` AND source NOT in Tier 1/2 |
| Data > 24 months | -10 | `data_age_months > 24` |
| Data > 12 months | -5 | `data_age_months > 12` |
| State data boost | variable | `calculate_data_availability_boost()` from `state_data_quality.py` |

**Score-to-level mapping** (confidence_scoring.py:217):
- >= 80 → `verified`
- 60-79 → `high`
- 40-59 → `medium`
- < 40 → `low`

**Note**: The pipeline module (`pipeline/interfaces.py`) has a slightly different mapping:
- >= 85 → `verified`
- 70-84 → `high`
- 50-69 → `medium`
- < 50 → `low`

### 4.3 Pipeline Cross-Validation — `pipeline/pipeline.py:_cross_validate()`

The pipeline module has its own cross-validation that uses `normalize_utility_name()` and `is_alias()` from `serp_verification.py` instead of the standalone `cross_validation.py` module. Same majority-vote logic but leverages SERP verification's more comprehensive alias matching.

### 4.4 Pipeline Selection — `pipeline/pipeline.py:_select_primary()`

Rule-based fallback selection when neither AISelector nor SmartSelector is available:

**SOURCE_PRIORITY** (weighted at 30%):
```
municipal: 100, state_gis: 90, electric_coop: 80, zip_mapping_gas: 75,
eia_861: 70, hifld: 40, county_default: 30
```

**Score formula**: `confidence_score + PRECISION_BONUS[match_type] + SOURCE_PRIORITY[source_name] * 0.3`

**Special case**: When both `municipal_water` and `special_district_water` match, municipal gets +15 boost and special_district gets -10 penalty (Texas cities typically absorb MUD services).

Cross-validation bonus: Only applied to sources with priority >= 70 (to prevent low-quality sources from ganging up), weighted at 50% of adjustment.

---

## 5. Data Models

### 5.1 Geocoding Result (utility_lookup_v1.py)

```python
{
    "lat": float,
    "lon": float,
    "city": str,              # e.g. "Austin"
    "county": str,            # e.g. "Travis"
    "state": str,             # e.g. "TX"
    "zip_code": str,          # 5-digit ZIP
    "matched_address": str,   # Geocoder's normalized address
    "block_geoid": str,       # Census block FIPS (for internet lookup)
    "source": str             # "census", "google", "nominatim", "city_centroid"
}
```

### 5.2 Internal Utility Result (before format_utility)

```python
# Electric/Gas (from lookup chains in utility_lookup_v1.py)
{
    "NAME": str,                      # Raw utility name
    "TELEPHONE": str,                 # Phone number
    "WEBSITE": str,                   # Website URL
    "STATE": str,                     # State code
    "CITY": str,                      # City name
    "ADDRESS": str,                   # Utility mailing address
    "_confidence": str|float,         # "verified", "high", "medium", "low" or numeric
    "_confidence_score": int,         # 0-100
    "_verification_source": str,      # Source key (e.g., "municipal_utility", "gis_state_api")
    "_selection_reason": str,         # Human-readable selection explanation
    "_is_deregulated": bool,          # Whether market is deregulated
    "_deregulated_note": str,         # Deregulation explanation
    "_serp_verified": bool,           # Whether SERP verification confirmed
    "_serp_utility": str,             # What SERP found (if different)
    "_alternatives": list,            # Other candidate utilities
}
```

### 5.3 API Response — Electric/Gas (from format_utility, api.py:648)

```python
{
    "name": str,                      # Normalized consumer-facing name
    "phone": str,
    "website": str,                   # Filtered (blocked domains removed)
    "service_check_url": str,         # Utility's own address lookup URL
    "address": str,
    "city": str,
    "state": str,
    "zip": str,
    "provider_id": str,               # From utility_providers_IDs.csv
    "confidence": str,                # "verified", "high", "medium", "low"
    "confidence_score": int,          # 0-100
    "confidence_factors": list,       # Breakdown of scoring factors
    "verified": bool,                 # SERP verification result
    "_source": str,                   # Data source identifier
    "deregulated_market": bool,
    "deregulated_info": {
        "is_deregulated": bool,
        "note": str,
        "tdu_name": str,              # Transmission/Distribution Utility
        "shop_url": str               # Power to Choose or equivalent
    }
}
```

### 5.4 API Response — Water

```python
{
    "name": str,
    "phone": str,
    "website": str,
    "address": str,
    "city": str,
    "state": str,
    "zip": str,
    "id": str,                        # PWSID (EPA water system ID)
    "provider_id": str,
    "population_served": int,
    "source_type": str,               # "Surface water", "Ground water", etc.
    "confidence": str,
    "confidence_score": int,
    "confidence_factors": list,
    "verified": bool,
    "_source": str
}
```

### 5.5 API Response — Internet

```python
[
    {
        "name": str,                  # ISP name
        "technology": str,            # "Fiber", "Cable", "DSL", "Fixed Wireless", "Satellite"
        "max_download": int,          # Mbps
        "max_upload": int,            # Mbps
        "source": str                 # "fcc_bdc", "broadbandnow", "allconnect"
    }
]
```

### 5.6 Pipeline Data Structures (pipeline/interfaces.py)

```python
class UtilityType(Enum):
    ELECTRIC = "electric"
    GAS = "gas"
    WATER = "water"

@dataclass
class LookupContext:
    lat: Optional[float]
    lon: Optional[float]
    address: str
    city: str
    county: str
    state: str                        # Auto-uppercased in __post_init__
    zip_code: str                     # Auto-truncated to 5 digits
    utility_type: UtilityType

@dataclass
class SourceResult:
    source_name: str
    utility_name: Optional[str]
    confidence_score: int             # 0-100
    match_type: str                   # 'point', 'zip', 'county', 'city', 'state'
    phone: Optional[str] = None
    website: Optional[str] = None
    raw_data: Optional[Dict] = None
    query_time_ms: int = 0
    error: Optional[str] = None

@dataclass
class PipelineResult:
    utility_name: Optional[str]
    utility_type: UtilityType
    confidence_score: int             # 0-100
    confidence_level: str             # 'verified', 'high', 'medium', 'low', 'none'
    source: str
    phone: Optional[str] = None
    website: Optional[str] = None
    brand_name: Optional[str] = None
    legal_name: Optional[str] = None
    deregulated_market: bool = False
    deregulated_note: Optional[str] = None
    sources_agreed: bool = True
    agreeing_sources: List[str] = []
    disagreeing_sources: List[str] = []
    serp_verified: Optional[bool] = None
    serp_utility: Optional[str] = None
    all_results: List[SourceResult] = []
    timing_ms: int = 0
```

### 5.7 Cross-Validation Result (cross_validation.py)

```python
@dataclass
class CrossValidationResult:
    primary_provider: Optional[str]
    agreement_level: AgreementLevel   # FULL, MAJORITY, SPLIT, SINGLE, NONE
    agreeing_sources: List[str]
    disagreeing_sources: List[str]
    all_candidates: Dict[str, List[str]]  # provider_name → [source_names]
    confidence_adjustment: int        # +20, +10, 0, -10, or -20
    notes: List[str]
```

### 5.8 Confidence Score Result (confidence_scoring.py)

```python
{
    "score": int,                     # 0-100, clamped
    "level": str,                     # "verified", "high", "medium", "low"
    "factors": [
        {
            "category": str,          # "Data Source", "Geographic Precision", etc.
            "points": int,            # Positive or negative
            "description": str
        }
    ],
    "recommendation": Optional[str]   # None for verified, guidance text for others
}
```

---

## 6. Known Overrides and Special Cases

### 6.1 Texas TDU Mapping (state_utility_verification.py:139)

5 Transmission/Distribution Utilities with ZIP prefix mapping:

```
ONCOR:       750-769 (Dallas/Fort Worth, Waco, Tyler)
CENTERPOINT: 770-779 (Houston, Galveston, Beaumont)
AEP_NORTH:   793-794 (Abilene, Lubbock)
AEP_CENTRAL: 780-789 (San Antonio, Corpus Christi, Rio Grande Valley)
TNMP:        Scattered areas (no clean prefix mapping)
```

**Municipal exclusions** (not served by TDUs): Austin, San Antonio, Garland, Georgetown, Greenville, Denton, New Braunfels, Boerne, Kerrville, Seguin, Lubbock, Brownsville, College Station, Bryan, Floresville

**Co-op exclusions**: Pedernales, Bluebonnet, Guadalupe Valley, CoServ, Tri-County, United Cooperative, Oncor/not-deregulated areas, Mid-South, Grayson-Collin, Sam Houston, Brazos, Lighthouse, South Plains, Magic Valley, Nueces

### 6.2 Texas Gas LDC Mapping (state_utility_verification.py)

4 Local Distribution Companies:

```
CENTERPOINT_GAS: 770-779 (Houston metro)
ATMOS_ENERGY:    750-769 (Dallas/Fort Worth)
TEXAS_GAS_SVC:   786-789 (San Antonio, Austin area)
COSERV_GAS:      Specific ZIPs in Denton County
```

**GAS_ZIP_OVERRIDES** (13 entries):
- Denton County ZIPs (76201, 76205, 76207, 76208, 76209, 76210, 76226, 76227, 76247, 76259): CoServ Gas
- Hays County ZIPs (78610, 78666, 78737): CenterPoint Energy (not Atmos)

### 6.3 STATE_GAS_LDCS — All 50 States (state_utility_verification.py)

Complete mapping of every US state + DC to their primary gas utility. Examples:
- TX: CenterPoint Energy, Atmos Energy, Texas Gas Service
- CA: SoCalGas, PG&E, SDG&E, Southwest Gas
- NY: Con Edison, National Fuel Gas, NYSEG, KeySpan
- FL: TECO Peoples Gas, Florida City Gas
- IL: Nicor Gas, Peoples Gas, Ameren Illinois

### 6.4 Exclusive Municipal Utilities (api.py:761)

Set of utilities that always get confidence_score=98 and level="verified":

```python
EXCLUSIVE_MUNICIPAL_UTILITIES = {
    'austin energy', 'cps energy', 'ladwp',
    'los angeles department of water and power',
    'seattle city light', 'sacramento municipal utility district', 'smud',
    'austin water', 'san antonio water system',
    'ouc', 'orlando utilities commission', 'jea',
    'lpnt', 'lubbock power & light', 'garland power & light',
    'new braunfels utilities', 'texas gas service',
    'atmos energy', 'centerpoint energy'
}
```

### 6.5 Corporate Mergers (brand_resolver.py:146)

```python
CORPORATE_MERGERS = {
    "chesapeake energy": "Expand Energy",      # Oct 2024 merger
    "chesapeake utilities": "Expand Energy",
    "southwestern energy": "Expand Energy",
    "progress energy": "Duke Energy",
    "questar": "Dominion Energy",
    "scana": "Dominion Energy",
    "integrys energy": "WEC Energy Group",
    "peoples gas chicago": "WEC Energy Group",
    "agl resources": "Atlanta Gas Light",
    "midamerican energy": "Berkshire Hathaway Energy",
    "pacificorp": "Berkshire Hathaway Energy",
    "nv energy": "Berkshire Hathaway Energy",
    "vectren": "CenterPoint Energy",
}
```

### 6.6 Common Brand Mappings (brand_resolver.py:180)

37 entries mapping legal names to consumer brands. Key examples:
- `wisconsin electric power co` → **WE Energies**
- `pacific gas and electric` → **PG&E**
- `southern california edison` → **SCE**
- `oncor electric delivery` → **Oncor**
- `commonwealth edison` → **ComEd**
- `florida power & light` → **FPL**
- `arizona public service` → **APS**
- `public service company of colorado` → **Xcel Energy**
- `consolidated edison` → **Con Edison**

### 6.7 Provider Aliases for Cross-Validation (cross_validation.py:45)

~15 alias groups used for matching during cross-validation:
```python
PROVIDER_ALIASES = {
    'pge': ['pacific gas', 'pg&e', 'pacific gas and electric', 'pg e', 'pge'],
    'sce': ['southern california edison', 'socal edison'],
    'sdge': ['san diego gas', 'sdg&e'],
    'fpl': ['florida power', 'florida power and light'],
    'duke': ['duke energy'],
    'oncor': ['oncor electric', 'oncor delivery'],
    'centerpoint': ['center point', 'entex'],
    'atmos': ['atmos energy'],
    'aep': ['american electric power', 'aep texas'],
    'pec': ['pedernales', 'pedernales electric'],
    'bluebonnet': ['bluebonnet electric'],
    'austin energy': ['austin energy', 'city of austin'],
    'texas gas': ['texas gas service'],
}
```

### 6.8 Problem Areas Registry (state_utility_verification.py)

Flagged areas where lookups are known to be unreliable. Three levels:
- **ZIP-level**: Specific ZIP codes with boundary overlap issues
- **County-level**: Counties with complex provider boundaries
- **State-level**: States with generally poor data availability

Problem area penalty: -15 confidence points (exempt for Tier 1 and Tier 2 sources).

### 6.9 Deregulated Markets (data/deregulated_markets.json)

16 states with deregulated electricity markets:
TX, PA, OH, IL, NY, CT, MD, NJ, DE, MA, NH, ME, RI, MI (partial), VA (partial), OR (partial)

Each entry specifies market structure (full retail choice, limited choice, etc.) and TDU/EDC field names.

### 6.10 Blocked Website Domains (api.py:654)

Websites never returned as utility websites:
```
mapquest.com, yelp.com, yellowpages.com, whitepages.com,
facebook.com, twitter.com, linkedin.com, instagram.com,
bbb.org, manta.com, chamberofcommerce.com, bizapedia.com,
opencorporates.com, dnb.com, zoominfo.com, crunchbase.com,
wikipedia.org, ncbi.nlm.nih.gov, indeed.com, glassdoor.com,
google.com, bing.com, yahoo.com, reddit.com
```

### 6.11 Pipeline Special Cases

**Municipal vs Special District (water)** — `pipeline/pipeline.py:_select_primary()`: When both `municipal_water` and `special_district_water` match, municipal gets +15 boost and special district gets -10 penalty. Rationale: Texas cities typically absorb MUD services even though MUD boundaries persist in TCEQ records.

**High-confidence municipal short-circuit** — `pipeline/pipeline.py:lookup()`: If a municipal source returns confidence >= 85, the AI selector is skipped entirely (saves 2-5 seconds).

**Pipeline 95+ short-circuit** — `pipeline/pipeline.py:_query_parallel()`: If any source returns confidence_score >= 95 during parallel querying, remaining queries are cancelled.

### 6.12 Georgia EMC Mapping (pipeline/sources/georgia_emc.py)

County-to-Electric-Membership-Corporation mapping for Georgia. Maps each Georgia county to its serving EMC (e.g., Walton EMC, Jackson EMC, Cobb EMC).

---

## 7. Configuration

### 7.1 Environment Variables

| Variable | Purpose | Used In |
|----------|---------|---------|
| `GOOGLE_MAPS_API_KEY` | Google Geocoding API | `utility_lookup_v1.py:geocode_with_google()` |
| `OPENAI_API_KEY` | OpenAI API for AI selector, brand formatting, SERP analysis | `pipeline/ai_selector.py`, `pipeline/smart_selector.py`, `brand_resolver.py`, `serp_verification.py`, `pipeline/sources/water.py` |
| `DATABASE_URL` | PostgreSQL connection for FCC BDC internet data | `bdc_internet_lookup.py` |
| `GUIDE_DATABASE_URL` | PostgreSQL for resident guide feature | `api.py`, `guide/guide_api.py` |
| `MASTER_API_KEY` | Master API key (survives deploys) | `api.py:validate_api_key()` |
| `ADMIN_SECRET` | Admin secret for key management (default: `utility-admin-2026`) | `api.py:generate_api_key()` |
| `AIRTABLE_API_KEY` | Airtable API for corrections | `pipeline/sources/corrections.py` |
| `AIRTABLE_BASE_ID` | Airtable base ID | `pipeline/sources/corrections.py` |
| `PORT` | Server port (default: 8080) | `api.py` |
| `FCC_API_UUID` | FCC BDC API identifier | `utility_lookup_v1.py` |

### 7.2 Hardcoded Configuration

| Item | Value | Location |
|------|-------|----------|
| BrightData proxy host | `brd.superproxy.io` | `utility_lookup_v1.py:96` |
| BrightData proxy port | `33335` | `utility_lookup_v1.py:97` |
| BrightData proxy user | `brd-customer-hl_6cc76bc7-zone-address_search` | `utility_lookup_v1.py:98` |
| BrightData proxy password | `n59dskgnctqr` | `utility_lookup_v1.py:99` |
| OpenAI model | `gpt-4o-mini` | `pipeline/ai_selector.py`, `pipeline/smart_selector.py`, `brand_resolver.py` |
| Address cache TTL | 3600 seconds (1 hour) | `api.py:77` |
| Address cache max size | 10,000 entries | `api.py:95` |
| Disagreement log max entries | 1,000 | `cross_validation.py:279` |
| Pipeline max workers | 5 threads | `pipeline/pipeline.py:36` |
| Pipeline parallel timeout | 3.0 seconds | `pipeline/pipeline.py:217` |
| Pipeline short-circuit threshold | 95 confidence score | `pipeline/pipeline.py:225` |
| SERP confidence threshold | 70 | `pipeline/pipeline.py:52` |
| API version | `2026-01-31-v36` | `api.py:245` |
| Rate limiting | Disabled (no limits) | `api.py:226` |
| API key prefix | `ulk_` | `api.py:261` |

### 7.3 .env File Loading (utility_lookup_v1.py:74)

Searches three locations in order:
1. `<repo_root>/.env`
2. `<repo_root>/../PMD_scrape/.env`
3. `<repo_root>/../BrightData_AppFolio_Scraper/.env`

### 7.4 Dockerfile Configuration

- Base: `python:3.11-slim`
- Installs: Playwright Chromium (for browser scraping)
- Runs: `gunicorn api:app --bind 0.0.0.0:8080 --workers 2 --timeout 120`
- Port: 8080

### 7.5 Feature Flags (pipeline/pipeline.py)

```python
self.enable_cross_validation = True
self.enable_serp_verification = True
self.enable_smart_selector = True   # Legacy SmartSelector
self.enable_ai_selector = True      # AI-first selection (preferred)
```

The `PIPELINE_AVAILABLE` flag in `utility_lookup_v1.py:68` controls whether the pipeline module is used at all (depends on successful import).

---

## 8. Pipeline Module

### 8.1 Overview

The `pipeline/` module is a newer, modular reimplementation of the lookup logic. It coexists with the older monolithic code in `utility_lookup_v1.py`. **Both are currently running** — the pipeline is called at Priority 1 in the electric/gas lookup chains when `PIPELINE_AVAILABLE=True` and `use_pipeline=True`.

### 8.2 Architecture

```
LookupPipeline (pipeline/pipeline.py)
├── DataSource implementations (pipeline/sources/*.py)
│   ├── electric.py: 7 sources
│   ├── gas.py: 6 sources
│   ├── water.py: 7 sources
│   ├── corrections.py: 1 source
│   └── georgia_emc.py: 1 source
├── AISelector (pipeline/ai_selector.py)
│   └── Uses OpenAI gpt-4o-mini with state_utility_knowledge.json context
├── SmartSelector (pipeline/smart_selector.py)
│   └── Uses OpenAI gpt-4o-mini with disk cache + rule-based fallback
└── Cross-validation (pipeline/pipeline.py:_cross_validate)
    └── Uses serp_verification.normalize_utility_name() and is_alias()
```

### 8.3 Pipeline Flow — `LookupPipeline.lookup()` (pipeline/pipeline.py:74)

1. **Filter sources** — Get sources that support the requested `UtilityType`
2. **Parallel query** — `_query_parallel()`: Submit all sources to `ThreadPoolExecutor(max_workers=5)`, collect results as they complete with 3-second timeout. Short-circuit if any result has confidence >= 95.
3. **Cross-validate** — `_cross_validate()`: Group by normalized name, calculate agreement level and confidence adjustment
4. **Select best result** — Decision hierarchy:
   - **Municipal short-circuit**: If municipal source returned confidence >= 85, use it directly (skip AI)
   - **AISelector** (preferred): If `_ai_selector` initialized, call `ai_selector.select(context, valid_results)` — sends all candidates + state knowledge to gpt-4o-mini for decision
   - **SmartSelector** (fallback): If AI not available and sources disagree, use `smart_selector.select_utility(context, valid_results)` — disk-cached OpenAI calls with rule-based fallback
   - **Rule-based** (final fallback): `_select_primary()` — score each result by `confidence_score + PRECISION_BONUS + SOURCE_PRIORITY * 0.3`
5. **Build result** — `_build_result()`: Combine primary selection with cross-validation data
6. **Enrich** — `_enrich()`: Brand resolution via `resolve_brand_name_with_fallback()`, deregulated market check
7. **SERP verification** — Only when sources disagree AND disagreeing >= agreeing (true tie or minority wins). SERP can confirm (+15, level=verified), switch to SERP-agreeing alternative (score=85, level=verified), or note disagreement without overriding.

### 8.4 DataSource ABC (pipeline/interfaces.py:155)

```python
class DataSource(ABC):
    @property
    def name(self) -> str: ...          # Unique source identifier
    @property
    def supported_types(self) -> List[UtilityType]: ...
    @property
    def base_confidence(self) -> int: ...  # 0-100
    @property
    def timeout_ms(self) -> int:        # Default: 2000ms
        return 2000
    def query(self, context: LookupContext) -> Optional[SourceResult]: ...
    def supports(self, utility_type: UtilityType) -> bool: ...
```

### 8.5 Electric Sources (pipeline/sources/electric.py)

| Source Class | name | base_confidence | Logic |
|-------------|------|-----------------|-------|
| `StateGISElectricSource` | `state_gis` | 85 | Calls `lookup_electric_utility_gis(lat, lon, state)` from `gis_utility_lookup.py` |
| `MunicipalElectricSource` | `municipal_electric` | 88 | Calls `lookup_municipal_electric(city, state)` from `municipal_utilities.py` |
| `CoopSource` | `electric_coop` | 68 | Checks rural electric cooperative databases |
| `EIASource` | `eia_861` | 70 | Calls `get_eia_utility_by_zip(zip_code)` from `state_utility_verification.py` |
| `HIFLDElectricSource` | `hifld` | 58 | Queries HIFLD ArcGIS FeatureServer for electric territory polygon |
| `TenantVerifiedElectricSource` | `tenant_verified` | 70 | Checks `tenant_verified_lookup.json` for address matches |
| `CountyDefaultElectricSource` | `county_default` | 50 | Looks up `county_utility_defaults.json` |

### 8.6 Gas Sources (pipeline/sources/gas.py)

| Source Class | name | base_confidence | Logic |
|-------------|------|-----------------|-------|
| `StateGISGasSource` | `state_gis_gas` | 85 | Calls `lookup_gas_utility_gis(lat, lon, state)` from `gis_utility_lookup.py` |
| `MunicipalGasSource` | `municipal_gas` | 88 | Calls `lookup_municipal_gas(city, state)` from `municipal_utilities.py` |
| `ZIPMappingGasSource` | `zip_mapping_gas` | 75 | Uses state LDC ZIP mappings from `state_utility_verification.py` |
| `HIFLDGasSource` | `hifld_gas` | 58 | Queries HIFLD ArcGIS for gas distribution territory |
| `CountyDefaultGasSource` | `county_default_gas` | 50 | Looks up `county_utility_defaults.json` gas field |

**Gas-specific logic**: Propane detection — if ZIP is in known propane areas, returns "No natural gas service" response. Official gas utility validation checks against known gas utilities per state.

### 8.7 Water Sources (pipeline/sources/water.py)

| Source Class | name | base_confidence | Logic |
|-------------|------|-----------------|-------|
| `StateGISWaterSource` | `state_gis_water` | 85 | Calls `lookup_water_utility_gis(lat, lon, state)` |
| `MunicipalWaterSource` | `municipal_water` | 88 | Calls `lookup_municipal_water(city, state)` |
| `SpecialDistrictWaterSource` | `special_district_water` | 85 | Calls `lookup_special_district(lat, lon, state, 'water')` |
| `EPAWaterSource` | `epa_water` | 55 | EPA SDWIS lookup from `water_utility_lookup.json` |
| `CSVWaterSource` | `csv_water` | 70 | CSV curated water data via `csv_water_lookup.py` |
| `CountyDefaultWaterSource` | `county_default_water` | 50 | County defaults JSON |
| `TexasMUDSupplementalSource` | `texas_mud_supplemental` | 80 | Uses OpenAI to extract subdivision names, matches against TCEQ MUD data |

### 8.8 Corrections Source (pipeline/sources/corrections.py)

`UserCorrectionSource` — Highest priority source:
1. Queries Airtable corrections API by address/ZIP/city
2. Falls back to local JSON files (`electric_zip_corrections.json`, etc.)
3. Auto-verification: corrections with >= 3 user confirmations get `user_confirmed` source (score=95)
4. Single reports get `user_feedback` source (score=88)

### 8.9 AISelector (pipeline/ai_selector.py)

Uses OpenAI gpt-4o-mini to select the best utility from competing candidates:
- Input: Full `LookupContext` + all `SourceResult` objects + state knowledge from `state_utility_knowledge.json`
- Output: Selected utility name, confidence, reasoning
- Temperature: 0 (deterministic)
- Timeout: 5 seconds

### 8.10 SmartSelector (pipeline/smart_selector.py)

Fallback to AISelector with additional features:
- **Disk cache**: Caches OpenAI decisions to `data/smart_selector_cache.json` keyed by ZIP+state+candidates hash
- **Tenant context**: Incorporates tenant-verified data when available
- **Geographic context**: Uses geographic boundary data for additional signal
- **Rule-based fallback**: If OpenAI call fails, uses scoring:
  - Municipal match: +30
  - State GIS match: +25
  - Tenant match: +20
  - Cross-validation agreement: +15
  - EIA match: +10

### 8.11 Relationship to Older Code

The pipeline module and `utility_lookup_v1.py` **both run simultaneously**:

1. `utility_lookup_v1.py` is the primary orchestrator, called from `api.py`
2. The pipeline is called as **Priority 1** within `utility_lookup_v1.py`'s lookup chains via `_pipeline_lookup()`
3. If the pipeline returns a result, it's used. If not, `utility_lookup_v1.py` falls through to its own State GIS → Municipal → HIFLD → etc. chain
4. The pipeline wraps many of the same underlying functions (GIS lookups, municipal lookups) but adds AI-based selection and parallel execution
5. Pre-pipeline checks (corrections, tenant overrides, geographic boundaries) run BEFORE the pipeline is called
6. Post-pipeline enrichment (SERP verification, brand resolution, confidence scoring) is handled by `utility_lookup_v1.py` regardless of whether the pipeline was used

**Both systems are active in production.** The pipeline handles the multi-source orchestration and AI selection, while the older code provides the overall flow control, fallback chain, and post-processing.
