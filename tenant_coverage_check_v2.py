#!/usr/bin/env python3
"""
Tenant Coverage Check v2

Uses normalize_provider_multi() with comma-split and REP detection.
Reports match rates by utility type, top unmatched, REP-flagged entries,
and comma-split success stats.

Output: TENANT_COVERAGE_REPORT.md
"""

import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from provider_normalizer import normalize_provider_multi, is_deregulated_rep

DATA_FILE = Path(__file__).parent / "data" / "canonical_providers.json"
TENANT_CSV = Path(__file__).parent / "addresses_with_tenant_verification_2026-02-06T06_57_49.470044438-06_00.csv"

# CSV columns to check
UTILITY_FIELDS = {
    "electric": "Electricity",
    "gas": "Gas",
    "water": "Water",
    "trash": "Trash",
    "sewer": "Sewer",
}


def main():
    # Load canonical count
    with open(DATA_FILE) as f:
        canonical_count = len(json.load(f))

    print(f"Loading tenant CSV: {TENANT_CSV.name}...")
    all_records = []
    with open(TENANT_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            all_records.append(row)
    print(f"  {len(all_records)} records loaded")

    # Per-utility-type stats
    stats = {}
    all_unmatched = Counter()  # across electric + gas only
    all_rep_flagged = Counter()
    all_null = 0
    all_propane = 0
    comma_split_total = 0
    comma_split_partial = 0
    comma_split_full = 0

    for utype, field in UTILITY_FIELDS.items():
        print(f"\nProcessing {utype} ({field})...")
        matched_instances = 0
        unmatched_instances = 0
        rep_flagged_instances = 0
        null_instances = 0
        propane_instances = 0
        total_instances = 0
        unmatched_names = Counter()
        rep_names = Counter()

        for rec in all_records:
            raw = rec.get(field, "").strip()
            if not raw:
                continue
            total_instances += 1

            results = normalize_provider_multi(raw)

            # Track comma-split stats
            if "," in raw:
                comma_split_total += 1
                matched_segs = [r for r in results if r["matched"]]
                if matched_segs:
                    comma_split_partial += 1
                if len(matched_segs) == len(results):
                    comma_split_full += 1

            # Check for null/propane first
            any_null = any(r.get("match_type") == "null_value" for r in results)
            any_propane = any(r.get("match_type") == "propane" for r in results)
            any_matched = any(r["matched"] for r in results)
            any_rep = any(r["is_rep"] for r in results)

            if any_null:
                null_instances += 1
            elif any_propane:
                propane_instances += 1
            elif any_rep:
                rep_flagged_instances += 1
                rep_names[raw] += 1
                if utype in ("electric", "gas"):
                    all_rep_flagged[raw] += 1
                if any_matched:
                    matched_instances += 1
            elif any_matched:
                matched_instances += 1
            else:
                unmatched_instances += 1
                unmatched_names[raw] += 1
                if utype in ("electric", "gas"):
                    all_unmatched[raw] += 1

        all_null += null_instances
        all_propane += propane_instances

        stats[utype] = {
            "total": total_instances,
            "matched": matched_instances,
            "unmatched": unmatched_instances,
            "rep_flagged": rep_flagged_instances,
            "null": null_instances,
            "propane": propane_instances,
            "unmatched_names": unmatched_names,
            "rep_names": rep_names,
        }
        pct = (matched_instances / total_instances * 100) if total_instances else 0
        print(f"  {total_instances} total, {matched_instances} matched ({pct:.1f}%), "
              f"{rep_flagged_instances} REP-flagged, {unmatched_instances} unmatched")

    # Combined electric + gas stats (primary focus)
    eg_types = ["electric", "gas"]
    combined_total = sum(stats[t]["total"] for t in eg_types if t in stats)
    combined_matched = sum(stats[t]["matched"] for t in eg_types if t in stats)
    combined_rep = sum(stats[t]["rep_flagged"] for t in eg_types if t in stats)
    combined_unmatched = sum(stats[t]["unmatched"] for t in eg_types if t in stats)
    combined_pct = (combined_matched / combined_total * 100) if combined_total else 0

    # Overall across all types
    all_total = sum(stats[t]["total"] for t in stats)
    all_matched = sum(stats[t]["matched"] for t in stats)
    all_pct = (all_matched / all_total * 100) if all_total else 0

    # ============================================================
    # GENERATE REPORT
    # ============================================================
    lines = []
    lines.append("# Tenant Coverage Report")
    lines.append("")
    lines.append(f"Generated from tenant-verified address data using `normalize_provider_multi()`.")
    lines.append(f"Normalization target: `data/canonical_providers.json` ({canonical_count} canonical providers).")
    lines.append(f"REP detection: `data/deregulated_reps.json` (93 TX REP names).")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Summary by Utility Type")
    lines.append("")
    lines.append("| Utility Type | Total | Matched | Match % | REP-Flagged | Null/Placeholder | Propane | Unmatched |")
    lines.append("|-------------|-------|---------|---------|-------------|-----------------|---------|-----------|")
    for utype in ["electric", "gas", "water", "trash", "sewer"]:
        s = stats.get(utype)
        if not s or s["total"] == 0:
            continue
        pct = (s["matched"] / s["total"] * 100) if s["total"] else 0
        lines.append(f"| {utype.title()} | {s['total']:,} | {s['matched']:,} | {pct:.1f}% | {s['rep_flagged']:,} | {s.get('null',0):,} | {s.get('propane',0):,} | {s['unmatched']:,} |")
    combined_null = sum(stats[t].get("null", 0) for t in eg_types if t in stats)
    combined_propane = sum(stats[t].get("propane", 0) for t in eg_types if t in stats)
    lines.append(f"| **Electric + Gas** | **{combined_total:,}** | **{combined_matched:,}** | **{combined_pct:.1f}%** | **{combined_rep:,}** | **{combined_null:,}** | **{combined_propane:,}** | **{combined_unmatched:,}** |")
    lines.append(f"| *All types* | *{all_total:,}* | *{all_matched:,}* | *{all_pct:.1f}%* | | *{all_null:,}* | *{all_propane:,}* | |")

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Comma-Split Statistics")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append(f"| Comma-separated entries processed | {comma_split_total:,} |")
    lines.append(f"| At least one segment matched | {comma_split_partial:,} |")
    lines.append(f"| All segments matched | {comma_split_full:,} |")
    partial_pct = (comma_split_partial / comma_split_total * 100) if comma_split_total else 0
    lines.append(f"| Partial+ match rate | {partial_pct:.1f}% |")

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Top 20 REP-Flagged Entries")
    lines.append("")
    lines.append("These entries contain known TX Retail Electric Provider names.")
    lines.append("The correct response is to return the TDU for the address, not the REP.")
    lines.append("")
    lines.append("| Rank | Entry | Occurrences |")
    lines.append("|------|-------|-------------|")
    for i, (name, count) in enumerate(all_rep_flagged.most_common(20), 1):
        # Truncate long entries
        display = name if len(name) <= 80 else name[:77] + "..."
        lines.append(f"| {i} | {display} | {count:,} |")

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Top 50 Unmatched Provider Names — Electric + Gas")
    lines.append("")
    lines.append("These are the most common provider name strings that do NOT match")
    lines.append("any canonical provider, alias, or known REP.")
    lines.append("")
    lines.append("| Rank | Provider Name | Occurrences | Category |")
    lines.append("|------|---------------|-------------|----------|")

    for i, (name, count) in enumerate(all_unmatched.most_common(50), 1):
        nl = name.lower()
        cat = ""
        if "," in name:
            cat = "Compound (comma-separated)"
        elif any(kw in nl for kw in ["choose", "select", "enter", "n/a", "none", "power to"]):
            cat = "Placeholder"
        elif any(kw in nl for kw in ["water", "sewer", "waste"]):
            cat = "Water/sewer"
        elif any(kw in nl for kw in ["co-op", "coop", "cooperative", "emc"]):
            cat = "Co-op"
        elif any(kw in nl for kw in ["municipal", "city of", "town of", "village of"]):
            cat = "Municipal"
        elif "gas" in nl and "electric" not in nl:
            cat = "Gas utility"
        display = name if len(name) <= 70 else name[:67] + "..."
        lines.append(f"| {i} | {display} | {count:,} | {cat} |")

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Methodology")
    lines.append("")
    lines.append(f"- **Data source:** `{TENANT_CSV.name}` ({len(all_records):,} addresses)")
    lines.append("- **Fields checked:** Electricity, Gas, Water, Trash, Sewer")
    lines.append("- **Normalizer:** `normalize_provider_multi()` with comma-split preprocessing")
    lines.append("- **REP detection:** Known TX REPs from `deregulated_reps.json` (93 names)")
    lines.append("- **Match method:** Direct case-insensitive lookup + partial substring matching")
    lines.append("- **Note:** Water match rates are expected to be low — water providers are mostly municipal utilities with thousands of naming variants. Focus analysis on Electric and Gas.")

    report = "\n".join(lines) + "\n"
    out_path = Path(__file__).parent / "TENANT_COVERAGE_REPORT.md"
    with open(out_path, "w") as f:
        f.write(report)
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
