"""
account_setup_run.py — One-Time Mobile Account Setup Runner

Runs the post-login account setup on GeelarK cloud phones.
Skips accounts where mobile_setup_done is already true.

Usage:
  python account_setup_run.py --account gl_001
  python account_setup_run.py --all
"""

import argparse
import logging
import sys
import yaml
from pathlib import Path

# ── Path setup ────────────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))

from core.paths import DATA_DIR

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("account_setup_run")

GEELARK_ACCOUNTS_FILE = DATA_DIR / "geelark_accounts.yaml"


def _load_accounts() -> list:
    if not GEELARK_ACCOUNTS_FILE.exists():
        log.error("geelark_accounts.yaml not found at %s", GEELARK_ACCOUNTS_FILE)
        sys.exit(1)
    data = yaml.safe_load(GEELARK_ACCOUNTS_FILE.read_text(encoding="utf-8")) or {}
    return data.get("accounts", [])


def _save_accounts(accounts: list) -> None:
    GEELARK_ACCOUNTS_FILE.write_text(
        yaml.dump({"accounts": accounts}, default_flow_style=False,
                  allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def run_setup_for(account: dict) -> bool:
    """Run setup for one account and persist the done flag on success."""
    from activities.mobile_account_setup import run_account_setup

    acc_id = account["id"]
    result = run_account_setup(account)

    if result["success"]:
        log.info("[%s] Setup succeeded. Steps: %s", acc_id, result["steps_completed"])
        # Persist mobile_setup_done = true
        accounts = _load_accounts()
        for acc in accounts:
            if acc["id"] == acc_id:
                acc["mobile_setup_done"] = True
                break
        _save_accounts(accounts)
        log.info("[%s] Marked mobile_setup_done = true", acc_id)
        return True
    else:
        log.warning(
            "[%s] Setup finished with issues. Steps done: %s | Error: %s",
            acc_id, result["steps_completed"], result["error"],
        )
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one-time mobile account setup")
    group  = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--account", metavar="ID",
                       help="Run setup for a single account (e.g. gl_001)")
    group.add_argument("--all", action="store_true",
                       help="Run setup for all accounts not yet done")
    args = parser.parse_args()

    accounts = _load_accounts()

    if args.account:
        acc = next((a for a in accounts if a["id"] == args.account), None)
        if not acc:
            log.error("Account %r not found in geelark_accounts.yaml", args.account)
            sys.exit(1)
        run_setup_for(acc)
    else:
        pending = [a for a in accounts if not a.get("mobile_setup_done")]
        if not pending:
            log.info("All accounts already have mobile_setup_done = true — nothing to do.")
            return
        log.info("Running setup for %d account(s): %s",
                 len(pending), [a["id"] for a in pending])
        ok = failed = 0
        for acc in pending:
            success = run_setup_for(acc)
            if success:
                ok += 1
            else:
                failed += 1
        log.info("Done. Succeeded: %d  Failed/partial: %d", ok, failed)


if __name__ == "__main__":
    main()
