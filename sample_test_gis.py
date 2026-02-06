#!/usr/bin/env python3
"""
Sample-based test: pick 20 mismatched addresses per top state,
geocode them, run through state GIS, measure fix rate, extrapolate.

Much faster than 30K+ API calls — ~200 total API calls.
"""

import csv
import re
import sys
import time
import random
import logging
from collections import defaultdict, Counter

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)

sys.path.insert(0, ".")
from batch_validate import compare_providers, _extract_state
from lookup_engine.state_gis import StateGISLookup
from lookup_engine.gas_mappings import GasZIPMappingLookup
from lookup_engine.geocoder import CensusGeocoder

SAMPLE_PER_STATE = 20
random.seed(42)


def main():
    t0 = time.time()
    state_gis = StateGISLookup()
    gas_map = GasZIPMappingLookup()
    geocoder = CensusGeocoder()

    # Read mismatches grouped by (utility_type, state)
    logger.info("Reading batch_results.csv...")
    by_key = defaultdict(list)  # (utype, state) -> [rows]
    match_counts = {"electric": 0, "gas": 0, "water": 0}
    mismatch_counts = {"electric": 0, "gas": 0, "water": 0}

    with open("batch_results.csv") as f:
        reader = csv.DictReader(f)
        for row in reader:
            utype = row.get("utility_type", "")
            comp = row.get("comparison", "")
            state = row.get("state", "") or _extract_state(row.get("address", ""))
            if comp == "MISMATCH" and utype in ("electric", "gas", "water"):
                by_key[(utype, state)].append(row)
                mismatch_counts[utype] += 1
            elif comp in ("MATCH", "MATCH_TDU", "MATCH_PARENT"):
                if utype in match_counts:
                    match_counts[utype] += 1

    # For each utility type, pick top states that have GIS coverage
    for utype in ("electric", "gas", "water"):
        print(f"\n{'='*70}")
        print(f"{utype.upper()} MISMATCH SAMPLING ({mismatch_counts[utype]} total)")
        print(f"{'='*70}")

        # Get states sorted by mismatch count
        state_counts = {}
        for (ut, st), rows in by_key.items():
            if ut == utype:
                state_counts[st] = len(rows)

        # For electric/water: only test states with GIS APIs
        # For gas: test states with gas ZIP mapping OR GIS APIs
        testable_states = []
        for st, n in sorted(state_counts.items(), key=lambda x: -x[1]):
            has_source = False
            if utype == "gas":
                has_source = gas_map.has_state(st) or state_gis.has_state_source(st, "gas")
            else:
                has_source = state_gis.has_state_source(st, utype)
            if has_source:
                testable_states.append((st, n))

        if not testable_states:
            print("  No testable states with GIS/mapping coverage.")
            continue

        total_testable = sum(n for _, n in testable_states)
        print(f"  Testable states: {len(testable_states)}, covering {total_testable}/{mismatch_counts[utype]} mismatches")
        print()

        # Sample and test
        total_sampled = 0
        total_fixed = 0
        total_tested = 0
        state_results = []

        for st, n_mismatches in testable_states[:15]:  # Top 15 states
            rows = by_key[(utype, st)]
            sample = random.sample(rows, min(SAMPLE_PER_STATE, len(rows)))

            # Geocode sample
            to_geocode = []
            for i, row in enumerate(sample):
                addr = row.get("address", "").strip()
                to_geocode.append((str(i), addr))

            geo_results = geocoder.geocode_batch(to_geocode)

            fixed = 0
            tested = 0
            examples = []

            for i, row in enumerate(sample):
                addr = row.get("address", "").strip()
                tenant_raw = row.get("tenant_raw", "")
                old_engine = row.get("engine_provider", "")

                geo = geo_results.get(str(i))
                if not geo or (geo.lat == 0.0 and geo.lon == 0.0):
                    continue

                _zip_m = re.search(r"\b(\d{5})(?:-\d{4})?\s*$", addr)
                addr_zip = _zip_m.group(1) if _zip_m else ""

                # Try state GIS
                new_name = None
                source = ""

                if utype == "gas" and addr_zip:
                    # Gas: try ZIP mapping first
                    gas_result = gas_map.query(addr_zip, st)
                    if gas_result:
                        new_name = gas_result["name"]
                        source = gas_result["source"]

                if not new_name:
                    # Try state GIS API
                    gis_result = state_gis.query(geo.lat, geo.lon, st, utype)
                    if gis_result:
                        new_name = gis_result["name"]
                        source = gis_result["source"]

                if not new_name:
                    tested += 1
                    continue

                tested += 1
                category, detail, tenant_norm = compare_providers(
                    new_name, tenant_raw, utype, st
                )

                if category in ("MATCH", "MATCH_TDU", "MATCH_PARENT"):
                    fixed += 1
                    if len(examples) < 3:
                        examples.append(f"    {old_engine} → {new_name} (tenant={tenant_raw[:35]}) [{source}]")

            fix_rate = fixed / tested if tested > 0 else 0
            projected_fixes = int(fix_rate * n_mismatches)
            total_sampled += len(sample)
            total_fixed += fixed
            total_tested += tested

            state_results.append({
                "state": st,
                "mismatches": n_mismatches,
                "sampled": len(sample),
                "tested": tested,
                "fixed": fixed,
                "fix_rate": fix_rate,
                "projected": projected_fixes,
                "examples": examples,
            })

            status = f"✓ {fixed}/{tested}" if fixed > 0 else f"  {fixed}/{tested}"
            print(f"  {st}: {status} sampled fix rate ({fix_rate*100:.0f}%) × {n_mismatches} mismatches → ~{projected_fixes} projected fixes")
            for ex in examples:
                print(ex)

        # Summary for this utility type
        total_projected = sum(r["projected"] for r in state_results)
        overall_fix_rate = total_fixed / total_tested if total_tested > 0 else 0

        print(f"\n  {'─'*60}")
        print(f"  SUMMARY: sampled {total_sampled}, tested {total_tested}, fixed {total_fixed} ({overall_fix_rate*100:.1f}%)")
        print(f"  PROJECTED FIXES: ~{total_projected} of {mismatch_counts[utype]} mismatches")

        old_match = match_counts[utype]
        total_compared = old_match + mismatch_counts[utype]
        old_pct = old_match / total_compared * 100 if total_compared else 0
        new_pct = (old_match + total_projected) / total_compared * 100 if total_compared else 0
        print(f"  ACCURACY: {old_pct:.1f}% → ~{new_pct:.1f}% (+{new_pct - old_pct:.1f}pp)")

    print(f"\n{'='*70}")
    print(f"Total time: {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
