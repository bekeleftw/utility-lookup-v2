#!/usr/bin/env python3
"""
Consolidate all provider name normalization sources into one canonical file.

Reads from 6 independent sources:
1. provider_normalizer.py  PROVIDER_ALIASES  (~60 providers)
2. serp_verification.py    UTILITY_ALIASES   (~100 providers)
3. cross_validation.py     PROVIDER_ALIASES  (~20 providers)
4. provider_aliases.json   auto-generated    (~400 providers)
5. brand_resolver.py       COMMON_BRAND_MAPPINGS (~30 providers)
6. utility_name_normalizer.py UTILITY_ALIASES (~50 providers)

Outputs:
- data/canonical_providers.json  — single merged mapping with 3 relationships:
    1. ALIAS:          name variants that collapse to one canonical ID
    2. DISPLAY NAME:   consumer-facing brand shown in API responses
    3. PARENT COMPANY: corporate ownership metadata (NEVER used for matching)
- consolidation_report.txt       — conflicts, coverage gaps, parent-co errors
"""

import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Set, Tuple

REPO_ROOT = Path(__file__).parent
OUTPUT_FILE = REPO_ROOT / "data" / "canonical_providers.json"
REPORT_FILE = REPO_ROOT / "consolidation_report.txt"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def clean_alias(name: str) -> str:
    """Collapse whitespace, strip trailing commas/periods."""
    if not name:
        return ""
    name = name.strip().rstrip(",").strip()
    name = re.sub(r"\s+", " ", name)
    return name


def norm_key(name: str) -> str:
    """Aggressive key for dedup: lowercase, strip punctuation, collapse ws."""
    n = clean_alias(name).lower()
    n = re.sub(r"[^\w\s]", "", n)
    return " ".join(n.split())


def is_compound_key(name: str) -> bool:
    """Detect compound keys like 'PG&E, Southern California Edison'."""
    # Split on comma and check if both halves look like provider names
    if ", " not in name:
        return False
    parts = [p.strip() for p in name.split(", ")]
    # If any part is a state suffix (2 chars) it's not compound
    if any(len(p) <= 3 for p in parts):
        return False
    # If we have 2+ substantial parts, it's compound
    substantial = [p for p in parts if len(p) > 5]
    return len(substantial) >= 2


def names_are_similar(name1: str, name2: str) -> bool:
    """Check if two provider names are plausibly the same entity.

    Used to filter provider_aliases.json which records disagreements,
    not true aliases.  'Oncor' and 'Bluebonnet Electric' are NOT similar.
    'Oncor' and 'Oncor Electric Delivery' ARE similar.
    """
    k1 = norm_key(name1)
    k2 = norm_key(name2)
    if not k1 or not k2:
        return False
    # Exact match
    if k1 == k2:
        return True
    # Substring containment (both > 3 chars to avoid false positives)
    if len(k1) > 3 and len(k2) > 3:
        if k1 in k2 or k2 in k1:
            return True
    # First significant word match (e.g. "duke energy" vs "duke energy florida")
    w1 = [w for w in k1.split() if len(w) > 3]
    w2 = [w for w in k2.split() if len(w) > 3]
    if w1 and w2 and w1[0] == w2[0]:
        return True
    return False


# ---------------------------------------------------------------------------
# Source readers — each returns Dict[canonical_name, List[alias]]
# ---------------------------------------------------------------------------

def read_provider_normalizer() -> Dict[str, List[str]]:
    """Source 1: provider_normalizer.py PROVIDER_ALIASES."""
    # Try git-restored original first (source file was already modified)
    orig = Path("/tmp/orig_provider_normalizer.py")
    spec_path = orig if orig.exists() else REPO_ROOT / "provider_normalizer.py"
    text = spec_path.read_text()
    match = re.search(
        r"^PROVIDER_ALIASES\s*=\s*\{(.+?)^\}",
        text,
        re.MULTILINE | re.DOTALL,
    )
    if not match:
        print("WARNING: Could not extract PROVIDER_ALIASES from provider_normalizer.py")
        return {}
    dict_text = "{" + match.group(1) + "}"
    ns: dict = {}
    exec(f"data = {dict_text}", ns)
    raw = ns.get("data", {})
    out: Dict[str, List[str]] = {}
    for canonical, aliases in raw.items():
        canonical_clean = clean_alias(canonical)
        out[canonical_clean] = [clean_alias(a) for a in aliases if clean_alias(a)]
    return out


def read_serp_verification() -> Dict[str, List[str]]:
    """Source 2: serp_verification.py UTILITY_ALIASES."""
    orig = Path("/tmp/orig_serp_verification.py")
    path = orig if orig.exists() else REPO_ROOT / "serp_verification.py"
    text = path.read_text()
    match = re.search(
        r"^UTILITY_ALIASES\s*=\s*\{(.+?)^\}",
        text,
        re.MULTILINE | re.DOTALL,
    )
    if not match:
        print("WARNING: Could not extract UTILITY_ALIASES from serp_verification.py")
        return {}
    dict_text = "{" + match.group(1) + "}"
    ns: dict = {}
    exec(f"data = {dict_text}", ns)
    raw = ns.get("data", {})

    # serp_verification uses short group-keys -> [aliases].
    # Promote the longest alias as canonical display name.
    out: Dict[str, List[str]] = {}
    for key, aliases in raw.items():
        aliases_clean = [clean_alias(a) for a in aliases if clean_alias(a)]
        if not aliases_clean:
            continue
        key_clean = clean_alias(key)
        all_names = list(dict.fromkeys([key_clean] + aliases_clean))
        candidates = [n for n in all_names if len(n) > 4]
        canonical = max(candidates, key=len) if candidates else all_names[0]
        canonical = canonical.title()
        canonical = _fix_casing(canonical)
        remaining = [a for a in all_names if a.lower() != canonical.lower()]
        if canonical in out:
            out[canonical].extend(remaining)
        else:
            out[canonical] = remaining
    return out


def read_cross_validation() -> Dict[str, List[str]]:
    """Source 3: cross_validation.py PROVIDER_ALIASES."""
    orig = Path("/tmp/orig_cross_validation.py")
    path = orig if orig.exists() else REPO_ROOT / "cross_validation.py"
    text = path.read_text()
    match = re.search(
        r"^PROVIDER_ALIASES\s*=\s*\{(.+?)^\}",
        text,
        re.MULTILINE | re.DOTALL,
    )
    if not match:
        print("WARNING: Could not extract PROVIDER_ALIASES from cross_validation.py")
        return {}
    dict_text = "{" + match.group(1) + "}"
    ns: dict = {}
    exec(f"data = {dict_text}", ns)
    raw = ns.get("data", {})

    out: Dict[str, List[str]] = {}
    for key, aliases in raw.items():
        key_clean = clean_alias(key)
        aliases_clean = [clean_alias(a) for a in aliases if clean_alias(a)]
        all_names = list(dict.fromkeys([key_clean] + aliases_clean))
        candidates = [n for n in all_names if len(n) > 4]
        canonical = max(candidates, key=len) if candidates else all_names[0]
        canonical = canonical.title()
        canonical = _fix_casing(canonical)
        remaining = [a for a in all_names if a.lower() != canonical.lower()]
        if canonical in out:
            out[canonical].extend(remaining)
        else:
            out[canonical] = remaining
    return out


def read_provider_aliases_json() -> Dict[str, List[str]]:
    """Source 4: provider_aliases.json (auto-generated from comparison).

    This file is noisy — keys can be compound names like
    'PG&E, Southern California Edison'.  We skip compound keys
    and only keep clean single-provider entries.
    """
    path = REPO_ROOT / "provider_aliases.json"
    if not path.exists():
        print(f"WARNING: {path} not found, skipping")
        return {}
    with open(path) as f:
        raw = json.load(f)
    out: Dict[str, List[str]] = {}
    skipped = 0
    for key, aliases in raw.items():
        key_clean = clean_alias(key)
        if not key_clean:
            continue
        # Skip compound keys that would cross-link unrelated providers
        if is_compound_key(key_clean):
            skipped += 1
            continue
        aliases_clean = [
            clean_alias(a) for a in aliases
            if clean_alias(a) and not is_compound_key(clean_alias(a))
        ]
        if aliases_clean:
            out[key_clean] = aliases_clean
    if skipped:
        print(f"     (skipped {skipped} compound keys)")
    return out


# ---------------------------------------------------------------------------
# Parent-company vs operating-utility classification
# ---------------------------------------------------------------------------
# Holding companies that should NEVER be returned as a provider name.
# If CORPORATE_MERGERS maps an operating utility to one of these,
# that entry is an error: the relationship is parent_company, not alias.
HOLDING_COMPANIES = {
    "berkshire hathaway energy",
    "wec energy group",
    "southern company",
    "exelon",
    "nextera energy",
    "eversource energy",   # holding co; operating brands are Eversource CT/MA/NH
    "entergy corporation",
    "firstenergy corp",
    "edison international",
    "sempra energy",
    "cms energy",
    "alliant energy",
    "avangrid",
    "ppl corporation",
    "nisource",
    "agl resources",       # old holding co for Atlanta Gas Light
    "scana",               # old holding co absorbed by Dominion
}

# Known parent → child operating-utility relationships.
# These are stored as metadata, never used for matching.
PARENT_COMPANY_MAP: Dict[str, List[str]] = {
    "Berkshire Hathaway Energy": [
        "MidAmerican Energy",
        "PacifiCorp",
        "NV Energy",
    ],
    "WEC Energy Group": [
        "WE Energies",
        "Wisconsin Public Service",
        "Peoples Gas",
        "North Shore Gas",
        "Michigan Gas Utilities",
        "Minnesota Energy Resources",
    ],
    "Southern Company": [
        "Georgia Power",
        "Alabama Power",
        "Mississippi Power",
        "Atlanta Gas Light",
    ],
    "Duke Energy": [
        "Duke Energy Carolinas",
        "Duke Energy Florida",
        "Duke Energy Indiana",
        "Duke Energy Ohio",
        "Duke Energy Kentucky",
        "Piedmont Natural Gas",
    ],
    "Dominion Energy": [
        "Dominion Energy Virginia",
        "Dominion Energy South Carolina",
    ],
}


def read_brand_resolver() -> Tuple[Dict[str, List[str]], Dict[str, str], List[dict]]:
    """Source 5: brand_resolver.py COMMON_BRAND_MAPPINGS + CORPORATE_MERGERS.

    Returns:
        (aliases_dict, display_names, parent_co_errors)
        - aliases_dict: canonical -> [aliases]  (only true mergers/rebrands)
        - display_names: legal_name_lower -> brand_display_name
        - parent_co_errors: list of flagged entries from CORPORATE_MERGERS
    """
    orig = Path("/tmp/orig_brand_resolver.py")
    path = orig if orig.exists() else REPO_ROOT / "brand_resolver.py"
    text = path.read_text()

    match = re.search(
        r"^COMMON_BRAND_MAPPINGS\s*=\s*\{(.+?)^\}",
        text,
        re.MULTILINE | re.DOTALL,
    )
    brand_map: Dict[str, str] = {}
    if match:
        dict_text = "{" + match.group(1) + "}"
        ns: dict = {}
        exec(f"data = {dict_text}", ns)
        brand_map = ns.get("data", {})

    match2 = re.search(
        r"^CORPORATE_MERGERS\s*=\s*\{(.+?)^\}",
        text,
        re.MULTILINE | re.DOTALL,
    )
    mergers: Dict[str, str] = {}
    if match2:
        dict_text2 = "{" + match2.group(1) + "}"
        ns2: dict = {}
        exec(f"data = {dict_text2}", ns2)
        mergers = ns2.get("data", {})

    # Invert COMMON_BRAND_MAPPINGS: alias(lowercase) -> brand
    # This gives us display_name mappings AND alias data
    out: Dict[str, List[str]] = {}
    display_names: Dict[str, str] = {}  # legal_lower -> brand
    for alias_lower, brand in brand_map.items():
        brand_clean = clean_alias(brand)
        alias_clean = clean_alias(alias_lower)
        display_names[alias_clean.lower()] = brand_clean
        if brand_clean not in out:
            out[brand_clean] = []
        if alias_clean.lower() != brand_clean.lower():
            out[brand_clean].append(alias_clean)

    # Audit CORPORATE_MERGERS: separate true mergers from parent-co errors
    parent_co_errors: List[dict] = []
    for old_name, new_name in mergers.items():
        new_lower = new_name.lower().strip()
        old_clean = clean_alias(old_name)
        new_clean = clean_alias(new_name)

        if new_lower in HOLDING_COMPANIES:
            # ERROR: operating utility mapped to holding company
            parent_co_errors.append({
                "old_name": old_clean,
                "mapped_to": new_clean,
                "reason": f"\"{new_clean}\" is a holding company, not an operating utility",
                "action": "Should be parent_company metadata, not an alias",
            })
        else:
            # Legitimate merger/rebrand — treat as alias
            if new_clean not in out:
                out[new_clean] = []
            out[new_clean].append(old_clean)

    return out, display_names, parent_co_errors


def read_utility_name_normalizer() -> Dict[str, List[str]]:
    """Source 6: utility_name_normalizer.py UTILITY_ALIASES."""
    orig = Path("/tmp/orig_utility_name_normalizer.py")
    path = orig if orig.exists() else REPO_ROOT / "utility_name_normalizer.py"
    text = path.read_text()
    match = re.search(
        r"^UTILITY_ALIASES\s*=\s*\{(.+?)^\}",
        text,
        re.MULTILINE | re.DOTALL,
    )
    if not match:
        print("WARNING: Could not extract UTILITY_ALIASES from utility_name_normalizer.py")
        return {}
    dict_text = "{" + match.group(1) + "}"
    ns: dict = {}
    exec(f"data = {dict_text}", ns)
    raw = ns.get("data", {})

    out: Dict[str, List[str]] = {}
    for canonical, aliases in raw.items():
        canonical_clean = clean_alias(canonical)
        aliases_clean = [clean_alias(a) for a in aliases if clean_alias(a)]
        out[canonical_clean] = aliases_clean
    return out


# ---------------------------------------------------------------------------
# Casing fixups
# ---------------------------------------------------------------------------

_CASING_OVERRIDES = {
    "pg&e": "PG&E",
    "pge": "PG&E",
    "sce": "SCE",
    "sdg&e": "SDG&E",
    "sdge": "SDG&E",
    "socalgas": "SoCalGas",
    "comed": "ComEd",
    "cps energy": "CPS Energy",
    "aep": "AEP",
    "aep texas": "AEP Texas",
    "aep ohio": "AEP Ohio",
    "dte": "DTE",
    "dte energy": "DTE Energy",
    "pse&g": "PSE&G",
    "pseg": "PSE&G",
    "bge": "BGE",
    "bg&e": "BGE",
    "og&e": "OG&E",
    "oge": "OG&E",
    "lg&e": "LG&E",
    "lge": "LG&E",
    "fpl": "FPL",
    "ouc": "OUC",
    "jea": "JEA",
    "smud": "SMUD",
    "ladwp": "LADWP",
    "srp": "SRP",
    "aps": "APS",
    "tep": "TEP",
    "peco": "PECO",
    "ppl": "PPL",
    "ppl electric": "PPL Electric",
    "nipsco": "NIPSCO",
    "ipl": "IPL",
    "teco": "TECO Energy",
    "teco energy": "TECO Energy",
    "tnmp": "TNMP",
    "pnm": "PNM",
    "mdu": "MDU",
    "mud": "MUD",
    "we energies": "WE Energies",
    "con edison": "Con Edison",
    "coned": "Con Edison",
    "nyseg": "NYSEG",
    "rg&e": "RG&E",
    "pepco": "PEPCO",
    "pgw": "PGW",
    "ugi": "UGI",
    "met-ed": "Met-Ed",
    "psc colorado": "PSC of Colorado",
    "nv energy": "NV Energy",
    "lg&e/ku": "LG&E/KU",
    "centerpoint energy": "CenterPoint Energy",
    "centerpoint": "CenterPoint Energy",
    "xcel energy": "Xcel Energy",
    "xcel energy colorado": "Xcel Energy Colorado",
    "xcel energy minnesota": "Xcel Energy Minnesota",
    "midamerican energy": "MidAmerican Energy",
    "pacificorp": "PacifiCorp",
    "con ed": "Con Edison",
}


def _fix_casing(name: str) -> str:
    """Fix casing for known abbreviations and brands."""
    key = name.lower().strip()
    if key in _CASING_OVERRIDES:
        return _CASING_OVERRIDES[key]
    name = name.replace(" & ", " & ")
    return name


# ---------------------------------------------------------------------------
# Merge engine
# ---------------------------------------------------------------------------

def _best_display(existing: str, candidate: str) -> str:
    """Pick the better display name between two options."""
    if not existing:
        return candidate
    if not candidate:
        return existing
    # Prefer non-ALL-CAPS
    if existing.isupper() and not candidate.isupper():
        return candidate
    if candidate.isupper() and not existing.isupper():
        return existing
    # Prefer longer (more descriptive) if neither is ALL CAPS
    if len(candidate) > len(existing):
        return candidate
    return existing


def merge_all_sources(
    sources: List[Tuple[str, Dict[str, List[str]]]],
    display_names: Dict[str, str],
) -> Tuple[Dict[str, dict], List[dict], List[dict]]:
    """
    Merge all sources into one canonical mapping with 3 relationships.

    Strategy:
    1. Build canonical groups from the 5 curated sources (not provider_aliases.json)
    2. Merge groups that share aliases across curated sources only
    3. Add provider_aliases.json entries into existing groups (no new cross-links)
    4. Attach display_name and parent_company metadata
    5. Detect conflicts and coverage gaps

    Returns:
        (merged, conflicts, coverage_gaps)
        - merged: {canonical_name: {display_name, aliases[], parent_company?}}
    """
    # Separate curated sources from the noisy auto-generated one
    curated_sources = [(n, d) for n, d in sources if n != "provider_aliases_json"]
    auto_source = dict(sources).get("provider_aliases_json", {})

    # Collect brand names from brand_resolver — these are the preferred
    # consumer-facing canonical names (e.g. "PG&E" not "Pacific Gas & Electric")
    brand_canonicals: Dict[str, str] = {}  # norm_key -> brand display name
    for src_name, src_data in sources:
        if src_name == "brand_resolver":
            for brand_name in src_data.keys():
                bk = norm_key(brand_name)
                brand_canonicals[bk] = _fix_casing(clean_alias(brand_name))

    # Also collect provider_normalizer canonical names as strong preferences
    pn_canonicals: Dict[str, str] = {}  # norm_key -> display name
    for src_name, src_data in sources:
        if src_name == "provider_normalizer":
            for canon_name in src_data.keys():
                pk = norm_key(canon_name)
                pn_canonicals[pk] = _fix_casing(clean_alias(canon_name))

    # Build a set of holding-company norm_keys to filter out of alias lists.
    # These must NEVER be used as aliases — they cross-link unrelated utilities.
    holding_co_keys: Set[str] = set()
    for hc in HOLDING_COMPANIES:
        hk = norm_key(hc)
        if hk:
            holding_co_keys.add(hk)

    # --- Phase 1: Build canonical groups from curated sources ---
    # canonical_display_name -> set of norm_keys (aliases)
    canon_groups: Dict[str, Set[str]] = {}
    # norm_key -> canonical_display_name (for conflict detection)
    key_to_canon: Dict[str, str] = {}
    # Track which sources contributed each canonical
    canonical_source_tracker: Dict[str, Set[str]] = defaultdict(set)
    # Best display text for each norm_key
    key_to_text: Dict[str, str] = {}

    for source_name, data in curated_sources:
        for canonical, aliases in data.items():
            canonical_clean = clean_alias(canonical)
            canonical_fixed = _fix_casing(canonical_clean)
            ck = norm_key(canonical_clean)

            canonical_source_tracker[canonical_fixed].add(source_name)

            # Update best display text
            key_to_text[ck] = _best_display(key_to_text.get(ck, ""), canonical_fixed)

            all_keys = {ck}
            for alias in aliases:
                ak = norm_key(alias)
                # Skip holding company names — they are parent_company
                # metadata, not aliases, and would cross-link unrelated utilities
                if ak and ak not in holding_co_keys:
                    all_keys.add(ak)
                    a_clean = clean_alias(alias)
                    a_fixed = _fix_casing(a_clean)
                    key_to_text[ak] = _best_display(key_to_text.get(ak, ""), a_fixed)

            # Check if this canonical or any alias already belongs to a group
            existing_canon = None
            for k in all_keys:
                if k in key_to_canon:
                    existing_canon = key_to_canon[k]
                    break

            if existing_canon:
                # Merge into existing group
                canon_groups[existing_canon].update(all_keys)
                for k in all_keys:
                    key_to_canon[k] = existing_canon
                # Update display name if this one is better
                better = _best_display(existing_canon, canonical_fixed)
                if better != existing_canon:
                    # Rename the group
                    group = canon_groups.pop(existing_canon)
                    canon_groups[better] = group
                    for k in group:
                        key_to_canon[k] = better
                    # Merge source tracking
                    canonical_source_tracker[better].update(
                        canonical_source_tracker.pop(existing_canon, set())
                    )
            else:
                # New group
                canon_groups[canonical_fixed] = all_keys
                for k in all_keys:
                    key_to_canon[k] = canonical_fixed

    # --- Phase 1b: Apply brand names as canonical overrides ---
    # brand_resolver names are the consumer-facing brands and should be
    # the canonical name when they exist in a group.
    renames: List[Tuple[str, str]] = []  # (old_canon, new_canon)
    for old_canon, alias_keys in list(canon_groups.items()):
        # Check if any key in this group has a brand name
        best_brand = None
        for ak in alias_keys:
            if ak in brand_canonicals:
                best_brand = brand_canonicals[ak]
                break
        # Fallback: check provider_normalizer canonical names
        if not best_brand:
            for ak in alias_keys:
                if ak in pn_canonicals:
                    best_brand = pn_canonicals[ak]
                    break
        if best_brand and best_brand != old_canon:
            renames.append((old_canon, best_brand))

    for old_canon, new_canon in renames:
        if old_canon in canon_groups:
            group = canon_groups.pop(old_canon)
            canon_groups[new_canon] = group
            for k in group:
                key_to_canon[k] = new_canon
            # Merge source tracking
            old_sources = canonical_source_tracker.pop(old_canon, set())
            canonical_source_tracker[new_canon].update(old_sources)

    # --- Phase 2: Add provider_aliases.json aliases into existing groups ---
    # IMPORTANT: provider_aliases.json records disagreements (CSV vs API),
    # not true name aliases.  We only add an alias if it is genuinely
    # similar to the key (substring match or shared first word).
    auto_added = 0
    auto_skipped = 0
    auto_new = 0
    for key, aliases in auto_source.items():
        key_clean = clean_alias(key)
        kk = norm_key(key_clean)

        # Filter aliases to only those that look like name variants of the key
        similar_aliases = [a for a in aliases if names_are_similar(key_clean, a)]
        dissimilar = len(aliases) - len(similar_aliases)
        auto_skipped += dissimilar

        # Try to find which group this key belongs to
        target_canon = key_to_canon.get(kk)

        if target_canon:
            # Add similar aliases to existing group
            for alias in similar_aliases:
                ak = norm_key(alias)
                if ak and ak not in key_to_canon:
                    canon_groups[target_canon].add(ak)
                    key_to_canon[ak] = target_canon
                    a_clean = clean_alias(alias)
                    a_fixed = _fix_casing(a_clean)
                    key_to_text[ak] = _best_display(key_to_text.get(ak, ""), a_fixed)
                    auto_added += 1
        else:
            # Check if any similar alias matches an existing group
            matched_canon = None
            for alias in similar_aliases:
                ak = norm_key(alias)
                if ak in key_to_canon:
                    matched_canon = key_to_canon[ak]
                    break

            if matched_canon:
                # Add key and remaining similar aliases to matched group
                if kk and kk not in key_to_canon:
                    canon_groups[matched_canon].add(kk)
                    key_to_canon[kk] = matched_canon
                    key_to_text[kk] = _best_display(key_to_text.get(kk, ""), _fix_casing(key_clean))
                    auto_added += 1
                for alias in similar_aliases:
                    ak = norm_key(alias)
                    if ak and ak not in key_to_canon:
                        canon_groups[matched_canon].add(ak)
                        key_to_canon[ak] = matched_canon
                        a_clean = clean_alias(alias)
                        a_fixed = _fix_casing(a_clean)
                        key_to_text[ak] = _best_display(key_to_text.get(ak, ""), a_fixed)
                        auto_added += 1
            else:
                # New provider not in any curated source — add as new group
                key_fixed = _fix_casing(key_clean)
                all_keys = {kk}
                key_to_text[kk] = _best_display(key_to_text.get(kk, ""), key_fixed)
                for alias in similar_aliases:
                    ak = norm_key(alias)
                    if ak:
                        all_keys.add(ak)
                        a_clean = clean_alias(alias)
                        a_fixed = _fix_casing(a_clean)
                        key_to_text[ak] = _best_display(key_to_text.get(ak, ""), a_fixed)
                canon_groups[key_fixed] = all_keys
                for k in all_keys:
                    key_to_canon[k] = key_fixed
                canonical_source_tracker[key_fixed].add("provider_aliases_json")
                auto_new += 1

    print(f"     (added {auto_added} aliases from provider_aliases.json to existing groups)")
    print(f"     (skipped {auto_skipped} dissimilar aliases from provider_aliases.json)")
    print(f"     (created {auto_new} new groups from provider_aliases.json)")

    # --- Phase 3: Build final merged dict with 3 relationships ---
    # Build reverse parent_company lookup: operating_utility_lower -> parent_name
    child_to_parent: Dict[str, str] = {}
    for parent, children in PARENT_COMPANY_MAP.items():
        for child in children:
            child_to_parent[child.lower()] = parent

    merged: Dict[str, dict] = {}
    for canonical, alias_keys in canon_groups.items():
        # Collect alias texts (excluding the canonical name itself)
        alias_texts = set()
        for ak in alias_keys:
            text = key_to_text.get(ak, "")
            if text and text.lower() != canonical.lower():
                alias_texts.add(text)

        # Determine display_name:
        # 1. Check if canonical itself has a brand mapping
        # 2. Check if any alias has a brand mapping
        # 3. Fall back to the canonical name itself
        # NEVER use a holding company name as display_name.
        # NEVER embed parent company in display_name (e.g. "Peoples Gas (WEC Energy)").
        def _valid_display(candidate: str) -> bool:
            cl = candidate.lower()
            if cl in HOLDING_COMPANIES:
                return False
            # Reject if it embeds a holding/parent company name
            for hc in HOLDING_COMPANIES:
                # Check both full name and name without trailing "corp"/"group"
                hc_stem = hc.replace(" group", "").replace(" corp", "").replace(" corporation", "")
                if hc in cl or (len(hc_stem) > 5 and hc_stem in cl):
                    return False
            # Reject if display differs from canonical only by being shorter
            # (e.g. "Duke Energy" for "Duke Energy Ohio" — loses specificity)
            if len(candidate) < len(canonical) and candidate.lower() in canonical.lower():
                return False
            return True

        disp = canonical
        if canonical.lower() in display_names:
            candidate = _fix_casing(display_names[canonical.lower()])
            if _valid_display(candidate):
                disp = candidate
        else:
            for ak in alias_keys:
                text = key_to_text.get(ak, "")
                if text and text.lower() in display_names:
                    candidate = _fix_casing(display_names[text.lower()])
                    if _valid_display(candidate):
                        disp = candidate
                        break

        # Determine parent_company (metadata only, never used for matching)
        parent = child_to_parent.get(canonical.lower())

        entry: dict = {
            "display_name": disp,
            "aliases": sorted(alias_texts, key=str.lower),
        }
        if parent:
            entry["parent_company"] = parent

        merged[canonical] = entry

    merged = dict(sorted(merged.items(), key=lambda x: x[0].lower()))

    # --- Phase 4: Detect conflicts ---
    # An alias that appears in multiple curated sources mapped to different canonicals
    alias_to_sources: Dict[str, Dict[str, List[str]]] = defaultdict(lambda: defaultdict(list))
    for source_name, data in curated_sources:
        for canonical, aliases in data.items():
            canonical_clean = clean_alias(canonical)
            canonical_fixed = _fix_casing(canonical_clean)
            ck = norm_key(canonical_clean)
            alias_to_sources[ck][canonical_fixed].append(source_name)
            for alias in aliases:
                ak = norm_key(alias)
                if ak:
                    alias_to_sources[ak][canonical_fixed].append(source_name)

    conflicts: List[dict] = []
    for alias_key, canon_map in alias_to_sources.items():
        # Normalize the canonical keys for comparison
        canon_norm_keys = set(norm_key(c) for c in canon_map.keys())
        if len(canon_norm_keys) > 1:
            alias_text = key_to_text.get(alias_key, alias_key)
            conflicts.append({
                "alias": alias_text,
                "alias_key": alias_key,
                "mappings": dict(canon_map),
            })

    conflicts.sort(key=lambda x: x["alias"].lower())

    # --- Phase 5: Coverage gaps ---
    coverage_gaps: List[dict] = []
    for canonical, src_set in canonical_source_tracker.items():
        if len(src_set) == 1:
            coverage_gaps.append({
                "canonical": canonical,
                "sources": list(src_set),
            })
    coverage_gaps.sort(key=lambda x: x["canonical"].lower())

    return merged, conflicts, coverage_gaps


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def generate_report(
    merged: Dict[str, dict],
    conflicts: List[dict],
    coverage_gaps: List[dict],
    parent_co_errors: List[dict],
    source_stats: List[Tuple[str, int, int]],
) -> str:
    """Generate a human-readable consolidation report."""
    lines = []
    lines.append("=" * 70)
    lines.append("PROVIDER NAME NORMALIZATION — CONSOLIDATION REPORT")
    lines.append("=" * 70)
    lines.append("")

    total_canonical = len(merged)
    total_aliases = sum(len(v["aliases"]) for v in merged.values())
    with_parent = sum(1 for v in merged.values() if v.get("parent_company"))
    with_display = sum(1 for k, v in merged.items() if v["display_name"] != k)
    lines.append("## SUMMARY")
    lines.append(f"  Total canonical providers:   {total_canonical}")
    lines.append(f"  Total aliases:               {total_aliases}")
    lines.append(f"  With display_name override:  {with_display}")
    lines.append(f"  With parent_company:         {with_parent}")
    lines.append(f"  Conflicts found:             {len(conflicts)}")
    lines.append(f"  Parent-company errors:       {len(parent_co_errors)}")
    lines.append(f"  Single-source providers:     {len(coverage_gaps)}")
    lines.append("")

    lines.append("## SOURCE BREAKDOWN")
    for name, n_canonical, n_aliases in source_stats:
        lines.append(f"  {name:40s}  {n_canonical:4d} canonical, {n_aliases:5d} aliases")
    lines.append("")

    # Parent-company errors (highest priority — these are bugs)
    if parent_co_errors:
        lines.append("## PARENT-COMPANY ERRORS (from CORPORATE_MERGERS)")
        lines.append("   These map an operating utility to a holding company.")
        lines.append("   The holding company should NEVER be returned as a provider name.")
        lines.append("")
        for i, e in enumerate(parent_co_errors, 1):
            lines.append(f"  {i}. \"{e['old_name']}\" -> \"{e['mapped_to']}\"")
            lines.append(f"     Reason: {e['reason']}")
            lines.append(f"     Action: {e['action']}")
            lines.append("")

    # Providers with parent_company metadata
    if with_parent:
        lines.append("## PARENT COMPANY RELATIONSHIPS (metadata only)")
        lines.append("   These are stored for reference but NEVER used for matching or display.")
        lines.append("")
        for canonical, entry in sorted(merged.items()):
            if entry.get("parent_company"):
                lines.append(f"  {canonical} (displays as \"{entry['display_name']}\") -> parent: {entry['parent_company']}")
        lines.append("")

    # Display name overrides
    if with_display:
        lines.append("## DISPLAY NAME OVERRIDES")
        lines.append("   Canonical name differs from consumer-facing display name.")
        lines.append("")
        for canonical, entry in sorted(merged.items()):
            if entry["display_name"] != canonical:
                lines.append(f"  {canonical} -> displays as \"{entry['display_name']}\"")
        lines.append("")

    if conflicts:
        lines.append("## CONFLICTS (same alias -> different canonicals)")
        lines.append("   These need manual review to pick the correct mapping.")
        lines.append("")
        for i, c in enumerate(conflicts[:100], 1):
            lines.append(f"  {i}. Alias: \"{c['alias']}\"  (key: {c['alias_key']})")
            for canonical, src_list in c["mappings"].items():
                lines.append(f"       -> \"{canonical}\"  (from: {', '.join(src_list)})")
            lines.append("")
        if len(conflicts) > 100:
            lines.append(f"  ... and {len(conflicts) - 100} more conflicts")
            lines.append("")

    if coverage_gaps:
        lines.append("## SINGLE-SOURCE PROVIDERS (coverage gaps)")
        lines.append("   These appear in only one source — may need verification.")
        lines.append("")
        for g in coverage_gaps[:200]:
            lines.append(f"  - \"{g['canonical']}\"  (from: {', '.join(g['sources'])})")
        if len(coverage_gaps) > 200:
            lines.append(f"  ... and {len(coverage_gaps) - 200} more")
        lines.append("")

    lines.append("## TOP 30 PROVIDERS BY ALIAS COUNT")
    sorted_by_aliases = sorted(merged.items(), key=lambda x: len(x[1]["aliases"]), reverse=True)
    for canonical, entry in sorted_by_aliases[:30]:
        aliases = entry["aliases"]
        disp = entry["display_name"]
        parent = entry.get("parent_company", "")
        label = f"{canonical}"
        if disp != canonical:
            label += f" (display: \"{disp}\")"
        if parent:
            label += f" [parent: {parent}]"
        lines.append(f"  {label} ({len(aliases)} aliases)")
        for a in aliases[:5]:
            lines.append(f"    - {a}")
        if len(aliases) > 5:
            lines.append(f"    ... and {len(aliases) - 5} more")
    lines.append("")

    lines.append("=" * 70)
    lines.append("END OF REPORT")
    lines.append("=" * 70)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    """Full regeneration: read all 6 original sources, merge, and produce
    canonical_providers.json with the 3-relationship schema.

    Source files were already modified to remove their dicts, so we read
    from git-restored originals in /tmp/orig_*.py when available.
    """
    print("Reading normalization sources...")

    sources: List[Tuple[str, Dict[str, List[str]]]] = []

    print("  1. provider_normalizer.py PROVIDER_ALIASES")
    s1 = read_provider_normalizer()
    sources.append(("provider_normalizer", s1))
    print(f"     -> {len(s1)} canonical, {sum(len(v) for v in s1.values())} aliases")

    print("  2. serp_verification.py UTILITY_ALIASES")
    s2 = read_serp_verification()
    sources.append(("serp_verification", s2))
    print(f"     -> {len(s2)} canonical, {sum(len(v) for v in s2.values())} aliases")

    print("  3. cross_validation.py PROVIDER_ALIASES")
    s3 = read_cross_validation()
    sources.append(("cross_validation", s3))
    print(f"     -> {len(s3)} canonical, {sum(len(v) for v in s3.values())} aliases")

    print("  4. provider_aliases.json")
    s4 = read_provider_aliases_json()
    sources.append(("provider_aliases_json", s4))
    print(f"     -> {len(s4)} canonical, {sum(len(v) for v in s4.values())} aliases")

    print("  5. brand_resolver.py COMMON_BRAND_MAPPINGS + CORPORATE_MERGERS")
    s5, brand_display_names, parent_co_errors = read_brand_resolver()
    sources.append(("brand_resolver", s5))
    print(f"     -> {len(s5)} canonical, {sum(len(v) for v in s5.values())} aliases")
    print(f"     -> {len(brand_display_names)} display-name mappings")
    if parent_co_errors:
        print(f"     !! {len(parent_co_errors)} CORPORATE_MERGERS entries flagged as parent-company errors")

    print("  6. utility_name_normalizer.py UTILITY_ALIASES")
    s6 = read_utility_name_normalizer()
    sources.append(("utility_name_normalizer", s6))
    print(f"     -> {len(s6)} canonical, {sum(len(v) for v in s6.values())} aliases")

    print("\nMerging all sources...")
    merged, conflicts, coverage_gaps = merge_all_sources(sources, brand_display_names)

    total_canonical = len(merged)
    total_aliases = sum(len(v["aliases"]) for v in merged.values())
    with_parent = sum(1 for v in merged.values() if v.get("parent_company"))
    with_display = sum(1 for k, v in merged.items() if v["display_name"] != k)
    print(f"  Merged: {total_canonical} canonical providers, {total_aliases} aliases")
    print(f"  With display_name override: {with_display}")
    print(f"  With parent_company: {with_parent}")
    print(f"  Conflicts: {len(conflicts)}")
    print(f"  Parent-company errors: {len(parent_co_errors)}")
    print(f"  Single-source providers: {len(coverage_gaps)}")

    os.makedirs(OUTPUT_FILE.parent, exist_ok=True)
    with open(OUTPUT_FILE, "w") as f:
        json.dump(merged, f, indent=2, ensure_ascii=False)
    print(f"\nWrote {OUTPUT_FILE}")

    source_stats = [
        (name, len(data), sum(len(v) for v in data.values()))
        for name, data in sources
    ]
    report = generate_report(merged, conflicts, coverage_gaps, parent_co_errors, source_stats)
    with open(REPORT_FILE, "w") as f:
        f.write(report)
    print(f"Wrote {REPORT_FILE}")

    # Spot-check well-known providers
    print("\n## SPOT CHECK (canonical -> display_name, aliases, parent)")
    for name in ["PG&E", "CenterPoint Energy", "ComEd", "Austin Energy",
                  "Oncor", "NV Energy", "FPL", "MidAmerican Energy",
                  "WE Energies", "Georgia Power"]:
        if name in merged:
            e = merged[name]
            disp = e["display_name"]
            n_aliases = len(e["aliases"])
            parent = e.get("parent_company", "—")
            print(f"  {name}: display=\"{disp}\", {n_aliases} aliases, parent={parent}")
        else:
            print(f"  {name}: NOT FOUND")

    # Print parent-company errors prominently
    if parent_co_errors:
        print("\n## PARENT-COMPANY ERRORS (bugs to fix in brand_resolver.py)")
        for e in parent_co_errors:
            print(f"  !! \"{e['old_name']}\" -> \"{e['mapped_to']}\" — {e['reason']}")

    print("\n" + "=" * 50)
    print(f"DONE — {total_canonical} canonical providers, {total_aliases} aliases")
    print(f"       {with_parent} with parent_company, {with_display} with display override")
    print(f"       {len(parent_co_errors)} parent-company errors flagged")
    print(f"       {len(conflicts)} conflicts flagged for review")
    print("=" * 50)


if __name__ == "__main__":
    main()
