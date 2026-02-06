#!/usr/bin/env python3
"""
Post-process batch results with AI resolver for low-confidence lookups.

Usage:
  python ai_resolve_batch.py --input batch_results.csv --output batch_results_ai.csv --provider openrouter --model anthropic/claude-sonnet-4-5
  python ai_resolve_batch.py --input batch_results.csv --output batch_results_ai.csv --provider openrouter --model openai/gpt-5.2
  python ai_resolve_batch.py --compare --input batch_results.csv   # Compare GPT-5.2 vs Sonnet on 20 samples

Options:
  --max-calls 5000    Limit total API calls (cost control)
  --min-confidence 0.80   Only resolve results below this confidence
  --dry-run          Show what would be resolved without calling API
  --compare          Run head-to-head comparison of two models on a sample
  --compare-size 20  Number of samples for comparison
"""

import argparse
import csv
import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from lookup_engine.ai_resolver import AIResolver


def load_env():
    """Load .env file if present."""
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ.setdefault(key.strip(), val.strip())


def get_needs_review_rows(rows, min_confidence):
    """Find rows needing AI review."""
    needs_review = []
    for i, row in enumerate(rows):
        conf_str = row.get("engine_confidence", "") or ""
        try:
            conf = float(conf_str)
        except ValueError:
            continue
        has_provider = bool(row.get("engine_provider"))
        has_alts = bool(row.get("engine_alternatives"))
        is_mismatch = row.get("comparison") in ("MISMATCH", "MATCH_ALT")

        if has_provider and conf < min_confidence:
            needs_review.append((i, row))
        elif is_mismatch and has_provider:
            needs_review.append((i, row))
    return needs_review


def parse_candidates(row):
    """Build candidate list from a batch result row."""
    candidates = []
    if row.get("engine_provider"):
        candidates.append({
            "provider": row["engine_provider"],
            "confidence": float(row.get("engine_confidence", 0.5) or 0.5),
            "source": row.get("engine_source", "unknown"),
        })

    alts_str = row.get("engine_alternatives", "")
    if alts_str:
        for alt in alts_str.split("|"):
            alt = alt.strip()
            if alt:
                candidates.append({
                    "provider": alt,
                    "confidence": 0.50,
                    "source": "alternative",
                })

    # Add tenant as a candidate if it's a mismatch
    tenant = (row.get("tenant_raw") or "").strip()
    if tenant and row.get("comparison") in ("MISMATCH", "MATCH_ALT"):
        candidates.append({
            "provider": tenant,
            "confidence": 0.60,
            "source": "tenant_reported",
        })

    return candidates


def extract_zip_city(row):
    """Extract ZIP and city from address."""
    address = row.get("address", "")
    zip_m = re.search(r"\b(\d{5})(?:-\d{4})?\s*$", address)
    zip_code = zip_m.group(1) if zip_m else ""
    city_m = re.search(r",\s*([^,]+?)\s*,\s*[A-Z]{2}", address)
    city = city_m.group(1).strip() if city_m else ""
    return zip_code, city


def run_resolve(args):
    """Main resolve flow."""
    load_env()
    api_key = args.api_key or os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        print("ERROR: No API key. Set --api-key or OPENROUTER_API_KEY env var.")
        sys.exit(1)

    resolver = AIResolver(api_key, args.provider, args.model)

    with open(args.input) as f:
        rows = list(csv.DictReader(f))

    needs_review = get_needs_review_rows(rows, args.min_confidence)
    print(f"Found {len(needs_review)} rows needing review (confidence < {args.min_confidence} or mismatch)")
    print(f"Max API calls: {args.max_calls}")
    print(f"Model: {resolver.model}")

    if args.dry_run:
        by_type = Counter(row.get("utility_type", "?") for _, row in needs_review)
        by_comp = Counter(row.get("comparison", "?") for _, row in needs_review)
        print("\nBy utility type:")
        for ut, count in by_type.most_common():
            print(f"  {ut}: {count}")
        print("\nBy comparison:")
        for comp, count in by_comp.most_common():
            print(f"  {comp}: {count}")
        sys.exit(0)

    resolved = 0
    changed = 0

    for idx, (row_idx, row) in enumerate(needs_review):
        if resolver.call_count >= args.max_calls:
            print(f"\nReached max calls ({args.max_calls}), stopping")
            break

        candidates = parse_candidates(row)
        if len(candidates) < 2:
            continue

        zip_code, city = extract_zip_city(row)

        result = resolver.resolve(
            address=row.get("address", ""),
            state=row.get("state", ""),
            utility_type=row.get("utility_type", ""),
            candidates=candidates,
            zip_code=zip_code,
            city=city,
        )

        resolved += 1

        if result:
            old_provider = rows[row_idx]["engine_provider"]
            rows[row_idx]["engine_provider"] = result["provider"]
            rows[row_idx]["engine_confidence"] = str(round(result["confidence"], 3))
            rows[row_idx]["engine_source"] = result["source"]
            rows[row_idx]["ai_reasoning"] = result.get("reasoning", "")
            rows[row_idx]["ai_resolved"] = "true"

            if result["provider"] != old_provider:
                changed += 1

        if (idx + 1) % 50 == 0:
            print(f"  Processed {idx + 1}/{len(needs_review)}, {changed} changed, {resolver.error_count} errors")

    # Write output
    fieldnames = list(rows[0].keys())
    for extra in ["ai_reasoning", "ai_resolved"]:
        if extra not in fieldnames:
            fieldnames.append(extra)

    with open(args.output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nDone. Resolved {resolved}, changed {changed}, errors {resolver.error_count}")
    print(f"API calls made: {resolver.call_count}")
    print(f"Output: {args.output}")


def run_compare(args):
    """Head-to-head comparison of two models on a sample."""
    load_env()
    api_key = args.api_key or os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        print("ERROR: No API key.")
        sys.exit(1)

    with open(args.input) as f:
        rows = list(csv.DictReader(f))

    needs_review = get_needs_review_rows(rows, args.min_confidence)
    # Filter to only mismatches with tenant data for clear scoring
    scorable = [
        (i, r) for i, r in needs_review
        if r.get("comparison") in ("MISMATCH", "MATCH_ALT") and r.get("tenant_raw")
    ]

    sample_size = min(args.compare_size, len(scorable))
    sample = scorable[:sample_size]

    print(f"Comparing models on {sample_size} mismatch samples")
    print(f"Model A: openai/gpt-5.2")
    print(f"Model B: anthropic/claude-sonnet-4-5")
    print("=" * 70)

    resolver_a = AIResolver(api_key, "openrouter", "openai/gpt-5.2")
    resolver_b = AIResolver(api_key, "openrouter", "anthropic/claude-sonnet-4-5")

    results = []
    for idx, (row_idx, row) in enumerate(sample):
        candidates = parse_candidates(row)
        if len(candidates) < 2:
            continue

        zip_code, city = extract_zip_city(row)
        kwargs = dict(
            address=row.get("address", ""),
            state=row.get("state", ""),
            utility_type=row.get("utility_type", ""),
            candidates=candidates,
            zip_code=zip_code,
            city=city,
        )

        result_a = resolver_a.resolve(**kwargs)
        result_b = resolver_b.resolve(**kwargs)

        tenant = (row.get("tenant_raw") or "").strip()
        engine_primary = row.get("engine_provider", "")

        pick_a = result_a["provider"] if result_a else "NONE"
        pick_b = result_b["provider"] if result_b else "NONE"

        # Score: does the pick match the tenant?
        def matches_tenant(pick):
            if not pick or pick == "NONE":
                return False
            return (
                pick.upper() in tenant.upper()
                or tenant.upper() in pick.upper()
                or any(w in pick.upper() for w in tenant.upper().split() if len(w) > 3)
            )

        a_correct = matches_tenant(pick_a)
        b_correct = matches_tenant(pick_b)

        results.append({
            "address": row.get("address", "")[:55],
            "type": row.get("utility_type", ""),
            "tenant": tenant[:30],
            "engine": engine_primary[:25],
            "gpt52": pick_a[:25],
            "sonnet": pick_b[:25],
            "gpt52_correct": a_correct,
            "sonnet_correct": b_correct,
            "gpt52_reasoning": (result_a or {}).get("reasoning", "")[:50],
            "sonnet_reasoning": (result_b or {}).get("reasoning", "")[:50],
        })

        status_a = "✓" if a_correct else "✗"
        status_b = "✓" if b_correct else "✗"
        print(f"\n[{idx+1}/{sample_size}] {row.get('address', '')[:55]}")
        print(f"  Tenant: {tenant[:40]}")
        print(f"  Engine: {engine_primary[:40]}")
        print(f"  GPT-5.2: {pick_a[:40]} {status_a}")
        print(f"  Sonnet:  {pick_b[:40]} {status_b}")

    # Summary
    a_total = sum(1 for r in results if r["gpt52_correct"])
    b_total = sum(1 for r in results if r["sonnet_correct"])
    both = sum(1 for r in results if r["gpt52_correct"] and r["sonnet_correct"])
    neither = sum(1 for r in results if not r["gpt52_correct"] and not r["sonnet_correct"])

    print("\n" + "=" * 70)
    print(f"COMPARISON RESULTS ({len(results)} samples)")
    print("=" * 70)
    print(f"  GPT-5.2 correct:  {a_total}/{len(results)} ({a_total/len(results)*100:.0f}%)")
    print(f"  Sonnet correct:   {b_total}/{len(results)} ({b_total/len(results)*100:.0f}%)")
    print(f"  Both correct:     {both}")
    print(f"  Neither correct:  {neither}")
    print(f"  GPT-5.2 only:     {a_total - both}")
    print(f"  Sonnet only:      {b_total - both}")
    print(f"\n  API calls: GPT-5.2={resolver_a.call_count}, Sonnet={resolver_b.call_count}")
    print(f"  Errors: GPT-5.2={resolver_a.error_count}, Sonnet={resolver_b.error_count}")

    # Save comparison CSV
    comp_path = Path(args.input).parent / "ai_comparison_results.csv"
    with open(comp_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        writer.writeheader()
        writer.writerows(results)
    print(f"\n  Detailed results: {comp_path}")


def main():
    parser = argparse.ArgumentParser(description="AI resolver for batch results")
    parser.add_argument("--input", required=True, help="Input batch_results.csv")
    parser.add_argument("--output", default="batch_results_ai.csv", help="Output CSV")
    parser.add_argument("--provider", choices=["anthropic", "openrouter"], default="openrouter")
    parser.add_argument("--api-key", default=None, help="API key (or set env var)")
    parser.add_argument("--model", default=None, help="Override model name")
    parser.add_argument("--max-calls", type=int, default=5000)
    parser.add_argument("--min-confidence", type=float, default=0.80)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--compare", action="store_true", help="Compare GPT-5.2 vs Sonnet")
    parser.add_argument("--compare-size", type=int, default=20, help="Samples for comparison")
    args = parser.parse_args()

    if args.compare:
        run_compare(args)
    else:
        run_resolve(args)


if __name__ == "__main__":
    main()
