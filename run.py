"""
Account Warmer — Entry Point

Usage:
  python run.py --all                          Run all accounts
  python run.py --account acc_001              Run one account
  python run.py --all --dry-run                Show what would run, no browser
  python run.py --account acc_001 --week 3     Override week number for testing
  python run.py --account acc_001 --activity search   Run only searches
  python run.py --all --pc pc_1                Run only accounts assigned to pc_1

PC filtering:
  Set PC_ID=pc_1 in warmer.env  OR  pass --pc pc_1 on the command line.
  Only accounts whose 'category' field matches PC_ID will be run.
  If PC_ID is not set and --pc is not passed, all accounts run (original behaviour).
"""

import asyncio
import argparse
import os
import sys
from pathlib import Path

from core.orchestrator import AccountOrchestrator
from core.logger import get_logger
from core.account_store import get_account_store

log = get_logger("runner")


def load_accounts(pc_filter: str = "") -> list[dict]:
    accounts = get_account_store().get_desktop_accounts()
    if pc_filter:
        filtered = [a for a in accounts if a.get("category", "") == pc_filter]
        log.info(f"PC filter '{pc_filter}': {len(filtered)}/{len(accounts)} accounts selected")
        return filtered
    return accounts


SESSION_TIMEOUT_MINS = 90   # kill any session that runs longer than this

async def run_account(account: dict, all_accounts: list, args) -> None:
    orchestrator = AccountOrchestrator(
        account       = account,
        all_accounts  = all_accounts,
        dry_run       = args.dry_run,
        force_week    = args.week,
        force_activity= args.activity,
    )
    try:
        await asyncio.wait_for(
            orchestrator.run_session(),
            timeout=SESSION_TIMEOUT_MINS * 60,
        )
    except asyncio.TimeoutError:
        log.error(
            f"Session for {account['id']} exceeded {SESSION_TIMEOUT_MINS} min timeout — "
            f"killing process. Browser/Multilogin may be hung."
        )
        sys.exit(1)


async def main(args) -> None:
    pc_filter = args.pc or os.environ.get("PC_ID", "").strip()
    accounts  = load_accounts(pc_filter)
    if not accounts:
        msg = "No accounts found in config/accounts.yaml"
        if pc_filter:
            msg += f" matching PC_ID='{pc_filter}' (check the 'category' field)"
        log.error(msg)
        sys.exit(1)

    if args.account:
        # Single account
        target = next((a for a in accounts if a["id"] == args.account), None)
        if not target:
            log.error(f"Account {args.account!r} not found in config")
            sys.exit(1)
        await run_account(target, accounts, args)

    elif args.all:
        # All accounts — run sequentially to avoid fingerprinting via simultaneous activity
        for account in accounts:
            log.info(f"--- Starting account: {account['id']} ---")
            try:
                await run_account(account, accounts, args)
            except Exception as e:
                log.error(f"Account {account['id']} failed: {e}")
            # Short gap between accounts
            import random, asyncio as _asyncio
            await _asyncio.sleep(random.uniform(15, 60))
    else:
        print(__doc__)
        sys.exit(0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Account Warmer")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--all",      action="store_true", help="Run all accounts")
    group.add_argument("--account",  type=str,            help="Run a specific account by ID")
    parser.add_argument("--dry-run", action="store_true", help="Log actions without opening browser")
    parser.add_argument("--week",    type=int,            help="Override week number (0-based, for testing)")
    parser.add_argument("--pc",      type=str,            help="Only run accounts whose category matches this PC ID (overrides PC_ID env var)")
    parser.add_argument("--activity",type=str,            choices=["search","email_read","email_send","browse","newsletter_signup","maps_browse","calendar_setup","calendar_browse","calendar_add","drive_setup","drive_browse","drive_edit","drive_create","business_signal"],
                        help="Force a specific activity only")
    args = parser.parse_args()
    asyncio.run(main(args))
