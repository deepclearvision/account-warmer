#!/usr/bin/env python3
"""
Fleet-wide provisioning check and auto-fix.
Checks every phone for Developer Options + Fake GPS mock app.
"""
import csv
import sys
import time
from pathlib import Path

_repo_root = Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced")
_orchestrator_src = _repo_root / "geelark_orchestrator" / "src"
_orchestrator_root = _repo_root / "geelark_orchestrator"
_account_warmer = _repo_root / "account-warmer"

sys.path.insert(0, str(_orchestrator_src))
sys.path.insert(0, str(_orchestrator_root))
sys.path.insert(0, str(_account_warmer))

from core.geelark_client import GeelarKClient, _post
from run_one_phone import (
    _verify_phone_provisioned,
    _provision_developer_and_mock,
    ensure_running,
    FAKE_GPS_PACKAGE,
)


def ensure_stopped(client: GeelarKClient, phone_id: str) -> bool:
    """Stop the phone after checking."""
    try:
        client.stop_phone(phone_id)
        time.sleep(3)
        return True
    except Exception as e:
        print(f"    WARNING: stop failed: {e}")
        return False


def check_and_fix(phone_id: str, account_email: str, account_id: str) -> dict:
    """Check + auto-fix a single phone. Returns result dict."""
    result = {
        "phone_id": phone_id,
        "account_email": account_email,
        "account_id": account_id,
        "category": "unknown",
        "details": "",
    }

    client = GeelarKClient()

    # 1. Ensure phone running
    print(f"\n--- {account_id} ({account_email}) ---")
    try:
        if not ensure_running(client, phone_id):
            result["category"] = "unreachable"
            result["details"] = "Phone did not start"
            return result
    except Exception as e:
        result["category"] = "unreachable"
        result["details"] = f"ensure_running error: {e}"
        return result

    # 2. Initial check
    try:
        prov_ok, prov_reason = _verify_phone_provisioned(phone_id)
    except Exception as e:
        result["category"] = "failed"
        result["details"] = f"verify exception: {e}"
        ensure_stopped(client, phone_id)
        return result

    if prov_ok:
        result["category"] = "already_provisioned"
        result["details"] = "Developer Options: yes, mock app: yes"
        ensure_stopped(client, phone_id)
        return result

    # 3. Missing — try auto-provision
    print(f"  Missing: {prov_reason}. Attempting auto-provision...")
    try:
        fix_ok, fix_reason = _provision_developer_and_mock(phone_id)
    except Exception as e:
        result["category"] = "failed"
        result["details"] = f"auto-provision exception: {e}"
        ensure_stopped(client, phone_id)
        return result

    # 4. Re-check after fix
    if fix_ok:
        prov_ok2, prov_reason2 = _verify_phone_provisioned(phone_id)
        if prov_ok2:
            result["category"] = "newly_fixed"
            result["details"] = f"was missing: {prov_reason}. now OK."
        else:
            result["category"] = "failed"
            result["details"] = f"auto-provision claimed success but still missing: {prov_reason2}"
    else:
        result["category"] = "failed"
        result["details"] = f"auto-provision failed: {fix_reason}"

    ensure_stopped(client, phone_id)
    return result


def main() -> int:
    csv_path = _repo_root / "geelark_orchestrator" / "MASTER_REAL_DATA.csv"

    # Load all profiles
    phones = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            phones.append({
                "account_id": row.get("account_id", "").strip(),
                "account_email": row.get("account_email", "").strip(),
                "phone_id": row.get("geelark_phone_id", "").strip(),
            })

    print(f"Fleet provisioning check: {len(phones)} phones")

    results = []
    for p in phones:
        if not p["phone_id"]:
            continue
        r = check_and_fix(p["phone_id"], p["account_email"], p["account_id"])
        results.append(r)
        time.sleep(2)  # brief cooldown between phones

    # Report
    print("\n" + "=" * 60)
    print("FLEET PROVISIONING REPORT")
    print("=" * 60)

    categories = {
        "already_provisioned": "=== ALREADY PROVISIONED (no action needed) ===",
        "newly_fixed": "=== NEWLY FIXED (auto-provision worked) ===",
        "failed": "=== FAILED (auto-provision could not fix) ===",
        "unreachable": "=== UNREACHABLE / SKIPPED ===",
    }

    for cat_key, cat_title in categories.items():
        items = [r for r in results if r["category"] == cat_key]
        print(f"\n{cat_title}")
        print("-" * 40)
        if not items:
            print("  (none)")
        for item in items:
            print(f"  {item['phone_id']}: {item['account_email']} ({item['account_id']}) — {item['details']}")

    print("\n" + "=" * 60)
    print(f"Summary: {len([r for r in results if r['category']=='already_provisioned'])} already OK, "
          f"{len([r for r in results if r['category']=='newly_fixed'])} fixed, "
          f"{len([r for r in results if r['category']=='failed'])} failed, "
          f"{len([r for r in results if r['category']=='unreachable'])} unreachable")
    print("=" * 60)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
