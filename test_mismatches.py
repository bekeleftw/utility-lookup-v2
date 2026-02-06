#!/usr/bin/env python3
"""
Re-run mismatched rows from batch_results.csv through the new engine
with state GIS + gas ZIP mappings to measure improvement.

Strategy:
- GAS: Use gas ZIP mapping directly (no coordinates needed — ZIP only)
- ELECTRIC/WATER: Need coordinates. Only ~1% are in engine cache, so
  we report coverage stats for state GIS and show what would change
  if we had coordinates for all rows.
"""

import csv
import re
import sys
import time
import logging
from collections import defaultdict

logging.basicConfig(level=logging.WARNING)

sys.path.insert(0, ".")
from batch_validate import compare_providers, _extract_state
from lookup_engine.gas_mappings import GasZIPMappingLookup
from lookup_engine.state_gis import StateGISLookup
from lookup_engine.eia_verification import EIAVerification


def main():
    print("Loading modules...")
    t0 = time.time()
    gas_map = GasZIPMappingLookup()
    state_gis = StateGISLookup()
    eia = EIAVerification()
    print(f"Loaded in {time.time()-t0:.1f}s\n")

    # Read all rows from batch_results.csv
    all_rows = {"electric": [], "gas": [], "water": []}
    mismatch_rows = {"electric": [], "gas": [], "water": []}
    match_counts = {"electric": 0, "gas": 0, "water": 0}

    with open("batch_results.csv") as f:
        reader = csv.DictReader(f)
        for row in reader:
            utype = row.get("utility_type", "")
            comp = row.get("comparison", "")
            if utype in all_rows:
                all_rows[utype].append(row)
                if comp == "MISMATCH":
                    mismatch_rows[utype].append(row)
                elif comp in ("MATCH", "MATCH_TDU", "MATCH_PARENT"):
                    match_counts[utype] += 1

    print("Current batch results:")
    for utype in ["electric", "gas", "water"]:
        total_compared = match_counts[utype] + len(mismatch_rows[utype])
        pct = match_counts[utype] / total_compared * 100 if total_compared else 0
        print(f"  {utype}: {match_counts[utype]}/{total_compared} = {pct:.1f}% accuracy, {len(mismatch_rows[utype])} mismatches")
    print()

    # =========================================================================
    # GAS: Re-test ALL gas mismatches using ZIP mapping (no coords needed)
    # =========================================================================
    print("=" * 70)
    print("GAS MISMATCH RE-TEST (ZIP mapping — no coordinates needed)")
    print("=" * 70)

    gas_fixed = 0
    gas_still = 0
    gas_no_zip = 0
    gas_no_mapping = 0
    gas_mapping_same = 0
    gas_mapping_diff = 0
    gas_examples_fixed = []
    gas_examples_still = []
    gas_by_state = defaultdict(lambda: {"fixed": 0, "total": 0})

    for row in mismatch_rows["gas"]:
        address = row.get("address", "")
        tenant_raw = row.get("tenant_raw", "")
        old_engine = row.get("engine_provider", "")
        state = row.get("state", "") or _extract_state(address)

        _zip_m = re.search(r"\b(\d{5})(?:-\d{4})?\s*$", address)
        addr_zip = _zip_m.group(1) if _zip_m else ""

        gas_by_state[state]["total"] += 1

        if not addr_zip:
            gas_no_zip += 1
            gas_still += 1
            continue

        # Try gas ZIP mapping
        mapping_result = gas_map.query(addr_zip, state)
        if not mapping_result:
            gas_no_mapping += 1
            gas_still += 1
            continue

        new_name = mapping_result["name"]

        # Is the mapping result different from old engine result?
        if new_name.upper().strip() == old_engine.upper().strip():
            gas_mapping_same += 1
            gas_still += 1
            continue

        gas_mapping_diff += 1

        # Compare new result against tenant
        category, detail, tenant_norm = compare_providers(
            new_name, tenant_raw, "gas", state
        )

        if category in ("MATCH", "MATCH_TDU", "MATCH_PARENT"):
            gas_fixed += 1
            gas_by_state[state]["fixed"] += 1
            if len(gas_examples_fixed) < 15:
                gas_examples_fixed.append({
                    "address": address[:65],
                    "old": old_engine,
                    "new": new_name,
                    "tenant": tenant_raw,
                    "zip": addr_zip,
                    "state": state,
                    "match": category,
                })
        else:
            gas_still += 1
            if len(gas_examples_still) < 5:
                gas_examples_still.append({
                    "address": address[:65],
                    "old": old_engine,
                    "new": new_name,
                    "tenant": tenant_raw,
                    "zip": addr_zip,
                })

    total_gas_mm = len(mismatch_rows["gas"])
    print(f"\nTotal gas mismatches: {total_gas_mm}")
    print(f"  Fixed (now MATCH):     {gas_fixed:>6} ({gas_fixed/total_gas_mm*100:.1f}%)")
    print(f"  Still MISMATCH:        {gas_still:>6}")
    print(f"  Breakdown:")
    print(f"    No ZIP in address:   {gas_no_zip:>6}")
    print(f"    No mapping for ZIP:  {gas_no_mapping:>6}")
    print(f"    Mapping = old result:{gas_mapping_same:>6}")
    print(f"    Mapping different:   {gas_mapping_diff:>6} (of which {gas_fixed} match tenant)")

    # State breakdown
    print(f"\n  By state (top fixers):")
    for st, counts in sorted(gas_by_state.items(), key=lambda x: -x[1]["fixed"])[:10]:
        if counts["fixed"] > 0:
            print(f"    {st}: {counts['fixed']}/{counts['total']} fixed")

    if gas_examples_fixed:
        print(f"\n  Examples FIXED:")
        for ex in gas_examples_fixed[:8]:
            print(f"    [{ex['state']}] {ex['address']}")
            print(f"      old={ex['old']} -> new={ex['new']} (tenant={ex['tenant']}) ZIP={ex['zip']}")

    if gas_examples_still:
        print(f"\n  Examples STILL MISMATCH (mapping returned different but wrong):")
        for ex in gas_examples_still[:3]:
            print(f"    {ex['address']}")
            print(f"      mapping={ex['new']} vs tenant={ex['tenant']} ZIP={ex['zip']}")

    # =========================================================================
    # ELECTRIC/WATER: Coverage analysis (which states would benefit from GIS)
    # =========================================================================
    print("\n" + "=" * 70)
    print("ELECTRIC MISMATCH COVERAGE ANALYSIS (state GIS availability)")
    print("=" * 70)

    elec_by_state = defaultdict(int)
    elec_covered = 0
    for row in mismatch_rows["electric"]:
        state = row.get("state", "") or _extract_state(row.get("address", ""))
        elec_by_state[state] += 1
        if state_gis.has_state_source(state, "electric"):
            elec_covered += 1

    print(f"\nTotal electric mismatches: {len(mismatch_rows['electric'])}")
    print(f"  In states with GIS API: {elec_covered} ({elec_covered/len(mismatch_rows['electric'])*100:.1f}%)")
    print(f"\n  Top mismatch states:")
    for st, n in sorted(elec_by_state.items(), key=lambda x: -x[1])[:15]:
        has_gis = "✓ GIS" if state_gis.has_state_source(st, "electric") else "  ---"
        print(f"    {st}: {n:>5} mismatches  {has_gis}")

    print("\n" + "=" * 70)
    print("WATER MISMATCH COVERAGE ANALYSIS (state GIS availability)")
    print("=" * 70)

    water_by_state = defaultdict(int)
    water_covered = 0
    for row in mismatch_rows["water"]:
        state = row.get("state", "") or _extract_state(row.get("address", ""))
        water_by_state[state] += 1
        if state_gis.has_state_source(state, "water"):
            water_covered += 1

    print(f"\nTotal water mismatches: {len(mismatch_rows['water'])}")
    print(f"  In states with GIS API: {water_covered} ({water_covered/len(mismatch_rows['water'])*100:.1f}%)")
    print(f"\n  Top mismatch states:")
    for st, n in sorted(water_by_state.items(), key=lambda x: -x[1])[:15]:
        has_gis = "✓ GIS" if state_gis.has_state_source(st, "water") else "  ---"
        print(f"    {st}: {n:>5} mismatches  {has_gis}")

    # =========================================================================
    # OVERALL IMPACT
    # =========================================================================
    print("\n" + "=" * 70)
    print("ESTIMATED ACCURACY IMPACT ON 91K BATCH")
    print("=" * 70)

    for utype in ["electric", "gas", "water"]:
        old_match = match_counts[utype]
        total_compared = old_match + len(mismatch_rows[utype])
        if total_compared == 0:
            continue
        old_pct = old_match / total_compared * 100

        if utype == "gas":
            new_match = old_match + gas_fixed
        else:
            new_match = old_match  # Can't measure without coords

        new_pct = new_match / total_compared * 100
        delta = new_pct - old_pct

        note = ""
        if utype == "gas":
            note = f" (+{gas_fixed} rows from ZIP mapping)"
        elif utype == "electric":
            note = f" (need full re-run for state GIS impact; {elec_covered} mismatches in GIS states)"
        elif utype == "water":
            note = f" (need full re-run for state GIS impact; {water_covered} mismatches in GIS states)"

        print(f"  {utype.upper()}: {old_pct:.1f}% -> {new_pct:.1f}% (+{delta:.1f}pp){note}")


if __name__ == "__main__":
    main()
