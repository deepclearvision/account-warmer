"""
install_apps_run.py — Re-run app installation only on GeelarK phones.

Skips accounts that:
  - have no phone_id
  - are not logged in (login_done != True) — Play Store shows "Sign in", wastes phone minutes
  - already have apps_installed = True

Usage:
  python install_apps_run.py --account gl_001
  python install_apps_run.py --all
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
log = logging.getLogger("install_apps_run")

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


def install_for(account: dict) -> bool:
    from core.geelark_client import GeelarKClient
    from activities.mobile_account_setup import _install_required_apps
    from activities.mobile_warmup import _wait_for_phone_ready

    acc_id   = account.get("id", "unknown")
    phone_id = account.get("geelark_phone_id")

    if not phone_id:
        log.warning("[%s] No phone_id — skipping.", acc_id)
        return False

    if account.get("apps_installed"):
        log.info("[%s] apps_installed=true — skipping.", acc_id)
        return True

    if not account.get("login_done"):
        log.warning("[%s] Not logged in (login_done != true) — skipping to avoid wasting phone minutes.", acc_id)
        return False

    client = GeelarKClient()

    log.info("[%s] Starting phone …", acc_id)
    try:
        client.start_phone(phone_id)
    except Exception as e:
        log.error("[%s] Failed to start phone: %s", acc_id, e)
        return False

    log.info("[%s] Waiting for boot …", acc_id)
    if not _wait_for_phone_ready(phone_id, acc_id, timeout=90):
        log.warning("[%s] Phone slow to boot — proceeding anyway.", acc_id)

    ok = _install_required_apps(phone_id, acc_id)

    try:
        client.stop_phone(phone_id)
        log.info("[%s] Phone stopped.", acc_id)
    except Exception as e:
        log.warning("[%s] Failed to stop phone: %s", acc_id, e)

    if ok:
        accounts = _load_accounts()
        for acc in accounts:
            if acc["id"] == acc_id:
                acc["apps_installed"] = True
                break
        _save_accounts(accounts)
        log.info("[%s] Marked apps_installed=true", acc_id)

    log.info("[%s] Install result: %s", acc_id, "OK" if ok else "FAILED")
    return ok


def main() -> None:
    parser = argparse.ArgumentParser(description="Install apps on GeelarK phones")
    group  = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--account", metavar="ID")
    group.add_argument("--all", action="store_true")
    args = parser.parse_args()

    accounts = _load_accounts()

    if args.account:
        acc = next((a for a in accounts if a["id"] == args.account), None)
        if not acc:
            log.error("Account %r not found.", args.account)
            sys.exit(1)
        install_for(acc)
    else:
        targets = [a for a in accounts if a.get("geelark_phone_id")]
        skipped_done    = sum(1 for a in targets if a.get("apps_installed"))
        skipped_no_login = sum(1 for a in targets if not a.get("login_done") and not a.get("apps_installed"))
        to_run = [a for a in targets if not a.get("apps_installed") and a.get("login_done")]
        log.info("Skipping %d already done, %d not logged in. Running %d account(s): %s",
                 skipped_done, skipped_no_login, len(to_run), [a["id"] for a in to_run])
        if not to_run:
            log.info("Nothing to do.")
            return
        ok = failed = 0
        for acc in to_run:
            success = install_for(acc)
            if success:
                ok += 1
            else:
                failed += 1
            time.sleep(2)
        log.info("Done. OK: %d  Failed: %d", ok, failed)


if __name__ == "__main__":
    main()
