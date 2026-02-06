#!/usr/bin/env python3
"""
Round 2 fixes from Claude Code review.

1. Fix Alliant Energy holding company leak
2. State-qualify columbia gas
3. Add ~20 top-frequency tenant names as aliases to existing entries
"""

import json
from pathlib import Path

DATA_FILE = Path(__file__).parent / "data" / "canonical_providers.json"

with open(DATA_FILE) as f:
    data = json.load(f)

changes = []

def log(msg):
    changes.append(msg)
    print(f"  {msg}")

def remove_alias(canonical, alias_text):
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
# 1. FIX ALLIANT ENERGY HOLDING COMPANY LEAK
# ============================================================
print("1. Fixing Alliant Energy leak...")

key = "Interstate Pwr&light Co. (alliant Energy)"
if remove_alias(key, "Alliant Energy"):
    log(f"Removed holding co alias \"Alliant Energy\" from \"{key}\"")
if key in data and "parent_company" not in data[key]:
    data[key]["parent_company"] = "Alliant Energy"
    log(f"Added parent_company \"Alliant Energy\" to \"{key}\"")
# Also add a cleaner alias
add_alias(key, "Interstate Power and Light")
add_alias(key, "IPL Iowa")
log("Added aliases \"Interstate Power and Light\", \"IPL Iowa\"")

# ============================================================
# 2. STATE-QUALIFY COLUMBIA GAS
# ============================================================
print("\n2. State-qualifying columbia gas...")

# Currently "columbia gas" is only in Columbia Gas PA.
# Add state-qualified versions to each Columbia Gas entry and remove bare "columbia gas"
if remove_alias("Columbia Gas PA", "columbia gas"):
    add_alias("Columbia Gas PA", "Columbia Gas Pennsylvania")
    log("Replaced bare \"columbia gas\" with \"Columbia Gas Pennsylvania\" in Columbia Gas PA")

# Add "Columbia Gas OH" alias to NIPSCO since Columbia Gas of Ohio is under NIPSCO/NiSource
# NIPSCO already has "COLUMBIA GAS OF OHIO"
add_alias("NIPSCO", "Columbia Gas OH")
add_alias("NIPSCO", "Columbia Gas Ohio")
log("Added \"Columbia Gas OH\", \"Columbia Gas Ohio\" to NIPSCO")

# Columbia Gas KY, VA, MA already have state-qualified names
add_alias("Columbia Gas KY", "Columbia Gas Kentucky")
add_alias("Columbia Gas VA", "Columbia Gas Virginia")
log("Added full state name aliases to Columbia Gas KY and VA")

# ============================================================
# 3. ADD TOP-FREQUENCY TENANT NAMES AS ALIASES
# ============================================================
print("\n3. Adding top-frequency tenant names...")

# Aliases to add to EXISTING canonical entries
alias_additions = [
    # Rank 2: AEP (OHIO) — 2,472 occurrences
    ("AEP Ohio", "AEP (OHIO)"),

    # Rank 6: National Grid - MA — 1,708 occurrences
    ("National Grid MA", "National Grid - MA"),

    # Rank 13: Portland General Electric - PGE — 996 (new entry needed)
    # Rank 15: PSEG — 925 occurrences
    ("PSE&G", "PSEG"),

    # Rank 28: Delmarva Power - DE — 366
    ("Delmarva Power and Light", "Delmarva Power - DE"),

    # Rank 36: Wisconsin Public Services — 336 (note plural typo)
    ("Wisconsin Public Service (WEC Energy)", "Wisconsin Public Services"),

    # Rank 44: SWEPCO — 278
    ("Southwestern Electric Power Company", "SWEPCO"),

    # Rank 45: EPB — 275
    ("Electric Power Board (EPB) - TN", "EPB"),

    # Rank 50: PSEG Long Island — 244 (new entry needed)

    # Other easy adds from the tenant data
    ("Enbridge Gas North Carolina", "Enbridge Gas NC"),
    ("Energy Services of Pensacola", "Pensacola Energy"),
    ("Southwestern Electric Power Company", "Southwestern Electric Power"),
]

for canonical, alias in alias_additions:
    if add_alias(canonical, alias):
        log(f"Added alias \"{alias}\" to \"{canonical}\"")

# New entries for providers that don't exist yet but have high tenant frequency
new_entries = {
    "Portland General Electric": {
        "display_name": "Portland General Electric",
        "aliases": [
            "PGE",
            "Portland General Electric - PGE",
            "Portland General Electric Co.",
            "Portland General Electric Company"
        ]
    },
    "Cascade Natural Gas": {
        "display_name": "Cascade Natural Gas",
        "aliases": [
            "Cascade Natural Gas Corp.",
            "Cascade Natural Gas Corporation"
        ]
    },
    "PSEG Long Island": {
        "display_name": "PSEG Long Island",
        "aliases": [
            "LIPA",
            "Long Island Power Authority"
        ]
    },
    "Cleveland Public Power": {
        "display_name": "Cleveland Public Power",
        "aliases": [
            "Cleveland Public Power - OH",
            "CPP"
        ]
    },
    "Greenville Utilities Commission": {
        "display_name": "Greenville Utilities Commission",
        "aliases": [
            "Greenville Utilities Commission (GUC) - NC",
            "GUC"
        ]
    },
    "New Braunfels Utilities": {
        "display_name": "New Braunfels Utilities",
        "aliases": [
            "NBU",
            "New Braunfels Utilities - TX"
        ]
    },
    "Huntsville Utilities": {
        "display_name": "Huntsville Utilities",
        "aliases": [
            "Huntsville Utilities - AL"
        ]
    },
    "Snohomish County PUD": {
        "display_name": "Snohomish County PUD",
        "aliases": [
            "SnoPUD",
            "Snohomish PUD"
        ]
    },
    "Fayetteville Public Works Commission": {
        "display_name": "Fayetteville Public Works Commission",
        "aliases": [
            "Fayetteville PWC",
            "Fayetteville PWC (NC)",
            "PWC Fayetteville"
        ]
    },
    "Gas South": {
        "display_name": "Gas South",
        "aliases": [
            "Gas South Avalon"
        ]
    },
    "City Utilities of Springfield": {
        "display_name": "City Utilities of Springfield",
        "aliases": [
            "City Utilities of Springfield MO",
            "City Utilities Springfield"
        ]
    },
}

for name, entry in new_entries.items():
    if name not in data:
        data[name] = entry
        log(f"Created new entry \"{name}\" with {len(entry['aliases'])} aliases")

# ============================================================
# SORT AND WRITE
# ============================================================
print("\n4. Sorting and writing...")
data = dict(sorted(data.items(), key=lambda x: x[0].lower()))

with open(DATA_FILE, "w") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)

# Verify
from collections import defaultdict
alias_map = defaultdict(list)
for canon, entry in data.items():
    for a in entry.get("aliases", []):
        alias_map[a.lower()].append(canon)
collisions = {k: v for k, v in alias_map.items() if len(v) > 1}

total_aliases = sum(len(v.get("aliases", [])) for v in data.values())
print(f"\nDONE: {len(data)} canonical providers, {total_aliases} aliases")
print(f"  {len(changes)} changes applied")
if collisions:
    print(f"  WARNING: {len(collisions)} collisions:")
    for a, cs in sorted(collisions.items()):
        print(f"    \"{a}\" -> {cs}")
else:
    print("  0 alias collisions")
