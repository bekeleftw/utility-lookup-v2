#!/usr/bin/env python3
"""
Extract TX REP names from tenant data and build deregulated_reps.json.
"""

import json
import re
from collections import Counter
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
SCRAPE_DIR = Path(__file__).parent.parent / "utility-provider-scrape"

FILES = [
    SCRAPE_DIR / "stratified_comparison_140k.json",
    SCRAPE_DIR / "targeted_comparison_74k.json",
]

STATE_RE = re.compile(r',\s*([A-Z]{2})\s+\d{5}')

# === KNOWN NON-REP ENTITIES ===
# TDUs (the correct answer for deregulated TX addresses)
TX_TDUS_LOWER = {
    "oncor", "oncor electric-tx", "oncor electric delivery",
    "oncor electric delivery company", "oncor electric delivery company llc",
    "centerpoint energy", "centerpoint energy - tx",
    "centerpoint energy houston electric",
    "aep texas", "aep texas central", "aep texas north", "aep texas inc.",
    "texas-new mexico power", "texas-new mexico power company", "tnmp",
    "lubbock power & light", "lubbock power & light system",
    "lubbock power & light - tx",
}

# Regulated IOUs that operate in TX but are NOT REPs
TX_REGULATED_LOWER = {
    "entergy texas", "entergy texas, inc.", "entergy",
    "el paso electric", "el paso electric company",
    "southwestern electric power company", "southwestern electric power company - ar",
    "swepco",
    "xcel energy", "xcel energy colorado", "xcel energy minnesota",
    "aep energy",
}

# Municipals
TX_MUNIS_LOWER = {
    "austin energy", "cps energy", "bryan texas utilities",
    "san marcos texas utilities", "college station utilities",
    "georgetown utility systems", "garland power & light",
    "new braunfels utilities", "brownsville public utilities board - tx",
    "floresville electric light & power system (felps)",
    "geus (greenville electric utility system)",
    "kerrville public utility board", "seguin electric division",
    "boerne utilities", "brenham", "denton municipal electric",
    "city of lubbock", "city of brownsville",
    "lubbock power & light - tx",
}

# Co-ops (not deregulated)
TX_COOPS_LOWER = {
    "pedernales electric cooperative", "pedernales electric", "pedernales", "pec",
    "bluebonnet electric", "coserv", "coserv electric cooperative, inc.",
    "gvec", "guadalupe valley electric cooperative, inc.",
    "hilco electric cooperative, inc.", "magic valley electric cooperative, inc.",
    "sam houston electric cooperative, inc.", "south plains electric co",
    "south plains electric cooperative, inc.", "south plains electric cooperative - tx",
    "tri-county electric cooperative, inc.",
    "trinity valley electric cooperative, inc.", "trinity valley electric",
    "united electric cooperative services, inc.",
    "grayson-collin electric cooperative, inc.",
    "deep east texas electric cooperative, inc.",
    "mid-south electric cooperative association",
    "bandera electric cooperative, inc.", "bartlett electric cooperative, inc.",
    "farmers electric cooperative, inc.",
}

# Placeholders and fragments
JUNK_LOWER = {
    "choose your electric here", "choose texas power", "power to choose",
    "tx)", "tx", "inc - (tx)", "spring", "inc.", "inc",
}

# Out-of-state utilities that appear due to data noise
OUT_OF_STATE_LOWER = {
    "green mountain power - vt", "green mountain power",
    "peco electric",  # Philadelphia, not TX
    "atmos energy",  # Gas utility, not electric REP
    "atmos energy - tx",
    "centerpoint energy gas",  # Gas, not electric REP
}

ALL_NON_REPS = (TX_TDUS_LOWER | TX_REGULATED_LOWER | TX_MUNIS_LOWER |
                TX_COOPS_LOWER | JUNK_LOWER | OUT_OF_STATE_LOWER)


def main():
    all_records = []
    for fp in FILES:
        if not fp.exists():
            print(f"SKIP: {fp}")
            continue
        with open(fp) as f:
            all_records.extend(json.load(f))

    # Extract TX electric segments
    tx_segments = Counter()
    tx_total = 0
    for rec in all_records:
        addr = rec.get("address", "")
        m = STATE_RE.search(addr)
        if not m or m.group(1) != "TX":
            continue
        elec = rec.get("mapped_electric", "").strip()
        if not elec:
            continue
        tx_total += 1
        for seg in elec.split(","):
            seg = seg.strip()
            if seg:
                tx_segments[seg] += 1

    # Classify
    reps = {}
    for seg, count in tx_segments.most_common():
        sl = seg.lower().strip()
        if sl in ALL_NON_REPS:
            continue
        if any(kw in sl for kw in ["cooperative", "co-op", "coop", "municipal",
                                     "city of", "choose", "county"]):
            continue
        if count < 2:
            continue
        if len(seg) < 3:
            continue
        reps[seg] = count

    print(f"TX entries: {tx_total}")
    print(f"Identified {len(reps)} TX REPs (freq >= 2)")
    print()

    # Build the JSON
    output = {
        "metadata": {
            "description": "Retail Electric Providers in deregulated Texas (ERCOT). REPs are NOT canonical providers — they should never be returned as the 'correct' utility for an address.",
            "correct_behavior": "When a REP name is detected, the lookup should return the TDU for that address instead. TDU is determined by physical location using HIFLD boundary polygons.",
            "last_updated": "2026-02-06",
            "source": "Extracted from 172K tenant-verified addresses"
        },
        "texas_tdus": [
            {"name": "Oncor", "canonical_id": "Oncor", "service_area": "Dallas-Fort Worth metro and surrounding"},
            {"name": "CenterPoint Energy Houston Electric", "canonical_id": "CenterPoint Energy", "service_area": "Greater Houston"},
            {"name": "AEP Texas Central", "canonical_id": "AEP Texas", "service_area": "Corpus Christi, McAllen, Rio Grande Valley"},
            {"name": "AEP Texas North", "canonical_id": "AEP Texas", "service_area": "Abilene, San Angelo, West Texas"},
            {"name": "Texas-New Mexico Power", "canonical_id": "TNMP", "service_area": "Non-contiguous: N-Central TX, Gulf Coast, West TX"},
            {"name": "Lubbock Power & Light", "canonical_id": "Lubbock Power & Light System", "service_area": "Lubbock area"}
        ],
        "reps": {}
    }

    for name, count in sorted(reps.items(), key=lambda x: -x[1]):
        output["reps"][name] = {"frequency_in_tenant_data": count}

    out_path = DATA_DIR / "deregulated_reps.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"Wrote {out_path}")

    # Summary
    print(f"\nTop 20 REPs by frequency:")
    for name, count in sorted(reps.items(), key=lambda x: -x[1])[:20]:
        print(f"  {count:5d}  {name}")

    total_rep_instances = sum(reps.values())
    print(f"\nTotal REP instances in TX data: {total_rep_instances}")
    print(f"Total TX entries: {tx_total}")
    print(f"REP % of TX entries: {total_rep_instances/tx_total*100:.1f}%")

    # Collect all REP aliases for provider_normalizer
    all_rep_names = set()
    for name in reps:
        all_rep_names.add(name.lower())
    print(f"\nUnique REP name strings: {len(all_rep_names)}")


if __name__ == "__main__":
    main()
