#!/usr/bin/env python3
"""
Batch Validation — Run all tenant-verified addresses through the lookup engine
and compare results against tenant-provided provider names.

Usage:
    python batch_validate.py --limit 100          # Test with 100 rows
    python batch_validate.py --limit 1000         # Larger test
    python batch_validate.py                      # Full run (~91K rows)
    python batch_validate.py --resume             # Resume from checkpoint
    python batch_validate.py --skip-water         # Skip water layer (save RAM)
    python batch_validate.py --geocoder chained   # Census + Google fallback
"""

import argparse
import csv
import json
import logging
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from lookup_engine.config import Config
from lookup_engine.engine import LookupEngine
from lookup_engine.geocoder import CensusGeocoder
from provider_normalizer import (
    is_deregulated_rep,
    normalize_provider,
    normalize_provider_multi,
    normalize_provider_verbose,
    providers_match,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("batch_validate")

# ============================================================
# Constants
# ============================================================

INPUT_CSV = Path(__file__).parent / "addresses_with_tenant_verification_2026-02-06T06_57_49.470044438-06_00.csv"
OUTPUT_CSV = Path(__file__).parent / "batch_results.csv"
REPORT_FILE = Path(__file__).parent / "BATCH_VALIDATION_REPORT.md"
CHECKPOINT_FILE = Path(__file__).parent / "batch_checkpoint.json"

TENANT_NULL_VALUES = {
    "", "n/a", "na", "none", "null", "unknown", "not applicable",
    "landlord", "included", "included in rent", "included in hoa",
    "hoa", "owner", "property", "management", "apt", "apartment",
    "complex", "community", "building", "self", "resident",
    "tenant", "renter", "occupant", "varies", "tbd", "pending",
    "see lease", "contact office", "ask management",
    "choose your electric here", "power to choose",
}

PROPANE_KEYWORDS = {
    "amerigas", "suburban propane", "ferrellgas", "blue rhino",
    "propane", "tank", "bottled gas",
}

TDU_NAMES = {
    "ONCOR", "ONCOR ELECTRIC DELIVERY",
    "CENTERPOINT", "CENTERPOINT ENERGY",
    "AEP TEXAS", "AEP TEXAS CENTRAL", "AEP TEXAS NORTH",
    "TEXAS-NEW MEXICO POWER", "TNMP",
    "LUBBOCK POWER", "LUBBOCK P&L",
}

# Parent company groups for MATCH_PARENT detection
PARENT_GROUPS = {
    "Dominion Energy": [
        "Dominion Virginia Power", "Dominion Energy Virginia",
        "Dominion Energy", "Dominion NC", "Dominion Energy South Carolina",
        "Dominion Energy Ohio", "Questar Gas", "Virginia Natural Gas",
        "Dominion",
    ],
    "Duke Energy": [
        "Duke Energy", "Duke Energy Carolinas", "Duke Energy Progress",
        "Duke Energy Indiana", "Duke Energy Ohio", "Duke Energy Florida",
        "Piedmont Natural Gas", "Duke",
    ],
    "Southern Company": [
        "Georgia Power", "Alabama Power", "Mississippi Power", "Gulf Power",
        "Southern Company",
    ],
    "Eversource": [
        "Eversource", "Eversource CT", "Eversource MA", "Eversource NH",
        "NSTAR", "Yankee Gas",
    ],
    "WEC Energy": [
        "We Energies", "Wisconsin Public Service", "Peoples Gas",
        "North Shore Gas", "WEC Energy",
    ],
    "Xcel Energy": [
        "Xcel Energy", "Northern States Power",
        "Public Service Company of Colorado", "Southwestern Public Service",
    ],
    "FirstEnergy": [
        "FirstEnergy", "Ohio Edison", "Cleveland Illuminating",
        "Cleveland Electric Illum", "Cleveland Electric Illuminating",
        "The Illuminating Company", "Illuminating Company",
        "Toledo Edison", "Mon Power", "Potomac Edison",
        "West Penn Power", "West Penn Power Company",
        "Jersey Central P&L", "Met-Ed", "Penelec",
    ],
    "AEP": [
        "AEP", "AEP Ohio", "AEP Texas Central", "AEP Texas North",
        "AEP Texas", "Appalachian Power", "Indiana Michigan Power",
        "Kentucky Power", "Public Service Company of Oklahoma",
        "Southwestern Electric Power",
    ],
    "Exelon": [
        "Exelon", "ComEd", "Commonwealth Edison", "PECO", "PECO Energy",
        "BGE", "Baltimore Gas and Electric", "Pepco",
        "Potomac Electric Power", "Delmarva Power", "Atlantic City Electric",
    ],
    "PSE&G": [
        "PSE&G", "PSEG", "Public Service Electric and Gas",
        "Public Service Enterprise Group",
    ],
    "National Grid": [
        "National Grid", "KeySpan", "New England Power",
    ],
    "Entergy": [
        "Entergy", "Entergy Arkansas", "Entergy Louisiana",
        "Entergy Mississippi", "Entergy New Orleans", "Entergy Texas",
    ],
    "Sempra": [
        "SDG&E", "San Diego Gas & Electric", "SoCalGas",
        "Southern California Gas",
    ],
    "PG&E": [
        "PG&E", "Pacific Gas and Electric", "Pacific Gas & Electric",
    ],
    "Con Edison": [
        "Con Edison", "ConEd", "Consolidated Edison",
        "Orange and Rockland", "O&R",
    ],
    "Alliant Energy": [
        "Alliant Energy", "Wisconsin Power & Light",
        "Wisconsin Power And Light", "Interstate Power and Light", "IPL",
    ],
    "Enbridge": [
        "Enbridge Gas", "Enbridge Gas Ohio",
        "Enbridge Gas North Carolina", "Enbridge Gas NC",
        "Public Service NC", "Public Service Company of North Carolina",
        "PSNC Energy", "Vectren Energy", "Vectren",
    ],
    "Gas South": [
        "Gas South", "Gas South Avalon", "Nicor Gas",
    ],
}

# Build reverse lookup: normalized name -> parent group
_NAME_TO_PARENT = {}
for parent, names in PARENT_GROUPS.items():
    for name in names:
        _NAME_TO_PARENT[name.lower()] = parent


def _get_parent(provider_name: str) -> str:
    """Get parent company group for a provider name."""
    if not provider_name:
        return ""
    lower = provider_name.lower().strip()
    # Direct match
    if lower in _NAME_TO_PARENT:
        return _NAME_TO_PARENT[lower]
    # Substring match (e.g., "Duke Energy Carolinas, LLC" contains "Duke Energy Carolinas")
    for name, parent in _NAME_TO_PARENT.items():
        if name in lower or lower in name:
            return parent
    return ""


def _is_tenant_null(value: str) -> bool:
    """Check if tenant value is a null/placeholder."""
    if not value:
        return True
    clean = value.strip().lower()
    if clean in TENANT_NULL_VALUES:
        return True
    # Check for very short non-company values
    if len(clean) <= 2:
        return True
    return False


def _is_propane(value: str) -> bool:
    """Check if tenant gas value is a propane company."""
    if not value:
        return False
    lower = value.lower()
    return any(kw in lower for kw in PROPANE_KEYWORDS)


def _is_tdu(provider_name: str) -> bool:
    """Check if a provider name is a Texas TDU."""
    if not provider_name:
        return False
    upper = provider_name.upper()
    return any(tdu in upper for tdu in TDU_NAMES)


def _extract_state(address: str) -> str:
    """Extract 2-letter state code from address string."""
    # Try to match ", ST ZIP" or ", ST" at end
    m = re.search(r",\s*([A-Z]{2})\s+\d{5}", address)
    if m:
        return m.group(1)
    m = re.search(r",\s*([A-Z]{2})\s*$", address)
    if m:
        return m.group(1)
    return ""


_WATER_STATE_SUFFIX = re.compile(r"\s*-\s*[A-Z]{2}\s*$", re.IGNORECASE)
_WATER_BARE_STATE = re.compile(r"\s+[A-Z]{2}\s*$")
_WATER_PARENS = re.compile(r"\s*\([^)]*\)")
_WATER_REVERSED_ENTITY = re.compile(
    r"^(.+?),\s*(City Of|Town Of|Village Of|County Of)\s*$", re.IGNORECASE
)
_WATER_GENERIC_SUFFIXES = re.compile(
    r"\b(Water Utilities Department|Water Utility Department|Water Department|"
    r"Water Utilities|Water Utility|Public Water System|Water System|Water Works|"
    r"Waterworks|Water Dept|Water Svc|Water Service|City Utilities|"
    r"Municipal Utilities|Public Utilities|Dept Of Public Utilities|"
    r"Department Of Public Utilities|Pws|Mud \d+)\b",
    re.IGNORECASE,
)
_WATER_ABBREVS = [
    (r"\bWtr\b", "Water"),
    (r"\bCo\b(?=\s|$)", "County"),
    (r"\bWs\b", "Water System"),
    (r"\bDept\b", "Department"),
    (r"\bAuth\b", "Authority"),
    (r"\bSvc\b", "Service"),
    (r"\bComm\b", "Commission"),
    (r"\bBd\b", "Board"),
    (r"\bKC\b", "Kansas City"),
    (r"\bFt\b", "Fort"),
    (r"\bSt\b(?=\s[A-Z])", "Saint"),
    (r"\bMt\b(?=\s)", "Mount"),
    (r"\bWsa\b", "Water and Sewer Authority"),
]
_WATER_GENERIC_WORDS = {
    "city", "of", "the", "water", "utilities", "utility", "department",
    "dept", "system", "public", "authority", "county", "town", "service",
    "services", "district", "plant", "filtration", "metropolitan", "regional",
    "board", "commission", "village", "municipal", "works",
}


def normalize_water_name(name: str) -> str:
    """Normalize a water utility name for comparison.

    Strips state suffixes, parenthetical IDs, reverses 'X, City Of' format,
    expands abbreviations, and removes generic suffixes.
    """
    if not name:
        return ""
    n = name.strip()

    # Strip state suffixes: "- NC", "- TX"
    n = _WATER_STATE_SUFFIX.sub("", n)
    # Strip bare trailing state abbreviation: " AZ", " VA"
    n = _WATER_BARE_STATE.sub("", n)
    # Strip parenthetical IDs: "(2310001)", "(Sc0410012)", "(TN)"
    n = _WATER_PARENS.sub("", n)
    # Normalize reversed entity: "Gilbert, Town Of" → "Town Of Gilbert"
    m = _WATER_REVERSED_ENTITY.match(n)
    if m:
        n = f"{m.group(2)} {m.group(1)}"
    # Expand abbreviations
    for pat, repl in _WATER_ABBREVS:
        n = re.sub(pat, repl, n, flags=re.IGNORECASE)
    # Strip generic suffixes
    n = _WATER_GENERIC_SUFFIXES.sub("", n)
    # Strip apostrophes: "Lee's" → "Lees"
    n = n.replace("'", "").replace("\u2019", "")
    # Collapse whitespace
    n = re.sub(r"\s+", " ", n).strip()
    # Remove trailing punctuation
    n = n.strip(" -,.")
    return n


def water_names_match(engine_name: str, tenant_name: str) -> bool:
    """Lenient water name comparison — matches on core municipality name."""
    engine_name = (engine_name or "").strip()
    tenant_name = (tenant_name or "").strip()
    if not engine_name or not tenant_name:
        return False

    norm_e = normalize_water_name(engine_name).lower()
    norm_t = normalize_water_name(tenant_name).lower()

    if not norm_e or not norm_t:
        return False

    # Exact match after normalization
    if norm_e == norm_t:
        return True

    # Substring match (at least 4 chars)
    if len(norm_e) >= 4 and len(norm_t) >= 4:
        if norm_e in norm_t or norm_t in norm_e:
            return True

    # Core-word overlap: strip generic words and compare what's left
    engine_core = set(norm_e.split()) - _WATER_GENERIC_WORDS
    tenant_core = set(norm_t.split()) - _WATER_GENERIC_WORDS

    if engine_core and tenant_core:
        overlap = engine_core & tenant_core
        if len(overlap) >= min(len(engine_core), len(tenant_core)):
            return True

    return False


def _names_match(engine_name: str, tenant_name: str) -> bool:
    """Check if two provider names refer to the same entity.

    Uses multiple strategies:
    1. Case-insensitive exact match
    2. One name contains the other (substring)
    3. Canonical ID match (both resolve to same canonical)
    4. providers_match() from normalizer (handles aliases)
    """
    engine_name = (engine_name or "").strip()
    tenant_name = (tenant_name or "").strip()
    if not engine_name or not tenant_name:
        return False

    e = engine_name.lower()
    t = tenant_name.lower()

    # 1. Exact
    if e == t:
        return True

    # 2. Substring (at least 4 chars to avoid false positives like "Gas" matching everything)
    if len(e) >= 4 and len(t) >= 4:
        if e in t or t in e:
            return True

    # 2b. Strip common suffixes/prefixes and re-check
    # "Pud No 1 Of Clark County - (Wa)" vs "Clark Public Utilities"
    # "City Of Chattanooga - (Tn)" vs "Electric Power Board (EPB) - TN"
    strip_patterns = [
        r"\s*-\s*\([a-z]{2}\)", r"\s*\([a-z]{2}\)",  # state suffixes like "- (Wa)"
        r"\bpud no \d+ of\b", r"\bcity of\b", r"\btown of\b",
        r"\binc\.?\b", r"\bcorp\.?\b", r"\bco\.?\b", r"\bllc\b",
        r"\belectric\b", r"\benergy\b", r"\bpower\b", r"\butilities\b",
        r"\bcooperative\b", r"\bcoop\b", r"\bmember\b", r"\bcorporation\b",
    ]
    e_core = e
    t_core = t
    for pat in strip_patterns:
        e_core = re.sub(pat, "", e_core).strip()
        t_core = re.sub(pat, "", t_core).strip()
    e_core = re.sub(r"\s+", " ", e_core).strip()
    t_core = re.sub(r"\s+", " ", t_core).strip()
    if len(e_core) >= 4 and len(t_core) >= 4:
        if e_core == t_core or e_core in t_core or t_core in e_core:
            return True

    # 3. Canonical ID match
    e_verbose = normalize_provider_verbose(engine_name)
    t_verbose = normalize_provider_verbose(tenant_name)
    e_canon = e_verbose.get("canonical_id")
    t_canon = t_verbose.get("canonical_id")
    if e_canon and t_canon and e_canon == t_canon:
        return True

    # 4. providers_match (handles aliases in canonical_providers.json)
    if providers_match(engine_name, tenant_name):
        return True

    return False


def compare_providers(
    engine_name: str,
    tenant_raw: str,
    utility_type: str,
    state: str,
    alternatives: list = None,
) -> tuple:
    """
    Compare engine result vs tenant result.

    Returns: (comparison_category, match_detail, tenant_normalized)
    """
    tenant_clean = (tenant_raw or "").strip()

    # Both empty
    if not engine_name and not tenant_clean:
        return "BOTH_EMPTY", "", ""

    # Tenant null
    if _is_tenant_null(tenant_clean):
        if engine_name:
            return "ENGINE_ONLY", "", ""
        return "BOTH_EMPTY", "", ""

    # Propane check for gas
    if utility_type == "gas" and _is_propane(tenant_clean):
        return "TENANT_PROPANE", f"propane={tenant_clean}", ""

    # Engine returned nothing
    if not engine_name:
        return "TENANT_ONLY", "no_polygon_hit", tenant_clean

    # Split tenant value into segments (handles "Energy Texas, TXU Energy")
    tenant_segments = normalize_provider_multi(tenant_clean)
    # Use original segment names for display (normalizer can produce false positives)
    tenant_original_names = [s.get("original_segment", "").strip() for s in tenant_segments]
    tenant_norm_str = " | ".join(tenant_original_names)

    # Check if ANY tenant segment matches the engine result
    tenant_any_match = False
    for seg in tenant_segments:
        seg_original = seg.get("original_segment", "").strip()
        seg_display = seg.get("display_name", seg_original)

        # Try matching against both the original tenant text and the normalized display
        if _names_match(engine_name, seg_original):
            tenant_any_match = True
            break
        if seg_display != seg_original and _names_match(engine_name, seg_display):
            tenant_any_match = True
            break
        # Water-specific lenient matching
        if utility_type == "water":
            if water_names_match(engine_name, seg_original):
                tenant_any_match = True
                break
            if seg_display != seg_original and water_names_match(engine_name, seg_display):
                tenant_any_match = True
                break

    if tenant_any_match:
        return "MATCH", "", tenant_norm_str

    # MATCH_TDU: TX electric, engine returned TDU, tenant entered REP(s)
    # Also handles mixed entries like "Coserv, Reliant Energy" where some segments
    # are co-ops and some are REPs — if engine returned a TDU, the REP segments
    # confirm the address is in deregulated territory.
    if state.upper() == "TX" and utility_type == "electric" and _is_tdu(engine_name):
        has_rep = any(s.get("is_rep", False) for s in tenant_segments)
        all_reps_or_null = all(
            s.get("is_rep", False) or _is_tenant_null(s.get("original_segment", ""))
            for s in tenant_segments
        )
        if (all_reps_or_null or has_rep) and len(tenant_segments) > 0:
            rep_names = " | ".join(s.get("original_segment", "") for s in tenant_segments)
            return "MATCH_TDU", f"tdu={engine_name}, rep={rep_names}", tenant_norm_str

    # MATCH_PARENT: different display names but same parent company
    engine_parent = _get_parent(engine_name)
    if engine_parent:
        for seg in tenant_segments:
            seg_original = seg.get("original_segment", "").strip()
            seg_display = seg.get("display_name", seg_original)
            # Check parent against both original and display name
            for name in {seg_original, seg_display}:
                tenant_parent = _get_parent(name)
                if tenant_parent and engine_parent == tenant_parent:
                    return "MATCH_PARENT", f"parent={engine_parent}", tenant_norm_str
    # Also check tenant parent against engine name
    for seg in tenant_segments:
        seg_original = seg.get("original_segment", "").strip()
        tenant_parent = _get_parent(seg_original)
        if tenant_parent:
            engine_parent_check = _get_parent(engine_name)
            if engine_parent_check and engine_parent_check == tenant_parent:
                return "MATCH_PARENT", f"parent={tenant_parent}", tenant_norm_str

    # MATCH_ALT: tenant matches an alternative (engine found it but ranked it wrong)
    if alternatives:
        for alt in alternatives:
            alt_name = alt.get("provider", "")
            if not alt_name:
                continue
            for seg in tenant_segments:
                seg_original = seg.get("original_segment", "").strip()
                seg_display = seg.get("display_name", seg_original)
                if _names_match(alt_name, seg_original) or _names_match(alt_name, seg_display):
                    return "MATCH_ALT", f"alt={alt_name}, primary={engine_name}", tenant_norm_str
                if utility_type == "water":
                    if water_names_match(alt_name, seg_original) or water_names_match(alt_name, seg_display):
                        return "MATCH_ALT", f"alt={alt_name}, primary={engine_name}", tenant_norm_str

    # MISMATCH
    return "MISMATCH", f"engine={engine_name} vs tenant={tenant_norm_str}", tenant_norm_str


# ============================================================
# Main batch runner
# ============================================================

def run_batch(args):
    """Run the batch validation in 3 phases."""
    t_start = time.time()

    # Load engine
    # Load .env file if present
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ.setdefault(key.strip(), val.strip())

    logger.info("Loading lookup engine...")
    config = Config()
    if args.geocoder:
        config.geocoder_type = args.geocoder
    google_key = os.environ.get("GOOGLE_API_KEY", "")
    if google_key:
        config.google_api_key = google_key

    engine = LookupEngine(config, skip_water=args.skip_water)
    logger.info("Engine loaded.")

    # Read input CSV
    logger.info(f"Reading {INPUT_CSV}...")
    with open(INPUT_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        all_rows = list(reader)
    logger.info(f"Read {len(all_rows)} rows.")

    # Apply --start and --limit
    start_idx = args.start or 0
    if args.resume and CHECKPOINT_FILE.exists():
        with open(CHECKPOINT_FILE) as f:
            cp = json.load(f)
        start_idx = cp.get("last_processed_row", 0) + 1
        logger.info(f"Resuming from row {start_idx} (checkpoint)")

    end_idx = len(all_rows)
    if args.limit:
        end_idx = min(start_idx + args.limit, len(all_rows))

    rows_to_process = all_rows[start_idx:end_idx]
    total = len(rows_to_process)
    logger.info(f"Processing rows {start_idx} to {start_idx + total - 1} ({total} rows)")

    # ================================================================
    # PHASE 1: Batch Geocoding (upfront)
    # ================================================================
    t_phase1 = time.time()
    logger.info("=" * 60)
    logger.info("PHASE 1: Geocoding")

    geocoder = CensusGeocoder()
    # Build address list and check cache
    address_coords = {}  # address -> GeocodedAddress or None
    uncached_addresses = []

    for i, row in enumerate(rows_to_process):
        address = row.get("display", "").strip()
        if not address:
            continue
        # Check engine cache
        cached = engine.cache.get(address)
        if cached:
            # Extract lat/lon from cached result
            address_coords[address] = "cached"
        else:
            uncached_addresses.append((str(start_idx + i), address))

    cached_count = total - len(uncached_addresses)
    logger.info(f"Geocoding: {total} addresses, {cached_count} cached, {len(uncached_addresses)} need geocoding")

    # Batch geocode uncached addresses
    batch_geo_results = {}
    if uncached_addresses:
        logger.info(f"Sending {len(uncached_addresses)} addresses to Census batch endpoint...")
        batch_geo_results = geocoder.geocode_batch(uncached_addresses)
        geo_matched = sum(1 for v in batch_geo_results.values() if v is not None)
        geo_failed = len(uncached_addresses) - geo_matched
        logger.info(f"Census batch: {geo_matched} matched, {geo_failed} failed")

        # Google Places API fallback for Census failures
        google_key = os.environ.get("GOOGLE_API_KEY", "")
        if google_key and geo_failed > 0:
            from lookup_engine.geocoder import GoogleGeocoder
            google_geo = GoogleGeocoder(google_key)
            failed_ids = [
                (uid, addr) for uid, addr in uncached_addresses
                if batch_geo_results.get(uid) is None
            ]
            logger.info(f"Google fallback: geocoding {len(failed_ids)} Census failures...")
            google_matched = 0
            for uid, addr in failed_ids:
                try:
                    result = google_geo.geocode(addr)
                    if result:
                        # Google doesn't return Census block — get it from TIGERweb
                        from lookup_engine.geocoder import get_census_block_geoid
                        result.block_geoid = get_census_block_geoid(result.lat, result.lon) or ""
                        batch_geo_results[uid] = result
                        google_matched += 1
                except Exception as e:
                    logger.debug(f"Google geocode error for {addr[:50]}: {e}")
            logger.info(f"Google fallback: {google_matched}/{len(failed_ids)} matched")

    phase1_time = time.time() - t_phase1
    logger.info(f"Phase 1 complete: {phase1_time:.1f}s")

    # ================================================================
    # PHASE 2: Spatial Lookup + Comparison
    # ================================================================
    t_phase2 = time.time()
    logger.info("=" * 60)
    logger.info("PHASE 2: Spatial lookup + comparison")

    # Open output CSV
    append_mode = args.resume and OUTPUT_CSV.exists()
    out_mode = "a" if append_mode else "w"
    outfile = open(OUTPUT_CSV, out_mode, newline="", encoding="utf-8")
    writer = csv.writer(outfile)
    if not append_mode:
        writer.writerow([
            "address", "state", "utility_type", "engine_provider",
            "engine_eia_id", "engine_confidence", "engine_match_method",
            "engine_is_deregulated", "engine_source", "engine_needs_review",
            "engine_alternatives",
            "engine_catalog_id", "engine_catalog_title",
            "engine_id_match_score", "engine_id_confident",
            "alt_catalog_ids",
            "tenant_raw", "tenant_normalized",
            "comparison", "match_detail",
        ])

    # Stats tracking
    stats = {
        "total_processed": 0,
        "geocode_success": 0,
        "geocode_fail": 0,
        "geocode_fail_addresses": [],
    }
    comparison_counts = defaultdict(lambda: Counter())
    mismatch_pairs = defaultdict(lambda: Counter())
    state_mismatches = defaultdict(lambda: Counter())
    tdu_breakdown = Counter()
    tx_coop_muni_correct = 0
    tx_total_electric = 0
    tx_no_result = 0

    # Process each row — geocoding is already done, just spatial + compare
    times_per_row = []
    google_fallback_addresses = []

    for i, row in enumerate(rows_to_process):
        row_idx = start_idx + i
        t_row = time.time()

        address = row.get("display", "").strip()
        if not address:
            stats["total_processed"] += 1
            continue

        state = _extract_state(address)
        # Extract ZIP code for gas mapping
        _zip_m = re.search(r"\b(\d{5})(?:-\d{4})?\s*$", address)
        addr_zip = _zip_m.group(1) if _zip_m else ""
        # Extract city for FindEnergy fallback
        _city_m = re.search(r",\s*([^,]+?)\s*,\s*[A-Z]{2}", address)
        addr_city = _city_m.group(1).strip() if _city_m else ""
        addr_county = ""  # County comes from geocoder result if available
        row_key = str(row_idx)

        _lkw = dict(zip_code=addr_zip, city=addr_city, county=addr_county, address=address)

        # Try to get coordinates: cache first, then batch results
        lat, lon = 0.0, 0.0
        geocode_conf = 0.0
        block_geoid = ""
        electric = None
        gas = None
        water = None
        sewer = None

        # Check if engine cache has this address
        cached_result = engine.cache.get(address)
        if cached_result:
            lat = cached_result.lat
            lon = cached_result.lon
            geocode_conf = cached_result.geocode_confidence
            # Re-run spatial queries with fresh overlap logic
            if lat != 0.0 or lon != 0.0:
                electric = engine._lookup_with_state_gis(lat, lon, state, "electric", **_lkw)
                gas = engine._lookup_with_state_gis(lat, lon, state, "gas", **_lkw)
                water = engine._lookup_with_state_gis(lat, lon, state, "water", **_lkw) if not args.skip_water else None
                sewer = engine._lookup_sewer(lat, lon, state, _lkw.get("zip_code", ""), _lkw.get("city", ""), _lkw.get("county", ""), water)
        else:
            # Try batch geocode result
            batch_geo = batch_geo_results.get(row_key)
            if batch_geo:
                lat = batch_geo.lat
                lon = batch_geo.lon
                geocode_conf = batch_geo.confidence
                block_geoid = getattr(batch_geo, "block_geoid", "") or ""
                electric = engine._lookup_with_state_gis(lat, lon, state, "electric", **_lkw)
                gas = engine._lookup_with_state_gis(lat, lon, state, "gas", **_lkw)
                water = engine._lookup_with_state_gis(lat, lon, state, "water", **_lkw) if not args.skip_water else None
                sewer = engine._lookup_sewer(lat, lon, state, _lkw.get("zip_code", ""), _lkw.get("city", ""), _lkw.get("county", ""), water)
            else:
                # Neither cache nor batch — try single geocode via engine
                result = engine.lookup(address, use_cache=True)
                lat = result.lat
                lon = result.lon
                geocode_conf = result.geocode_confidence
                electric = result.electric
                gas = result.gas
                water = result.water
                sewer = result.sewer

        if lat == 0.0 and lon == 0.0:
            stats["geocode_fail"] += 1
            if len(stats["geocode_fail_addresses"]) < 100:
                stats["geocode_fail_addresses"].append(address)
            # Save for potential Google fallback
            google_fallback_addresses.append((row_idx, row))
        else:
            stats["geocode_success"] += 1

        stats["total_processed"] += 1

        # Compare for each utility type
        utility_map = {
            "electric": (electric, row.get("Electricity", "")),
            "gas": (gas, row.get("Gas", "")),
            "water": (water, row.get("Water", "")),
            "sewer": (sewer, row.get("Sewer", "")),
        }

        for utype, (engine_result, tenant_raw) in utility_map.items():
            engine_name = engine_result.provider_name if engine_result else ""
            engine_eia = engine_result.eia_id if engine_result else ""
            engine_conf = round(engine_result.confidence, 3) if engine_result else ""
            engine_method = engine_result.match_method if engine_result else ""
            engine_dereg = engine_result.is_deregulated if engine_result else False

            # Handle geocode failure
            if lat == 0.0 and lon == 0.0 and not engine_name:
                tenant_clean = (tenant_raw or "").strip()
                if tenant_clean and not _is_tenant_null(tenant_clean):
                    comparison = "TENANT_ONLY"
                    detail = "geocode_failed"
                    tenant_norm = tenant_clean
                else:
                    comparison = "BOTH_EMPTY"
                    detail = ""
                    tenant_norm = ""
            else:
                alts = engine_result.alternatives if engine_result else []
                comparison, detail, tenant_norm = compare_providers(
                    engine_name, tenant_raw, utype, state, alternatives=alts
                )

            comparison_counts[utype][comparison] += 1

            if comparison == "MISMATCH":
                mismatch_pairs[utype][(engine_name, tenant_norm)] += 1
                state_mismatches[utype][state] += 1

            # TX deregulated tracking
            if state.upper() == "TX" and utype == "electric":
                tx_total_electric += 1
                if comparison == "MATCH_TDU":
                    tdu_breakdown[engine_name] += 1
                if engine_result and not engine_result.is_deregulated:
                    tx_coop_muni_correct += 1
                if not engine_name:
                    tx_no_result += 1

            engine_source = engine_result.polygon_source if engine_result else ""
            engine_needs_review = engine_result.needs_review if engine_result else ""
            engine_alts = "|".join(
                a.get("provider", "") for a in (engine_result.alternatives if engine_result else [])
            )
            engine_catalog_id = engine_result.catalog_id if engine_result else ""
            engine_catalog_title = engine_result.catalog_title if engine_result else ""
            engine_id_score = engine_result.id_match_score if engine_result else ""
            engine_id_confident = engine_result.id_confident if engine_result else ""
            alt_catalog_ids = "|".join(
                str(a.get("catalog_id", "")) for a in (engine_result.alternatives if engine_result else []) if a.get("catalog_id")
            )

            writer.writerow([
                address, state, utype, engine_name, engine_eia,
                engine_conf, engine_method, engine_dereg, engine_source,
                engine_needs_review, engine_alts,
                engine_catalog_id, engine_catalog_title,
                engine_id_score, engine_id_confident,
                alt_catalog_ids,
                tenant_raw.strip() if tenant_raw else "",
                tenant_norm, comparison, detail,
            ])

        # Internet lookup (separate from utility_map — returns multiple providers)
        if engine.internet and block_geoid and not args.skip_internet:
            inet_result = engine.internet.lookup(block_geoid)
            if inet_result and inet_result.get("providers"):
                inet_providers = " | ".join(
                    f"{p['name']} ({p['technology']}, {p['max_down']}/{p['max_up']})"
                    for p in inet_result["providers"]
                )
                writer.writerow([
                    address, state, "internet", inet_providers,
                    "", inet_result.get("confidence", 0.95), "", False,
                    inet_result.get("source", "fcc_bdc"), False, "",
                    "", "", "", "", "",
                    "", "", "ENGINE_ONLY", f"block={block_geoid} count={inet_result['provider_count']} fiber={inet_result['has_fiber']} cable={inet_result['has_cable']} max_down={inet_result['max_download_speed']}",
                ])
                comparison_counts["internet"]["ENGINE_ONLY"] += 1

        row_ms = (time.time() - t_row) * 1000
        times_per_row.append(row_ms)

        # Progress logging
        processed = i + 1
        if processed % 1000 == 0 or processed == total:
            avg_ms = sum(times_per_row[-100:]) / min(len(times_per_row), 100)
            remaining = (total - processed) * avg_ms / 1000
            elapsed = time.time() - t_start
            logger.info(
                f"Phase 2: {processed}/{total} ({processed/total*100:.1f}%) | "
                f"Avg: {avg_ms:.0f}ms/row | "
                f"Elapsed: {elapsed:.0f}s | "
                f"ETA: {remaining:.0f}s"
            )

        # Checkpoint every 5000 rows
        if processed % 5000 == 0:
            with open(CHECKPOINT_FILE, "w") as cpf:
                json.dump({"last_processed_row": row_idx, "timestamp": datetime.utcnow().isoformat()}, cpf)
            outfile.flush()

    outfile.close()
    phase2_time = time.time() - t_phase2
    logger.info(f"Phase 2 complete: {phase2_time:.1f}s")

    # ================================================================
    # PHASE 3: AI Resolver (auto-resolve low-confidence / MATCH_ALT)
    # ================================================================
    phase3_time = 0
    ai_key = os.environ.get("OPENROUTER_API_KEY", "")
    if ai_key and not args.skip_ai:
        t_phase3 = time.time()
        logger.info("=" * 60)
        logger.info("PHASE 3: AI Resolver")

        from lookup_engine.ai_resolver import AIResolver
        resolver = AIResolver(ai_key, "openrouter", "anthropic/claude-sonnet-4-5")

        # Re-read the CSV we just wrote to find rows needing AI resolution
        with open(OUTPUT_CSV, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            all_result_rows = list(reader)
        fieldnames = list(all_result_rows[0].keys()) if all_result_rows else []
        for extra in ["ai_resolved", "ai_reasoning"]:
            if extra not in fieldnames:
                fieldnames.append(extra)

        # Build batch of items needing AI resolution
        ai_items = []  # (row_index, resolve_kwargs)
        for ri, row in enumerate(all_result_rows):
            comp = row.get("comparison", "")
            try:
                conf = float(row.get("engine_confidence", "") or "1.0")
            except ValueError:
                conf = 1.0
            needs_ai = (
                comp in ("MATCH_ALT", "MISMATCH")
                or (conf < 0.70 and row.get("engine_alternatives"))
            )
            if not needs_ai or row.get("utility_type") == "internet":
                continue

            candidates = []
            if row.get("engine_provider"):
                candidates.append({"provider": row["engine_provider"], "confidence": conf, "source": row.get("engine_source", "")})
            for alt in (row.get("engine_alternatives") or "").split("|"):
                alt = alt.strip()
                if alt:
                    candidates.append({"provider": alt, "confidence": 0.50, "source": "alternative"})
            tenant = (row.get("tenant_raw") or "").strip()
            if tenant and comp in ("MISMATCH", "MATCH_ALT"):
                candidates.append({"provider": tenant, "confidence": 0.60, "source": "tenant_reported"})
            if len(candidates) < 2:
                continue

            addr = row.get("address", "")
            zip_m = re.search(r"\b(\d{5})(?:-\d{4})?\s*$", addr)
            city_m = re.search(r",\s*([^,]+?)\s*,\s*[A-Z]{2}", addr)
            ai_items.append((ri, {
                "address": addr, "state": row.get("state", ""),
                "utility_type": row.get("utility_type", ""), "candidates": candidates,
                "zip_code": zip_m.group(1) if zip_m else "",
                "city": city_m.group(1).strip() if city_m else "",
            }))

        logger.info(f"  AI resolver: {len(ai_items)} rows to resolve (20 concurrent workers)")

        # Resolve concurrently
        batch_results = resolver.resolve_batch(
            [item for _, item in ai_items], max_workers=20
        )

        ai_resolved = len(batch_results)
        ai_changed = 0
        for (ri, _kwargs), (_item, result) in zip(ai_items, batch_results):
            if not result:
                continue
            row = all_result_rows[ri]
            old_provider = row["engine_provider"]
            row["engine_provider"] = result["provider"]
            row["engine_confidence"] = str(round(result["confidence"], 3))
            row["engine_source"] = result["source"]
            row["ai_resolved"] = "true"
            row["ai_reasoning"] = result.get("reasoning", "")
            if result["provider"] != old_provider:
                ai_changed += 1
                tenant_raw = (row.get("tenant_raw") or "").strip()
                if tenant_raw:
                    alts = [a.strip() for a in (row.get("engine_alternatives") or "").split("|") if a.strip()]
                    new_comp, new_detail, _ = compare_providers(
                        result["provider"], tenant_raw, row.get("utility_type", ""),
                        row.get("state", ""), alternatives=[{"provider": a} for a in alts]
                    )
                    row["comparison"] = new_comp
                    row["match_detail"] = new_detail

        # Write updated CSV
        with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(all_result_rows)

        # Update comparison_counts with AI changes
        comparison_counts_post_ai = defaultdict(lambda: Counter())
        for row in all_result_rows:
            utype = row.get("utility_type", "")
            comp = row.get("comparison", "")
            if utype and comp:
                comparison_counts_post_ai[utype][comp] += 1

        phase3_time = time.time() - t_phase3
        logger.info(
            f"Phase 3 complete: {phase3_time:.1f}s | "
            f"Resolved {ai_resolved}, changed {ai_changed}, errors {resolver.error_count}"
        )

        # Update comparison_counts for the report
        comparison_counts.update(comparison_counts_post_ai)
    elif not ai_key:
        logger.info("PHASE 3: Skipped (no OPENROUTER_API_KEY)")
    elif args.skip_ai:
        logger.info("PHASE 3: Skipped (--skip-ai flag)")

    # Final checkpoint
    with open(CHECKPOINT_FILE, "w") as cpf:
        json.dump({
            "last_processed_row": start_idx + total - 1,
            "timestamp": datetime.utcnow().isoformat(),
            "completed": True,
        }, cpf)

    total_time = time.time() - t_start
    logger.info("=" * 60)
    logger.info(
        f"Batch complete: {stats['total_processed']} rows in {total_time:.1f}s | "
        f"Phase 1 (geocoding): {phase1_time:.1f}s | "
        f"Phase 2 (spatial): {phase2_time:.1f}s | "
        f"Phase 3 (fallback): {phase3_time:.1f}s"
    )

    # Generate report
    generate_report(stats, comparison_counts, mismatch_pairs, state_mismatches,
                    tdu_breakdown, tx_total_electric, tx_coop_muni_correct,
                    tx_no_result, total_time, times_per_row,
                    phase1_time, phase2_time, phase3_time)


def generate_report(stats, comparison_counts, mismatch_pairs, state_mismatches,
                    tdu_breakdown, tx_total_electric, tx_coop_muni_correct,
                    tx_no_result, total_time, times_per_row,
                    phase1_time=0, phase2_time=0, phase3_time=0):
    """Generate BATCH_VALIDATION_REPORT.md."""

    lines = []
    lines.append("# Batch Validation Report")
    lines.append(f"## {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}")
    lines.append("")

    # Overall
    lines.append("### Overall")
    total = stats["total_processed"]
    geo_ok = stats["geocode_success"]
    geo_fail = stats["geocode_fail"]
    geo_rate = geo_ok / total * 100 if total else 0
    lines.append(f"- Total addresses processed: {total:,}")
    lines.append(f"- Geocoding success rate: {geo_rate:.1f}%")
    lines.append(f"- Geocoding failures: {geo_fail:,}")
    if stats["geocode_fail_addresses"]:
        lines.append("- Sample failed addresses:")
        for addr in stats["geocode_fail_addresses"][:20]:
            lines.append(f"  - `{addr}`")
    lines.append("")

    # Per utility type
    for utype in ["electric", "gas", "water"]:
        counts = comparison_counts[utype]
        lines.append(f"### {utype.title()} Accuracy")

        scoreable = counts["MATCH"] + counts["MATCH_TDU"] + counts["MATCH_PARENT"] + counts["MISMATCH"]
        correct = counts["MATCH"] + counts["MATCH_TDU"] + counts["MATCH_PARENT"]
        accuracy = correct / scoreable * 100 if scoreable else 0

        lines.append(f"- Scoreable rows: {scoreable:,}")
        lines.append(f"- **MATCH: {counts['MATCH']:,} ({counts['MATCH']/scoreable*100:.1f}%)**" if scoreable else f"- MATCH: {counts['MATCH']:,}")
        lines.append(f"- MATCH_TDU: {counts['MATCH_TDU']:,} ({counts['MATCH_TDU']/scoreable*100:.1f}%)" if scoreable else f"- MATCH_TDU: {counts['MATCH_TDU']:,}")
        lines.append(f"- MATCH_PARENT: {counts['MATCH_PARENT']:,} ({counts['MATCH_PARENT']/scoreable*100:.1f}%)" if scoreable else f"- MATCH_PARENT: {counts['MATCH_PARENT']:,}")
        lines.append(f"- **Total correct: {correct:,} ({accuracy:.1f}%)**")
        lines.append(f"- MISMATCH: {counts['MISMATCH']:,} ({counts['MISMATCH']/scoreable*100:.1f}%)" if scoreable else f"- MISMATCH: {counts['MISMATCH']:,}")
        lines.append(f"- ENGINE_ONLY: {counts['ENGINE_ONLY']:,}")
        lines.append(f"- TENANT_ONLY: {counts['TENANT_ONLY']:,}")
        lines.append(f"- TENANT_NULL: {counts['TENANT_NULL']:,}")
        lines.append(f"- BOTH_EMPTY: {counts['BOTH_EMPTY']:,}")
        if utype == "gas":
            lines.append(f"- TENANT_PROPANE: {counts['TENANT_PROPANE']:,}")
        lines.append("")

        # Mismatch analysis
        if mismatch_pairs[utype]:
            lines.append(f"### Mismatch Analysis — {utype.title()}")
            lines.append("#### Top 20 Mismatch Pairs")
            lines.append("| Engine Provider | Tenant Provider | Count |")
            lines.append("|---|---|---|")
            for (eng, ten), cnt in mismatch_pairs[utype].most_common(20):
                lines.append(f"| {eng} | {ten} | {cnt} |")
            lines.append("")

            if state_mismatches[utype]:
                lines.append("#### Top 10 States by Mismatch Count")
                lines.append("| State | Mismatches |")
                lines.append("|---|---|")
                for st, cnt in state_mismatches[utype].most_common(10):
                    lines.append(f"| {st or '(unknown)'} | {cnt} |")
                lines.append("")

    # TX Deregulated Market
    lines.append("### TX Deregulated Market")
    lines.append(f"- Total TX electric rows: {tx_total_electric:,}")
    lines.append(f"- REP detected (MATCH_TDU): {sum(tdu_breakdown.values()):,}")
    if tdu_breakdown:
        lines.append("- TDU breakdown:")
        for tdu, cnt in tdu_breakdown.most_common():
            lines.append(f"  - {tdu}: {cnt}")
    lines.append(f"- TX co-ops/municipals correctly NOT deregulated: {tx_coop_muni_correct:,}")
    lines.append(f"- TX addresses with no engine result: {tx_no_result:,}")
    lines.append("")

    # Geocoding Failures
    lines.append("### Geocoding Failures")
    lines.append(f"- Total: {stats['geocode_fail']:,}")
    if stats["geocode_fail_addresses"]:
        # Group by state
        fail_states = Counter()
        for addr in stats["geocode_fail_addresses"]:
            st = _extract_state(addr)
            fail_states[st or "(unknown)"] += 1
        lines.append("- By state:")
        for st, cnt in fail_states.most_common(10):
            lines.append(f"  - {st}: {cnt}")
        lines.append("- Sample failed addresses (up to 20):")
        for addr in stats["geocode_fail_addresses"][:20]:
            lines.append(f"  - `{addr}`")
    lines.append("")

    # Performance
    lines.append("### Performance")
    lines.append(f"- Total runtime: {total_time:.1f}s ({total_time/60:.1f}min)")
    avg_ms = sum(times_per_row) / len(times_per_row) if times_per_row else 0
    lines.append(f"- Average per address (Phase 2): {avg_ms:.1f}ms")
    lines.append(f"- Phase 1 (geocoding): {phase1_time:.1f}s ({phase1_time/60:.1f}min)")
    lines.append(f"- Phase 2 (spatial + compare): {phase2_time:.1f}s ({phase2_time/60:.1f}min)")
    if phase3_time > 0:
        lines.append(f"- Phase 3 (AI resolver): {phase3_time:.1f}s ({phase3_time/60:.1f}min)")
    lines.append("")

    report_text = "\n".join(lines)
    with open(REPORT_FILE, "w") as f:
        f.write(report_text)
    logger.info(f"Report written to {REPORT_FILE}")

    # Print summary to console
    print("\n" + "=" * 60)
    print("BATCH VALIDATION SUMMARY")
    print("=" * 60)
    for utype in ["electric", "gas", "water", "sewer"]:
        counts = comparison_counts[utype]
        scoreable = counts["MATCH"] + counts["MATCH_TDU"] + counts["MATCH_PARENT"] + counts["MISMATCH"]
        correct = counts["MATCH"] + counts["MATCH_TDU"] + counts["MATCH_PARENT"]
        accuracy = correct / scoreable * 100 if scoreable else 0
        print(f"  {utype.title():10s}: {correct:,}/{scoreable:,} correct ({accuracy:.1f}%)")
    print(f"  Geocoding: {geo_ok:,}/{total:,} ({geo_rate:.1f}%)")
    print(f"  Runtime:   {total_time:.1f}s ({total_time/60:.1f}min)")
    print("=" * 60)


def run_recompare(args):
    """Re-run comparison logic on existing batch_results.csv without geocoding or spatial queries."""
    t_start = time.time()

    logger.info("=" * 60)
    logger.info("RECOMPARE MODE: re-running comparison logic on existing results")

    # Read existing results
    if not OUTPUT_CSV.exists():
        logger.error(f"No existing results at {OUTPUT_CSV}")
        return

    rows = []
    with open(OUTPUT_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    logger.info(f"Read {len(rows)} result rows from {OUTPUT_CSV}")

    # Save old results for before/after comparison
    old_counts = defaultdict(lambda: Counter())
    for row in rows:
        old_counts[row["utility_type"]][row["comparison"]] += 1

    # Stats tracking
    stats = {"total_processed": 0, "geocode_success": 0, "geocode_fail": 0, "geocode_fail_addresses": []}
    comparison_counts = defaultdict(lambda: Counter())
    mismatch_pairs = defaultdict(lambda: Counter())
    state_mismatches = defaultdict(lambda: Counter())
    tdu_breakdown = Counter()
    tx_coop_muni_correct = 0
    tx_total_electric = 0
    tx_no_result = 0

    # Re-compare each row
    new_rows = []
    addresses_seen = set()
    for row in rows:
        address = row["address"]
        state = row["state"]
        utype = row["utility_type"]
        engine_name = row["engine_provider"]
        tenant_raw = row["tenant_raw"]

        # Track geocoding stats (once per address)
        if address not in addresses_seen:
            addresses_seen.add(address)
            stats["total_processed"] += 1
            if engine_name or row.get("engine_confidence"):
                stats["geocode_success"] += 1
            else:
                stats["geocode_fail"] += 1
                if len(stats["geocode_fail_addresses"]) < 100:
                    stats["geocode_fail_addresses"].append(address)

        # Re-run comparison with updated logic
        if not engine_name and not (tenant_raw or "").strip():
            comparison, detail, tenant_norm = "BOTH_EMPTY", "", ""
        elif not engine_name:
            tenant_clean = (tenant_raw or "").strip()
            if _is_tenant_null(tenant_clean):
                comparison, detail, tenant_norm = "BOTH_EMPTY", "", ""
            else:
                comparison, detail, tenant_norm = "TENANT_ONLY", "no_polygon_hit", tenant_clean
        else:
            comparison, detail, tenant_norm = compare_providers(
                engine_name, tenant_raw, utype, state
            )

        comparison_counts[utype][comparison] += 1

        if comparison == "MISMATCH":
            mismatch_pairs[utype][(engine_name, tenant_norm)] += 1
            state_mismatches[utype][state] += 1

        if state.upper() == "TX" and utype == "electric":
            tx_total_electric += 1
            if comparison == "MATCH_TDU":
                tdu_breakdown[engine_name] += 1
            # engine_is_deregulated is stored as string "True"/"False" in CSV
            engine_dereg = row.get("engine_is_deregulated", "")
            if engine_name and engine_dereg not in ("True", "true", "1"):
                tx_coop_muni_correct += 1
            if not engine_name:
                tx_no_result += 1

        new_rows.append({
            **row,
            "tenant_normalized": tenant_norm,
            "comparison": comparison,
            "match_detail": detail,
        })

    # Write updated results
    recompare_csv = Path(__file__).parent / "batch_results_recompare.csv"
    with open(recompare_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=new_rows[0].keys())
        writer.writeheader()
        writer.writerows(new_rows)
    logger.info(f"Updated results written to {recompare_csv}")

    total_time = time.time() - t_start

    # Print before/after comparison
    print("\n" + "=" * 60)
    print("RECOMPARE: BEFORE vs AFTER")
    print("=" * 60)
    for utype in ["electric", "gas", "water"]:
        old_c = old_counts[utype]
        new_c = comparison_counts[utype]
        old_scoreable = old_c["MATCH"] + old_c["MATCH_TDU"] + old_c["MATCH_PARENT"] + old_c["MISMATCH"]
        old_correct = old_c["MATCH"] + old_c["MATCH_TDU"] + old_c["MATCH_PARENT"]
        old_acc = old_correct / old_scoreable * 100 if old_scoreable else 0
        new_scoreable = new_c["MATCH"] + new_c["MATCH_TDU"] + new_c["MATCH_PARENT"] + new_c["MISMATCH"]
        new_correct = new_c["MATCH"] + new_c["MATCH_TDU"] + new_c["MATCH_PARENT"]
        new_acc = new_correct / new_scoreable * 100 if new_scoreable else 0
        delta = new_acc - old_acc
        print(f"  {utype.title():10s}: {old_acc:.1f}% → {new_acc:.1f}% ({delta:+.1f}pp) | {new_correct:,}/{new_scoreable:,}")
    print(f"  Runtime: {total_time:.1f}s")
    print("=" * 60)

    # Generate updated report
    generate_report(stats, comparison_counts, mismatch_pairs, state_mismatches,
                    tdu_breakdown, tx_total_electric, tx_coop_muni_correct,
                    tx_no_result, total_time, [])


def main():
    parser = argparse.ArgumentParser(description="Batch validation against tenant-verified addresses")
    parser.add_argument("--limit", type=int, help="Process only first N rows")
    parser.add_argument("--start", type=int, default=0, help="Start from row N")
    parser.add_argument("--resume", action="store_true", help="Resume from checkpoint")
    parser.add_argument("--skip-water", action="store_true", help="Skip water layer")
    parser.add_argument("--skip-ai", action="store_true", help="Skip AI resolver phase")
    parser.add_argument("--skip-internet", action="store_true", help="Skip internet provider lookup")
    parser.add_argument("--geocoder", default="census", choices=["census", "chained", "google"],
                        help="Geocoder type (default: census)")
    parser.add_argument("--recompare-only", action="store_true",
                        help="Re-run comparison logic on existing batch_results.csv (no geocoding/spatial)")
    args = parser.parse_args()
    if args.recompare_only:
        run_recompare(args)
    else:
        run_batch(args)


if __name__ == "__main__":
    main()
