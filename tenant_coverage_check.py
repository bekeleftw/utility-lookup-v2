#!/usr/bin/env python3
"""
Tenant Coverage Check

Loads tenant-verified address data, extracts provider names,
runs each through normalize_provider(), and reports coverage stats.

Output: TENANT_COVERAGE_REPORT.md
"""

import json
import re
import sys
from collections import Counter
from pathlib import Path

# Load the updated canonical_providers.json directly (not via provider_normalizer
# which may still be cached with old data)
DATA_FILE = Path(__file__).parent / "data" / "canonical_providers.json"
SCRAPE_DIR = Path(__file__).parent.parent / "utility-provider-scrape"

TENANT_FILES = [
    SCRAPE_DIR / "stratified_comparison_140k.json",
    SCRAPE_DIR / "targeted_comparison_74k.json",
]

# Provider fields to extract from tenant data
PROVIDER_FIELDS = ["mapped_electric", "mapped_gas"]


def load_canonical():
    """Load canonical_providers.json and build alias->canonical index."""
    with open(DATA_FILE) as f:
        data = json.load(f)

    alias_to_canonical = {}
    for canonical, entry in data.items():
        if isinstance(entry, dict):
            aliases = entry.get("aliases", [])
        else:
            aliases = entry  # old flat schema fallback

        alias_to_canonical[canonical.lower()] = canonical
        for alias in aliases:
            alias_to_canonical[alias.lower()] = canonical

    return data, alias_to_canonical


def clean_name(name):
    if not name:
        return ""
    cleaned = name.strip()
    cleaned = re.sub(r'\s+', ' ', cleaned)
    return cleaned


def normalize(name, alias_to_canonical):
    """Simple normalization: direct lookup only (no partial match)."""
    if not name:
        return None
    cleaned = clean_name(name)
    lookup = cleaned.lower()
    return alias_to_canonical.get(lookup)


def main():
    print("Loading canonical_providers.json...")
    data, alias_to_canonical = load_canonical()
    print(f"  {len(data)} canonical providers, {len(alias_to_canonical)} index entries")

    # Collect all unique provider names from tenant data
    print("\nLoading tenant-verified data...")
    all_provider_names = []
    addresses_loaded = 0

    for filepath in TENANT_FILES:
        if not filepath.exists():
            print(f"  SKIP: {filepath} not found")
            continue
        print(f"  Loading {filepath.name}...")
        with open(filepath) as f:
            records = json.load(f)
        print(f"    {len(records)} records")
        addresses_loaded += len(records)

        for record in records:
            for field in PROVIDER_FIELDS:
                name = record.get(field, "")
                if name and name.strip():
                    all_provider_names.append(name.strip())

    print(f"\n  Total addresses loaded: {addresses_loaded}")
    print(f"  Total provider name instances: {len(all_provider_names)}")

    # Deduplicate to unique names
    unique_names = Counter(all_provider_names)
    print(f"  Unique provider names: {len(unique_names)}")

    # Run each through normalization
    print("\nRunning normalization...")
    matched = Counter()       # name -> count (matched)
    unmatched = Counter()     # name -> count (unmatched)
    matched_instances = 0
    unmatched_instances = 0

    for name, count in unique_names.items():
        canonical = normalize(name, alias_to_canonical)
        if canonical:
            matched[name] += count
            matched_instances += count
        else:
            unmatched[name] += count
            unmatched_instances += count

    total_instances = matched_instances + unmatched_instances
    total_unique = len(unique_names)
    matched_unique = len(matched)
    unmatched_unique = len(unmatched)

    match_rate_instances = (matched_instances / total_instances * 100) if total_instances else 0
    match_rate_unique = (matched_unique / total_unique * 100) if total_unique else 0

    print(f"\n  Matched:   {matched_instances:,} instances ({match_rate_instances:.1f}%), {matched_unique} unique names")
    print(f"  Unmatched: {unmatched_instances:,} instances ({100-match_rate_instances:.1f}%), {unmatched_unique} unique names")

    # Top 50 unmatched by frequency
    top_unmatched = unmatched.most_common(50)

    # Generate report
    report_lines = []
    report_lines.append("# Tenant Coverage Report")
    report_lines.append("")
    report_lines.append(f"Generated from tenant-verified address data.")
    report_lines.append(f"Normalization target: `data/canonical_providers.json` ({len(data)} canonical providers).")
    report_lines.append("")
    report_lines.append("---")
    report_lines.append("")
    report_lines.append("## Summary")
    report_lines.append("")
    report_lines.append("| Metric | Value |")
    report_lines.append("|--------|-------|")
    report_lines.append(f"| Addresses loaded | {addresses_loaded:,} |")
    report_lines.append(f"| Provider name instances checked | {total_instances:,} |")
    report_lines.append(f"| Unique provider names | {total_unique:,} |")
    report_lines.append(f"| **Matched (instances)** | **{matched_instances:,} ({match_rate_instances:.1f}%)** |")
    report_lines.append(f"| **Unmatched (instances)** | **{unmatched_instances:,} ({100-match_rate_instances:.1f}%)** |")
    report_lines.append(f"| Matched (unique names) | {matched_unique:,} ({match_rate_unique:.1f}%) |")
    report_lines.append(f"| Unmatched (unique names) | {unmatched_unique:,} ({100-match_rate_unique:.1f}%) |")
    report_lines.append("")
    report_lines.append("---")
    report_lines.append("")
    report_lines.append("## Top 50 Unmatched Provider Names (by frequency)")
    report_lines.append("")
    report_lines.append("These are the most common tenant-reported provider names that do NOT match")
    report_lines.append("any canonical provider or alias in `canonical_providers.json`.")
    report_lines.append("")
    report_lines.append("| Rank | Provider Name | Occurrences | Notes |")
    report_lines.append("|------|---------------|-------------|-------|")

    for i, (name, count) in enumerate(top_unmatched, 1):
        # Try to identify what kind of entry this is
        notes = ""
        nl = name.lower()
        if "," in name and any(kw in nl for kw in ["pge", "pg&e", "sce", "sdg&e", "edison", "gas"]):
            notes = "Compound key (multi-provider ZIP)"
        elif any(kw in nl for kw in ["choose", "select", "enter", "n/a", "none", "unknown"]):
            notes = "Placeholder/invalid"
        elif any(kw in nl for kw in ["water", "sewer", "waste"]):
            notes = "Water/sewer utility (not electric/gas)"
        elif "co-op" in nl or "coop" in nl or "cooperative" in nl or "emc" in nl:
            notes = "Co-op/EMC"
        elif "municipal" in nl or "city of" in nl or "town of" in nl:
            notes = "Municipal utility"
        elif "gas" in nl and "electric" not in nl:
            notes = "Gas utility"
        report_lines.append(f"| {i} | {name} | {count:,} | {notes} |")

    report_lines.append("")
    report_lines.append("---")
    report_lines.append("")
    report_lines.append("## Methodology")
    report_lines.append("")
    report_lines.append("- **Data sources:** `stratified_comparison_140k.json` and `targeted_comparison_74k.json`")
    report_lines.append("- **Fields checked:** `mapped_electric` and `mapped_gas` from each record")
    report_lines.append("- **Match method:** Direct case-insensitive lookup against canonical names and aliases (no partial/fuzzy matching)")
    report_lines.append("- **Note:** Partial matching (used in `provider_normalizer.py`) would increase the match rate but also increase false positives. This report uses strict matching to identify true coverage gaps.")

    report_text = "\n".join(report_lines) + "\n"

    output_path = Path(__file__).parent / "TENANT_COVERAGE_REPORT.md"
    with open(output_path, "w") as f:
        f.write(report_text)
    print(f"\nWrote {output_path}")


if __name__ == "__main__":
    main()
