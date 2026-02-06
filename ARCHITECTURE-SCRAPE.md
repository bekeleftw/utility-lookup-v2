# Architecture Reference: utility-provider-scrape

> Exhaustive reference for rebuilding the analysis pipeline.
> Generated 2026-02-06 from full repository read.

---

## Table of Contents

1. [File Map](#1-file-map)
2. [Data Flow](#2-data-flow)
3. [Input Data](#3-input-data)
4. [Output Data](#4-output-data)
5. [Normalization Logic](#5-normalization-logic)
6. [Boundary Intelligence](#6-boundary-intelligence)
7. [Current State](#7-current-state)

---

## 1. File Map

### Root Directory

#### Core Application

| File | Description |
|------|-------------|
| `api.py` (2,651 lines) | Flask REST API server; main entry point for Railway deployment with `/api/lookup`, `/api/lookup/batch`, `/api/lookup/stream` endpoints. |
| `utility_lookup.py` (606 lines) | V2 pipeline-based lookup orchestrator; single entry point that geocodes once and fans out to electric/gas/water pipelines. |
| `utility_lookup_v1.py` (3,951 lines) | Legacy v1 lookup with all data source logic inline; still used for geocoding functions and as fallback. |
| `utility_lookup_currently_deployed.py` (3,676 lines) | Snapshot of the currently deployed version on Railway for reference/diffing. |

#### GIS & Geocoding

| File | Description |
|------|-------------|
| `gis_utility_lookup.py` (1,942 lines) | ArcGIS REST API integrations for water, electric, gas utilities across ~30 states. |
| `geocoding.py` | Multi-source geocoding with consensus voting (Census, Nominatim, Google, Smarty). |
| `water_gis_lookup.py` | Water-specific GIS lookups; thin wrapper around `gis_utility_lookup`. |
| `nj_utility_gis.py` | New Jersey-specific utility GIS queries. |

#### Utility Lookup Modules

| File | Description |
|------|-------------|
| `municipal_utilities.py` (993 lines) | City-owned utility database and lookups for electric, gas, water, trash, sewer with regional sub-lookups. |
| `special_districts.py` | MUD/CDD/Metro District lookup using Shapely point-in-polygon matching. |
| `deregulated_markets.py` (397 lines) | Deregulated electricity market handling for TX, PA, OH, IL, NY, NJ, MD, CT, MA, ME, NH, RI, DC, DE. |
| `rural_utilities.py` | Electric cooperative lookups by ZIP/county. |
| `sewer_lookup.py` (763 lines) | Sewer/wastewater utility lookup across TX PUC, CA, FL, CT, WA, NJ, MA, with HIFLD fallback. |
| `bdc_internet_lookup.py` | Internet provider lookup using local 55GB FCC BDC SQLite database. |
| `propane_service.py` | Propane service area detection. |
| `well_septic.py` | Private well and septic system detection. |
| `special_areas.py` | Special area detection logic. |
| `building_types.py` | Building type classification (residential, commercial, etc.). |

#### Verification & Validation

| File | Description |
|------|-------------|
| `serp_verification.py` (576 lines) | Google SERP verification via BrightData proxy + OpenAI analysis; includes 30-day file-based cache. |
| `cross_validation.py` | Multi-source cross-validation when data sources disagree; calculates agreement levels and confidence adjustments. |
| `state_utility_verification.py` (1,429 lines) | State-specific verification rules and problem area tracking. |
| `utility_website_verification.py` (3,783 lines) | Verifies utility websites are reachable and correct. |
| `browser_verification.py` (1,023 lines) | Playwright-based browser verification for utility service areas. |

#### Name Normalization & Resolution

| File | Description |
|------|-------------|
| `provider_normalizer.py` (457 lines) | Canonical provider name normalization with ~60 major provider alias mappings; pure in-memory, no file dependencies. |
| `name_normalizer.py` | Display name formatting (ALL CAPS to Title Case, "CITY OF X" inversion); uses GPT-4o-mini fallback. |
| `brand_resolver.py` (456 lines) | Maps legal/official names to consumer-facing brand names (e.g., "Wisconsin Electric Power Co" -> "WE Energies"). |
| `provider_id_matcher.py` (604 lines) | Matches provider names to IDs from `utility_providers_IDs.csv` using multi-strategy matching with OpenAI fallback. |
| `utility_name_normalizer.py` | Batch name normalization utilities. |
| `normalize_utility_names.py` | Batch normalization script. |
| `utility_normalization.py` | Additional normalization utilities. |

#### Confidence & Scoring

| File | Description |
|------|-------------|
| `confidence_scoring.py` | Confidence calculation with 5-tier source scoring (15-95), precision bonuses, SERP adjustments, and state data quality boosts. |
| `state_data_quality.py` | Adjusts confidence based on per-state data availability; 3-tier system (Excellent/Good/Federal Only). |
| `tenant_confidence_scorer.py` | Scores confidence of tenant-verified utility data. |

#### Address Processing

| File | Description |
|------|-------------|
| `address_normalization.py` | USPS-standard address formatting with street type abbreviations, directionals, and unit parsing. |
| `address_cache.py` | Caching layer for address lookups. |
| `address_inference.py` | Infers missing address components from partial data. |

#### Boundary Analysis

| File | Description |
|------|-------------|
| `boundary_lookup.py` (480 lines) | Predicts utility provider for new addresses using collected boundary data points; nearest-neighbor and split-line methods. |
| `boundary_mapper.py` | Builds granular utility territory maps from disagreement data; clusters points and identifies sub-ZIP territories. |
| `boundary_resolver.py` | Resolves boundary conflicts between data sources. |
| `utility_boundary_learner.py` (457 lines) | Learns utility boundaries from accumulated data points. |
| `ai_boundary_analyzer.py` (413 lines) | AI-powered boundary analysis using OpenAI. |
| `ai_boundary_analyzer_concurrent.py` | Concurrent version of AI boundary analysis. |
| `geographic_boundary_analyzer.py` (442 lines) | Geographic boundary analysis using coordinates. |
| `geographic_boundary_lookup.py` | Geographic boundary lookup by lat/lon. |

#### Mismatch Analysis

| File | Description |
|------|-------------|
| `mismatch_analyzer.py` (564 lines) | Resolves CSV vs API provider disagreements using SERP + OpenAI; stores resolved points for boundary mapping. |
| `mismatch_analyzer_fast.py` | Optimized mismatch analysis for bulk processing. |
| `build_accuracy_checker.py` | Reads `*comparison*.json` files and outputs `provider_aliases.json`, `boundary_issues.json`, and `accuracy_report.md`. |
| `batch_analyzer.py` (593 lines) | Batch analysis runner for large comparison datasets. |
| `verify_provider_disputes.py` | Verifies and resolves provider dispute cases. |

#### Comparison & Testing Scripts

| File | Description |
|------|-------------|
| `run_full_api_comparison.py` | Runs full API comparison against mapped provider data. |
| `run_massive_comparison.py` | Large-scale comparison runner. |
| `run_stratified_analysis.py` | Stratified sampling analysis runner. |
| `run_targeted_comparison.py` | Targeted comparison for specific mismatch subsets. |
| `run_google_geocode_comparison.py` | Compares geocoding results across providers. |
| `quick_analysis.py` | Quick analysis utilities. |

#### Tenant Verification

| File | Description |
|------|-------------|
| `tenant_verified_lookup.py` | Looks up utilities from tenant-verified data. |
| `tenant_override_lookup.py` | Applies tenant override data to lookups. |
| `build_tenant_overrides.py` | Builds tenant override datasets from verified data. |
| `analyze_tenant_verification.py` | Analyzes tenant verification data quality. |
| `generate_tenant_rules.py` | Generates tenant verification rules. |
| `geocode_tenant_addresses.py` | Geocodes tenant addresses for boundary mapping. |

#### External Data Sources

| File | Description |
|------|-------------|
| `findenergy_lookup.py` (668 lines) | FindEnergy.com integration for electric provider lookups. |
| `findenergy_bulk_collect.py` | Bulk collection from FindEnergy.com. |
| `broadbandnow_lookup.py` | BroadbandNow.com integration for internet providers. |
| `allconnect_lookup.py` | AllConnect.com integration. |
| `epa_echo_lookup.py` | EPA ECHO database lookup for water systems. |
| `combined_internet_lookup.py` | Combines multiple internet provider sources. |
| `csv_utility_lookup.py` | CSV-based utility lookup from mapped provider data. |
| `csv_water_lookup.py` | CSV-based water utility lookup. |

#### User Corrections

| File | Description |
|------|-------------|
| `corrections_lookup.py` (524 lines) | SQLite-based user corrections system with verification workflow (3+ confirmations = auto-verify). |

#### Data Export (FCC BDC -> PostgreSQL)

| File | Description |
|------|-------------|
| `export_to_postgres.py` | Exports FCC BDC data from SQLite to PostgreSQL; multiprocessing, Fiber/Cable only. |
| `export_to_postgres_robust.py` | Robust version with better error handling. |
| `export_streamed.py` | Streamed export with checkpointing. |
| `export_concurrent.py` | Concurrent export implementation. |
| `export_fast.py` | Fast export implementation. |
| `export_light.py` | Lightweight export. |
| `export_dsl_satellite.py` | Exports DSL and satellite data. |
| `export_dsl_sat_concurrent.py` | Concurrent DSL/satellite export. |

#### Other Modules

| File | Description |
|------|-------------|
| `bulk_lookup.py` (391 lines) | Bulk address lookup runner. |
| `utility_auth.py` (600 lines) | API key authentication (bcrypt + JWT). |
| `utility_scrapers.py` (486 lines) | Web scrapers for utility company websites. |
| `utility_directory.py` | Utility company directory lookups. |
| `utility_direct_lookup.py` (430 lines) | Direct utility website lookups. |
| `ml_enhancements.py` | ML-based prediction enhancements. |
| `water_reconciler.py` (375 lines) | Reconciles water utility data from multiple sources. |
| `enrich_utility_websites.py` | Enriches utility data with website URLs. |
| `validate_websites.py` | Validates utility website availability. |
| `build_water_lookup.py` | Builds the water utility lookup JSON from EPA SDWIS data. |
| `build_name_mappings.py` | Builds name mapping datasets. |
| `deregulated_market_handler.py` | Handles deregulated market edge cases. |
| `logging_config.py` | Centralized logging with JSON and console formatters. |

#### Test Files

| File | Description |
|------|-------------|
| `test_addresses.py` (958 lines) | Automated test suite with 53+ test addresses across multiple states. |
| `test_gis_apis.py` | Tests for GIS API integrations. |
| `test_batch_100.py` | Batch testing of first 100 comparison results. |

#### Config & Deployment

| File | Description |
|------|-------------|
| `requirements.txt` | Python dependencies: flask, requests, beautifulsoup4, playwright, shapely, psycopg2, weasyprint, redis, rq, boto3. |
| `Dockerfile` | Railway deployment; Playwright Python image with Xvfb, gunicorn on port 8080. |
| `.env` | Environment variables (API keys, proxy config). |
| `.gitignore` | Git ignore rules. |
| `run_export.sh` | Shell script for FCC BDC export with caffeinate and auto-restart. |
| `run_export_loop.sh` | Looping export script. |

### `pipeline/` - Modular Pipeline Architecture

| File | Description |
|------|-------------|
| `__init__.py` | Package init. |
| `interfaces.py` | Core interfaces: `UtilityType` enum, `LookupContext`/`SourceResult`/`PipelineResult` dataclasses, `DataSource` ABC, `SOURCE_CONFIDENCE` dict, `PRECISION_BONUS` dict. |
| `pipeline.py` | Main orchestrator: parallel source queries, cross-validation, AI/SmartSelector/rule-based selection, SERP verification. |
| `smart_selector.py` | SmartSelector for tie-breaking when sources disagree (legacy, used when AI selector unavailable). |
| `ai_selector.py` | AI-powered selector using OpenAI for utility selection; evaluates all candidates with full context. |

### `pipeline/sources/` - Data Source Implementations

| File | Description |
|------|-------------|
| `__init__.py` | Package init. |
| `electric.py` | Electric sources: StateGIS (85), Municipal (88), Coop (68), EIA (70), HIFLD (58), CountyDefault (50), TenantVerified. |
| `gas.py` | Gas sources: StateGIS (85), Municipal (88), ZIPMapping (50), HIFLD (58), CountyDefault (50), TenantVerified; includes propane detection. |
| `water.py` | Water sources: Municipal (88), StateGIS (85), SpecialDistrict (85), EPA (55), CountyDefault (50), TenantVerified. |
| `corrections.py` | UserCorrectionSource (95) - highest priority override from user feedback. |
| `correction_verifier.py` | Verifies user correction validity. |
| `georgia_emc.py` | Georgia Electric Membership Cooperatives source. |

### `guide/` - Resident Guide (PDF Generation)

| File | Description |
|------|-------------|
| `__init__.py` | Package init. |
| `guide_api.py` | Flask Blueprint for `/api/guide/request` endpoint. |
| `job_processor.py` | Background job processing (Redis/RQ) for PDF generation. |
| `pdf_generator.py` | WeasyPrint-based PDF generation. |
| `instruction_extraction.py` | Extracts utility setup instructions from websites. |
| `logo_retrieval.py` | Retrieves utility company logos. |
| `fallback_templates.py` | Fallback templates when data unavailable. |
| `deregulated_explainers.py` | Explainer content for deregulated markets. |

### `scripts/` - Utility Scripts

| File | Description |
|------|-------------|
| `validate_accuracy.py` | Validates lookup accuracy against known data. |
| `accuracy_monitor.py` | Monitors accuracy over time. |
| `audit_with_serp.py` | Audits results using SERP verification. |
| `benchmark_current.py` | Benchmarks current implementation performance. |
| `ab_test_runner.py` | Runs A/B tests between pipeline versions. |
| `analyze_data_gaps.py` | Analyzes coverage gaps by state/utility type. |
| `build_zip_index.py` | Builds ZIP code index for fast lookups. |
| `build_eia_zip_lookup.py` | Builds EIA Form 861 ZIP-to-utility mapping. |
| `download_tceq_data.py` | Downloads Texas TCEQ special district data. |
| `download_florida_cdds.py` | Downloads Florida CDD boundary data. |
| `ingest_special_districts.py` | Ingests and processes special district data. |
| `migrate_hardcoded_dicts.py` | Migrates hardcoded Python dictionaries to JSON files. |
| `validate_data.py` | Validates data file integrity. |

### `tests/` - Test Suites

| File | Description |
|------|-------------|
| `run_golden_tests.py` | Runs golden test suite against known-good results. |
| `run_pipeline_tests.py` | Pipeline-specific tests. |
| `test_current_behavior.py` | Tests current behavior for regression detection. |
| `test_regression_v2.py` | Regression tests for the v2 pipeline. |
| `snapshots/` | Test snapshot data for golden tests. |

### `monitoring/` - Metrics

| File | Description |
|------|-------------|
| `__init__.py` | Package init. |
| `metrics.py` | Performance metrics tracking (`track_lookup`, `LookupTimer`). |

### `docs/` - Documentation

| File | Description |
|------|-------------|
| `AUTH_SETUP.md` | API key authentication setup guide. |
| `MIGRATION_GUIDE.md` | Migration guide from v1 to v2 pipeline. |
| `austin-utilities-guide.md` | Austin, TX utilities reference. |
| `comprehensive_audit_report.md` | Full audit of lookup accuracy. |
| `external_dependencies.md` | External API dependencies documentation. |
| `tenant-verification-*.md` | Tenant verification system documentation (5 files). |
| `utility_data_audit_summary.md` | Utility data audit summary. |
| `utility_normalization_summary.md` | Name normalization summary. |

### `utility_specs/` - System Specifications

| File | Description |
|------|-------------|
| `00_IMPLEMENTATION_ROADMAP.md` | Overall implementation roadmap. |
| `01_USER_FEEDBACK_SYSTEM.md` | User feedback system spec. |
| `02_CONFIDENCE_SCORING.md` | Confidence scoring spec. |
| `03_PROBLEM_AREAS_REGISTRY.md` | Problem areas registry spec. |
| `04_SPECIAL_DISTRICTS_ALL_STATES.md` | Special districts nationwide spec. |
| `05_SPECIAL_DISTRICT_IMPLEMENTATION.md` | Special district implementation details. |
| `06_UTILITY_API_SCRAPERS.md` | Utility API scraper spec. |
| `07_CROSS_VALIDATION.md` | Cross-validation spec. |
| `08_BATCH_VALIDATION.md` | Batch validation spec. |

### `static/` - Frontend Widgets

| File | Description |
|------|-------------|
| `widget.js` (~37KB) | Webflow utility lookup widget for end users. |
| `widget_pm.js` (~22KB) | Property manager variant of the widget. |
| `utility-auth.js` (~4KB) | Authentication JavaScript. |

### `public/js/`

| File | Description |
|------|-------------|
| `utility-lookup-leadgen.js` | Lead generation variant of the lookup widget. |

### `webflow_embeds/` - Webflow Integration

| File | Description |
|------|-------------|
| `1_auth_css.html` through `5_stats_dashboard.html` | HTML embed snippets for Webflow site integration. |
| `README.md` | Webflow embed documentation. |

### Empty/Placeholder Directories

| Directory | Intended Purpose |
|-----------|-----------------|
| `data/ab_tests/` | A/B test results storage. |
| `data/electric_puds/` | Public Utility District data. |
| `data/feedback/` | User feedback data. |
| `data/findenergy/` | FindEnergy.com cached data. |
| `data/franchise_agreements/` | Utility franchise agreement data. |
| `data/gas_mappings/` | Gas utility mapping data. |
| `data/metrics/` | Performance metrics storage. |
| `data/serp_cache/` | SERP query result cache. |
| `data/smart_selector_cache/` | Smart selector algorithm cache. |
| `data/special_districts/processed/` | Processed special district data. |
| `data/special_districts/raw/texas/` | Raw Texas district data. |
| `data/special_districts/raw/florida/` | Raw Florida district data. |
| `data/utility_directory/` | Utility company directory. |
| `SDWA_latest_downloads/` | Safe Drinking Water Act data. |
| `bdc_downloads/` | FCC Broadband Data Collection downloads. |
| `schemas/` | Data schema definitions. |
| `utility_gis_data/` | GIS data files (states: indiana, minnesota). |
| `water_gis_data/` | Water GIS data (federal Census tables). |

### Root-Level Markdown Documentation

| File | Description |
|------|-------------|
| `CODEBASE_OVERVIEW.md` | Comprehensive codebase overview for AI assistants. |
| `DEVELOPER_HANDOFF.md` | Developer handoff guide with quick start. |
| `DATA_FILES.md` | Data file inventory and sourcing. |
| `BACKEND_SPEC.md` | Backend specification (untracked). |
| `COMPARISON_ANALYSIS.md` | Analysis of API vs CSV comparison results. |
| `accuracy_report.md` | Auto-generated accuracy report from `build_accuracy_checker.py`. |
| `REFACTOR_PROPOSAL.md` | v1 to v2 refactoring proposal. |
| `REFACTORING_*.md` (4 files) | Refactoring assessment, plan, summary, completion notes. |
| `DEPLOYMENT_VERIFICATION.md` | Deployment verification checklist. |
| `PRODUCTION_DEPLOYMENT_CHECKLIST.md` | Production deployment checklist. |
| `GIS_API_STATUS_SUMMARY.md` | Status of GIS API integrations by state. |
| `*_gis_inventory.md` (3 files) | GIS inventory for water, electric, gas APIs. |
| `GAS_UTILITY_EXPANSION_NOTES.md` | Gas utility data expansion notes. |
| `SEWER_API_STATUS.md` | Sewer API integration status. |
| `sewer-*.md` (2 files) | Sewer lookup and GIS coverage specs. |
| `MAPPED_PROVIDERS_*.md` (2 files) | Mapped providers analysis and implementation plans. |
| `PHASE_IMPLEMENTATION_SUMMARY.md` | Implementation phase summary. |
| `nationwide_reliability_improvements.md` | Nationwide reliability improvement notes. |
| `PROVIDER_VERIFICATION_GUIDE.md` | Provider verification methodology guide. |

---

## 2. Data Flow

### End-to-End Batch Analysis Run

```
INPUT: Address list (CSV or JSON)
  e.g., "123 Main St, Austin, TX 78701"
       │
       ▼
┌──────────────────────────────────────────────────────┐
│  Step 1: GEOCODING (utility_lookup_v1.py)            │
│                                                      │
│  Priority chain:                                     │
│    Census Bureau API → Google Geocoding API →         │
│    Nominatim (OSM) → City Centroid → ZIP Centroid    │
│                                                      │
│  Output: { lat, lon, city, county, state, zip_code } │
└──────────────────────┬───────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────┐
│  Step 2: PIPELINE DISPATCH (utility_lookup.py)       │
│                                                      │
│  Creates LookupContext from geocode result.           │
│  Dispatches to 3 independent pipelines:               │
│    _get_electric_pipeline()                           │
│    _get_gas_pipeline()                                │
│    _get_water_pipeline()                              │
└──────────────────────┬───────────────────────────────┘
                       │
          ┌────────────┼────────────┐
          ▼            ▼            ▼
┌─────────────┐ ┌───────────┐ ┌───────────┐
│  ELECTRIC   │ │    GAS    │ │   WATER   │
│  Pipeline   │ │  Pipeline │ │  Pipeline │
└──────┬──────┘ └─────┬─────┘ └─────┬─────┘
       │              │              │
       ▼              ▼              ▼
┌──────────────────────────────────────────────────────┐
│  Step 3: PARALLEL SOURCE QUERIES (pipeline.py)       │
│                                                      │
│  ThreadPoolExecutor (max_workers=5) queries all       │
│  sources simultaneously. Short-circuits on 95+        │
│  confidence. 3-second global timeout.                 │
│                                                      │
│  ELECTRIC sources (in priority order):                │
│    UserCorrections (95) → Municipal (88) →            │
│    StateGIS (85) → Coop (68) → GeorgiaEMC →          │
│    TenantVerified → EIA (70) → HIFLD (58) →          │
│    CountyDefault (50)                                 │
│                                                      │
│  GAS sources:                                         │
│    UserCorrections (95) → Municipal (88) →            │
│    StateGIS (85) → TenantVerified →                   │
│    ZIPMapping (50) → HIFLD (58) →                     │
│    CountyDefault (50)                                 │
│                                                      │
│  WATER sources:                                       │
│    UserCorrections (95) → Municipal (88) →            │
│    StateGIS (85) → SpecialDistrict (85) →             │
│    TenantVerified → EPA (55) →                        │
│    CountyDefault (50)                                 │
└──────────────────────┬───────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────┐
│  Step 4: CROSS-VALIDATION (pipeline.py)              │
│                                                      │
│  Groups results by normalized utility name.           │
│  Uses serp_verification.normalize_utility_name()      │
│  and is_alias() for fuzzy matching.                   │
│                                                      │
│  Confidence adjustments:                              │
│    All agree:      +20                                │
│    Majority agree: +10                                │
│    Split:          -10                                │
└──────────────────────┬───────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────┐
│  Step 5: RESULT SELECTION (pipeline.py)               │
│                                                      │
│  Decision chain:                                      │
│    1. Municipal match >= 85 confidence? Use it.       │
│       (Skips AI to save 2-5 seconds)                  │
│    2. AI Selector available + >1 result?              │
│       → OpenAI evaluates all candidates with context  │
│    3. SmartSelector (legacy fallback for ties)         │
│    4. Rule-based: score = confidence + precision_bonus │
│       + (source_priority * 0.3) + CV_adjustment       │
└──────────────────────┬───────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────┐
│  Step 6: ENRICHMENT (pipeline.py → brand_resolver)   │
│                                                      │
│  - Brand resolution: legal name → consumer brand      │
│    ("Wisconsin Electric Power Co" → "WE Energies")    │
│  - Deregulated market detection (TX, PA, OH, etc.)    │
│  - Deregulated note generation                        │
└──────────────────────┬───────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────┐
│  Step 7: SERP VERIFICATION (conditional)             │
│                                                      │
│  Triggered ONLY when:                                 │
│    sources_agreed == false AND                        │
│    disagreeing_sources >= agreeing_sources             │
│                                                      │
│  Process:                                             │
│    1. Query Google via BrightData proxy               │
│    2. Analyze results with OpenAI or regex fallback   │
│    3. If SERP confirms: +15 confidence, level=verified│
│    4. If SERP matches a disagreeing source:           │
│       switch to that source, confidence=85            │
│    5. If SERP finds unknown provider: keep original   │
│       (curated sources > web scraping)                │
│                                                      │
│  Cached in data/serp_cache/ for 30 days               │
└──────────────────────┬───────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────┐
│  Step 8: RESPONSE FORMATTING (utility_lookup.py)     │
│                                                      │
│  Builds response dict with:                           │
│    NAME, TELEPHONE, WEBSITE, STATE, CITY              │
│    _confidence, _confidence_score, _source            │
│    _legal_name, _sources_agreed                       │
│    _agreeing_sources, _disagreeing_sources             │
│    _other_providers (top 3 alternatives)              │
│    _deregulated_market, _deregulated_note             │
│    _serp_verified, _timing_ms                         │
└──────────────────────┬───────────────────────────────┘
                       │
                       ▼
OUTPUT: JSON response per address
```

### Batch Comparison Flow (Accuracy Analysis)

```
INPUT: stratified_sample_140k.json (mapped provider data from partner)
       │
       ▼
┌──────────────────────────────────────────────────┐
│  run_stratified_analysis.py / run_targeted_comparison.py │
│                                                  │
│  For each address:                               │
│    1. Call lookup_utilities_by_address()          │
│    2. Compare API result vs CSV mapped provider   │
│    3. Record: exact match / alias match / mismatch│
└──────────────────────┬───────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────┐
│  stratified_comparison_140k.json                 │
│  targeted_comparison_74k.json                    │
│                                                  │
│  Schema: { address, zip,                         │
│    mapped_electric, mapped_gas, mapped_water,    │
│    api_electric, api_gas, api_water, success }   │
└──────────────────────┬───────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────┐
│  build_accuracy_checker.py                       │
│                                                  │
│  Reads all *comparison*.json files.              │
│  Classifies: exact / alias / mismatch            │
│  Outputs:                                        │
│    - provider_aliases.json (alias mappings)       │
│    - boundary_issues.json (problem ZIPs)          │
│    - accuracy_report.md (metrics)                 │
└──────────────────────┬───────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────┐
│  mismatch_analyzer.py                            │
│                                                  │
│  For each mismatch:                              │
│    1. Geocode address (if not already)            │
│    2. SERP search for utility                     │
│    3. AI analysis (csv vs api vs serp)            │
│    4. Resolve to correct provider                 │
│    5. Save to data/mismatch_cache/                │
│    6. Save boundary point to boundary_points.json │
└──────────────────────┬───────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────┐
│  BOUNDARY INTELLIGENCE                           │
│                                                  │
│  boundary_points.json accumulates resolved       │
│  conflict points. boundary_lookup.py and         │
│  boundary_mapper.py use these to predict          │
│  providers in boundary ZIP codes.                 │
└──────────────────────────────────────────────────┘
```

---

## 3. Input Data

### Large Data Files (Not in Git, Required at Runtime)

| File | Size | Rows (approx) | Schema | Used By |
|------|------|----------------|--------|---------|
| `bdc_internet_new.db` | ~55 GB | Millions | SQLite; FCC BDC broadband availability by census block | `bdc_internet_lookup.py` |
| `All mapped providers.csv` | ~89 MB | ~500K+ | Address, Internet, Electricity, Gas, Water, Sewer, Trash, zip, Partner Name/Slug, UPT Link | `csv_utility_lookup.py`, comparison scripts |
| `regulated_addresses.csv` | ~63 MB | ~300K+ | Addresses in regulated utility markets | Comparison/testing scripts |
| `deregulated_addresses.csv` | ~27 MB | ~130K+ | Addresses in deregulated markets | Comparison/testing scripts |
| `addresses_with_tenant_verification.csv` | ~7.7 MB | ~30K+ | Address, verified utility providers from tenants | `tenant_verified_lookup.py`, override building |
| `utility_providers_IDs.csv` | — | ~5K+ | id, title, utility_type (2=electric, 3=water, 4=gas, 5=trash, 6=sewer) | `provider_id_matcher.py` |

### Comparison/Analysis Files (In Git)

| File | Size | Rows | Schema | Purpose |
|------|------|------|--------|---------|
| `stratified_sample_140k.json` | 75 MB | ~140K | Partner Name/Slug, UPT Link, Address, Internet, Electricity, Gas, Water, Sewer, Trash, zip | Source of truth from partner data for comparison |
| `stratified_comparison_140k.json` | 45 MB | ~140K | address, zip, mapped_electric/gas/water, api_electric/gas/water, success | Comparison results: mapped vs API lookups |
| `targeted_comparison_74k.json` | 11 MB | ~74K | Same as above | Subset of comparisons targeting mismatches |
| `mismatches_for_analysis.json` | 16 MB | ~24K+ | Addresses where mapped != API result | Queue for mismatch analyzer |
| `mismatches_overnight.json` | 7.9 MB | ~24K | Same as above | Overnight mismatch analysis batch |

### Municipal/Regional Water Data (Expected in `data/`)

These JSON files are loaded by `municipal_utilities.py`:

| File | Purpose |
|------|---------|
| `data/municipal_utilities.json` | Master municipal utility database (city -> services) |
| `data/long_island_water_districts.json` | Long Island, NY water districts by ZIP |
| `data/socal_water_districts.json` | Southern California water districts by ZIP |
| `data/dfw_water_districts.json` | Dallas-Fort Worth water districts by ZIP |
| `data/houston_water_districts.json` | Houston area water districts by ZIP |
| `data/philly_water_districts.json` | Philadelphia metro water districts |
| `data/dc_water_districts.json` | DC metro water districts |
| `data/atlanta_water_districts.json` | Atlanta area water districts |
| `data/florida_water_districts.json` | Florida water districts |
| `data/remaining_states_water.json` | Tenant-verified water data for remaining states |
| `data/remaining_states_electric.json` | Tenant-verified electric data for remaining states |
| `data/remaining_states_gas.json` | Tenant-verified gas data for remaining states |

### Other Expected Data Files

| File | Purpose |
|------|---------|
| `data/eia_zip_utility_lookup.json` | EIA Form 861 ZIP-to-electric-utility mapping |
| `data/water_utility_lookup.json` | EPA SDWIS water utility data |
| `data/official_gas_utilities.json` | Official gas utility registry by state |
| `data/eia_176_companies.json` | EIA Form 176 gas companies by state |
| `data/service_check_urls.json` | Utility service verification URLs |
| `data/api_keys.json` | API key storage |
| `data/provider_name_mappings.json` | OpenAI-generated provider name mappings |
| `data/provider_simple_lookup.json` | Simplified provider name lookup |
| `data/provider_match_cache.json` | Persistent provider match cache |
| `data/canonical_provider_ids.json` | Canonical provider ID mappings (dedup) |
| `data/name_normalization_cache.json` | Cached name normalization results |
| `data/cross_validation_disagreements.json` | Logged cross-validation disagreements |
| `data/corrections.db` | SQLite database for user corrections |

### Email Bounce Data (In Git, Not Part of Pipeline)

| File | Rows | Purpose |
|------|------|---------|
| `All Verified PM Contacts - Sheet1-bouncer-results.csv` | ~50K | Property manager email bounce verification |
| `ALL_emails_for_bounce_check-bouncer-results (1).csv` | ~11K | Email bounce check results |
| `IMPORT_generated_emails_FINAL-bouncer-results (1).csv` | ~4K | Generated emails bounce results |

---

## 4. Output Data

### API Response Format

The primary output is the JSON response from `/api/lookup`. This is what the API repo consumes.

```json
{
  "address": "123 Main St, Austin, TX 78701",
  "location": {
    "lat": 30.2672,
    "lon": -97.7431,
    "city": "Austin",
    "county": "Travis",
    "state": "TX",
    "zip_code": "78701"
  },
  "utilities": {
    "electric": [
      {
        "name": "Austin Energy",
        "phone": "(512) 494-9400",
        "website": "https://austinenergy.com",
        "confidence": "verified",
        "confidence_score": 95,
        "source": "municipal",
        "legal_name": "City of Austin dba Austin Energy",
        "verification_source": "municipal",
        "sources_agreed": true,
        "agreeing_sources": ["municipal", "state_gis"],
        "deregulated_market": true,
        "deregulated_note": "Texas uses ERCOT...",
        "other_providers": []
      }
    ],
    "gas": [...],
    "water": [...],
    "internet": {...},
    "sewer": {...},
    "trash": {...}
  },
  "_version": "v2",
  "_total_time_ms": 450
}
```

### Key Fields in Each Utility Object

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Consumer-facing brand name |
| `phone` | string | Primary phone number |
| `website` | string | Utility website URL |
| `confidence` | string | `verified`, `high`, `medium`, `low` |
| `confidence_score` | int | 0-100 numeric score |
| `source` | string | Data source that provided this result |
| `legal_name` | string | Official/legal name (may differ from brand) |
| `provider_id` | string | ID from `utility_providers_IDs.csv` (for API repo database) |
| `sources_agreed` | bool | Whether all queried sources agree |
| `agreeing_sources` | list | Source names that returned the same provider |
| `disagreeing_sources` | list | Source names that returned different providers |
| `other_providers` | list | Up to 3 alternative providers from other sources |
| `deregulated_market` | bool | Whether this is a deregulated market |
| `deregulated_note` | string | Explanation of deregulation for this state |
| `serp_verified` | bool | Whether SERP verification was run and confirmed |

### Analysis Output Files

| File | Format | Schema | Purpose |
|------|--------|--------|---------|
| `provider_aliases.json` | JSON | `{ "Canonical Name": ["alias1", "alias2", ...] }` | Generated by `build_accuracy_checker.py`; consumed by normalization |
| `boundary_issues.json` | JSON | `{ "ZIP": { count, electric_conflicts, gas_conflicts } }` | Problem ZIPs where sources disagree |
| `accuracy_report.md` | Markdown | Stats: total, exact/alias/mismatch counts for electric/gas | Human-readable accuracy metrics |
| `data/boundary_points.json` | JSON | Array of resolved conflict points (see Boundary Intelligence section) | Feeds boundary prediction |
| `batch_results_*.json` | JSON | Array of mismatch analysis results with timestamps | Batch processing outputs |
| `serp_test_100.json` | JSON | 100 SERP verification test results | SERP verification validation |

### Provider ID Matching Output

The `provider_id_matcher.py` returns match results used by the API to link providers to the database:

```json
{
  "id": "12345",
  "title": "Austin Energy",
  "matched_via": "csv_exact",
  "match_score": 1.0
}
```

Matching strategies in priority order:
1. Canonical ID mappings (resolves duplicates/parent companies)
2. Persistent cache
3. OpenAI-generated mappings (fuzzy)
4. Simple normalized lookup
5. CSV exact match by utility type
6. Partial string matching (>80%)
7. Aggressive normalization
8. OpenAI API fallback

---

## 5. Normalization Logic

### provider_normalizer.py

**Location:** `provider_normalizer.py` (457 lines)

**Purpose:** Resolves different names for the same utility to a single canonical form.

**How it works:**

1. **`PROVIDER_ALIASES` dictionary** maps ~60 canonical provider names to their known aliases. Examples:
   - `"Pacific Gas & Electric"` -> `["PG&E", "PGE", "Pacific Gas & Electric Company", ...]`
   - `"ComEd"` -> `["Commonwealth Edison", "Commonwealth Edison Co.", "Comed"]`
   - `"CenterPoint Energy"` -> `["Centerpoint Energy", "CenterPoint Energy Houston Electric", ...]`

2. **`_ALIAS_TO_CANONICAL` reverse lookup** is auto-generated at module load:
   ```python
   for canonical, aliases in PROVIDER_ALIASES.items():
       _ALIAS_TO_CANONICAL[canonical.lower()] = canonical
       for alias in aliases:
           _ALIAS_TO_CANONICAL[alias.lower()] = canonical
   ```

3. **`normalize_provider(name)`** resolution order:
   - Clean whitespace
   - Direct lowercase lookup in `_ALIAS_TO_CANONICAL`
   - Substring matching: checks if any alias is contained in the input or vice versa
   - Returns cleaned original if no match

4. **`providers_match(name1, name2)`** normalizes both names and checks:
   - Exact canonical match
   - Substring containment of normalized forms

**Coverage by state:** CA, OR, TX, IL, GA, FL, MD, OH, PA, NJ, MI, CO, IN, IA, NC, WA, AZ, NV, UT, MO, KY, WI, MN, OK, VA, NY, MA, CT, RI, TN, AL

**No external file dependencies.** All mappings are hardcoded in the module.

### Related: provider_aliases.json

**Location:** `provider_aliases.json` (73KB, ~400 providers)

**Generated by:** `build_accuracy_checker.py` from comparison analysis

**Schema:** Same as `PROVIDER_ALIASES` but much larger (~400 vs ~60 entries). This is the auto-discovered alias list from comparing API results to CSV mapped data.

**Used by:** `serp_verification.py` (has its own `UTILITY_ALIASES` dict with ~100 entries) and `cross_validation.py` (has its own `PROVIDER_ALIASES` dict).

### Normalization Chain in Practice

There are **multiple independent normalization systems** that are not unified:

| Module | Dict Name | Coverage | Used For |
|--------|-----------|----------|----------|
| `provider_normalizer.py` | `PROVIDER_ALIASES` | ~60 providers | `mismatch_analyzer.py`, general matching |
| `serp_verification.py` | `UTILITY_ALIASES` | ~100 providers | SERP result matching, cross-validation in pipeline |
| `cross_validation.py` | `PROVIDER_ALIASES` | ~20 providers | Cross-validation name grouping |
| `provider_aliases.json` | — | ~400 providers | Auto-generated from comparison data |
| `name_normalizer.py` | — | Rules-based + GPT-4o-mini | Display formatting only |
| `brand_resolver.py` | `COMMON_BRAND_MAPPINGS` | ~30 providers | Legal-to-brand name mapping |

---

## 6. Boundary Intelligence

### Overview

The boundary intelligence system resolves utility provider assignments in ZIP codes where multiple providers serve different areas. It works by accumulating resolved data points (from SERP/AI mismatch analysis) and using them to predict the correct provider for new addresses based on geographic proximity.

### boundary_lookup.py (480 lines)

**Purpose:** Predicts the correct utility provider for a given lat/lon coordinate using accumulated boundary data.

**Data dependency:** `data/boundary_points.json`

**Key functions:**

- **`lookup_with_boundary_data(lat, lon, zip_code, csv_provider, api_provider)`** - Main entry point
- **`add_resolved_point(...)`** - Adds a new resolved data point after mismatch analysis
- **`get_boundary_confidence(zip_code)`** - Returns boundary analysis for a ZIP
- **`get_zip_summary(zip_code)`** - Detailed summary of data in a ZIP

**Prediction methods (in priority order):**

1. **Split line + nearest neighbor agree:** ZIP has a known geographic split (e.g., east/west at longitude -84.01). Both the split line and the 5 nearest resolved points agree. **Confidence: high.**
2. **Nearest neighbor only:** The 5 nearest resolved points agree on a provider. **Confidence: medium.**
3. **Split line only:** The coordinate falls clearly on one side of a known split. **Confidence: medium.**
4. **Single provider ZIP:** All resolved points in the ZIP show the same provider. **Confidence: high.**
5. **No data:** No resolved points exist for this ZIP. **Confidence: none.**

**Index structure** (built per ZIP):

```json
{
  "90210": {
    "is_boundary": true,
    "providers": ["Provider A", "Provider B"],
    "point_count": 10,
    "split_direction": "east_west",
    "split_line": -118.4,
    "provider_regions": { "Provider A": "west", "Provider B": "east" },
    "centroids": {
      "Provider A": { "lat": 34.07, "lon": -118.42 },
      "Provider B": { "lat": 34.08, "lon": -118.38 }
    }
  }
}
```

**Output format:**

```json
{
  "predicted_provider": "Austin Energy",
  "confidence": "high",
  "method": "split_line_and_nn_agree",
  "point_count": 15,
  "split_direction": "east_west",
  "region": "west",
  "nearest_points": 5,
  "nn_provider": "Austin Energy",
  "nn_confidence": 0.95
}
```

### boundary_mapper.py

**Purpose:** Builds granular utility territory maps from disagreement data points.

**Data dependency:** `data/boundary_points.json`

**Key functions:**

- **`get_provider_territory(zip_code)`** - Analyzes provider territories within a ZIP
- **`lookup_provider_for_location(lat, lon, zip_code=None)`** - Looks up provider for a coordinate
- **`export_boundary_geojson(output_file)`** - Exports data as GeoJSON for mapping
- **`get_zips_needing_data()`** - Identifies ZIPs that need more data points

**Output format (territory analysis):**

```json
{
  "zip": "75068",
  "status": "multi_provider",
  "providers": ["Oncor", "CoServ"],
  "point_count": 12,
  "split_direction": "north_south",
  "centroid_distance_km": 3.2,
  "territories": {
    "Oncor": {
      "point_count": 7,
      "centroid": { "lat": 33.16, "lon": -96.93 },
      "bounds": { "min_lat": 33.14, "max_lat": 33.18, "min_lon": -96.95, "max_lon": -96.91 },
      "spread_km": 4.1,
      "sample_addresses": ["123 Main St", "..."],
      "region": "south"
    },
    "CoServ": { "..." }
  }
}
```

### data/boundary_points.json

**Size:** 242KB, ~500+ entries

**Schema per entry:**

```json
{
  "lat": 33.123,
  "lon": -96.789,
  "zip": "75068",
  "city": "Little Elm",
  "state": "TX",
  "county": "Denton",
  "utility_type": "electric",
  "csv_provider": "CoServ Electric",
  "api_provider": "Oncor Electric Delivery",
  "resolved_provider": "CoServ Electric",
  "confidence": "high",
  "source": "ai_confirms_csv",
  "analyzed_at": "2026-02-04T12:30:00Z",
  "address": "1401 Thrasher Dr, Little Elm, TX 75068"
}
```

**Written by:** `mismatch_analyzer.py` after resolving a CSV-vs-API disagreement
**Read by:** `boundary_lookup.py`, `boundary_mapper.py`

### How Boundary Data Accumulates

```
1. Comparison run finds mismatch:
   CSV says "CoServ", API says "Oncor" for 123 Main St, ZIP 75068
       │
       ▼
2. mismatch_analyzer.py processes it:
   a. Geocode address → get lat/lon
   b. SERP search: "electric utility 123 Main St Little Elm TX"
   c. AI analysis: evaluates CSV provider, API provider, SERP snippets
   d. Resolves: "CoServ is correct" (confidence: high, source: ai_confirms_csv)
       │
       ▼
3. Saves to data/mismatch_cache/{md5_hash}.json (prevents reprocessing)
       │
       ▼
4. Saves boundary point to data/boundary_points.json:
   { lat, lon, zip, resolved_provider, confidence, source, ... }
       │
       ▼
5. Next lookup in ZIP 75068:
   boundary_lookup.py checks if lat/lon is near resolved points
   → predicts correct provider using nearest-neighbor + split-line
```

---

## 7. Current State

### What Has Been Run

| Analysis | Status | Output Files | Details |
|----------|--------|--------------|---------|
| 140K stratified comparison | **Complete** | `stratified_sample_140k.json` (75MB), `stratified_comparison_140k.json` (45MB) | Full comparison of API vs partner-mapped data across 140K addresses |
| 74K targeted comparison | **Complete** | `targeted_comparison_74k.json` (11MB) | Focused re-comparison of addresses with mismatches |
| Mismatch extraction | **Complete** | `mismatches_for_analysis.json` (16MB), `mismatches_overnight.json` (7.9MB) | ~24K addresses where API disagrees with mapped data |
| Accuracy report generation | **Complete** | `provider_aliases.json`, `boundary_issues.json`, `accuracy_report.md` | Provider alias discovery and boundary issue identification |
| First 100 batch SERP analysis | **Complete** | `batch_results_20260204_*.json` (4 files), `serp_test_100.json` | SERP-based resolution of first ~100 mismatches |
| Mismatch cache population | **Partially complete** | `data/mismatch_cache/` (504 files) | 504 of ~24K mismatches have been analyzed with SERP/AI |
| Boundary point collection | **In progress** | `data/boundary_points.json` (242KB, ~500 points) | ~500 resolved conflict points accumulated |

### Checkpoint Files

| File | Size | Purpose | Status |
|------|------|---------|--------|
| `data/batch_checkpoints/checkpoint_targeted_comparison_74k.json` | 15KB | Tracks progress of targeted comparison batch job | Contains partial results and `processed_addresses` list for resume |
| `export_streamed_checkpoint.txt` | — | FCC BDC export progress checkpoint | Tracks which states/blocks have been exported |

### Intermediate Data in the Repo

| File | Size | Status |
|------|------|--------|
| `batch_results_20260204_124439.json` | 25KB | Completed batch - first 100 SERP analyses |
| `batch_results_20260204_130215.json` | 76KB | Completed batch - second batch |
| `batch_results_20260204_130922.json` | 15KB | Completed batch - third batch |
| `batch_results_20260204_134317.json` | 53KB | Completed batch - fourth batch |
| `serp_test_100.json` | 22KB | SERP verification test results |
| `data/mismatch_cache/*.json` | 504 files, ~700KB total | Individual mismatch analysis results |
| `data/boundary_points.json` | 242KB | Accumulated boundary intelligence data |

### What Has NOT Been Run

| Task | Description | Blocker |
|------|-------------|---------|
| Full mismatch analysis | Only 504 of ~24K mismatches analyzed | Cost/time (~$0.01-0.05 per SERP + AI call) |
| Boundary data integration into pipeline | `boundary_lookup.py` exists but is not wired into `pipeline/pipeline.py` | Not yet integrated as a data source |
| Special district data ingestion | `data/special_districts/processed/` is empty | Scripts exist (`download_tceq_data.py`, `download_florida_cdds.py`) but haven't been run |
| Provider match cache seeding | `data/provider_match_cache.json` not present | Need to run initial matching pass |
| A/B testing | `data/ab_tests/` is empty | `scripts/ab_test_runner.py` exists but hasn't been run |
| Accuracy monitoring | `data/metrics/` is empty | `scripts/accuracy_monitor.py` exists but hasn't been run |
| User feedback collection | `data/feedback/` is empty | No feedback collected yet |
| Gas mapping data | `data/gas_mappings/` is empty | Data not yet generated |
| Electric PUD data | `data/electric_puds/` is empty | Data not yet ingested |
| FindEnergy cache | `data/findenergy/` is empty | Not yet populated |
| SERP cache | `data/serp_cache/` is empty | SERP results cached in `data/mismatch_cache/` instead |
| Smart selector cache | `data/smart_selector_cache/` is empty | AI selector used instead |
| Utility directory | `data/utility_directory/` is empty | Master directory not built |
| SDWA downloads | `SDWA_latest_downloads/` is empty | Not yet downloaded |
| Corrections database | `data/corrections.db` may not exist | No user corrections submitted yet |

### Pipeline Architecture State

The codebase is in a **v1-to-v2 transition**:

- **v2 pipeline** (`utility_lookup.py` + `pipeline/`) is the active code path
- **v1 legacy** (`utility_lookup_v1.py`) is still used for:
  - Geocoding (`geocode_address`, `geocode_with_census`, etc.)
  - HIFLD API calls (consumed by pipeline sources)
  - Direct lookup functions imported by `api.py`
- The `utility_lookup_currently_deployed.py` file preserves the Railway-deployed version for comparison

### Environment Dependencies

| Variable | Purpose |
|----------|---------|
| `GOOGLE_API_KEY` | Google Geocoding API |
| `OPENAI_API_KEY` | GPT-4o-mini for AI selector, name normalization, mismatch analysis |
| `BRIGHTDATA_PROXY_*` | BrightData SERP proxy (host, port, username, password) |
| `SMARTY_AUTH_ID` / `SMARTY_AUTH_TOKEN` | Smarty address verification |
| `ADMIN_KEY` | Admin API key for key management |
| `REDIS_URL` | Redis for guide job queue |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | S3 for guide PDF storage |
| `DATABASE_URL` | PostgreSQL for FCC BDC data (production) |

### Deployment

- **Platform:** Railway
- **Runtime:** Gunicorn on port 8080
- **Container:** Playwright Python image with Xvfb
- **Entry point:** `api.py`
