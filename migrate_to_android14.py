"""
Migrate GeelarK cloud phones to Android 14 and re-login accounts.

Usage (from account-warmer/):
    python migrate_to_android14.py --dry-run           # preview without changes
    python migrate_to_android14.py --limit 4           # first 4 accounts only
    python migrate_to_android14.py --accounts acc_001 acc_002 acc_003 acc_004
    python migrate_to_android14.py                     # all provisioned accounts

Flow per account:
    1. Rotate proxy IP → get fresh unique IP
    2. ABORT if same IP used consecutively (accounts would be linked)
    3. Recreate phone as Android 14 (stop → delete → create)
    4. Update YAML with new phone_id and device info
    5. Start phone, wait for boot
    6. Run "Google auto login" RPA flow (email + password + TOTP)
    7. Poll until completion, verify account in AccountManager
    8. Configure locale (timezone + GPS)
    9. Stop phone, save result to YAML
"""

import argparse
import os
import sys
import time
import yaml
from pathlib import Path

# ── Bootstrap ──────────────────────────────────────────────────────────────────
_here = Path(__file__).parent
sys.path.insert(0, str(_here))

_env_file = _here / "warmer.env"
if _env_file.exists():
    for _line in _env_file.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())

from core.geelark_client import GeelarKClient, pick_fast_model
from activities.google_login_mobile import (
    _verify_google_account,
    _configure_device_locale,
    _dismiss_post_login_screens,
    _shell,
    _AUTO_LOGIN_FLOW_ID,
)

# ── Constants ──────────────────────────────────────────────────────────────────

ACCOUNTS_FILE = Path(os.environ.get("WARMER_DATA_DIR", "C:/WarmingData")) / "geelark_accounts.yaml"
FLOW_ID = _AUTO_LOGIN_FLOW_ID  # "628594965093548419"

# StreamVia proxy one-hit API (from proxy info sheet)
_PROXY_API_TOKEN = "xuClkA9-wS6_-4wWCy2bWsWXjepW0wVD1AYLdlze_sc"
_PROXY_API_BASE = "https://mobile-proxy-140-166.streamvia.io/api.php"


def _proxy_get_ip() -> str:
    """Get current proxy exit IP via StreamVia status API. Returns IP or ''."""
    import requests
    try:
        r = requests.get(
            _PROXY_API_BASE,
            params={"token": _PROXY_API_TOKEN, "action": "status"},
            timeout=15,
        )
        import re
        m = re.search(r"Ready:\s*([\d.]+)", r.text)
        if m:
            return m.group(1)
        m = re.search(r"(\d+\.\d+\.\d+\.\d+)", r.text)
        if m:
            return m.group(1)
    except Exception:
        pass
    return ""


# Track the last time we successfully rotated the proxy (in-process global).
# The StreamVia API enforces a minimum gap between rotations.
_last_rotation_time: float = 0.0
_ROTATION_COOLDOWN = 185  # seconds — 180s minimum + 5s buffer


def _proxy_rotate() -> str:
    """
    Rotate the StreamVia mobile proxy to a fresh IP.

    Strategy:
      1. Try ``changeipunique`` (IP not used in last 24h) — preferred.
      2. If throttled > 10 min, fall back to ``changeip`` (random IP).
      3. Detect "Throttled: Wait N" responses, wait, and retry.
      4. Poll until ``Ready: x.x.x.x`` shows a new IP (up to 10 min).
      5. If the IP still hasn't changed after all retries, return ''.

    Returns new IP on success, '' on failure / no change.
    """
    import requests as _req
    import re as _re

    global _last_rotation_time

    # ── Enforce minimum gap between rotations ──────────────────────────────
    gap = time.time() - _last_rotation_time
    if gap < _ROTATION_COOLDOWN:
        wait = _ROTATION_COOLDOWN - gap
        print(f"  Proxy: cooldown - waiting {wait:.0f}s...")
        time.sleep(wait)

    old_ip = _proxy_get_ip()
    if not old_ip:
        print("  Proxy: cannot reach status API - aborting rotation")
        return ""

    print(f"  Proxy current IP: {old_ip}")

    # ── Request rotation (with throttle handling + fallback) ───────────────
    action = "changeipunique"  # preferred
    max_poll_seconds = 600     # 10 minutes

    for attempt in range(3):
        try:
            r = _req.get(
                _PROXY_API_BASE,
                params={"token": _PROXY_API_TOKEN, "action": action},
                timeout=30,
            )
            resp = r.text.strip()
        except Exception as e:
            print(f"  Proxy: API error on {action}: {e}")
            time.sleep(10)
            continue

        # Detect throttle
        throttle = _re.search(
            r'Throttled:\s*Wait\s*(\d+)\s*Seconds?', resp, _re.I,
        )
        if throttle:
            wait_sec = int(throttle.group(1))
            if wait_sec > 600 and action == "changeipunique" and attempt == 0:
                print(f"  Proxy: {action} throttled {wait_sec}s (>10 min) - "
                      f"falling back to changeip")
                action = "changeip"
                continue
            if wait_sec > 900:
                print(f"  Proxy: {action} throttled {wait_sec}s (>15 min) - "
                      f"giving up on rotation")
                return ""
            print(f"  Proxy: {action} throttled - waiting {wait_sec + 5}s...")
            time.sleep(wait_sec + 5)
            continue

        # Rotation started
        print(f"  Proxy: {action} - {resp[:100]}")
        break
    else:
        print("  Proxy: still throttled after 3 attempts - giving up")
        return ""

    # ── Poll for new IP ────────────────────────────────────────────────────
    for i in range(max_poll_seconds // 5):
        time.sleep(5)
        ip = _proxy_get_ip()
        if ip and ip != old_ip:
            elapsed = (i + 1) * 5
            print(f"  Proxy rotated: {old_ip} -> {ip} (took {elapsed}s)")
            _last_rotation_time = time.time()
            return ip
        if i % 24 == 0 and i > 0:
            print(f"  Proxy: still {ip or '?'} after {(i + 1) * 5}s...")

    # Final check
    final_ip = _proxy_get_ip()
    if final_ip and final_ip != old_ip:
        print(f"  Proxy rotated (late): {old_ip} -> {final_ip}")
        _last_rotation_time = time.time()
        return final_ip

    print(f"  Proxy: IP unchanged after {max_poll_seconds}s - {old_ip}")
    return ""

# ── CLI ────────────────────────────────────────────────────────────────────────

parser = argparse.ArgumentParser(description="Migrate GeelarK phones to Android 14")
group = parser.add_mutually_exclusive_group()
group.add_argument("--accounts", metavar="ID", nargs="+",
                   help="Specific account IDs to migrate")
group.add_argument("--limit", type=int, metavar="N",
                   help="Migrate only the first N eligible accounts")
group.add_argument("--all", action="store_true",
                   help="Migrate ALL eligible accounts (default: only not yet Android 14)")
parser.add_argument("--dry-run", action="store_true",
                    help="Print what would happen, make no changes")
parser.add_argument("--force", action="store_true",
                    help="Migrate even accounts already marked Android 14")
parser.add_argument("--file", metavar="PATH",
                    help="Path to geelark_accounts.yaml (default: WarmingData2)")
args = parser.parse_args()


# ── Helpers ────────────────────────────────────────────────────────────────────

def _get_accounts_file() -> Path:
    """Return the accounts file path (--file override or default)."""
    if args.file:
        return Path(args.file)
    return ACCOUNTS_FILE


def _load_accounts() -> list:
    """Load account list from the accounts file."""
    path = _get_accounts_file()
    if not path.exists():
        print(f"ERROR: {path} not found")
        sys.exit(1)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data.get("accounts", [])


def _save_accounts(accounts: list) -> None:
    """Write the full account list back to the accounts file."""
    path = _get_accounts_file()
    path.write_text(
        yaml.dump({"accounts": accounts}, default_flow_style=False,
                  allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _wait_for_boot(phone_id: str, acc_id: str) -> bool:
    """Poll wm size until phone responds (up to 120s)."""
    for i in range(24):
        time.sleep(5)
        ok, out = _shell(phone_id, "wm size")
        if ok and out.strip():
            print(f"  Booted after {(i + 1) * 5}s")
            return True
    return False


def _poll_task(client: GeelarKClient, task_id: str, deadline: float) -> tuple:
    """Poll a task until completion. Returns (status, fail_desc, cost)."""
    while time.time() < deadline:
        try:
            tasks = client.query_tasks([task_id])
            if tasks:
                t = tasks[0]
                s = t.get("status", 0)
                cost = t.get("cost", 0)
                dots = "." * (1 + (int(time.time()) // 3) % 3)
                print(f"\r  Login{cost}s{dots}", end="", flush=True)
                if s == 3:
                    print(f"\r  Login: {cost}s  COMPLETED")
                    return 3, "", cost
                elif s == 4:
                    fail = (t.get("failDesc", "") or "")[:200]
                    print(f"\r  Login: FAILED — {fail}")
                    return 4, fail, cost
                elif s == 7:
                    print(f"\r  Login: CANCELLED")
                    return 7, "", cost
        except Exception:
            pass
        time.sleep(5)
    return 0, "timeout", 0


# ── Main migration function ────────────────────────────────────────────────────

def migrate_account(account: dict, client: GeelarKClient,
                    previous_ip: str,
                    previous_phone_id: str = "") -> tuple[bool, str, str, str]:
    """
    Recreate one account's phone as Android 14 and re-login.

    Returns (success: bool, new_ip: str, diagnosis: str, new_phone_id: str).
    """
    acc_id  = account.get("id", "?")
    email   = account.get("email", "")
    password = account.get("password", "")
    totp    = (account.get("totp_secret") or "").strip()
    old_pid = account.get("geelark_phone_id", "")
    geo_city = account.get("geo_city", "london")
    old_os  = account.get("os_version", "?")
    old_dev = f"{account.get('device_brand','?')} {account.get('device_model','?')}"

    print(f"\n{'=' * 70}")
    print(f"  {acc_id}  {email}")
    print(f"  Old: {old_pid} ({old_os}, {old_dev})")

    if not old_pid:
        return False, "", "No phone_id — not provisioned", ""

    if not totp:
        return False, "", "No TOTP secret — cannot complete 2FA", ""

    # ── 0. Dry-run — exit before any API calls ─────────────────────────────
    if args.dry_run:
        print("  [DRY RUN] Would recreate as Android 14, login, and save.")
        return True, "", "dry_run_ok", ""

    # ── 1. Rotate proxy IP ─────────────────────────────────────────────────
    print("  Rotating proxy IP...")
    new_ip = _proxy_rotate()
    if not new_ip:
        # Rotation failed — check if proxy is even reachable
        fallback_ip = _proxy_get_ip()
        if fallback_ip:
            print(f"  WARNING: Rotation failed — proxy still at {fallback_ip}")
            if previous_ip and fallback_ip == previous_ip:
                print(f"  *** ABORT: Same IP as previous account ({previous_ip})")
                print(f"  *** Skipping this account to avoid account linking.")
                return False, fallback_ip, "DUPLICATE_IP_ABORT", ""
            new_ip = fallback_ip
        else:
            print("  FATAL: Proxy unreachable — aborting migration")
            return False, "", "PROXY_UNREACHABLE", ""

    # ── 2. IP dedup check ──────────────────────────────────────────────────
    if previous_ip and new_ip and new_ip == previous_ip:
        print(f"\n  *** ABORT: Same IP used consecutively!")
        print(f"  *** {previous_ip} -> {new_ip}")
        print(f"  *** Accounts would be linked. Stopping migration.")
        return False, new_ip, "DUPLICATE_IP_ABORT", ""

    # ── 2a. IP banner + 60s settling buffer before touching any phone ──────
    print()
    print(f"  >>> {acc_id} ASSIGNED PROXY IP: {new_ip} <<<")
    print(f"  Settling 15s...")
    time.sleep(15)

    # ── 2b. Pre-flight: verify no phone from previous account is running ──
    # Only checks the previous phone ID — never queries all phones globally.
    if previous_phone_id:
        print(f"  Pre-flight: verifying previous phone {previous_phone_id} is stopped...")
        if not client.verify_phone_stopped(previous_phone_id):
            print(f"  WARNING: Previous phone {previous_phone_id} still running!")
            print(f"  Attempting emergency stop...")
            if not client.stop_phone(previous_phone_id):
                print(f"  FATAL: Cannot stop previous phone. Aborting batch.")
                return False, new_ip, "PREVIOUS_PHONE_STILL_RUNNING", ""
            print(f"  Emergency stop succeeded.")

    # ── 3. Sign out old phone before deleting ──────────────────────────────
    # Starting the old phone and clearing its Google Play Services data
    # prevents the "Verify on old device" prompt during new login.
    print("  Starting old phone to sign out...")
    try:
        client.start_phone(old_pid)
        # Wait for old phone to actually boot before issuing commands
        booted = False
        for _ in range(6):
            time.sleep(5)
            ok, out = _shell(old_pid, "wm size")
            if ok and out.strip():
                booted = True
                break
        if booted:
            ok, _ = _shell(old_pid, "pm clear com.google.android.gms")
            if ok:
                print("  Signed out of old phone.")
            else:
                print("  WARNING: pm clear failed — may trigger verification.")
        else:
            print("  WARNING: Old phone did not boot — may trigger verification.")
        if not client.stop_phone(old_pid):
            print("  WARNING: Old phone did not stop — retrying...")
            time.sleep(5)
            if not client.stop_phone(old_pid):
                print("  FATAL: Old phone refuses to stop. Aborting batch.")
                return False, new_ip, "OLD_PHONE_WONT_STOP", ""
    except Exception as e:
        print(f"  WARNING: Could not sign out old phone: {e}")

    # ── 4. Recreate phone ──────────────────────────────────────────────────
    print("  Recreating as Android 14...")
    try:
        new_pid, equip = client.recreate_phone(
            old_phone_id=old_pid,
            profile_name=email,
            android_version="Android 14",
        )
    except RuntimeError as e:
        return False, new_ip, f"Recreate failed: {e}", ""

    new_brand = equip.get("deviceBrand", "?")
    new_model = equip.get("deviceModel", "?")
    new_os    = equip.get("osVersion", "Android 14")
    print(f"  New phone: {new_pid} ({new_brand} {new_model}, {new_os})")

    # ── 4. Update YAML immediately ─────────────────────────────────────────
    accounts = _load_accounts()
    for a in accounts:
        if a.get("id") == acc_id:
            a["geelark_phone_id"] = new_pid
            a["device_brand"]     = new_brand
            a["device_model"]     = new_model
            a["os_version"]       = new_os
            a["login_status"]     = "pending"
            a["mobile_setup_done"] = False
            break
    _save_accounts(accounts)

    # ── 5. Start phone ─────────────────────────────────────────────────────
    print("  Starting phone...")
    try:
        viewer = client.start_phone(new_pid)
        print(f"  Viewer: {viewer[:80]}...")
    except Exception as e:
        return False, new_ip, f"Start failed: {e}", new_pid

    if not _wait_for_boot(new_pid, acc_id):
        return False, new_ip, "Phone did not boot in 120s", new_pid

    # ── 6. Login ───────────────────────────────────────────────────────────
    print("  Running Google auto login flow...")
    param_map = {
        "User Email": email,
        "Password":   password,
        "2faCode":    totp.replace(" ", "").upper(),
    }
    try:
        task_id = client.run_custom_flow(
            flow_id=FLOW_ID,
            phone_id=new_pid,
            param_map=param_map,
            task_name=f"Migrate login — {email}",
        )
    except Exception as e:
        return False, new_ip, f"Login flow failed to start: {e}", new_pid

    status, fail_desc, cost = _poll_task(client, task_id, time.time() + 600)

    if status != 3:
        return False, new_ip, f"Login flow ended with status {status}: {fail_desc}", new_pid

    # ── 7. Dismiss post-login screens + verify ──────────────────────────────
    print("  Dismissing post-login screens...")
    confirmed = _dismiss_post_login_screens(new_pid, email, acc_id)

    if not confirmed:
        # One retry with the login flow
        print("  Account not verified — re-running login flow (attempt 2)...")
        try:
            task_id2 = client.run_custom_flow(
                flow_id=FLOW_ID,
                phone_id=new_pid,
                param_map=param_map,
                task_name=f"Migrate login retry — {email}",
            )
        except Exception as e:
            return False, new_ip, f"Retry flow failed to start: {e}", new_pid

        status2, fail2, cost2 = _poll_task(client, task_id2, time.time() + 600)
        if status2 == 3:
            confirmed = _dismiss_post_login_screens(new_pid, email, acc_id)
            if confirmed:
                print(f"  Account verified on retry! (cost: {cost + cost2}s)")

    if not confirmed:
        return False, new_ip, "Account not found in AccountManager after login", new_pid

    print("  Account verified [OK]")

    # ── 8. Configure locale ────────────────────────────────────────────────
    print(f"  Configuring locale ({geo_city})...")
    _configure_device_locale(new_pid, account, acc_id)

    # ── 9. Stop phone (VERIFIED) ──────────────────────────────────────────
    print("  Stopping phone...")
    if not client.stop_phone(new_pid):
        print("  WARNING: Phone did not stop — retrying...")
        time.sleep(5)
        if not client.stop_phone(new_pid):
            print("  FATAL: Phone refuses to stop. Aborting batch.")
            return False, new_ip, "NEW_PHONE_WONT_STOP", new_pid

    # ── 9a. Verify stopped before proceeding ───────────────────────────────
    if not client.verify_phone_stopped(new_pid):
        print("  FATAL: Phone status check failed after stop. Aborting batch.")
        return False, new_ip, "STOP_VERIFY_FAILED", new_pid

    print(f"  Phone stopped & verified. (IP was {new_ip})")

    # ── 9a. 60s settling buffer after phone stops ──────────────────────────
    print(f"  Post-phone settling 15s (IP {new_ip})...")
    time.sleep(15)

    # ── 10. Save success ───────────────────────────────────────────────────
    accounts = _load_accounts()
    for a in accounts:
        if a.get("id") == acc_id:
            a["login_status"] = "success"
            a["login_done"]   = True
            break
    _save_accounts(accounts)

    return True, new_ip, f"Migrated successfully ({cost}s login)", new_pid


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    all_accounts = _load_accounts()
    print(f"Loaded {len(all_accounts)} accounts from {_get_accounts_file()}")

    # Filter to eligible accounts
    candidates = []
    for a in all_accounts:
        aid = a.get("id", "?")
        pid = a.get("geelark_phone_id", "")
        osv = a.get("os_version", "")

        if not pid:
            continue  # not provisioned

        if osv.startswith("Android 14") and not args.force:
            continue  # already migrated

        candidates.append(a)

    # Apply --accounts or --limit if provided
    if args.accounts:
        candidates = [a for a in candidates if a.get("id") in args.accounts]
        if not candidates:
            print(f"ERROR: None of the specified accounts are eligible")
            sys.exit(1)

    if args.limit:
        candidates = candidates[:args.limit]

    if not candidates:
        print("No eligible accounts to migrate. All done!")
        return

    # Sort by ID for predictable ordering
    candidates.sort(key=lambda a: a.get("id", ""))

    print(f"\nMigrating {len(candidates)} account(s):")
    for a in candidates:
        print(f"  {a['id']:15} {a.get('email','?'):40} "
              f"phone={a.get('geelark_phone_id','?')}  "
              f"os={a.get('os_version','?')}")
    print()

    if args.dry_run:
        print("[DRY RUN] — no changes will be made.\n")

    client = GeelarKClient()
    ok = 0
    failed = 0
    prev_ip = ""
    prev_phone_id = ""

    for i, account in enumerate(candidates):
        acc_id  = account.get("id", "?")
        success, new_ip, diagnosis, new_phone_id = migrate_account(
            account, client, prev_ip, prev_phone_id,
        )

        if diagnosis == "DUPLICATE_IP_ABORT":
            print(f"\n  Migration ABORTED after {ok} OK, {failed} failed.")
            break

        if success:
            ok += 1
            print(f"  RESULT: OK SUCCESS  ({diagnosis})")
        else:
            failed += 1
            print(f"  RESULT: X FAILED  ({diagnosis})")

        prev_ip = new_ip or prev_ip
        prev_phone_id = new_phone_id or prev_phone_id

    # ── Summary ────────────────────────────────────────────────────────────
    print(f"\n{'=' * 70}")
    print(f"SUMMARY: {ok} OK, {failed} failed, {len(candidates)} total")
    if ok == len(candidates):
        print("All migrations successful!")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
