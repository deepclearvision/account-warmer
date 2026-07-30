#!/usr/bin/env python3
"""
dry_run.py — Resolve a profile into a RunPlan and PRINT EVERY DECISION.
No Geelark calls. No cloud phone. Completely safe to run any time.

This is the transparency tool. It answers "what would the system do for this
phone, and why?" with a full decision trail — the thing you said the current
implementation doesn't give you.

Usage:
    python3 scripts/dry_run.py data/sample_profiles.csv Southwark_Plumbers_01
    python3 scripts/dry_run.py data/sample_profiles.csv Southwark_Plumbers_01 --seed 42
    python3 scripts/dry_run.py data/sample_profiles.csv --all
    python3 scripts/dry_run.py data/sample_profiles.csv Southwark_Plumbers_01 --runs 5

--seed   reproduce an exact plan.
--all    dry-run every profile in the file (validates the whole dataset).
--runs N show N consecutive runs for one phone, each avoiding the previous
         pick — so you can SEE the per-run variation a few-run phone will show.
"""

import argparse
import sys
from pathlib import Path

# Make src/ importable whether run from repo root or scripts/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from data_layer import load_profiles_csv, load_profile_csv_by_key, Coordinate, DataError
from resolver import resolve
from run_logger import log_plan
from provisioning import check_phone_fit


def show_profile_warnings(p) -> None:
    if p.warnings:
        print(f"  data warnings for {p.profile_key}:")
        for w in p.warnings:
            print(f"    ! {w}")
    fit, reason = check_phone_fit(p)
    if not fit:
        print(f"  PROVISIONING WARNING for {p.profile_key}: {reason}")


def dry_run_one(profile, seed=None, avoid_gps=None, avoid_search=None, log_dir=None):
    plan = resolve(profile, seed=seed, avoid_gps=avoid_gps, avoid_search=avoid_search)
    print(plan.summary())
    print("  decision trail:")
    for d in plan.decisions:
        print(f"    - {d}")
    print()
    if log_dir:
        log_plan(plan, log_dir=log_dir, extra={"phase": "dry_run"})
    return plan


def main() -> int:
    ap = argparse.ArgumentParser(description="Dry-run the resolver (no Geelark).")
    ap.add_argument("csv_path")
    ap.add_argument("profile_key", nargs="?", help="omit with --all")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--all", action="store_true", help="dry-run every profile")
    ap.add_argument("--runs", type=int, default=1, help="show N consecutive runs")
    ap.add_argument("--log", metavar="DIR", default=None,
                    help="also write logs/runs.log + runs.jsonl to DIR")
    args = ap.parse_args()

    try:
        if args.all:
            profiles = load_profiles_csv(args.csv_path)
            print(f"Loaded and validated {len(profiles)} profile(s).\n")
            for p in profiles:
                show_profile_warnings(p)
                dry_run_one(p, seed=args.seed, log_dir=args.log)
            return 0

        if not args.profile_key:
            ap.error("provide a profile_key, or use --all")

        profile = load_profile_csv_by_key(args.csv_path, args.profile_key)
        show_profile_warnings(profile)

        avoid_gps = None
        avoid_search = None
        for i in range(args.runs):
            if args.runs > 1:
                print(f"===== run {i + 1} of {args.runs} =====")
            # Each run uses a different seed unless one was pinned.
            seed = args.seed if (args.seed is not None and args.runs == 1) else None
            plan = dry_run_one(
                profile, seed=seed, avoid_gps=avoid_gps, avoid_search=avoid_search,
                log_dir=args.log
            )
            # Feed this run's picks forward so the next run avoids repeating them.
            if not plan.abort:
                avoid_gps = Coordinate(plan.gps_lat, plan.gps_lng)
                avoid_search = plan.search_term
        return 0

    except DataError as e:
        print(f"DATA ERROR: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
