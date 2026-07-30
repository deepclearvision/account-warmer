#!/usr/bin/env python3
"""
dry_run_real.py — Dry-run the resolver against the REAL production CSV.

Usage:
    python3 scripts/dry_run_real.py <real_csv> --all
    python3 scripts/dry_run_real.py <real_csv> acc_049 --seed 42
    python3 scripts/dry_run_real.py <real_csv> acc_049 --runs 5

Guardrail #4: run --all FIRST, before any phone, so data problems surface up front.
"""
import argparse
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root / "src"))
sys.path.insert(0, str(_root))

from data_adapter import load_real_profiles_csv, load_real_profile_by_key, DataError
from data_layer import Coordinate
from resolver import resolve
from run_logger import log_plan
from flow_adapter import print_adapter_audit
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
    if not plan.abort:
        print_adapter_audit(plan, profile)
    if log_dir:
        log_plan(plan, log_dir=log_dir, extra={"phase": "dry_run_real"})
    return plan


def main() -> int:
    ap = argparse.ArgumentParser(description="Dry-run resolver on real CSV (no phone).")
    ap.add_argument("csv_path", help="Path to geelark_flow_data_2026-05-13.csv")
    ap.add_argument("profile_key", nargs="?", help="e.g. acc_049; omit with --all")
    ap.add_argument("--all", action="store_true", help="dry-run every profile")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--runs", type=int, default=1, help="N consecutive runs for one phone")
    ap.add_argument("--log", metavar="DIR", default=None,
                      help="write logs/runs.log + runs.jsonl to DIR")
    args = ap.parse_args()

    try:
        if args.all:
            profiles = load_real_profiles_csv(args.csv_path)
            print(f"Loaded and validated {len(profiles)} profile(s) from real CSV.\n")
            for p in profiles:
                show_profile_warnings(p)
                dry_run_one(p, seed=args.seed, log_dir=args.log)
            return 0

        if not args.profile_key:
            ap.error("provide a profile_key, or use --all")

        profile = load_real_profile_by_key(args.csv_path, args.profile_key)
        show_profile_warnings(profile)

        avoid_gps = None
        avoid_search = None
        for i in range(args.runs):
            if args.runs > 1:
                print(f"===== run {i + 1} of {args.runs} =====")
            seed = args.seed if (args.seed is not None and args.runs == 1) else None
            plan = dry_run_one(
                profile, seed=seed, avoid_gps=avoid_gps, avoid_search=avoid_search,
                log_dir=args.log
            )
            if not plan.abort:
                avoid_gps = Coordinate(plan.gps_lat, plan.gps_lng)
                avoid_search = plan.search_term
        return 0

    except DataError as e:
        print(f"DATA ERROR: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
