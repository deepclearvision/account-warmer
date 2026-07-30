#!/usr/bin/env python3
"""
GPS Mock Quick Test — Test A: Steps 1-3 only (no START button).
Then opens Maps to verify if mock is active.

Usage:
  python test_gps_step3_only.py --account acc_005
"""
import argparse
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
from run_one_phone import dumpsys_mock_check, _get_screen_size

CSV_PATH = _repo_root / "data" / "accounts_business_mapping.csv"


def load_all_rows():
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def pick_phone(rows, account_id=None):
    if account_id:
        for r in rows:
            if r.get("account_id") == account_id:
                return r
        raise ValueError(f"Account {account_id} not found")
    import random
    return random.choice(rows)


def ensure_running(client, phone_id):
    try:
        s = client.get_phone_status([phone_id])[0].get("status", -1)
    except Exception:
        s = -1
    if s == 0:
        print(f"  Phone {phone_id} already running.")
        return True
    print(f"  Starting phone {phone_id} ...")
    client.start_phone(phone_id)
    for _ in range(24):
        time.sleep(5)
        try:
            if client.get_phone_status([phone_id])[0].get("status") == 0:
                print("  Phone running.")
                return True
        except Exception:
            pass
    print("  ERROR: phone did not start within 120s.")
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", default="acc_005")
    args = ap.parse_args()

    client = GeelarKClient()
    rows = load_all_rows()
    selected = pick_phone(rows, account_id=args.account)

    phone_id = selected["geelark_phone_id"]
    business_lat = float(selected["business_lat"])
    business_lng = float(selected["business_lng"])

    # Pick a nearby point or use business location
    nearby_raw = selected.get("nearby_points", "")
    import random
    nearby_points = []
    if nearby_raw:
        for pt in nearby_raw.split("|"):
            pt = pt.strip()
            if not pt:
                continue
            try:
                lat_s, lng_s = pt.split(",")
                nearby_points.append((float(lat_s), float(lng_s)))
            except Exception:
                pass
    target = random.choice(nearby_points) if nearby_points else (business_lat, business_lng)
    lat, lng = target

    print(f"=== GPS Mock Test A — Steps 1-3 Only ===")
    print(f"Account: {selected['account_email']} ({args.account})")
    print(f"Phone:   {phone_id}")
    print(f"Target:  {lat}, {lng}")

    if not ensure_running(client, phone_id):
        print("FAILED: Phone could not start.")
        return 1

    print("\n--- Step 1: Open Fake GPS MainActivity ---")
    _post(
        "/open/v1/shell/execute",
        {
            "id": phone_id,
            "cmd": "am start -n com.theappninjas.fakegpsjoystick/com.theappninjas.fakegpsjoystick.MainActivity",
        },
    )
    time.sleep(5)

    print("\n--- Step 2: Start OverlayService ---")
    _post(
        "/open/v1/shell/execute",
        {"id": phone_id, "cmd": "am startservice -n com.theappninjas.fakegpsjoystick/.service.OverlayService"},
    )
    time.sleep(3)

    print("\n--- Step 3: Send deep-link ---")
    url = f"gpsjoystick://teleport?lat={lat}&lng={lng}"
    print(f"    URL: {url}")
    _post(
        "/open/v1/shell/execute",
        {
            "id": phone_id,
            "cmd": f"am start -a android.intent.action.VIEW -d '{url}' com.theappninjas.fakegpsjoystick",
        },
    )
    time.sleep(10)

    print("\n--- Verify mock in dumpsys ---")
    mock_ok, mock_info = dumpsys_mock_check(phone_id)
    print(f"    Mock: {mock_info if mock_ok else 'NONE'}")

    if mock_ok and mock_info:
        try:
            dl, dln = mock_info.split(",")
            coords_match = abs(float(dl) - lat) <= 0.0002 and abs(float(dln) - lng) <= 0.0002
            print(f"    Match: {coords_match}")
        except Exception:
            coords_match = False
    else:
        coords_match = False

    if coords_match:
        print("\n✅ SUCCESS: Mock is active with correct coordinates!")
        print("\n--- Opening Maps to verify ---")
        _post(
            "/open/v1/shell/execute",
            {"id": phone_id, "cmd": "am start -n com.google.android.apps.maps/com.google.android.maps.MapsActivity"},
        )
        time.sleep(8)
        mock_ok2, mock_info2 = dumpsys_mock_check(phone_id)
        print(f"    Mock after Maps open: {mock_info2 if mock_ok2 else 'NONE'}")
    else:
        print("\n❌ FAIL: Mock NOT active after deep-link.")
        print("   This means we NEED the 'start using gps joystick' button (Test B).")

    # Get live view URL
    try:
        statuses = client.get_phone_status([phone_id])
        live_url = statuses[0].get("liveViewUrl", "") if statuses else ""
        if live_url:
            print(f"\nLive view: {live_url}")
    except Exception:
        pass

    return 0 if coords_match else 1


if __name__ == "__main__":
    raise SystemExit(main())
