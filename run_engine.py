#!/usr/bin/env python3
"""
CLI for the Utility Provider Lookup Engine.

Usage:
    python run_engine.py "1600 Pennsylvania Ave, Dallas, TX 75201"
    python run_engine.py --batch addresses.csv --output results.csv
    python run_engine.py --skip-water "233 S Wacker Dr, Chicago, IL 60606"
"""

import argparse
import csv
import json
import logging
import sys
import time

from lookup_engine.config import Config
from lookup_engine.engine import LookupEngine


def setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def single_lookup(engine: LookupEngine, address: str):
    """Look up a single address and print JSON result."""
    result = engine.lookup(address)
    print(json.dumps(result.to_dict(), indent=2))


def batch_lookup(engine: LookupEngine, input_csv: str, output_csv: str, delay_ms: int):
    """Batch lookup from CSV file."""
    addresses = []
    with open(input_csv, "r") as f:
        reader = csv.DictReader(f)
        addr_col = None
        for col in reader.fieldnames or []:
            if col.lower() in ("address", "display", "full_address"):
                addr_col = col
                break
        if not addr_col:
            addr_col = (reader.fieldnames or ["address"])[0]
        f.seek(0)
        next(reader)  # skip header
        for row in reader:
            addr = row.get(addr_col, "").strip()
            if addr:
                addresses.append(addr)

    print(f"Loaded {len(addresses)} addresses from {input_csv}")
    results = engine.lookup_batch(addresses, delay_ms=delay_ms)

    # Write output
    with open(output_csv, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "address", "lat", "lon", "geocode_confidence",
            "electric_provider", "electric_confidence", "electric_method", "electric_deregulated",
            "gas_provider", "gas_confidence", "gas_method",
            "water_provider", "water_confidence",
            "lookup_time_ms",
        ])
        for r in results:
            writer.writerow([
                r.address, r.lat, r.lon, r.geocode_confidence,
                r.electric.provider_name if r.electric else "",
                round(r.electric.confidence, 3) if r.electric else "",
                r.electric.match_method if r.electric else "",
                r.electric.is_deregulated if r.electric else "",
                r.gas.provider_name if r.gas else "",
                round(r.gas.confidence, 3) if r.gas else "",
                r.gas.match_method if r.gas else "",
                r.water.provider_name if r.water else "",
                round(r.water.confidence, 3) if r.water else "",
                r.lookup_time_ms,
            ])

    print(f"Wrote {len(results)} results to {output_csv}")


def main():
    parser = argparse.ArgumentParser(description="Utility Provider Lookup Engine")
    parser.add_argument("address", nargs="?", help="Address to look up")
    parser.add_argument("--batch", help="Input CSV file for batch processing")
    parser.add_argument("--output", default="results.csv", help="Output CSV for batch mode")
    parser.add_argument("--delay", type=int, default=200, help="Delay between geocode calls (ms)")
    parser.add_argument("--skip-water", action="store_true", help="Skip loading water layer (saves ~30s)")
    parser.add_argument("--no-cache", action="store_true", help="Disable cache")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")
    parser.add_argument("--geocoder", choices=["census", "google"], default="census")
    parser.add_argument("--google-key", default="", help="Google Maps API key")

    args = parser.parse_args()
    setup_logging(args.verbose)

    if not args.address and not args.batch:
        parser.print_help()
        sys.exit(1)

    config = Config(
        geocoder_type=args.geocoder,
        google_api_key=args.google_key,
    )

    print("Loading engine (shapefiles)...")
    t0 = time.time()
    engine = LookupEngine(config, skip_water=args.skip_water)
    print(f"Engine ready in {time.time() - t0:.1f}s")

    if args.batch:
        batch_lookup(engine, args.batch, args.output, args.delay)
    else:
        single_lookup(engine, args.address)


if __name__ == "__main__":
    main()
