#!/usr/bin/env python3
"""
One-shot script to apply all AUDIT.md fixes to canonical_providers.json.

Changes:
1. Resolve 15 alias collisions
2. Remove holding company aliases (Berkshire Hathaway Energy, NiSource, AGL Resources)
3. Remove TXU Energy; create deregulated_reps.json
4. Fix AES Ohio: separate from FirstEnergy
5. Delete invalid/placeholder entries; fix Colombia->Columbia typo
6. Merge duplicate pairs
7. Add United Illuminating
"""

import json
from pathlib import Path
from copy import deepcopy

DATA_FILE = Path(__file__).parent / "data" / "canonical_providers.json"
REPS_FILE = Path(__file__).parent / "data" / "deregulated_reps.json"

with open(DATA_FILE) as f:
    data = json.load(f)

original_count = len(data)
changes_log = []

def log(msg):
    changes_log.append(msg)
    print(f"  {msg}")

def remove_alias(canonical, alias_text):
    """Remove an alias from a canonical entry's alias list (case-insensitive match)."""
    entry = data.get(canonical)
    if not entry:
        return False
    aliases = entry.get("aliases", [])
    new_aliases = [a for a in aliases if a.lower() != alias_text.lower()]
    if len(new_aliases) < len(aliases):
        entry["aliases"] = new_aliases
        return True
    return False

def add_alias(canonical, alias_text):
    """Add an alias to a canonical entry if not already present."""
    entry = data.get(canonical)
    if not entry:
        return False
    aliases = entry.get("aliases", [])
    if alias_text.lower() not in [a.lower() for a in aliases]:
        aliases.append(alias_text)
        aliases.sort(key=str.lower)
        entry["aliases"] = aliases
        return True
    return False

# ============================================================
# 1. RESOLVE ALIAS COLLISIONS
# ============================================================
print("1. Resolving alias collisions...")

# 1a. AEP Texas Central, AEP Texas North, AEP Texas — remove from AEP Ohio
# These are Texas TDU aliases, belong with AEP Texas
for alias in ["AEP Texas Central", "AEP Texas North", "AEP Texas"]:
    if remove_alias("AEP Ohio", alias):
        log(f"Removed \"{alias}\" from AEP Ohio (belongs to AEP Texas)")

# Also remove "AEP" from AEP Ohio — too ambiguous, could be any AEP subsidiary
# Keep it nowhere (users should specify state)
if remove_alias("AEP Ohio", "AEP"):
    log("Removed ambiguous \"AEP\" from AEP Ohio")

# 1b. columbia gas — assign to Columbia Gas PA, remove from NIPSCO
# NIPSCO is Northern Indiana Public Service, "columbia gas" is Columbia Gas of Ohio/PA
# NIPSCO should keep "COLUMBIA GAS OF OHIO" since NiSource owns both
if remove_alias("NIPSCO", "columbia gas"):
    log("Removed \"columbia gas\" from NIPSCO (assign to Columbia Gas PA)")

# 1c. Columbia Gas of Virginia — assign to Columbia Gas VA, remove from Dominion Energy
# Columbia Gas of Virginia is a NiSource subsidiary, not Dominion
if remove_alias("Dominion Energy", "Columbia Gas of Virginia"):
    log("Removed \"Columbia Gas of Virginia\" from Dominion Energy (belongs to Columbia Gas VA)")

# 1d. Connecticut Light & Power, Connecticut Light & Power Co. — assign to Eversource CT only
# CL&P is the CT operating company
for alias in ["Connecticut Light & Power", "Connecticut Light & Power Co.", "Connecticut Light And Power", "cl&p"]:
    if remove_alias("Eversource MA", alias):
        log(f"Removed \"{alias}\" from Eversource MA (belongs to Eversource CT)")

# 1e. Eversource, CT — assign to Eversource CT only
if remove_alias("Eversource MA", "Eversource, CT"):
    log("Removed \"Eversource, CT\" from Eversource MA (belongs to Eversource CT)")

# 1f. YANKEE GAS SERVICE CO (EVERSOURCE) — assign to Eversource CT only
# Yankee Gas is a CT gas utility
if remove_alias("Eversource MA", "YANKEE GAS SERVICE CO (EVERSOURCE)"):
    log("Removed \"YANKEE GAS SERVICE CO (EVERSOURCE)\" from Eversource MA (CT gas utility)")

# 1g. Eversource — the holding company brand, remove from Eversource MA
# Users should specify state: "Eversource CT" or "Eversource MA"
if remove_alias("Eversource MA", "Eversource"):
    log("Removed ambiguous \"Eversource\" from Eversource MA")

# Also remove eversource connecticut, eversource nh, eversource new hampshire from MA
for alias in ["eversource connecticut", "eversource new hampshire", "eversource nh",
              "public service co of nh", "Public Service Company Of Nh", "public service nh"]:
    if remove_alias("Eversource MA", alias):
        log(f"Removed \"{alias}\" from Eversource MA (wrong state)")

# Add NH aliases to a note — they should be Eversource NH if we create that entry
# For now, add them to Eversource CT since PSNH merged into Eversource CT/NH

# 1h. PECO Electric — assign to PECO (Philadelphia), remove from Pedernales
# PECO Electric is the common name for PECO Energy in Philadelphia
# Pedernales should use "PEC" as its abbreviation
if remove_alias("Pedernales Electric Cooperative", "PECO Electric"):
    log("Removed \"PECO Electric\" from Pedernales (belongs to PECO Philadelphia)")

# 1i. Peoples Gas — disambiguate by state
# Peoples Gas (WEC Energy) is IL/PA, Peoples Gas Florida is FL
# Remove bare "Peoples Gas" from both, add state-qualified versions
if remove_alias("Peoples Gas (WEC Energy)", "Peoples Gas"):
    add_alias("Peoples Gas (WEC Energy)", "Peoples Gas IL")
    add_alias("Peoples Gas (WEC Energy)", "Peoples Gas PA")
    log("Replaced \"Peoples Gas\" with \"Peoples Gas IL\"/\"Peoples Gas PA\" in Peoples Gas (WEC Energy)")
if remove_alias("Peoples Gas Florida", "Peoples Gas"):
    add_alias("Peoples Gas Florida", "Peoples Gas FL")
    log("Replaced \"Peoples Gas\" with \"Peoples Gas FL\" in Peoples Gas Florida")

# 1j. questar — assign to Dominion Energy Utah only
# Questar Gas is the Utah gas utility, now Dominion Energy Utah
if remove_alias("Dominion Energy", "questar"):
    log("Removed \"questar\" from Dominion Energy (belongs to Dominion Energy Utah)")

# 1k. Spire / Spire Energy — assign to Spire Missouri (HQ), remove from Spire Alabama
# Spire Inc is HQ'd in St. Louis. "Spire" without qualifier = Spire Missouri
if remove_alias("Spire Alabama", "Spire"):
    log("Removed \"Spire\" from Spire Alabama (assign to Spire Missouri)")
if remove_alias("Spire Alabama", "Spire Energy, Spire"):
    log("Removed \"Spire Energy, Spire\" from Spire Alabama")
if remove_alias("Spire Alabama", "Spire, Spire Energy"):
    log("Removed \"Spire, Spire Energy\" from Spire Alabama")
# Also remove Spire Mississippi and Spire Missouri from Spire Alabama aliases
if remove_alias("Spire Alabama", "Spire Mississippi"):
    log("Removed \"Spire Mississippi\" from Spire Alabama (separate entity)")
if remove_alias("Spire Alabama", "Spire Missouri"):
    log("Removed \"Spire Missouri\" from Spire Alabama (separate entity)")

# 1l. virginia natural gas — assign to Dominion Energy only, remove from Nicor Gas
# Virginia Natural Gas is a Dominion subsidiary, not related to Nicor/Southern Company Gas
if remove_alias("Nicor Gas", "virginia natural gas"):
    log("Removed \"virginia natural gas\" from Nicor Gas (belongs to Dominion Energy)")

# ============================================================
# 2. REMOVE HOLDING COMPANY ALIASES
# ============================================================
print("\n2. Removing holding company aliases...")

holding_co_aliases = {
    "Berkshire Hathaway Energy": "MidAmerican Energy",
    "NiSource": "NIPSCO",
    "AGL Resources": "Nicor Gas",
}
for hc_alias, canonical in holding_co_aliases.items():
    if remove_alias(canonical, hc_alias):
        log(f"Removed holding company alias \"{hc_alias}\" from {canonical}")

# Also remove "wec energy" from WE Energies — too close to "WEC Energy Group"
if remove_alias("WE Energies", "wec energy"):
    log("Removed \"wec energy\" from WE Energies (too close to holding co name)")

# Remove "Southern Company Gas" from Nicor Gas — Southern Company is a holding co
if remove_alias("Nicor Gas", "Southern Company Gas"):
    log("Removed \"Southern Company Gas\" from Nicor Gas (holding company reference)")

# ============================================================
# 3. REMOVE TXU ENERGY; CREATE deregulated_reps.json
# ============================================================
print("\n3. Removing TXU Energy (REP, not TDU)...")

txu_entry = data.pop("Txu Energy", None)
if txu_entry:
    log("Removed \"Txu Energy\" from canonical providers (REP, not TDU)")

reps_data = {
    "description": "Known retail electric providers (REPs) in deregulated markets. These should trigger a 'deregulated market' response, not a provider match.",
    "reps": {
        "TXU Energy": {
            "state": "TX",
            "aliases": ["TXU", "TXU Energy", "txu"],
            "note": "Retail electric provider in ERCOT deregulated market. Return the TDU (Oncor, CenterPoint, AEP Texas, TNMP) instead."
        }
    }
}
with open(REPS_FILE, "w") as f:
    json.dump(reps_data, f, indent=2, ensure_ascii=False)
log(f"Created {REPS_FILE}")

# ============================================================
# 4. FIX AES OHIO
# ============================================================
print("\n4. Fixing AES Ohio...")

if remove_alias("FirstEnergy", "AES Ohio"):
    log("Removed \"AES Ohio\" from FirstEnergy aliases")

data["AES Ohio"] = {
    "display_name": "AES Ohio",
    "aliases": [
        "Dayton Power and Light",
        "Dayton Power & Light",
        "DP&L",
        "DPL",
        "AES Dayton"
    ]
}
log("Created canonical entry for AES Ohio with aliases [Dayton Power and Light, DP&L, DPL, AES Dayton]")

# ============================================================
# 5. DELETE INVALID ENTRIES; FIX TYPOS
# ============================================================
print("\n5. Deleting invalid entries and fixing typos...")

# Delete placeholder
if "Choose your electric here" in data:
    del data["Choose your electric here"]
    log("Deleted placeholder \"Choose your electric here\"")

# Delete compound keys (multi-provider entries that aren't real providers)
compound_keys = [
    "Arizona Public Service, SRP",
    "PG&E, Public Service Electric & Gas",
    "PG&E, Southern California Edison",
    "Public Service Electric & Gas, PG&E",
    "Spire, Ameren MO",
    "Merced Irrigation District, PG&E",
]
for ck in compound_keys:
    if ck in data:
        del data[ck]
        log(f"Deleted compound key \"{ck}\"")

# Fix Colombia -> Columbia typo
if "Colombia Gas of Pennsylvania-PA" in data:
    del data["Colombia Gas of Pennsylvania-PA"]
    # This is a duplicate of Columbia Gas PA which already exists
    log("Deleted misspelled \"Colombia Gas of Pennsylvania-PA\" (duplicate of Columbia Gas PA)")

# ============================================================
# 6. MERGE DUPLICATE PAIRS
# ============================================================
print("\n6. Merging duplicate pairs...")

def merge_into(keep_key, remove_key, new_display=None):
    """Merge remove_key entry into keep_key, combining aliases."""
    if keep_key not in data or remove_key not in data:
        print(f"    SKIP merge: {keep_key} or {remove_key} not found")
        return
    keep = data[keep_key]
    remove = data[remove_key]

    # Combine aliases
    existing_aliases = set(a.lower() for a in keep.get("aliases", []))
    existing_aliases.add(keep_key.lower())

    # Add remove_key itself as an alias (if different)
    if remove_key.lower() not in existing_aliases:
        keep["aliases"].append(remove_key)

    # Add all aliases from the removed entry
    for alias in remove.get("aliases", []):
        if alias.lower() not in existing_aliases:
            keep["aliases"].append(alias)
            existing_aliases.add(alias.lower())

    # Sort aliases
    keep["aliases"] = sorted(keep["aliases"], key=str.lower)

    # Preserve parent_company if present in either
    if "parent_company" in remove and "parent_company" not in keep:
        keep["parent_company"] = remove["parent_company"]

    # Update display name if requested
    if new_display:
        keep["display_name"] = new_display

    del data[remove_key]
    log(f"Merged \"{remove_key}\" into \"{keep_key}\" ({len(keep['aliases'])} aliases)")

# 6a. Duquesne Light + Duquesne Light Company - PA
merge_into("Duquesne Light", "Duquesne Light Company - PA")

# 6b. NYSEG + New York State Electric & Gas + New York State Electric & Gas (NYSEG)
merge_into("NYSEG", "New York State Electric & Gas")
merge_into("NYSEG", "New York State Electric & Gas (NYSEG)")

# 6c. Con Edison + Consolidated Edison Co-ny
merge_into("Con Edison", "Consolidated Edison Co-ny")

# 6d. North Shore Gas (WEC Energy) + NORTH SHORE GAS CO
merge_into("North Shore Gas (WEC Energy)", "NORTH SHORE GAS CO")

# 6e. PSE + PSE-Puget Sound Energy
merge_into("PSE", "PSE-Puget Sound Energy")

# 6f. Potomac Electric Power + Potomac Electric Power Co.
# Clean up the PECO/PEPCO compound aliases
if "Potomac Electric Power" in data:
    # Remove compound aliases
    for bad_alias in ["PECO Electric, PEPCO", "PEPCO, PECO Electric"]:
        remove_alias("Potomac Electric Power", bad_alias)
merge_into("Potomac Electric Power", "Potomac Electric Power Co.", new_display="PEPCO")
# Add clean alias
add_alias("Potomac Electric Power", "Potomac Electric Power Company")

# 6g. LADWP: merge the two entries
merge_into("Los Angeles Department Of Water And Power", "LOS ANGELES DEPARTMENT OF WATER & POWER",
           new_display="LADWP")

# 6h. Memphis Light Gas & Water + Memphis Light, Gas, and Water (MLGW)
merge_into("Memphis Light Gas & Water", "Memphis Light, Gas, and Water (MLGW)",
           new_display="Memphis Light Gas & Water")

# Additional duplicates found in audit
# Holyoke Gas & Electric
merge_into("Holyoke Gas & Electric (HG&E)", "Holyoke Gas & Electric (HG&E), MA")

# National Fuel: merge 3 entries
merge_into("National Fuel", "National Fuel Gas Company - PA")
merge_into("National Fuel", "NATIONAL FUEL GAS DISTRIBUTION - NY")

# Bluebonnet Electric
merge_into("Bluebonnet Electric", "Bluebonnet Electric Cooperative, Inc.")

# Connecticut Natural Gas
merge_into("Connecticut Natural Gas", "Connecticut Natural Gas (CNG) ,CT")

# PPL Electric
merge_into("PPL Electric", "PPL Electric Utilities Corporation")

# Wakefield Municipal
merge_into("Wakefield Municipal Gas & Light Department (WMGLD) - MA",
           "Wakefield Municipal Gas and Light Department",
           new_display="Wakefield Municipal Gas & Light")

# Northern Indiana Public Service Company -> merge into NIPSCO
merge_into("NIPSCO", "Northern Indiana Public Service Company")

# PSEG duplicates
if "PSEG, PSE&G (Public Service Electric and Gas) - NJ" in data:
    merge_into("PSE&G", "PSEG, PSE&G (Public Service Electric and Gas) - NJ")

# ============================================================
# 7. ADD UNITED ILLUMINATING
# ============================================================
print("\n7. Adding United Illuminating...")

data["United Illuminating"] = {
    "display_name": "United Illuminating",
    "aliases": [
        "UI",
        "The United Illuminating Company"
    ],
    "parent_company": "Avangrid"
}
log("Added United Illuminating (CT EDC, parent: Avangrid)")

# ============================================================
# SORT AND WRITE
# ============================================================
print("\n8. Sorting and writing...")

data = dict(sorted(data.items(), key=lambda x: x[0].lower()))

with open(DATA_FILE, "w") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)

final_count = len(data)
total_aliases = sum(len(v.get("aliases", [])) for v in data.values())

print(f"\n{'='*60}")
print(f"DONE: {original_count} -> {final_count} canonical providers")
print(f"      {total_aliases} total aliases")
print(f"      {len(changes_log)} changes applied")
print(f"{'='*60}")

# Verify no remaining collisions
from collections import defaultdict
alias_to_canonicals = defaultdict(list)
for canonical, entry in data.items():
    for alias in entry.get("aliases", []):
        alias_to_canonicals[alias.lower()].append(canonical)

remaining = {k: v for k, v in alias_to_canonicals.items() if len(v) > 1}
if remaining:
    print(f"\nWARNING: {len(remaining)} alias collisions remain:")
    for alias, canonicals in sorted(remaining.items()):
        print(f"  \"{alias}\" -> {canonicals}")
else:
    print("\n✓ No alias collisions remain")
