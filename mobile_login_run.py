"""
mobile_login_run.py — Run Google login on GeelarK phones.

Usage:
  python mobile_login_run.py --account acc_008
  python mobile_login_run.py --accounts acc_008 acc_012 acc_013
  python mobile_login_run.py --all          # all with login_done != True
"""

import argparse
import logging
import sys
import time
import yaml
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

_env = Path(__file__).parent / "warmer.env"
if _env.exists():
    import os
    for _l in _env.read_text(encoding="utf-8").splitlines():
        _l = _l.strip()
        if _l and not _l.startswith("#") and "=" in _l:
            k, _, v = _l.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

from core.paths import DATA_DIR

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("mobile_login_run")

GEELARK_FILE = DATA_DIR / "geelark_accounts.yaml"


def _load_accounts() -> list:
    if not GEELARK_FILE.exists():
        log.error("geelark_accounts.yaml not found at %s", GEELARK_FILE)
        sys.exit(1)
    data = yaml.safe_load(GEELARK_FILE.read_text(encoding="utf-8")) or {}
    return data.get("accounts", [])


def _save_accounts(accounts: list) -> None:
    GEELARK_FILE.write_text(
        yaml.dump({"accounts": accounts}, default_flow_style=False,
                  allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def login_for(account: dict) -> bool:
    from activities.google_login_mobile import run_google_login

    acc_id = account.get("id", "unknown")
    if not account.get("geelark_phone_id"):
        log.warning("[%s] No phone_id — skipping.", acc_id)
        return False

    log.info("[%s] Starting mobile login …", acc_id)
    result = run_google_login(account, stop_phone_on_success=True)

    if result.get("success"):
        log.info("[%s] Login SUCCESS.", acc_id)
        accounts = _load_accounts()
        for acc in accounts:
            if acc["id"] == acc_id:
                acc["login_done"] = True
                break
        _save_accounts(accounts)
        log.info("[%s] Marked login_done = true", acc_id)
        return True
    else:
        log.warning("[%s] Login FAILED — %s", acc_id,
                    result.get("error") or result.get("diagnosis", "unknown"))
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Google login on GeelarK phones")
    group  = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--account",  metavar="ID")
    group.add_argument("--accounts", metavar="ID", nargs="+")
    group.add_argument("--all", action="store_true",
                       help="All accounts where login_done is not True")
    args = parser.parse_args()

    accounts = _load_accounts()

    if args.account:
        targets = [a for a in accounts if a["id"] == args.account]
    elif args.accounts:
        targets = [a for a in accounts if a["id"] in args.accounts]
    else:
        targets = [a for a in accounts
                   if a.get("geelark_phone_id") and not a.get("login_done")]

    if not targets:
        log.info("No matching accounts found.")
        return

    log.info("Running login for %d account(s): %s",
             len(targets), [a["id"] for a in targets])

    ok = failed = 0
    for acc in targets:
        success = login_for(acc)
        if success:
            ok += 1
        else:
            failed += 1
        time.sleep(3)

    log.info("Done. Succeeded: %d  Failed: %d", ok, failed)


if __name__ == "__main__":
    main()
