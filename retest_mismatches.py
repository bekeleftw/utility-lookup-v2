#!/usr/bin/env python3
"""
Re-geocode and re-run ONLY the mismatched rows from batch_results.csv
through the new engine (state GIS + gas ZIP mappings + EIA verification).

Steps:
1. Extract unique addresses from MISMATCH rows
2. Batch geocode them via Census
3. Run each through engine._lookup_with_state_gis
4. Re-compare against tenant and report improvements
"""

import csv
import re
import sys
import time
import logging
from collections import Counter, defaultdict

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

sys.path.insert(0, ".")
from batch_validate import compare_providers, _extract_state
from lookup_engine.engine import LookupEngine
from lookup_engine.geocoder import CensusGeocoder
from lookup_engine.config import Config


def main():
    t_start = time.time()

    # ================================================================
    # 1. Read mismatched rows
    # ================================================================
    logger.info("Reading batch_results.csv...")
    mismatch_rows = []  # list of dicts
    match_counts = {"electric": 0, "gas": 0, "water": 0}
    mismatch_counts = {"electric": 0, "gas": 0, "water": 0}

    with open("batch_results.csv") as f:
        reader = csv.DictReader(f)
        for row in reader:
            utype = row.get("utility_type", "")
            comp = row.get("comparison", "")
            if comp == "MISMATCH" and utype in ("electric", "gas", "water"):
                mismatch_rows.append(row)
                mismatch_counts[utype] += 1
            elif comp in ("MATCH", "MATCH_TDU", "MATCH_PARENT"):
                if utype in match_counts:
                    match_counts[utype] += 1

    # Get unique addresses
    unique_addresses = {}
    for row in mismatch_rows:
        addr = row.get("address", "").strip()
        if addr and addr not in unique_addresses:
            unique_addresses[addr] = len(unique_addresses)

    logger.info(f"Mismatches: electric={mismatch_counts['electric']}, "
                f"gas={mismatch_counts['gas']}, water={mismatch_counts['water']}")
    logger.info(f"Unique addresses to geocode: {len(unique_addresses)}")

    # ================================================================
    # 2. Load engine
    # ================================================================
    logger.info("Loading engine...")
    engine = LookupEngine(skip_water=False)
    logger.info("Engine loaded.")

    # ================================================================
    # 3. Geocode — check engine cache first, then Census batch
    # ================================================================
    logger.info("Checking engine cache for coordinates...")
    addr_coords = {}  # address -> (lat, lon)
    uncached = []

    for addr, idx in unique_addresses.items():
        cached = engine.cache.get(addr)
        if cached and (cached.lat != 0.0 or cached.lon != 0.0):
            addr_coords[addr] = (cached.lat, cached.lon)
        else:
            uncached.append((str(idx), addr))

    logger.info(f"Cache hits: {len(addr_coords)}, need geocoding: {len(uncached)}")

    if uncached:
        logger.info(f"Sending {len(uncached)} addresses to Census batch geocoder...")
        geocoder = CensusGeocoder()
        batch_results = geocoder.geocode_batch(uncached)
        geo_ok = 0
        for key, geo in batch_results.items():
            if geo and (geo.lat != 0.0 or geo.lon != 0.0):
                # Find the address for this key
                idx = int(key)
                for addr, aidx in unique_addresses.items():
                    if aidx == idx:
                        addr_coords[addr] = (geo.lat, geo.lon)
                        geo_ok += 1
                        break
        logger.info(f"Census batch: {geo_ok} matched, {len(uncached) - geo_ok} failed")

    logger.info(f"Total addresses with coordinates: {len(addr_coords)}/{len(unique_addresses)}")

    # ================================================================
    # 4. Re-run lookups and compare
    # ================================================================
    logger.info("Re-running lookups through state GIS + gas mappings...")

    results = {}
    for utype in ("electric", "gas", "water"):
        results[utype] = {
            "total": mismatch_counts[utype],
            "fixed": 0,
            "still_mismatch": 0,
            "no_coords": 0,
            "new_source_counts": Counter(),
            "examples_fixed": [],
            "examples_still": [],
            "by_state": defaultdict(lambda: {"fixed": 0, "total": 0}),
        }

    for i, row in enumerate(mismatch_rows):
        utype = row.get("utility_type", "")
        address = row.get("address", "").strip()
        tenant_raw = row.get("tenant_raw", "")
        old_engine = row.get("engine_provider", "")
        state = row.get("state", "") or _extract_state(address)

        r = results[utype]
        r["by_state"][state]["total"] += 1

        coords = addr_coords.get(address)
        if not coords:
            r["no_coords"] += 1
            r["still_mismatch"] += 1
            continue

        lat, lon = coords
        _zip_m = re.search(r"\b(\d{5})(?:-\d{4})?\s*$", address)
        addr_zip = _zip_m.group(1) if _zip_m else ""

        # New lookup
        new_result = engine._lookup_with_state_gis(
            lat, lon, state, utype, zip_code=addr_zip
        )

        new_name = new_result.provider_name if new_result else ""
        new_source = new_result.polygon_source if new_result else "none"
        r["new_source_counts"][new_source] += 1

        # Compare
        category, detail, tenant_norm = compare_providers(
            new_name, tenant_raw, utype, state
        )

        if category in ("MATCH", "MATCH_TDU", "MATCH_PARENT"):
            r["fixed"] += 1
            r["by_state"][state]["fixed"] += 1
            if len(r["examples_fixed"]) < 10:
                r["examples_fixed"].append({
                    "addr": address[:65],
                    "old": old_engine,
                    "new": new_name,
                    "tenant": tenant_raw[:40],
                    "src": new_source,
                    "cat": category,
                })
        else:
            r["still_mismatch"] += 1
            if len(r["examples_still"]) < 5:
                r["examples_still"].append({
                    "addr": address[:65],
                    "old": old_engine,
                    "new": new_name,
                    "tenant": tenant_raw[:40],
                    "src": new_source,
                })

        if (i + 1) % 5000 == 0:
            logger.info(f"  Processed {i+1}/{len(mismatch_rows)} mismatch rows...")

    elapsed = time.time() - t_start
    logger.info(f"Done in {elapsed:.1f}s")

    # ================================================================
    # 5. Report
    # ================================================================
    print("\n" + "=" * 70)
    print("MISMATCH RE-TEST RESULTS")
    print(f"(State GIS + Gas ZIP Mappings + EIA Verification)")
    print("=" * 70)

    for utype in ("electric", "gas", "water"):
        r = results[utype]
        total = r["total"]
        if total == 0:
            continue
        tested = total - r["no_coords"]
        fix_pct = r["fixed"] / total * 100

        print(f"\n{'─'*70}")
        print(f"{utype.upper()} — {total} mismatches")
        print(f"{'─'*70}")
        print(f"  Geocoded:              {tested:>6} / {total}")
        print(f"  No coordinates:        {r['no_coords']:>6}")
        print(f"  ✓ Fixed (now MATCH):   {r['fixed']:>6} ({fix_pct:.1f}% of all mismatches)")
        print(f"  ✗ Still MISMATCH:      {r['still_mismatch']:>6}")

        # Source breakdown
        print(f"\n  New lookup source breakdown:")
        for src, n in r["new_source_counts"].most_common(10):
            print(f"    {src}: {n}")

        # State breakdown
        print(f"\n  Top states fixed:")
        for st, c in sorted(r["by_state"].items(), key=lambda x: -x[1]["fixed"])[:10]:
            if c["fixed"] > 0:
                print(f"    {st}: {c['fixed']:>5} / {c['total']} fixed")

        if r["examples_fixed"]:
            print(f"\n  Examples FIXED:")
            for ex in r["examples_fixed"][:5]:
                print(f"    {ex['addr']}")
                print(f"      old={ex['old']} → new={ex['new']}")
                print(f"      tenant={ex['tenant']}  [{ex['src']}] [{ex['cat']}]")

        if r["examples_still"]:
            print(f"\n  Examples still MISMATCH:")
            for ex in r["examples_still"][:3]:
                print(f"    {ex['addr']}")
                print(f"      engine={ex['new']} vs tenant={ex['tenant']}  [{ex['src']}]")

    # Overall accuracy impact
    print(f"\n{'='*70}")
    print("ACCURACY IMPACT ON 91K BATCH")
    print(f"{'='*70}")
    for utype in ("electric", "gas", "water"):
        r = results[utype]
        old_match = match_counts[utype]
        total_compared = old_match + r["total"]
        if total_compared == 0:
            continue
        old_pct = old_match / total_compared * 100
        new_match = old_match + r["fixed"]
        new_pct = new_match / total_compared * 100
        delta = new_pct - old_pct
        print(f"  {utype.upper():>8}: {old_pct:.1f}% → {new_pct:.1f}%  "
              f"(+{delta:.1f}pp, +{r['fixed']} rows)")


if __name__ == "__main__":
    main()
