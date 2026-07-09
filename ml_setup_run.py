"""
ml_setup_run.py — One-time Multilogin Account Setup Runner

Runs the post-login account setup via the Multilogin desktop browser profile.
Skips accounts where ml_setup_done is already true.

Usage:
  python ml_setup_run.py --account acc_001
  python ml_setup_run.py --accounts acc_001 acc_002 acc_005
  python ml_setup_run.py --all
  python ml_setup_run.py --all --concurrency 3
"""

import argparse
import asyncio
import logging
import random
import sys
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

from core.paths import DATA_DIR, APP_DIR

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("ml_setup_run")

_accounts_in_data   = DATA_DIR / "accounts.yaml"
_accounts_in_config = APP_DIR / "config" / "accounts.yaml"
ACCOUNTS_FILE = _accounts_in_data if _accounts_in_data.exists() else _accounts_in_config
PHOTOS_DIR    = APP_DIR / "data" / "profile_photos"
DEFAULT_CONCURRENCY = 2


def _assign_photos(accounts: list) -> list:
    """
    Assign an unused profile photo to every account that doesn't have one.
    Returns the updated list. Caller is responsible for saving.
    """
    all_photos = sorted(p.name for p in PHOTOS_DIR.glob("face_*.jpg"))
    if not all_photos:
        log.warning("No photos found in %s — run download_profile_photos.py first", PHOTOS_DIR)
        return accounts

    used = {a.get("profile_photo") for a in accounts if a.get("profile_photo")}
    available = [p for p in all_photos if p not in used]

    for acc in accounts:
        if acc.get("profile_photo"):
            continue
        if not available:
            log.warning("Ran out of photos — %d account(s) have no photo assigned",
                        sum(1 for a in accounts if not a.get("profile_photo")))
            break
        photo = available.pop(random.randrange(len(available)))
        acc["profile_photo"] = photo
        log.info("[%s] Assigned profile photo: %s", acc["id"], photo)

    return accounts


def _load_accounts() -> list:
    if not ACCOUNTS_FILE.exists():
        log.error("accounts.yaml not found at %s", ACCOUNTS_FILE)
        sys.exit(1)
    data = yaml.safe_load(ACCOUNTS_FILE.read_text(encoding="utf-8")) or {}
    return data.get("accounts", [])


def _save_accounts(accounts: list) -> None:
    ACCOUNTS_FILE.write_text(
        yaml.dump({"accounts": accounts}, default_flow_style=False,
                  allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


async def run_ml_setup(account: dict, maps_only: bool = False, security_only: bool = False) -> bool:
    """Run the ML setup for one account and return True on success."""
    from core.profile_manager import ProfileSession
    from activities.multilogin_account_setup import MultiloginAccountSetup
    from core.paths import BEHAVIOUR_FILE
    import yaml as _yaml

    acc_id = account["id"]
    profile_id = account.get("multilogin_profile_id", "")
    if not profile_id:
        log.error("[%s] No multilogin_profile_id — skipping", acc_id)
        return False

    behaviour_cfg: dict = {}
    if BEHAVIOUR_FILE.exists():
        behaviour_cfg = _yaml.safe_load(BEHAVIOUR_FILE.read_text(encoding="utf-8")) or {}

    if security_only:
        mode = "security-only"
    elif maps_only:
        mode = "maps-only"
    else:
        mode = "full"
    log.info("[%s] Starting ML profile for setup (mode=%s) ...", acc_id, mode)
    try:
        async with ProfileSession(
            profile_id=profile_id,
            account_id=acc_id,
            account=account,
        ) as page:
            activity = MultiloginAccountSetup(page, account, behaviour_cfg)
            success  = await activity.run(maps_only=maps_only, security_only=security_only)
    except Exception as exc:
        log.error("[%s] Session error: %s", acc_id, exc)
        return False

    if success and not maps_only and not security_only:
        log.info("[%s] ML setup succeeded — marking ml_setup_done=true", acc_id)
        accounts = _load_accounts()
        for a in accounts:
            if a["id"] == acc_id:
                a["ml_setup_done"] = True
                break
        _save_accounts(accounts)
    elif success and maps_only:
        log.info("[%s] Maps address setup succeeded", acc_id)
    elif success and security_only:
        log.info("[%s] Security clear succeeded", acc_id)
    else:
        log.warning("[%s] ML setup finished with errors", acc_id)

    return success


async def run_with_concurrency(pending: list, concurrency: int, maps_only: bool = False, security_only: bool = False) -> tuple[int, int]:
    """Run setup for all pending accounts, at most `concurrency` at a time."""
    semaphore = asyncio.Semaphore(concurrency)
    ok = failed = 0
    lock = asyncio.Lock()

    async def _run_one(acc: dict) -> None:
        nonlocal ok, failed
        async with semaphore:
            success = await run_ml_setup(acc, maps_only=maps_only, security_only=security_only)
            async with lock:
                if success:
                    ok += 1
                else:
                    failed += 1

    await asyncio.gather(*[_run_one(acc) for acc in pending])
    return ok, failed


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one-time ML account setup")
    group  = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--account",  metavar="ID",
                       help="Run setup for one account (e.g. acc_001)")
    group.add_argument("--accounts", metavar="ID", nargs="+",
                       help="Run setup for specific accounts (e.g. acc_001 acc_002 acc_005)")
    group.add_argument("--all",      action="store_true",
                       help="Run setup for all accounts not yet done")
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY,
                        help=f"Max parallel setups (default: {DEFAULT_CONCURRENCY})")
    parser.add_argument("--force", action="store_true",
                        help="Re-run setup even for accounts already marked ml_setup_done=true")
    parser.add_argument("--maps-only", action="store_true",
                        help="Only set Home/Work labeled places in Google Maps (skips all other steps)")
    parser.add_argument("--security-only", action="store_true",
                        help="Only run the security-alerts dismissal step (myaccount.google.com/security)")
    args = parser.parse_args()

    security_only = getattr(args, "security_only", False)
    maps_only     = args.maps_only and not security_only

    accounts = _load_accounts()

    if not maps_only and not security_only:
        accounts = _assign_photos(accounts)
        _save_accounts(accounts)

    if args.account:
        acc = next((a for a in accounts if a["id"] == args.account), None)
        if not acc:
            log.error("Account %r not found in accounts.yaml", args.account)
            sys.exit(1)
        if acc.get("ml_setup_done") and not args.force and not maps_only and not security_only:
            log.info("Account %r already has ml_setup_done=true — use --force to re-run", args.account)
            return
        asyncio.run(run_ml_setup(acc, maps_only=maps_only, security_only=security_only))

    elif args.accounts:
        pending = []
        for acc_id in args.accounts:
            acc = next((a for a in accounts if a["id"] == acc_id), None)
            if not acc:
                log.warning("Account %r not found — skipping", acc_id)
            else:
                pending.append(acc)
        if not pending:
            log.error("No valid accounts found.")
            sys.exit(1)
        log.info("Running ML setup for %d account(s): %s",
                 len(pending), [a["id"] for a in pending])
        ok, failed = asyncio.run(run_with_concurrency(pending, args.concurrency, maps_only=maps_only, security_only=security_only))
        log.info("Done. Succeeded: %d  Failed/partial: %d", ok, failed)

    else:  # --all
        if maps_only:
            pending = [a for a in accounts if a.get("home_address") or a.get("work_address")]
        else:
            pending = [a for a in accounts if not a.get("ml_setup_done") or args.force]
        if not pending:
            log.info("No accounts to process.")
            return
        log.info("Running ML setup for %d account(s) with concurrency=%d: %s",
                 len(pending), args.concurrency, [a["id"] for a in pending])
        ok, failed = asyncio.run(run_with_concurrency(pending, args.concurrency, maps_only=maps_only, security_only=security_only))
        log.info("Done. Succeeded: %d  Failed/partial: %d", ok, failed)


if __name__ == "__main__":
    main()
