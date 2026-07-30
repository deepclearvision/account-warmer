#!/usr/bin/env python3
"""
run_batch.py — Batch orchestrator with rate-limit instrumentation.

Features:
  - Sequential dispatch, parallel polling (jittered starts)
  - API call counter with per-minute rate reporting
  - 47002 / 40007 detection and exponential backoff
  - Polling backoff (10s → 30s after 3 polls)
  - Per-phone 5-condition evidence
  - Aggregate batch report

Usage:
    python run_batch.py batch_1.csv --log-dir ../logs
"""
from __future__ import annotations

import argparse
import random
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Explicit absolute repo root — avoids __file__ resolution failures when launched
# from working directories other than the project root (e.g. Geelark tasks, cron).
_repo_root = Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced")
if not (_repo_root / "geelark_orchestrator" / "src").exists():
    # Fallback for local development / unexpected locations
    _repo_root = Path(__file__).resolve().parent.parent.parent

_orchestrator_root = _repo_root / "geelark_orchestrator"
_orchestrator_src = _orchestrator_root / "src"
_account_warmer = _repo_root / "account-warmer"

sys.path.insert(0, str(_orchestrator_src))
sys.path.insert(0, str(_orchestrator_root))
sys.path.insert(0, str(_account_warmer))

# --- Instrument _post BEFORE any other imports that use it ---
from core import geelark_client as _gc

_original_post = _gc._post

_api_calls: deque[float] = deque()
_total_api_calls = 0
_47002_hits = 0
_40007_hits = 0


def _instrumented_post(path: str, payload: dict | None = None) -> dict:
    """Wrap _post to count calls and detect rate-limit errors."""
    global _total_api_calls, _47002_hits, _40007_hits
    t0 = time.time()
    try:
        result = _original_post(path, payload)
    except RuntimeError as e:
        msg = str(e)
        if "47002" in msg:
            _47002_hits += 1
            print(f"  [WARN] 47002 CONCURRENT LOCKOUT DETECTED: {msg}", file=sys.stderr)
        elif "40007" in msg:
            _40007_hits += 1
            print(f"  [WARN] 40007 RATE LIMIT DETECTED: {msg}", file=sys.stderr)
        raise
    _api_calls.append(t0)
    _total_api_calls += 1
    return result


_gc._post = _instrumented_post

# Now safe to import modules that use _post
from data_adapter import load_real_profiles_csv, DataError
from run_one_phone import run_phone


@dataclass
class BatchResult:
    profile_key: str
    phone_id: str
    status: str
    duration_seconds: float | None = None
    failed_step: str | None = None
    gps_verified: int = 0
    gps_dumpsys_coords: str | None = None
    search_submitted: bool = False
    business_found: bool = False
    interactions_present: list[str] = field(default_factory=list)
    screenshot_path: str | None = None
    run_id: str = ""


def _call_rate_last_minute() -> int:
    """Return number of API calls in the last 60 seconds."""
    cutoff = time.time() - 60
    while _api_calls and _api_calls[0] < cutoff:
        _api_calls.popleft()
    return len(_api_calls)


def _print_call_rate(prefix: str = "") -> None:
    rate = _call_rate_last_minute()
    print(f"{prefix}API call rate (last 60s): {rate}/min  |  47002 hits: {_47002_hits}  |  40007 hits: {_40007_hits}")


def run_batch(csv_path: str, seed: int | None = None, dry_run: bool = False,
              log_dir: str = str(_repo_root / "geelark_orchestrator" / "logs"),
              db_path: str = str(_repo_root / "geelark_orchestrator" / "runs.sqlite"),
              schema_path: str = str(_repo_root / "geelark_orchestrator" / "schema.sql")) -> list[BatchResult]:

    # 1. Load profiles
    try:
        profiles = list(load_real_profiles_csv(csv_path))
    except DataError as e:
        print(f"DATA ERROR: {e}", file=sys.stderr)
        sys.exit(2)

    if not profiles:
        print("No profiles found in CSV.", file=sys.stderr)
        sys.exit(2)

    # Filter to provisioned + maps_verified only
    eligible = [p for p in profiles if getattr(p, "provisioned", False) and getattr(p, "maps_verified", False)]
    if not eligible:
        print("No eligible profiles (provisioned=true + maps_verified=true).", file=sys.stderr)
        sys.exit(2)

    # 2. Shuffle order (anti-detection)
    rng = random.Random(seed)
    rng.shuffle(eligible)

    print(f"\n=== BATCH START ===")
    print(f"Total profiles: {len(profiles)}  |  Eligible: {len(eligible)}")
    print(f"Dispatch order: {[p.profile_key for p in eligible]}")
    print(f"Log dir: {log_dir}")
    print(f"DB path: {db_path}")
    _print_call_rate()
    print()

    results: list[BatchResult] = []
    batch_start = time.time()

    for idx, profile in enumerate(eligible, start=1):
        # Jitter: 0-120s base + 30-60s inter-phone spacing
        jitter = rng.randint(0, 120)
        spacing = rng.randint(30, 60) if idx > 1 else 0
        delay = jitter + spacing

        print(f"\n--- Batch phone {idx}/{len(eligible)}: {profile.profile_key} ---")
        print(f"  Start delay: {delay}s (jitter={jitter}s, spacing={spacing}s)")
        if delay > 0:
            print(f"  Waiting {delay}s before dispatch...")
            time.sleep(delay)

        phone_start = time.time()
        try:
            run_result = run_phone(
                profile=profile,
                csv_path=csv_path,
                seed=seed,
                dry_run=dry_run,
                log_dir=log_dir,
                db_path=db_path,
                schema_path=schema_path,
            )
        except Exception as e:
            print(f"  UNEXPECTED ERROR: {e}", file=sys.stderr)
            run_result = {
                "status": "failed_infra",
                "failed_step": f"unexpected_exception: {e}",
                "run_id": "",
                "duration_seconds": None,
                "gps_verified": 0,
                "gps_dumpsys_coords": None,
                "search_submitted": False,
                "business_found": False,
                "interactions_present": [],
                "screenshot_path": None,
            }

        phone_duration = time.time() - phone_start
        result = BatchResult(
            profile_key=profile.profile_key,
            phone_id=profile.geelark_profile_id,
            status=run_result.get("status", "unknown"),
            duration_seconds=run_result.get("duration_seconds") or phone_duration,
            failed_step=run_result.get("failed_step"),
            gps_verified=run_result.get("gps_verified", 0),
            gps_dumpsys_coords=run_result.get("gps_dumpsys_coords"),
            search_submitted=run_result.get("search_submitted", False),
            business_found=run_result.get("business_found", False),
            interactions_present=run_result.get("interactions_present", []),
            screenshot_path=run_result.get("screenshot_path"),
            run_id=run_result.get("run_id", ""),
        )
        results.append(result)

        _print_call_rate(prefix="  ")
        print(f"  Phone {profile.profile_key} finished: status={result.status}  duration={result.duration_seconds:.1f}s")

    batch_duration = time.time() - batch_start

    # 3. Aggregate report
    print("\n" + "=" * 60)
    print("BATCH REPORT")
    print("=" * 60)
    print(f"Batch size: {len(results)}")
    print(f"Total batch duration: {batch_duration:.1f}s")
    print(f"Total API calls (instrumented): {_total_api_calls}")
    print(f"47002 hits: {_47002_hits}  |  40007 hits: {_40007_hits}")
    print()

    success_count = sum(1 for r in results if r.status == "success")
    print(f"Success: {success_count}/{len(results)}")
    for r in results:
        marker = "[OK]" if r.status == "success" else "[FAIL]"
        interactions = ",".join(r.interactions_present) if r.interactions_present else "none"
        print(f"  {marker} {r.profile_key} ({r.phone_id})")
        print(f"      status={r.status} | duration={r.duration_seconds:.1f}s | failed_step={r.failed_step or 'n/a'}")
        print(f"      GPS={r.gps_dumpsys_coords} | search={r.search_submitted} | business={r.business_found} | interactions=[{interactions}]")
        if r.screenshot_path:
            print(f"      screenshot={r.screenshot_path}")
    print("=" * 60)

    return results


def main() -> int:
    ap = argparse.ArgumentParser(description="Run a batch of phones with rate-limit instrumentation.")
    ap.add_argument("csv_path", help="Path to CSV with profile data")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--log-dir", default=str(_repo_root / "geelark_orchestrator" / "logs"))
    ap.add_argument("--db-path", default=str(_repo_root / "geelark_orchestrator" / "runs.sqlite"))
    ap.add_argument("--schema-path", default=str(_repo_root / "geelark_orchestrator" / "schema.sql"))
    args = ap.parse_args()

    results = run_batch(
        csv_path=args.csv_path,
        seed=args.seed,
        dry_run=args.dry_run,
        log_dir=args.log_dir,
        db_path=args.db_path,
        schema_path=args.schema_path,
    )
    success_count = sum(1 for r in results if r.status == "success")
    return 0 if success_count == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
