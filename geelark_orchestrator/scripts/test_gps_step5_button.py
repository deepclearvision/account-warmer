#!/usr/bin/env python3
"""
GPS Mock Quick Test — Test B: Steps 1-5 (including "start using gps joystick" button).
Then opens Maps to verify if mock is active.

Usage:
  python test_gps_step5_button.py --account acc_005
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
from run_one_phone import dumpsys_mock_check, _find_bounds, _get_screen_size

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

    print(f"=== GPS Mock Test B — Steps 1-5 (with START button) ===")
    print(f"Account: {selected['account_email']} ({args.account})")
    print(f"Phone:   {phone_id}")
    print(f"Target:  {lat}, {lng}")

    if not ensure_running(client, phone_id):
        print("FAILED: Phone could not start.")
        return 1

    # Step 1: Open MainActivity
    print("\n--- Step 1: Open Fake GPS MainActivity ---")
    _post(
        "/open/v1/shell/execute",
        {
            "id": phone_id,
            "cmd": "am start -n com.theappninjas.fakegpsjoystick/com.theappninjas.fakegpsjoystick.MainActivity",
        },
    )
    time.sleep(5)

    # Step 2: Start OverlayService
    print("\n--- Step 2: Start OverlayService ---")
    _post(
        "/open/v1/shell/execute",
        {"id": phone_id, "cmd": "am startservice -n com.theappninjas.fakegpsjoystick/.service.OverlayService"},
    )
    time.sleep(3)

    # Step 3: Deep-link
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
    time.sleep(8)

    # Step 4: Dismiss any dialogs
    print("\n--- Step 4: Dismiss dialogs ---")
    for dialog_attempt in range(1, 4):
        _post("/open/v1/shell/execute", {"id": phone_id, "cmd": f"uiautomator dump /sdcard/test_b_dialog_{dialog_attempt}.xml"})
        time.sleep(1)
        r = _post("/open/v1/shell/execute", {"id": phone_id, "cmd": f"cat /sdcard/test_b_dialog_{dialog_attempt}.xml"})
        xml = r.get("output", "")
        dismissed = False
        for label in ("Cancel", "cancel", "Done", "done", "Got it", "OK", "Allow"):
            pos = _find_bounds(xml, label)
            if pos:
                print(f"    Dismissing '{label}' at {pos}")
                _post("/open/v1/shell/execute", {"id": phone_id, "cmd": f"input tap {pos[0]} {pos[1]}"})
                time.sleep(3)
                dismissed = True
                break
        if not dismissed:
            print("    No dialogs found.")
            break

    # Step 5: Find and tap "start using gps joystick"
    print("\n--- Step 5: Find 'start using gps joystick' button ---")
    start_pos = None
    for start_attempt in range(1, 4):
        time.sleep(2)
        _post("/open/v1/shell/execute", {"id": phone_id, "cmd": f"uiautomator dump /sdcard/test_b_start_{start_attempt}.xml"})
        time.sleep(1)
        r = _post("/open/v1/shell/execute", {"id": phone_id, "cmd": f"cat /sdcard/test_b_start_{start_attempt}.xml"})
        xml = r.get("output", "")
        for label in ("start using gps joystick", "Start using GPS Joystick", "START USING GPS JOYSTICK", "Start", "START"):
            start_pos = _find_bounds(xml, label)
            if start_pos:
                print(f"    Found '{label}' at {start_pos}")
                break
        if start_pos:
            break

    if start_pos:
        print(f"    Tapping START at {start_pos}")
        _post("/open/v1/shell/execute", {"id": phone_id, "cmd": f"input tap {start_pos[0]} {start_pos[1]}"})
        time.sleep(5)
    else:
        print("    WARNING: Button not found. Trying map-area fallback...")
        screen_w, screen_h = _get_screen_size(phone_id)
        tap_x = screen_w // 2
        tap_y = int(screen_h * 0.8)
        _post("/open/v1/shell/execute", {"id": phone_id, "cmd": f"input tap {tap_x} {tap_y}"})
        time.sleep(5)

    # Verify mock
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
        print("\n✅ SUCCESS: Mock is active!")
        print("\n--- Opening Maps to verify ---")
        _post(
            "/open/v1/shell/execute",
            {"id": phone_id, "cmd": "am start -n com.google.android.apps.maps/com.google.android.maps.MapsActivity"},
        )
        time.sleep(8)
        mock_ok2, mock_info2 = dumpsys_mock_check(phone_id)
        print(f"    Mock after Maps open: {mock_info2 if mock_ok2 else 'NONE'}")
    else:
        print("\n❌ FAIL: Mock NOT active even after tapping button.")

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
