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
from concurrent.futures import ThreadPoolExecutor
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

def _load_catalog_aliases():
    alias_path = os.path.join(os.path.dirname(__file__), "data", "catalog_id_aliases.json")
    if os.path.exists(alias_path):
        with open(alias_path) as f:
            return json.load(f)
    return {}

CATALOG_ID_ALIASES = _load_catalog_aliases()

def _resolve_canonical_id(catalog_id):
    """Resolve a catalog ID to its canonical form using the alias table."""
    if not catalog_id:
        return catalog_id
    return CATALOG_ID_ALIASES.get(str(catalog_id), str(catalog_id))

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
    "BLUEBONNET ELECTRIC", "BLUEBONNET ELEC",
}

# Known cross-state shapefile errors: (engine_name_substring, wrong_state) -> True
# These polygons incorrectly extend into states where the utility doesn't operate.
CROSS_STATE_OVERRIDES = {
    ("public service company of new mexico", "NH"): True,
    ("public service company of new mexico", "VT"): True,
    ("public service company of new mexico", "MA"): True,
    ("public service company of new mexico", "CT"): True,
    ("public service company of new mexico", "ME"): True,
    ("nicor gas", "GA"): True,
    ("nicor gas", "SC"): True,
    ("nicor gas", "TN"): True,
    ("nicor gas", "NC"): True,
}

# Gas providers that should never appear in electric results
GAS_ONLY_PROVIDERS = {
    "intermountain gas",
}

# Keywords indicating tenant entered an electric company for their gas provider
ELECTRIC_KEYWORDS_IN_GAS = {
    "electric", " emc", "membership corp", "power company",
    "duke energy", "rocky mountain power", "xcel energy",
}

# GA deregulated gas market: LDCs (infrastructure) vs marketers (retail)
# Analogous to TX electric TDU vs REP.
GA_GAS_LDC_NAMES = {
    "nicor gas",
    "atlanta gas light",
    "liberty utilities",
    "atmos energy",
}

GA_GAS_MARKETER_NAMES = {
    "georgia natural gas",
    "scana energy",
    "gas south",
    "constellation",
    "constellation energy",
    "infinite energy",
    "xoom energy",
    "xoom",
    "stream energy",
    "stream",
    "santanna energy",
    "volunteer energy",
    "true natural gas",
    "true gas",
    "walton emc natural gas",
    "snapping shoals emc natural gas",
    "sawnee emc",
    "fuel georgia",
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
        "NSTAR", "Yankee Gas", "Yankee Gas Service",
        "United Illuminating",
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
        "Toledo Edison", "Mon Power", "Monongahela Power",
        "Potomac Edison",
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
        "National Grid", "National Grid MA", "KeySpan", "New England Power",
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
    "Hawaiian Electric": [
        "Hawaiian Electric", "Hawaiian Electric Company",
        "Hawaii Electric Light", "Hawaii Electric Light Company",
        "HELCO", "Maui Electric", "MECO",
    ],
    "Seattle Utilities": [
        "Seattle City Light", "Seattle Public Utilities",
    ],
    "Dominion Energy Utah": [
        "Dominion Energy", "Dominion Energy Utah", "Questar Gas",
    ],
    "Columbia Gas": [
        "Columbia Gas", "Columbia Gas VA", "Columbia Gas of Virginia",
        "Columbia Gas of Pennsylvania", "Columbia Gas of Ohio",
        "Columbia Gas of Maryland",
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


# Water name aliases: EPA/SDWIS system names → canonical names used by tenants.
# Both sides are lowercased for comparison. Multiple EPA names can map to the same canonical.
WATER_NAME_ALIASES = {
    "charlotte-mecklenburg utilities": "charlotte water",
    "charlotte mecklenburg utility department": "charlotte water",
    "cmud": "charlotte water",
    "city of winston-salem": "winston-salem",
    "winston salem water": "winston-salem",
    "mo american st louis": "missouri american water",
    "mo american st louis st charles counties": "missouri american water",
    "mo american water co": "missouri american water",
    "missouri american water company": "missouri american water",
    "pa amer water co pittsburgh dist": "pennsylvania american water",
    "pa american water co": "pennsylvania american water",
    "pennsylvania american water company": "pennsylvania american water",
    "az water co - pinal valley": "epcor water arizona",
    "az water co - pinal valley water system": "epcor water arizona",
    "arizona water company": "epcor water arizona",
    "chaparral city water": "epcor water arizona",
    "epcor water": "epcor water arizona",
    "in american water co": "indiana american water",
    "indiana american water company": "indiana american water",
    "wv american water co": "west virginia american water",
    "west virginia american water company": "west virginia american water",
    "tn american water co": "tennessee american water",
    "tennessee american water company": "tennessee american water",
    "il american water co": "illinois american water",
    "illinois american water company": "illinois american water",
    "ia american water co": "iowa american water",
    "iowa american water company": "iowa american water",
    "nj american water co": "new jersey american water",
    "new jersey american water company": "new jersey american water",
    "elizabethtown water co": "new jersey american water",
    "savannah-main": "city of savannah",
    "savannah main": "city of savannah",
    "tucson water": "city of tucson water",
    "city of richmond dpu": "richmond dpu",
    "richmond dept of public utilities": "richmond dpu",
    "pittsburgh w and s auth": "pittsburgh water sewer authority",
    "mobile, bd. of w&s comm. of the city of": "mobile area water and sewer system",
    "mobile bd of w&s comm": "mobile area water and sewer system",
    "hcpud/south-central": "hillsborough county",
    "hcpud south central": "hillsborough county",
    "gsw&sa": "grand strand water and sewer authority",
    "pwcsa - east": "prince william water",
    "pwcsa east": "prince william water",
    "pwcsa": "prince william water",
    "saws texas research park": "san antonio water system",
    "saws": "san antonio water system",
    "saws northeast": "san antonio water system",
    "saws southeast": "san antonio water system",
    "saws northwest": "san antonio water system",
    "saws southwest": "san antonio water system",
    "wayne wd combined": "wayne water districts",
    "o fallon": "city of o'fallon",
    "global water - santa cruz water": "global water resources",
    "global water santa cruz water": "global water resources",
    "cal am water company - monterey": "california american water",
    "cal am water company monterey": "california american water",
    "okaloosa co.wtr.; swr.system": "okaloosa county water sewer",
    "okaloosa co wtr swr system": "okaloosa county water sewer",
    "orange county water and sewer authority": "owasa",
    # --- Expanded water aliases (GPT-4o-mini identified) ---
    "american water (pa)": "pennsylvania american water",
    "ames water treatment plant - ia": "city of ames",
    "aqua - nc": "aqua",
    "aqua - nj": "aqua",
    "aqua - ohio": "aqua ohio",
    "aqua -nj": "aqua",
    "aqua virginia, inc.": "aqua virginia",
    "aqua- nc": "aqua",
    "aquarion water co - ct": "aquarion water company",
    "aquarion water company - ct": "aquarion water company",
    "arizona water co": "arizona water company",
    "chandler arizona": "city of chandler",
    "charlotte water (nc)": "charlotte water",
    "charlotte water - nc": "charlotte water",
    "city of ames - ia": "city of ames",
    "city of anacortes - wa": "city of anacortes",
    "city of canton - ga": "city of canton",
    "city of canton water department - ga": "city of canton",
    "city of chandler - az": "city of chandler",
    "city of claremore - ok": "city of claremore",
    "city of columbus utilities - in": "city of columbus utilities",
    "city of dexter public works": "city of dexter",
    "city of dexter water department": "city of dexter",
    "city of flowood, ms": "city of flowood",
    "city of heath (tx)": "city of heath",
    "city of hermiston (oregon)": "city of hermiston",
    "city of hermiston - or": "city of hermiston",
    "city of marysville - wa": "city of marysville",
    "city of orange water department": "city of orange",
    "city of painesville - oh": "city of painesville",
    "colorado springs utilities - co": "colorado springs utilities",
    "columbus city utilities - in": "city of columbus utilities",
    "columbus pws - oh": "columbus water",
    "columbus water & power - oh": "columbus water",
    "fort bend mud 194 - inframark": "fort bend county mud 194",
    "grovetown ga": "city of grovetown",
    "highland city water department": "highland city water",
    "highland city water system": "highland city water",
    "illinois american water - il": "illinois american water",
    "indiana-american water company, inc.": "indiana american water",
    "regional utilities (santa rosa beach, fl)": "regional utilities (santa rosa beach)",
    "st. mary's county metropolitan commission - md": "st. mary's county metropolitan commission",
    "texas city (gcwa)": "city of texas city",
    "texas city - tx": "city of texas city",
    "the city of columbus - oh": "columbus water",
    "the city of flowood - ms": "city of flowood",
    "the city of heath - tx": "city of heath",
    "the city of painsville-oh": "city of painesville",
    "the city of waxahachie - tx": "city of waxahachie",
    "tualatin valley water district (tvwd)": "tualatin valley water district",
    "tuscambia utilities (al)": "tuscumbia utilities",
    "tuscumbia utilities (al)": "tuscumbia utilities",
    "washington water services east pierce": "washington water services",
    "washington water services gig harbor": "washington water services",
    "waxahachie texas": "city of waxahachie",
    "winston-salem/forsyth county": "winston-salem/forsyth county utilities",
    "city of arlington - wa": "city of arlington",
    "city of arlington, wa": "city of arlington",
    "city of janesville - wi": "city of janesville",
    "city of janesville, wi": "city of janesville",
    "city of saint charles missouri (mo)": "city of saint charles water division",
    "city of saint charles water division - mo": "city of saint charles water division",
}

# Electric name aliases: abbreviations, rebrands, and formatting variants.
ELECTRIC_NAME_ALIASES = {
    "alabama power - al": "alabama power",
    "brownsville energy authority (bea) - tn": "brownsville energy authority",
    "brownsville energy authority, tn": "brownsville energy authority",
    "city of holyoke - ma": "holyoke gas & electric",
    "city of lubbock - tx": "city of lubbock",
    "city of seguin - tx": "city of seguin",
    "city of wamego - ks": "city of wamego",
    "city of wamego-ks": "city of wamego",
    "duke energy corporation": "duke energy",
    "duke-energy": "duke energy",
    "easton utilities - md": "easton utilities",
    "easton utilities commision - md": "easton utilities",
    "eugene water & electric board": "eugene water and electric board",
    "eugene water and electric board (eweb)": "eugene water and electric board",
    "fpl. palm coast (fl)": "florida power and light",
    "gulf power company (now fpl) - fl": "florida power and light",
    "holyoke gas & electric (hg&e), ma": "holyoke gas & electric",
    "idaho power - id": "idaho power",
    "lakeview light and power": "lakeview light & power",
    "lg&e kentucky utilities": "louisville gas and electric",
    "lg&e/ku": "louisville gas and electric",
    "lge/ku": "louisville gas and electric",
    "lge ku": "louisville gas and electric",
    "lgeku": "louisville gas and electric",
    "lg&e and ku": "louisville gas and electric",
    "lg&e and ku energy": "louisville gas and electric",
    "kentucky utilities": "louisville gas and electric",
    "kentucky utilities company": "louisville gas and electric",
    "louisville gas & electric": "louisville gas and electric",
    "louisville gas and electric - ky": "louisville gas and electric",
    "lubbock power & light - tx": "city of lubbock",
    "memphis light, gas & water": "memphis light, gas and water",
    "new york state electric & gas (nyseg)": "new york state electric & gas",
    "ppl electric utilities - pa": "ppl electric utilities",
    "public service of oklahoma": "public service company of oklahoma",
    "rhode island energy - ri": "rhode island energy",
    "rhode island energy-ri": "rhode island energy",
    "seguin, tx": "city of seguin",
    "surry-yadkin emc - nc": "surry-yadkin emc",
    "tennessee valley authority (tva)": "tennessee valley authority",
    "town of apex - nc": "town of apex",
}

# Gas name aliases: abbreviations, mergers, plan-specific suffixes.
GAS_NAME_ALIASES = {
    "colombia gas of pennsylvania-pa": "columbia gas of pennsylvania",
    "enbridge ohio": "enbridge gas ohio",
    "gas south anita hale": "gas south",
    "gas south aormeese jenkins": "gas south",
    "gas south avalon": "gas south",
    "gas south | anita i hale": "gas south",
    "georgia natural gas - avalon 2024": "georgia natural gas",
    "georgia natural gas | anita i hale": "georgia natural gas",
    "georgia natural gas-atlanta area pm": "georgia natural gas",
    "liberty (nh)": "liberty utilities-nh",
    "liberty utilities (nh)": "liberty utilities-nh",
    "liberty utilities - nh": "liberty utilities-nh",
    "mdu (montana-dakota utilities)": "montana-dakota utilities",
    "montana-dakota utilities co": "montana-dakota utilities",
    "north western energy - mt": "northwestern energy",
    "northwestern energy (mt)": "northwestern energy",
    "peoples gas - pa": "peoples gas",
    "scana energy | anita i hale": "gas south",
    "ugi utilities inc - pa": "ugi utilities inc",
    "westfield gas and electric - ma": "westfield gas & electric - ma",
}

def _resolve_water_alias(name: str) -> str:
    """Resolve a water provider name through the alias table."""
    if not name:
        return ""
    lower = name.lower().strip()
    return WATER_NAME_ALIASES.get(lower, lower)

def _resolve_electric_alias(name: str) -> str:
    """Resolve an electric provider name through the alias table."""
    if not name:
        return ""
    lower = name.lower().strip()
    return ELECTRIC_NAME_ALIASES.get(lower, lower)

def _resolve_gas_alias(name: str) -> str:
    """Resolve a gas provider name through the alias table."""
    if not name:
        return ""
    lower = name.lower().strip()
    return GAS_NAME_ALIASES.get(lower, lower)

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
    (r"\bSud\b", "Special Utility District"),
    (r"\bWsc\b", "Water Supply Corporation"),
    (r"\bWcid\b", "Water Control and Improvement District"),
    (r"\bFwsd\b", "Fresh Water Supply District"),
    (r"\bPwsd\b", "Public Water Supply District"),
    (r"\bSd\b(?=\s*\d)", "Supply District"),
    (r"\bWtp\b", "Water Treatment Plant"),
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

    # Check water alias table first — resolves EPA/SDWIS names to canonical
    alias_e = _resolve_water_alias(engine_name)
    alias_t = _resolve_water_alias(tenant_name)
    if alias_e == alias_t:
        return True

    norm_e = normalize_water_name(engine_name).lower()
    norm_t = normalize_water_name(tenant_name).lower()

    if not norm_e or not norm_t:
        return False

    # Also check aliases on normalized names
    alias_ne = _resolve_water_alias(norm_e)
    alias_nt = _resolve_water_alias(norm_t)
    if alias_ne == alias_nt:
        return True

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
        # Utility-type-specific alias matching
        if utility_type == "water":
            if water_names_match(engine_name, seg_original):
                tenant_any_match = True
                break
            if seg_display != seg_original and water_names_match(engine_name, seg_display):
                tenant_any_match = True
                break
        elif utility_type == "electric":
            if _resolve_electric_alias(engine_name) == _resolve_electric_alias(seg_original):
                tenant_any_match = True
                break
            if seg_display != seg_original and _resolve_electric_alias(engine_name) == _resolve_electric_alias(seg_display):
                tenant_any_match = True
                break
        elif utility_type == "gas":
            if _resolve_gas_alias(engine_name) == _resolve_gas_alias(seg_original):
                tenant_any_match = True
                break
            if seg_display != seg_original and _resolve_gas_alias(engine_name) == _resolve_gas_alias(seg_display):
                tenant_any_match = True
                break

    if tenant_any_match:
        return "MATCH", "", tenant_norm_str

    # MATCH_TDU: TX electric, engine returned TDU, tenant entered REP(s) or co-op
    # In deregulated TX territory, the TDU is the correct infrastructure provider.
    # Tenants may report their REP, a co-op name, or any other electric provider.
    if state.upper() == "TX" and utility_type == "electric" and _is_tdu(engine_name):
        if len(tenant_segments) > 0:
            rep_names = " | ".join(s.get("original_segment", "") for s in tenant_segments)
            return "MATCH_TDU", f"tdu={engine_name}, rep={rep_names}", tenant_norm_str

    # MATCH_TDU: GA gas deregulated market — LDC vs marketer
    if state.upper() == "GA" and utility_type == "gas":
        eng_lower = engine_name.lower().strip()
        ten_lower = tenant_norm_str.lower().strip()
        eng_is_ldc = any(ldc in eng_lower for ldc in GA_GAS_LDC_NAMES)
        ten_is_marketer = any(mkt in ten_lower for mkt in GA_GAS_MARKETER_NAMES)
        eng_is_marketer = any(mkt in eng_lower for mkt in GA_GAS_MARKETER_NAMES)
        ten_is_ldc = any(ldc in ten_lower for ldc in GA_GAS_LDC_NAMES)
        if (eng_is_ldc and ten_is_marketer) or (eng_is_marketer and ten_is_ldc):
            return "MATCH_TDU", f"ga_gas_deregulated: ldc={engine_name}, marketer={tenant_norm_str}", tenant_norm_str

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

    # Cross-state shapefile override: engine polygon is in the wrong state
    engine_lower = engine_name.lower()
    state_upper = state.upper()
    for (override_name, override_state), _ in CROSS_STATE_OVERRIDES.items():
        if override_name in engine_lower and state_upper == override_state:
            return "MISMATCH", f"cross_state_override: engine={engine_name} wrong for {state_upper}", tenant_norm_str

    # Gas-only provider in electric results: engine returned a gas company
    if utility_type == "electric" and engine_lower in GAS_ONLY_PROVIDERS:
        return "MISMATCH", f"gas_provider_in_electric: engine={engine_name}", tenant_norm_str

    # Tenant entered electric company for gas provider
    if utility_type == "gas":
        tenant_lower = tenant_norm_str.lower()
        for kw in ELECTRIC_KEYWORDS_IN_GAS:
            if kw in tenant_lower:
                return "MATCH", f"tenant_electric_in_gas: tenant={tenant_norm_str}", tenant_norm_str

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

    def _save_geo_cache(geo_disk_cache, label=""):
        """Persist geocode cache to disk immediately."""
        GEOCODE_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(GEOCODE_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(geo_disk_cache, f)
        logger.info(f"Geocode disk cache saved: {len(geo_disk_cache)} entries{' (' + label + ')' if label else ''}")

    def _cache_geo_result(geo_disk_cache, addr, geo):
        """Add a single geocode result to the disk cache dict."""
        if geo and addr not in geo_disk_cache:
            geo_disk_cache[addr] = {
                "lat": geo.lat, "lon": geo.lon,
                "confidence": geo.confidence,
                "city": getattr(geo, 'city', ''),
                "state": getattr(geo, 'state', ''),
                "zip_code": getattr(geo, 'zip_code', ''),
                "county": getattr(geo, 'county', ''),
                "block_geoid": getattr(geo, 'block_geoid', ''),
            }
            return True
        return False

    def _queue_census_failures(chunk_results, chunk_addrs, nom_queue, nom_lock, geo_disk_cache):
        """Queue Census failures for Nominatim AND save successful results to disk cache."""
        new_cached = 0
        for uid, addr in chunk_addrs:
            if chunk_results.get(uid) is None:
                with nom_lock:
                    nom_queue.append((uid, addr))
            else:
                if _cache_geo_result(geo_disk_cache, addr, chunk_results[uid]):
                    new_cached += 1
        if new_cached:
            _save_geo_cache(geo_disk_cache, f"after Census chunk, +{new_cached}")

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

    # Disk-based geocode cache — survives across runs
    GEOCODE_CACHE_FILE = Path("data/geocode_cache.json")
    geo_disk_cache = {}  # address -> {lat, lon, confidence, city, state, zip_code, county, block_geoid}
    if GEOCODE_CACHE_FILE.exists():
        with open(GEOCODE_CACHE_FILE, encoding="utf-8") as f:
            geo_disk_cache = json.load(f)
        logger.info(f"Geocode disk cache: {len(geo_disk_cache)} entries loaded")

    geocoder = CensusGeocoder()
    # Build address list and check cache
    address_coords = {}  # address -> GeocodedAddress or None
    uncached_addresses = []

    for i, row in enumerate(rows_to_process):
        address = row.get("display", "").strip()
        if not address:
            continue
        # Check engine cache first, then disk geocode cache
        cached = engine.cache.get(address)
        if cached:
            address_coords[address] = "cached"
        elif address in geo_disk_cache:
            address_coords[address] = "geo_cached"
        else:
            uncached_addresses.append((str(start_idx + i), address))

    cached_count = total - len(uncached_addresses)
    logger.info(f"Geocoding: {total} addresses, {cached_count} cached, {len(uncached_addresses)} need geocoding")

    # Batch geocode uncached addresses
    # Strategy: Census batch in 10K chunks + Nominatim runs concurrently on failures
    # Google only handles what both miss.
    batch_geo_results = {}
    nom_results = {}  # filled by background Nominatim thread
    nom_total = 0
    nom_matched_count = 0

    if uncached_addresses:
        import threading

        # Background Nominatim workers — process Census failures concurrently
        NOM_WORKERS = 5
        nom_queue = []  # (uid, addr) pairs queued by Census chunks
        nom_queue_idx = 0  # next index to process
        nom_done = threading.Event()  # no more items coming
        nom_stop = threading.Event()  # stop workers now
        nom_lock = threading.Lock()

        def _nominatim_worker(worker_id):
            nonlocal nom_matched_count, nom_queue_idx
            from lookup_engine.geocoder import NominatimGeocoder
            nominatim = NominatimGeocoder()
            time.sleep(worker_id * 0.2)
            while not nom_stop.is_set():
                item = None
                with nom_lock:
                    if nom_queue_idx < len(nom_queue):
                        item = nom_queue[nom_queue_idx]
                        nom_queue_idx += 1
                if item is None:
                    if nom_done.is_set():
                        with nom_lock:
                            if nom_queue_idx >= len(nom_queue):
                                break
                    time.sleep(0.3)
                    continue
                uid, addr = item
                try:
                    result = nominatim.geocode(addr)
                    if result:
                        with nom_lock:
                            nom_results[uid] = result
                            nom_matched_count += 1
                except Exception:
                    pass

        nom_threads = []
        for w in range(NOM_WORKERS):
            t = threading.Thread(target=_nominatim_worker, args=(w,), daemon=True)
            t.start()
            nom_threads.append(t)

        # Census batch — as each chunk returns, queue failures for Nominatim
        logger.info(f"Sending {len(uncached_addresses)} addresses to Census batch endpoint...")
        logger.info("Nominatim running concurrently on Census failures...")
        batch_geo_results = geocoder.geocode_batch(
            uncached_addresses,
            on_chunk_complete=lambda chunk_results, chunk_addrs: _queue_census_failures(
                chunk_results, chunk_addrs, nom_queue, nom_lock, geo_disk_cache
            ),
        )
        geo_matched = sum(1 for v in batch_geo_results.values() if v is not None)
        geo_failed = len(uncached_addresses) - geo_matched
        logger.info(f"Census batch: {geo_matched} matched, {geo_failed} failed")

        # Signal Nominatim that no more items are coming, wait up to 120s then move on
        nom_done.set()
        deadline = time.time() + 120
        for t in nom_threads:
            remaining = max(deadline - time.time(), 1)
            t.join(timeout=remaining)
        nom_stop.set()  # force-stop any still-running workers
        nom_total = len(nom_queue)
        logger.info(f"Nominatim fallback: {nom_matched_count}/{nom_total} matched (ran concurrently)")

        # Merge Nominatim results into batch results + save to disk cache
        nom_new = 0
        for uid, result in nom_results.items():
            if uid not in batch_geo_results or batch_geo_results[uid] is None:
                batch_geo_results[uid] = result
        # Cache all Nominatim hits
        uid_to_addr = {uid: addr for uid, addr in uncached_addresses}
        for uid, result in nom_results.items():
            if _cache_geo_result(geo_disk_cache, uid_to_addr.get(uid, ""), result):
                nom_new += 1
        if nom_new:
            _save_geo_cache(geo_disk_cache, f"after Nominatim, +{nom_new}")

        # Google Places API fallback for remaining failures
        google_key = os.environ.get("GOOGLE_API_KEY", "")
        still_failed = [
            (uid, addr) for uid, addr in uncached_addresses
            if batch_geo_results.get(uid) is None
        ]
        if google_key and still_failed:
            from lookup_engine.geocoder import GoogleGeocoder
            google_geo = GoogleGeocoder(google_key)
            logger.info(f"Google fallback: geocoding {len(still_failed)} remaining failures...")
            google_matched = 0
            google_new = 0
            for uid, addr in still_failed:
                try:
                    result = google_geo.geocode(addr)
                    if result:
                        result.block_geoid = ""
                        batch_geo_results[uid] = result
                        google_matched += 1
                        if _cache_geo_result(geo_disk_cache, addr, result):
                            google_new += 1
                        # Save every 100 Google results incrementally
                        if google_new > 0 and google_new % 100 == 0:
                            _save_geo_cache(geo_disk_cache, f"Google progress, +{google_new}")
                except Exception as e:
                    logger.debug(f"Google geocode error for {addr[:50]}: {e}")
            if google_new:
                _save_geo_cache(geo_disk_cache, f"after Google, +{google_new}")
            logger.info(f"Google fallback: {google_matched}/{len(still_failed)} matched")

    phase1_time = time.time() - t_phase1
    logger.info(f"Phase 1 complete: {phase1_time:.1f}s")

    # ================================================================
    # PHASE 2: Spatial Lookup + Comparison
    # ================================================================
    t_phase2 = time.time()
    logger.info("=" * 60)
    logger.info("PHASE 2: Spatial lookup + comparison")

    # Spatial result disk cache — stores full ProviderResult data keyed by address.
    # On re-runs, this skips all spatial lookups (shapefile queries + state GIS HTTP calls).
    SPATIAL_CACHE_FILE = Path(__file__).parent / "data" / "spatial_cache.json"
    spatial_cache = {}
    spatial_cache_dirty = 0
    if SPATIAL_CACHE_FILE.exists():
        try:
            spatial_cache = json.loads(SPATIAL_CACHE_FILE.read_text())
            logger.info(f"Spatial cache loaded: {len(spatial_cache):,} entries")
        except Exception as e:
            logger.warning(f"Failed to load spatial cache: {e}")

    def _pr_to_dict(pr):
        """Serialize a ProviderResult to a dict for caching."""
        if pr is None:
            return None
        return {
            "provider_name": pr.provider_name,
            "canonical_id": pr.canonical_id,
            "eia_id": pr.eia_id,
            "utility_type": pr.utility_type,
            "confidence": round(pr.confidence, 4),
            "match_method": pr.match_method,
            "is_deregulated": pr.is_deregulated,
            "deregulated_note": pr.deregulated_note,
            "polygon_source": pr.polygon_source,
            "needs_review": pr.needs_review,
            "alternatives": pr.alternatives,
            "catalog_id": pr.catalog_id,
            "catalog_title": pr.catalog_title,
            "id_match_score": pr.id_match_score,
            "id_confident": pr.id_confident,
        }

    def _dict_to_pr(d):
        """Deserialize a dict back to a ProviderResult."""
        if d is None:
            return None
        from lookup_engine.models import ProviderResult
        return ProviderResult(
            provider_name=d["provider_name"],
            canonical_id=d.get("canonical_id"),
            eia_id=d.get("eia_id"),
            utility_type=d.get("utility_type", "electric"),
            confidence=d.get("confidence", 0.0),
            match_method=d.get("match_method", "none"),
            is_deregulated=d.get("is_deregulated", False),
            deregulated_note=d.get("deregulated_note"),
            polygon_source=d.get("polygon_source"),
            needs_review=d.get("needs_review", False),
            alternatives=d.get("alternatives", []),
            catalog_id=d.get("catalog_id"),
            catalog_title=d.get("catalog_title"),
            id_match_score=d.get("id_match_score", 0),
            id_confident=d.get("id_confident", False),
        )

    def _save_spatial_cache(reason=""):
        try:
            SPATIAL_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            SPATIAL_CACHE_FILE.write_text(json.dumps(spatial_cache))
            logger.info(f"Spatial cache saved: {len(spatial_cache):,} entries ({reason})")
        except Exception as e:
            logger.warning(f"Failed to save spatial cache: {e}")

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
            "tenant_catalog_id", "tenant_catalog_title", "tenant_id_match_score",
            "comparison", "match_detail", "id_match",
        ])

    # Stats tracking
    stats = {
        "total_processed": 0,
        "geocode_success": 0,
        "geocode_fail": 0,
        "geocode_fail_addresses": [],
    }
    comparison_counts = defaultdict(lambda: Counter())
    id_match_counts = defaultdict(lambda: Counter())
    mismatch_pairs = defaultdict(lambda: Counter())
    state_mismatches = defaultdict(lambda: Counter())
    tdu_breakdown = Counter()
    tx_coop_muni_correct = 0
    tx_total_electric = 0
    tx_no_result = 0

    # Process each row — geocoding is already done, just spatial + compare
    times_per_row = []
    google_fallback_addresses = []

    def _do_spatial_lookup(i, row):
        """Run spatial lookups for a single row. Returns all data needed for comparison."""
        nonlocal spatial_cache_dirty
        row_idx = start_idx + i
        address = row.get("display", "").strip()
        if not address:
            return {"i": i, "row": row, "row_idx": row_idx, "address": "", "skip": True}

        state = _extract_state(address)
        _zip_m = re.search(r"\b(\d{5})(?:-\d{4})?\s*$", address)
        addr_zip = _zip_m.group(1) if _zip_m else ""
        _city_m = re.search(r",\s*([^,]+?)\s*,\s*[A-Z]{2}", address)
        addr_city = _city_m.group(1).strip() if _city_m else ""
        addr_county = ""
        row_key = str(row_idx)

        _lkw = dict(zip_code=addr_zip, city=addr_city, county=addr_county, address=address)

        # Check spatial result cache first — skip all spatial queries on hit
        if address in spatial_cache:
            sc = spatial_cache[address]
            return {
                "i": i, "row": row, "row_idx": row_idx, "address": address,
                "state": state,
                "lat": sc["lat"], "lon": sc["lon"],
                "geocode_conf": sc.get("geocode_conf", 0.9),
                "block_geoid": sc.get("block_geoid", ""),
                "electric": _dict_to_pr(sc.get("electric")),
                "gas": _dict_to_pr(sc.get("gas")),
                "water": _dict_to_pr(sc.get("water")),
                "sewer": _dict_to_pr(sc.get("sewer")),
                "skip": False,
            }

        lat, lon = 0.0, 0.0
        geocode_conf = 0.0
        block_geoid = ""
        electric = None
        gas = None
        water = None
        sewer = None

        # Note: engine.cache (SQLite) is not thread-safe, so we only use
        # geo_disk_cache (plain dict) here. It covers 99%+ of addresses.
        if address in geo_disk_cache:
            gc = geo_disk_cache[address]
            lat = gc["lat"]
            lon = gc["lon"]
            geocode_conf = gc.get("confidence", 0.9)
            block_geoid = gc.get("block_geoid", "")
            addr_county = gc.get("county", "") or addr_county
            _lkw["county"] = addr_county
            if lat != 0.0 or lon != 0.0:
                electric = engine._lookup_with_state_gis(lat, lon, state, "electric", **_lkw)
                gas = engine._lookup_with_state_gis(lat, lon, state, "gas", **_lkw)
                water = engine._lookup_with_state_gis(lat, lon, state, "water", **_lkw) if not args.skip_water else None
                sewer = engine._lookup_sewer(lat, lon, state, _lkw.get("zip_code", ""), _lkw.get("city", ""), _lkw.get("county", ""), water)
        else:
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
                # engine.lookup uses SQLite cache (not thread-safe).
                # Flag for sequential fallback in the main thread.
                return {
                    "i": i, "row": row, "row_idx": row_idx, "address": address,
                    "skip": False, "needs_engine_lookup": True,
                }

        # Save to spatial cache
        spatial_cache[address] = {
            "lat": lat, "lon": lon, "geocode_conf": geocode_conf,
            "block_geoid": block_geoid,
            "electric": _pr_to_dict(electric),
            "gas": _pr_to_dict(gas),
            "water": _pr_to_dict(water),
            "sewer": _pr_to_dict(sewer),
        }
        spatial_cache_dirty += 1

        return {
            "i": i, "row": row, "row_idx": row_idx, "address": address,
            "state": state, "lat": lat, "lon": lon, "geocode_conf": geocode_conf,
            "block_geoid": block_geoid,
            "electric": electric, "gas": gas, "water": water, "sewer": sewer,
            "skip": False,
        }

    # Use thread pool to run spatial lookups across multiple rows in parallel.
    # Each row does 3 HTTP calls to state GIS APIs — overlapping these across
    # rows is the key speedup (from ~600 lines/min to ~6000+ lines/min).
    BATCH_SIZE = 100
    _lookup_pool = ThreadPoolExecutor(max_workers=32)

    for batch_start in range(0, total, BATCH_SIZE):
        batch_end = min(batch_start + BATCH_SIZE, total)
        batch_rows = rows_to_process[batch_start:batch_end]

        # Submit all rows in this batch to the thread pool
        futures = []
        for j, row in enumerate(batch_rows):
            futures.append(_lookup_pool.submit(_do_spatial_lookup, batch_start + j, row))

        # Collect results in order and process sequentially
        for future in futures:
            r = future.result()
            i = r["i"]
            row = r["row"]
            row_idx = r["row_idx"]
            t_row = time.time()

            if r["skip"]:
                stats["total_processed"] += 1
                continue

            address = r["address"]

            # Handle rows that need engine.lookup (SQLite, must run on main thread)
            if r.get("needs_engine_lookup"):
                result = engine.lookup(address, use_cache=True)
                lat = result.lat
                lon = result.lon
                geocode_conf = result.geocode_confidence
                block_geoid = ""
                electric = result.electric
                gas = result.gas
                water = result.water
                sewer = result.sewer
                state = _extract_state(address)
            else:
                state = r["state"]
                lat = r["lat"]
                lon = r["lon"]
                geocode_conf = r["geocode_conf"]
                block_geoid = r["block_geoid"]
                electric = r["electric"]
                gas = r["gas"]
                water = r["water"]
                sewer = r["sewer"]

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

                # Tenant provider ID matching
                tenant_catalog_id = ""
                tenant_catalog_title = ""
                tenant_id_match_score = ""
                tenant_clean = (tenant_raw or "").strip()
                if tenant_clean and not _is_tenant_null(tenant_clean) and engine.id_matcher.loaded:
                    tenant_id_result = engine.id_matcher.match(tenant_clean, utype, state)
                    if tenant_id_result:
                        tenant_catalog_id = tenant_id_result["id"]
                        tenant_catalog_title = tenant_id_result["title"]
                        tenant_id_match_score = tenant_id_result["match_score"]

                # ID-to-ID comparison (resolve through alias table first)
                engine_canonical = _resolve_canonical_id(engine_catalog_id)
                tenant_canonical = _resolve_canonical_id(tenant_catalog_id)
                if engine_canonical and tenant_canonical:
                    if str(engine_canonical) == str(tenant_canonical):
                        id_match = "ID_MATCH"
                    elif comparison == "MATCH_TDU":
                        id_match = "ID_MATCH_TDU"
                    elif comparison in ("MATCH", "MATCH_PARENT", "MATCH_ALT"):
                        id_match = "NAME_MATCH_ID_MISMATCH"
                    else:
                        id_match = "TRUE_MISMATCH"
                elif engine_canonical and not tenant_canonical:
                    id_match = "TENANT_ID_MISSING"
                elif not engine_canonical and tenant_canonical:
                    id_match = "ENGINE_ID_MISSING"
                else:
                    id_match = "BOTH_ID_MISSING"
                id_match_counts[utype][id_match] += 1

                writer.writerow([
                    address, state, utype, engine_name, engine_eia,
                    engine_conf, engine_method, engine_dereg, engine_source,
                    engine_needs_review, engine_alts,
                    engine_catalog_id, engine_catalog_title,
                    engine_id_score, engine_id_confident,
                    alt_catalog_ids,
                    tenant_raw.strip() if tenant_raw else "",
                    tenant_norm,
                    tenant_catalog_id, tenant_catalog_title, tenant_id_match_score,
                    comparison, detail, id_match,
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
                        "", "",
                        "", "", "",  # tenant_catalog_id, tenant_catalog_title, tenant_id_match_score
                        "ENGINE_ONLY", f"block={block_geoid} count={inet_result['provider_count']} fiber={inet_result['has_fiber']} cable={inet_result['has_cable']} max_down={inet_result['max_download_speed']}", "",
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
                if spatial_cache_dirty >= 1000:
                    _save_spatial_cache(f"checkpoint at {processed}")
                    spatial_cache_dirty = 0

    outfile.close()
    _lookup_pool.shutdown(wait=False)
    engine.state_gis.save_disk_cache()
    if spatial_cache_dirty > 0:
        _save_spatial_cache("end of Phase 2")
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
        # Protected sources: authoritative data that the AI should NOT override.
        # Analysis showed AI overriding eia_zip has 0% accuracy and HIFLD has 5.7%.
        _PROTECTED_SOURCE_KEYWORDS = {"eia_zip", "eia_id", "hifld", "state_gis", "epa"}
        def _is_protected_source(source_str: str) -> bool:
            if not source_str:
                return False
            s = source_str.lower()
            return any(kw in s for kw in _PROTECTED_SOURCE_KEYWORDS)

        ai_items = []  # (row_index, resolve_kwargs)
        for ri, row in enumerate(all_result_rows):
            comp = row.get("comparison", "")
            try:
                conf = float(row.get("engine_confidence", "") or "1.0")
            except ValueError:
                conf = 1.0
            needs_ai = (
                comp in ("MATCH_ALT", "MISMATCH")
                or (conf < 0.80 and row.get("engine_alternatives"))
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
        ai_blocked_protected = 0
        for (ri, _kwargs), (_item, result) in zip(ai_items, batch_results):
            if not result:
                continue
            row = all_result_rows[ri]
            old_provider = row["engine_provider"]
            old_source = row.get("engine_source", "")

            # Post-resolution guard: if AI wants to REPLACE the primary provider
            # and the original came from a protected/authoritative source, reject it.
            # AI overriding eia_zip has 0% accuracy, HIFLD 5.7%.
            if result["provider"] != old_provider and _is_protected_source(old_source):
                ai_blocked_protected += 1
                continue

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
            f"Resolved {ai_resolved}, changed {ai_changed}, "
            f"blocked {ai_blocked_protected} (protected source), errors {resolver.error_count}"
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

    # Generate catalog dupe report
    _generate_catalog_dupe_report()

    # Generate report
    generate_report(stats, comparison_counts, mismatch_pairs, state_mismatches,
                    tdu_breakdown, tx_total_electric, tx_coop_muni_correct,
                    tx_no_result, total_time, times_per_row,
                    phase1_time, phase2_time, phase3_time,
                    id_match_counts=id_match_counts)


def _generate_catalog_dupe_report():
    """Generate catalog_dupes_report.txt from NAME_MATCH_ID_MISMATCH rows."""
    if not OUTPUT_CSV.exists():
        return
    dupes = Counter()
    with open(OUTPUT_CSV, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("id_match") == "NAME_MATCH_ID_MISMATCH":
                e_id = row.get("engine_catalog_id", "")
                e_title = row.get("engine_catalog_title", "") or row.get("engine_provider", "")
                t_id = row.get("tenant_catalog_id", "")
                t_title = row.get("tenant_catalog_title", "") or row.get("tenant_raw", "")
                key = (e_title, e_id, t_title, t_id)
                dupes[key] += 1

    if not dupes:
        logger.info("No catalog dupe pairs found.")
        return

    lines = ["Catalog Duplicate Pairs", "=" * 60, ""]
    lines.append("These rows matched by NAME but have different catalog IDs.")
    lines.append("This usually means the catalog has duplicate entries for the same provider.")
    lines.append("")
    for (e_title, e_id, t_title, t_id), count in dupes.most_common():
        lines.append(f"  {e_title} (ID {e_id}) <-> {t_title} (ID {t_id}): {count} occurrences")
    lines.append("")
    lines.append(f"Total pairs: {len(dupes)}")
    lines.append(f"Total affected rows: {sum(dupes.values())}")

    report_path = Path("catalog_dupes_report.txt")
    report_path.write_text("\n".join(lines))
    logger.info(f"Catalog dupe report: {report_path} ({len(dupes)} pairs, {sum(dupes.values())} rows)")


def generate_report(stats, comparison_counts, mismatch_pairs, state_mismatches,
                    tdu_breakdown, tx_total_electric, tx_coop_muni_correct,
                    tx_no_result, total_time, times_per_row,
                    phase1_time=0, phase2_time=0, phase3_time=0,
                    id_match_counts=None):
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

    # Provider ID Match Rate (headline metric)
    if id_match_counts:
        lines.append("### Provider ID Match Rate")
        lines.append("")
        # Catalog alias stats
        if CATALOG_ID_ALIASES:
            n_aliases = len(CATALOG_ID_ALIASES)
            n_groups = len(set(CATALOG_ID_ALIASES.values()))
            lines.append(f"- Catalog ID alias table: {n_aliases} aliases across {n_groups} canonical groups")
        lines.append("")
        overall_id_match = 0
        overall_id_scoreable = 0
        for utype in ["electric", "gas", "water"]:
            idc = id_match_counts[utype]
            id_scoreable = idc["ID_MATCH"] + idc.get("ID_MATCH_TDU", 0) + idc["NAME_MATCH_ID_MISMATCH"] + idc["TRUE_MISMATCH"]
            id_correct = idc["ID_MATCH"] + idc.get("ID_MATCH_TDU", 0)
            id_pct = id_correct / id_scoreable * 100 if id_scoreable else 0
            overall_id_match += id_correct
            overall_id_scoreable += id_scoreable
            lines.append(f"- **{utype.title()}: {id_correct:,}/{id_scoreable:,} ({id_pct:.1f}%)**")
        lines.append(f"- Sewer: N/A (no tenant data)")
        lines.append(f"- Internet: N/A (informational)")
        overall_pct = overall_id_match / overall_id_scoreable * 100 if overall_id_scoreable else 0
        lines.append(f"- **Overall: {overall_id_match:,}/{overall_id_scoreable:,} ({overall_pct:.1f}%)**")
        lines.append("")

        # ID Match Breakdown
        lines.append("### ID Match Breakdown")
        lines.append("")
        all_idc = Counter()
        for utype in ["electric", "gas", "water"]:
            for k, v in id_match_counts[utype].items():
                all_idc[k] += v
        total_id = sum(all_idc.values())
        for bucket, label in [
            ("ID_MATCH", "same catalog ID ✓"),
            ("ID_MATCH_TDU", "TX deregulated — TDU vs REP, both correct ✓"),
            ("NAME_MATCH_ID_MISMATCH", "catalog dupe issue, not engine error"),
            ("TRUE_MISMATCH", "engine got it wrong ✗"),
            ("TENANT_ID_MISSING", "couldn't ID-match tenant name"),
            ("ENGINE_ID_MISSING", "couldn't ID-match engine name"),
            ("BOTH_ID_MISSING", "neither side matched"),
        ]:
            cnt = all_idc.get(bucket, 0)
            pct = cnt / total_id * 100 if total_id else 0
            lines.append(f"- {bucket:30s} {cnt:>6,} ({pct:5.1f}%)  — {label}")
        lines.append("")

        # Adjusted accuracy (treating NAME_MATCH_ID_MISMATCH as correct)
        lines.append("### Adjusted Accuracy (treating catalog dupes as correct)")
        lines.append("")
        for utype in ["electric", "gas", "water"]:
            idc = id_match_counts[utype]
            adj_scoreable = idc["ID_MATCH"] + idc.get("ID_MATCH_TDU", 0) + idc["NAME_MATCH_ID_MISMATCH"] + idc["TRUE_MISMATCH"]
            adj_correct = idc["ID_MATCH"] + idc.get("ID_MATCH_TDU", 0) + idc["NAME_MATCH_ID_MISMATCH"]
            adj_pct = adj_correct / adj_scoreable * 100 if adj_scoreable else 0
            lines.append(f"- {utype.title()}: {adj_correct:,}/{adj_scoreable:,} ({adj_pct:.1f}%)")
        lines.append("")

    # Per utility type — Name-Based Accuracy
    for utype in ["electric", "gas", "water"]:
        counts = comparison_counts[utype]
        lines.append(f"### {utype.title()} Name-Based Accuracy")

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
            # Parse alternatives from CSV if available
            alts_raw = row.get("engine_alternatives", "")
            alts = []
            if alts_raw:
                for a in alts_raw.split("|"):
                    a = a.strip()
                    if a:
                        alts.append({"provider": a})
            comparison, detail, tenant_norm = compare_providers(
                engine_name, tenant_raw, utype, state, alternatives=alts
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
        old_scoreable = old_c["MATCH"] + old_c["MATCH_TDU"] + old_c["MATCH_PARENT"] + old_c["MATCH_ALT"] + old_c["MISMATCH"]
        old_correct = old_c["MATCH"] + old_c["MATCH_TDU"] + old_c["MATCH_PARENT"] + old_c["MATCH_ALT"]
        old_acc = old_correct / old_scoreable * 100 if old_scoreable else 0
        new_scoreable = new_c["MATCH"] + new_c["MATCH_TDU"] + new_c["MATCH_PARENT"] + new_c["MATCH_ALT"] + new_c["MISMATCH"]
        new_correct = new_c["MATCH"] + new_c["MATCH_TDU"] + new_c["MATCH_PARENT"] + new_c["MATCH_ALT"]
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
