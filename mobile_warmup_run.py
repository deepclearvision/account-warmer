"""
mobile_warmup_run.py — Daily Mobile Warm-Up Runner

Runs 2–5 minute warm-up sessions on GeelarK cloud phones.
Sessions are strictly sequential (one phone at a time) because all phones
share a single StreamVia mobile proxy.

Usage:
  python mobile_warmup_run.py               # Run all enabled accounts
  python mobile_warmup_run.py --account gl_001   # Single account
  python mobile_warmup_run.py --status      # Show last session for each account
"""

import argparse
import json
import logging
import os
import sys
import yaml
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from core.paths import DATA_DIR

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("mobile_warmup_run")

GEELARK_ACCOUNTS_FILE = DATA_DIR / "geelark_accounts.yaml"
SESSION_LOG_FILE      = DATA_DIR / "logs" / "mobile_sessions.json"


def _load_accounts(pc_filter: str = "") -> list:
    if not GEELARK_ACCOUNTS_FILE.exists():
        log.error("geelark_accounts.yaml not found at %s", GEELARK_ACCOUNTS_FILE)
        sys.exit(1)
    data = yaml.safe_load(GEELARK_ACCOUNTS_FILE.read_text(encoding="utf-8")) or {}
    accounts = data.get("accounts", [])
    if pc_filter:
        accounts = [a for a in accounts if a.get("category", "") == pc_filter]
        log.info("PC filter %r: %d account(s) selected", pc_filter, len(accounts))
    return accounts


def _show_status() -> None:
    """Print the last session for each account from mobile_sessions.json."""
    if not SESSION_LOG_FILE.exists():
        print("No session log found — no sessions have run yet.")
        return
    try:
        records = json.loads(SESSION_LOG_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"Failed to read session log: {e}")
        return

    # Group by account_id — keep last entry per account
    last: dict = {}
    for r in records:
        last[r["account_id"]] = r

    print(f"\n{'Account':<12} {'Timestamp':<28} {'IP':<16} {'Duration':>9}  Steps")
    print("-" * 80)
    for acc_id, r in sorted(last.items()):
        ts   = r.get("timestamp", "")[:19].replace("T", " ")
        ip   = r.get("ip", "—")
        dur  = f"{r.get('duration_s', 0):.0f}s"
        steps = ", ".join(r.get("steps_done", []))
        print(f"{acc_id:<12} {ts:<28} {ip:<16} {dur:>9}  {steps}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run mobile warm-up sessions")
    group  = parser.add_mutually_exclusive_group()
    group.add_argument("--account", metavar="ID",
                       help="Run a single account (e.g. gl_001)")
    group.add_argument("--status", action="store_true",
                       help="Show last session per account and exit")
    parser.add_argument("--pc", type=str,
                        help="Only run accounts whose category matches this PC ID (overrides PC_ID env var)")
    args = parser.parse_args()

    if args.status:
        _show_status()
        return

    from activities.mobile_warmup import run_mobile_schedule_session, run_all_warmup_sessions

    pc_filter = args.pc or os.environ.get("PC_ID", "").strip()
    accounts  = _load_accounts(pc_filter)

    if args.account:
        acc = next((a for a in accounts if a["id"] == args.account), None)
        if not acc:
            log.error("Account %r not found in geelark_accounts.yaml", args.account)
            sys.exit(1)
        result = run_mobile_schedule_session(acc, SESSION_LOG_FILE, schedule_state=None)
        status = "OK" if result["success"] else "FAILED"
        log.info("Result: %s | Steps: %s | Duration: %.0fs | IP: %s",
                 status, result["steps_done"], result["duration_s"], result["ip"])
    else:
        results = run_all_warmup_sessions(accounts, SESSION_LOG_FILE)
        ok      = sum(1 for r in results if r["success"])
        log.info("Done. %d/%d accounts completed successfully.", ok, len(results))


if __name__ == "__main__":
    main()
