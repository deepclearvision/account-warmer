"""
Run mobile account setup for one or more GeelarK accounts.

Usage:
    python mobile_setup_run.py --account acc_009       # single account
    python mobile_setup_run.py --all                   # all Android 14 accounts
    python mobile_setup_run.py --android14             # only Android 14 accounts
    python mobile_setup_run.py --dry-run               # preview only
"""
import argparse
import os
import sys
import time
import yaml
from pathlib import Path

_here = Path(__file__).parent
sys.path.insert(0, str(_here))

_env_file = _here / "warmer.env"
if _env_file.exists():
    for line in _env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

ACCOUNTS_FILE = Path(os.environ.get("WARMER_DATA_DIR", "C:/WarmingData")) / "geelark_accounts.yaml"

parser = argparse.ArgumentParser(description="Run mobile account setup")
parser.add_argument("--account", metavar="ID", help="Single account ID to set up")
parser.add_argument("--all", action="store_true", help="Set up all accounts not yet set up")
parser.add_argument("--android14", action="store_true", help="Set up only Android 14 accounts")
parser.add_argument("--dry-run", action="store_true", help="Preview, no changes")
args = parser.parse_args()


def load_accounts():
    if not ACCOUNTS_FILE.exists():
        print(f"ERROR: {ACCOUNTS_FILE} not found")
        sys.exit(1)
    data = yaml.safe_load(ACCOUNTS_FILE.read_text(encoding="utf-8")) or {}
    return data.get("accounts", [])


def save_accounts(accounts):
    ACCOUNTS_FILE.write_text(
        yaml.dump({"accounts": accounts}, default_flow_style=False,
                  allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def main():
    all_accounts = load_accounts()

    # Filter candidates
    candidates = []
    for a in all_accounts:
        pid = a.get("geelark_phone_id", "")
        if not pid:
            continue
        if a.get("mobile_setup_done"):
            continue
        if args.account:
            if a.get("id") == args.account:
                candidates.append(a)
                break
        elif args.android14:
            if (a.get("os_version", "")).startswith("Android 14"):
                candidates.append(a)
        elif args.all:
            candidates.append(a)

    if args.account and not candidates:
        print(f"ERROR: Account {args.account} not found or already set up")
        sys.exit(1)

    if not candidates:
        print("No accounts need setup. All done!")
        return

    candidates.sort(key=lambda a: a.get("id", ""))

    print(f"Setting up {len(candidates)} account(s):")
    for a in candidates:
        print(f"  {a['id']:15} {a.get('email', '?'):40} "
              f"phone={a.get('geelark_phone_id', '?')}  "
              f"os={a.get('os_version', '?')}")
    print()

    if args.dry_run:
        print("[DRY RUN] - no changes.\n")
        return

    from activities.mobile_account_setup import run_account_setup

    ok = 0
    failed = 0

    for account in candidates:
        acc_id = account.get("id", "?")
        print(f"\n{'=' * 60}")
        print(f"  Setup: {acc_id}  {account.get('email', '?')}")
        print(f"{'=' * 60}")

        try:
            result = run_account_setup(account)
        except Exception as e:
            print(f"  SETUP ERROR: {e}")
            result = {"success": False, "error": str(e), "steps_completed": []}

        if result.get("success"):
            ok += 1
            print(f"  RESULT: OK  ({result.get('steps_completed', [])})")
            # Persist mobile_setup_done
            accounts = load_accounts()
            for a in accounts:
                if a.get("id") == acc_id:
                    a["mobile_setup_done"] = True
                    break
            save_accounts(accounts)
        else:
            failed += 1
            print(f"  RESULT: FAILED  ({result.get('error', 'unknown')})")

        if len(candidates) > 1:
            print("  Cooldown 5s...")
            time.sleep(5)

    print(f"\n{'=' * 60}")
    print(f"SUMMARY: {ok} OK, {failed} failed, {len(candidates)} total")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
