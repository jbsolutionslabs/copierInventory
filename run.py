#!/usr/bin/env python3
"""
run.py — Copier Inventory Aggregator

Usage:
  python run.py                     # fetch all auto sources + load imports/
  python run.py --source rci als    # only fetch specific sources
  python run.py --imports-only      # skip web fetching, only process imports/
  python run.py --list-sources      # show available sources and exit
"""

import argparse
import sys
import traceback

import normalizer
import aggregator
from config import SOURCES


def _fetch_source(key: str) -> list:
    """Fetch a single source by key. Returns list of DataFrames (may be empty)."""
    cfg = SOURCES[key]
    name = cfg["name"]
    print(f"\n[{key.upper()}] Fetching {name} ...")

    try:
        if key == "rci":
            from fetchers.rci import fetch
        elif key == "als":
            from fetchers.als import fetch
        elif key == "ars":
            from fetchers.equipment_recovery import fetch
        else:
            print(f"  No fetcher implemented for '{key}' — skipping.")
            return []

        raw = fetch()
        if raw.empty:
            print(f"  No data returned.")
            return []

        df = normalizer.normalize(raw, name)
        print(f"  {len(df)} rows fetched.")
        return [df]

    except Exception as exc:
        print(f"  ERROR: {exc}")
        traceback.print_exc()
        return []


def main():
    parser = argparse.ArgumentParser(description="Copier Inventory Aggregator")
    parser.add_argument(
        "--source", "-s",
        nargs="+",
        choices=list(SOURCES.keys()),
        help="Only fetch these sources (default: all)",
    )
    parser.add_argument(
        "--imports-only",
        action="store_true",
        help="Skip web fetching; only load files from imports/",
    )
    parser.add_argument(
        "--list-sources",
        action="store_true",
        help="Print available sources and exit",
    )
    args = parser.parse_args()

    if args.list_sources:
        print("\nAuto-fetch sources:")
        for k, v in SOURCES.items():
            print(f"  {k:15s} {v['name']}  ({v['type']})")
        print("\nManual import: drop files into  imports/")
        print("  Prefix filename with source key, e.g. tnt_inventory.xlsx")
        print("  Supported keys: tnt, wulff, mars, rsi, copex, impact, intercom")
        sys.exit(0)

    frames = []

    # --- Web fetchers ---
    if not args.imports_only:
        keys = args.source if args.source else list(SOURCES.keys())
        for key in keys:
            frames.extend(_fetch_source(key))

    # --- Manual imports ---
    print("\n[IMPORTS] Loading files from imports/ ...")
    frames.extend(aggregator.load_manual_imports())

    # --- Aggregate & write ---
    print("\n[OUTPUT] Aggregating ...")
    out_path = aggregator.write_excel(frames)

    if out_path:
        print(f"\nDone! Open your inventory at:\n  {out_path}\n")
    else:
        print("\nNo data collected. Nothing to write.\n")


if __name__ == "__main__":
    main()
