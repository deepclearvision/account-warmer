"""
Unified Account Warmer

Assign warming strategies to accounts and run sessions.

Usage:
  python warmer.py --list                        List all accounts, their strategy and current week
  python warmer.py --strategies                  Show available strategies with descriptions
  python warmer.py --assign acc_001 maps_heavy   Assign a strategy to an account
  python warmer.py --run                         Run all accounts
  python warmer.py --run acc_001                 Run a specific account
  python warmer.py --run --dry-run               Show what would run without opening a browser
  python warmer.py --run acc_001 --week 2        Force week number (0-based, for testing)
  python warmer.py --run acc_001 --activity search  Force a specific activity only

Strategies:
  standard    Progressive warmup over weeks (search -> email -> maps -> calendar -> drive)
  maps_heavy  Maps and Local Guide focused throughout all phases
  light       Low intensity — search and browse only, fewer sessions
  pin_prep    Maximum maps focus for accounts being groomed to add a Google Maps pin
"""

import asyncio
import argparse
import os
import random
import sys
from datetime import date
from pathlib import Path
import yaml

from core.orchestrator import AccountOrchestrator
from core.logger import get_logger

from core.paths import ACCOUNTS_FILE as CONFIG_FILE, STRATEGIES_FILE
log = get_logger("warmer")

VALID_STRATEGIES = ["standard", "maps_heavy", "light", "pin_prep"]

STRATEGY_DESCRIPTIONS = {
    "standard":   "Progressive warmup over weeks (search -> email -> maps -> calendar -> drive). Default for all accounts.",
    "maps_heavy": "Maps and Local Guide focused. Heavy maps_browse + maps_review weighting from week 1.",
    "light":      "Low intensity. Search and browse only, fewer actions per day. Good holding strategy.",
    "pin_prep":   "Maximum maps focus (maps_browse + maps_review) for accounts being groomed to add a Google Maps pin.",
}

ACTIVITY_CHOICES = [
    "search", "email_read", "email_send", "browse", "newsletter_signup",
    "maps_browse", "maps_review", "calendar_setup", "calendar_browse",
    "calendar_add", "drive_setup", "drive_browse", "drive_edit",
    "drive_create", "business_signal",
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def load_accounts(pc_filter: str = "") -> list[dict]:
    if not CONFIG_FILE.exists():
        print(f"\n  No accounts file found at {CONFIG_FILE}\n")
        sys.exit(1)
    with open(CONFIG_FILE, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    accounts = data.get("accounts", [])
    if pc_filter:
        accounts = [a for a in accounts if a.get("category", "") == pc_filter]
        log.info(f"PC filter '{pc_filter}': {len(accounts)} account(s) selected")
    return accounts


def save_accounts(accounts: list[dict]) -> None:
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        yaml.dump(
            {"accounts": accounts},
            f,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        )


def _week_number(account: dict) -> int:
    start_str = account.get("warmup_start_date", str(date.today()))
    try:
        start = date.fromisoformat(start_str)
        return max(1, (date.today() - start).days // 7 + 1)
    except Exception:
        return 1


# ── Commands ──────────────────────────────────────────────────────────────────

def cmd_list() -> None:
    accounts = load_accounts()
    if not accounts:
        print("\n  No accounts configured.\n")
        return

    print()
    print(f"  {'ID':<12} {'Email':<35} {'Location':<22} {'Wk':<4} {'Strategy'}")
    print(f"  {'-'*12} {'-'*35} {'-'*22} {'-'*4} {'-'*12}")
    for a in accounts:
        week     = _week_number(a)
        strategy = a.get("strategy", "standard")
        print(
            f"  {a['id']:<12} {a.get('email',''):<35} "
            f"{a.get('location',''):<22} {week:<4} {strategy}"
        )
    print()


def cmd_strategies() -> None:
    # Load phase labels from strategies.yaml if available
    phase_labels = {}
    if STRATEGIES_FILE.exists():
        with open(STRATEGIES_FILE, encoding="utf-8") as f:
            all_strats = yaml.safe_load(f) or {}
        for name in VALID_STRATEGIES:
            strat = all_strats.get(name, {})
            labels = [
                strat.get(band, {}).get("label", band)
                for band in ["weeks_1_2", "weeks_3_4", "weeks_5_6", "weeks_7_plus"]
            ]
            phase_labels[name] = labels

    print()
    print("  Available warming strategies")
    print("  " + "-" * 62)
    for name, desc in STRATEGY_DESCRIPTIONS.items():
        print(f"\n  {name}")
        print(f"    {desc}")
        if name in phase_labels:
            print(f"    Phases: {' -> '.join(phase_labels[name])}")
    print()


def cmd_assign(account_id: str, strategy: str) -> None:
    if strategy not in VALID_STRATEGIES:
        print(f"\n  Unknown strategy: {strategy!r}")
        print(f"  Valid options: {', '.join(VALID_STRATEGIES)}\n")
        sys.exit(1)

    # Reload fresh from disk to avoid clobbering concurrent edits
    accounts = load_accounts()
    target = next((a for a in accounts if a["id"] == account_id), None)
    if target is None:
        print(f"\n  Account not found: {account_id!r}")
        print(f"  Run --list to see available accounts.\n")
        sys.exit(1)

    old = target.get("strategy", "standard")
    target["strategy"] = strategy
    save_accounts(accounts)
    print(f"\n  {account_id}: {old} -> {strategy}\n")


# ── Run ───────────────────────────────────────────────────────────────────────

async def _run_account(account: dict, all_accounts: list, args) -> None:
    orchestrator = AccountOrchestrator(
        account        = account,
        all_accounts   = all_accounts,
        dry_run        = args.dry_run,
        force_week     = args.week,
        force_activity = args.activity,
    )
    await orchestrator.run_session()


async def cmd_run(args) -> None:
    pc_filter = args.pc or os.environ.get("PC_ID", "").strip()
    accounts  = load_accounts(pc_filter)
    if not accounts:
        msg = "No accounts found in config/accounts.yaml"
        if pc_filter:
            msg += f" matching PC_ID='{pc_filter}' (check the 'category' field)"
        log.error(msg)
        sys.exit(1)

    account_id = args.run if (args.run and args.run is not True) else None

    if account_id:
        target = next((a for a in accounts if a["id"] == account_id), None)
        if not target:
            log.error(f"Account {account_id!r} not found in config")
            sys.exit(1)
        await _run_account(target, accounts, args)
    else:
        for i, account in enumerate(accounts):
            strategy = account.get("strategy", "standard")
            log.info(f"--- Starting account: {account['id']} | strategy: {strategy} ---")
            try:
                await _run_account(account, accounts, args)
            except Exception as e:
                log.error(f"Account {account['id']} failed: {e}")
            if i < len(accounts) - 1:
                await asyncio.sleep(random.uniform(15, 60))


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Unified Account Warmer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Strategies: " + ", ".join(VALID_STRATEGIES),
    )

    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--list", action="store_true",
        help="List all accounts with their strategy and current week",
    )
    group.add_argument(
        "--strategies", action="store_true",
        help="Show available strategies with descriptions",
    )
    group.add_argument(
        "--assign", nargs=2, metavar=("ACCOUNT_ID", "STRATEGY"),
        help="Assign a strategy to an account",
    )
    group.add_argument(
        "--run", nargs="?", const=True, metavar="ACCOUNT_ID",
        help="Run all accounts, or a specific account by ID",
    )

    parser.add_argument(
        "--dry-run", action="store_true",
        help="Log what would run without opening a browser",
    )
    parser.add_argument(
        "--week", type=int,
        help="Force week number (0-based) for testing",
    )
    parser.add_argument(
        "--pc", type=str,
        help="Only run accounts whose category matches this PC ID (overrides PC_ID env var)",
    )
    parser.add_argument(
        "--activity", type=str, choices=ACTIVITY_CHOICES,
        help="Force a specific activity only",
    )

    args = parser.parse_args()

    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if args.list:
        cmd_list()
    elif args.strategies:
        cmd_strategies()
    elif args.assign:
        cmd_assign(args.assign[0], args.assign[1])
    elif args.run is not None:
        asyncio.run(cmd_run(args))
    else:
        parser.print_help()
        sys.exit(0)
