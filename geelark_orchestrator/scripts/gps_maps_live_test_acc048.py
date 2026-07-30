#!/usr/bin/env python3
"""
GPS + Maps live test for acc_048 (oakleighoneal9987@gmail.com)
Phone: 614216903581237315 | Business: Southwark Plumbers
Uses the FULL _activate_gps_android13() method from run_one_phone.py.
Opens the Geelark live-view URL in the default browser before GPS steps.
"""
import csv
import json
import os
import random
import sys
import time
import urllib.parse
import webbrowser
from datetime import datetime
from pathlib import Path

_repo_root = Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced")
_orchestrator_src = _repo_root / "geelark_orchestrator" / "src"
_orchestrator_root = _repo_root / "geelark_orchestrator"
_account_warmer = _repo_root / "account-warmer"
_data_dir = _repo_root / "data"

sys.path.insert(0, str(_orchestrator_src))
sys.path.insert(0, str(_orchestrator_root))
sys.path.insert(0, str(_account_warmer))

from core.geelark_client import GeelarKClient, _post
from run_one_phone import _activate_gps_android13, dumpsys_mock_check, _ui_clear_storage

PHONE_ID = "614216903581237315"
EMAIL = "oakleighoneal9987@gmail.com"
CSV_PATH = _data_dir / "accounts_business_mapping.csv"

START_TIMEOUT = 360
INTER_PHONE_DELAY = 30
MAX_ATTEMPTS = 3

_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
_log_dir = _repo_root / "geelark_orchestrator" / "scripts" / f"live_test_acc048_{_timestamp}"
_log_dir.mkdir(parents=True, exist_ok=True)
_text_log = _log_dir / "run.log"
_screenshot_dir = _log_dir / "screenshots"
_screenshot_dir.mkdir(parents=True, exist_ok=True)
_jsonl_file = _repo_root / "logs" / "run_history.jsonl"


def _write(line: str) -> None:
    print(line)
    with open(_text_log, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def write_jsonl(record: dict) -> None:
    _jsonl_file.parent.mkdir(parents=True, exist_ok=True)
    with open(_jsonl_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def load_mapping() -> dict:
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("account_email") == EMAIL:
                return row
    raise RuntimeError(f"Account {EMAIL} not found in CSV")


def _get_running_phones(client: GeelarKClient) -> list[dict]:
    try:
        return [p for p in client.list_phones(page_size=100) if p.get("status") == 0]
    except Exception as e:
        _write(f"  ERROR listing phones: {e}")
        return []


def _global_cleanup(client: GeelarKClient) -> None:
    _write("=== Pre-run global cleanup ===")
    running = _get_running_phones(client)
    if not running:
        _write("  No phones running.")
        return
    for p in running:
        pid = p["id"]
        _write(f"  Stopping phone {pid} ...")
        try:
            client.stop_phone(pid)
        except Exception:
            pass
    for i in range(12):
        time.sleep(5)
        remaining = len(_get_running_phones(client))
        _write(f"  Poll {i+1}/12: {remaining} phones still running")
        if remaining == 0:
            _write("  All stopped.")
            return
    _write("  WARNING: Some phones did not stop within 60s.")


def ensure_running(client: GeelarKClient, phone_id: str) -> bool:
    try:
        s = client.get_phone_status([phone_id])[0].get("status", -1)
    except Exception:
        s = -1
    if s == 0:
        _write(f"  Phone {phone_id} already running.")
        return True
    _write(f"  Starting phone {phone_id} ...")
    try:
        client.start_phone(phone_id)
    except Exception as e:
        _write(f"  ERROR: start failed: {e}")
        return False
    deadline = time.time() + START_TIMEOUT
    while time.time() < deadline:
        time.sleep(5)
        try:
            if client.get_phone_status([phone_id])[0].get("status") == 0:
                _write("  Phone running.")
                return True
        except Exception:
            pass
    _write("  ERROR: phone did not start within 360s.")
    return False


def _confirm_stop(client: GeelarKClient, phone_id: str) -> bool:
    for _ in range(3):
        try:
            client.stop_phone(phone_id)
        except Exception:
            pass
        for _ in range(3):
            time.sleep(5)
            try:
                if client.get_phone_status([phone_id])[0].get("status") != 0:
                    return True
            except Exception:
                pass
    return False


def run_maps_search(client: GeelarKClient, phone_id: str, business_name: str) -> dict:
    result = {
        "maps_search_result": "UNKNOWN",
        "business_found_text": None,
        "screenshot_path": None,
        "ui_dump_path": None,
        "error": None,
    }
    encoded = urllib.parse.quote(business_name)
    geo_url = f"geo:0,0?q={encoded}"
    try:
        _post("/open/v1/shell/execute",
              {"id": phone_id, "cmd": f"am start -a android.intent.action.VIEW -d '{geo_url}' com.google.android.apps.maps"})
    except Exception as e:
        result["error"] = f"Maps start failed: {e}"
        return result

    _write("  Waiting 10s for Maps to load...")
    time.sleep(10)

    dump_local = str(_screenshot_dir / f"{phone_id}_maps_ui.xml")
    screenshot_local = str(_screenshot_dir / f"{phone_id}_maps_screen.png")

    try:
        _post("/open/v1/shell/execute",
              {"id": phone_id, "cmd": "uiautomator dump /sdcard/maps_ui.xml"})
        time.sleep(2)
        _post("/open/v1/shell/execute",
              {"id": phone_id, "cmd": "screencap -p /sdcard/maps_screen.png"})
        time.sleep(2)

        r = _post("/open/v1/shell/execute",
                  {"id": phone_id, "cmd": "cat /sdcard/maps_ui.xml"})
        xml = r.get("output", "")
        with open(dump_local, "w", encoding="utf-8") as f:
            f.write(xml)
        result["ui_dump_path"] = dump_local

        if business_name in xml:
            result["maps_search_result"] = "FOUND"
            result["business_found_text"] = business_name
        else:
            words = business_name.split()[:3]
            partial = " ".join(words)
            if partial in xml:
                result["maps_search_result"] = "FOUND"
                result["business_found_text"] = partial
            else:
                result["maps_search_result"] = "NOT_FOUND"

        _write(f"  Maps search: {result['maps_search_result']}")

        # Screenshot via API
        try:
            data = client.take_screenshot(phone_id, max_wait=30)
            if data:
                with open(screenshot_local, "wb") as f:
                    f.write(data)
                result["screenshot_path"] = screenshot_local
                _write(f"  Screenshot saved: {screenshot_local}")
        except Exception as e:
            _write(f"  Screenshot API failed: {e}")

    except Exception as e:
        result["error"] = str(e)
        _write(f"  ERROR during Maps capture: {e}")

    return result


def main() -> int:
    client = GeelarKClient()

    _write("=" * 70)
    _write(f"GPS + MAPS LIVE TEST — acc_048 — {_timestamp}")
    _write("=" * 70)

    # Load mapping
    mapping = load_mapping()
    business_name = mapping["business_name"]
    business_lat = float(mapping["business_lat"])
    business_lng = float(mapping["business_lng"])
    nearby_raw = mapping.get("nearby_points", "")
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

    _write(f"Account:      {EMAIL}")
    _write(f"Phone ID:     {PHONE_ID}")
    _write(f"Business:     {business_name}")
    _write(f"Business GPS: {business_lat},{business_lng}")
    _write(f"Nearby pts:   {len(nearby_points)}")

    if not nearby_points:
        _write("ERROR: No nearby coordinates!")
        return 1

    target = random.choice(nearby_points)
    target_lat, target_lng = target
    _write(f"Selected nearby: {target_lat}, {target_lng}")

    # 1. Global cleanup
    _global_cleanup(client)

    # 2. Start phone
    start_ok = False
    for attempt in range(1, MAX_ATTEMPTS + 1):
        _write(f"\n=== Start attempt {attempt}/{MAX_ATTEMPTS} ===")
        start_ok = ensure_running(client, PHONE_ID)
        if start_ok:
            break
        if attempt < MAX_ATTEMPTS:
            time.sleep(INTER_PHONE_DELAY)

    if not start_ok:
        _write("FAILED: Phone could not start.")
        return 1

    # 3. Open live-view URL in default browser BEFORE GPS steps
    dashboard_url = f"https://agent.geelark.com/phone/{PHONE_ID}"
    _write(f"\n*** OPENING LIVE VIEW ***")
    _write(f"URL: {dashboard_url}")
    try:
        webbrowser.open(dashboard_url, new=2)  # new=2 opens new tab
        _write("  Browser opened.")
    except Exception as e:
        _write(f"  WARNING: Could not open browser automatically: {e}")
        _write(f"  Please open the URL manually: {dashboard_url}")

    _write("  Pausing 15s for live view to load...")
    time.sleep(15)

    # 4. Pre-GPS: UI Clear Storage for clean state
    _write("\n=== Pre-GPS: UI Clear Storage ===")
    try:
        _ui_clear_storage(PHONE_ID)
    except Exception as e:
        _write(f"  UI Clear Storage warning: {e}")

    # 5. GPS activation using proven full A13 method
    _write("\n=== Activating GPS via _activate_gps_android13() ===")
    try:
        gps_ok = _activate_gps_android13(PHONE_ID, float(target_lat), float(target_lng))
    except Exception as e:
        _write(f"  GPS activation error: {e}")
        gps_ok = False

    _write(f"GPS activation result: {gps_ok}")

    # 5. Dumpsys check
    mock_ok, mock_info = dumpsys_mock_check(PHONE_ID)
    _write(f"Dumpsys mock: ok={mock_ok}, info={mock_info}")

    # 6. Wait 5s then Maps search
    time.sleep(5)
    maps_result = run_maps_search(client, PHONE_ID, business_name)

    # 7. Stop phone
    _write("\n=== Stopping phone ===")
    _confirm_stop(client, PHONE_ID)

    # 8. Final report
    final_log = {
        "timestamp": datetime.now().isoformat(),
        "phone_id": PHONE_ID,
        "account_email": EMAIL,
        "business_name": business_name,
        "business_lat": business_lat,
        "business_lng": business_lng,
        "target_nearby_lat": target_lat,
        "target_nearby_lng": target_lng,
        "mock_set_success": gps_ok,
        "dumpsys_mock_ok": mock_ok,
        "dumpsys_mock_info": mock_info,
        "maps_search_result": maps_result.get("maps_search_result"),
        "business_found_text": maps_result.get("business_found_text"),
        "screenshot_path": str(maps_result.get("screenshot_path")) if maps_result.get("screenshot_path") else None,
        "ui_dump_path": str(maps_result.get("ui_dump_path")) if maps_result.get("ui_dump_path") else None,
        "error": maps_result.get("error"),
    }

    json_path = _log_dir / "result.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(final_log, f, indent=2)

    jsonl_record = {
        "timestamp": final_log["timestamp"],
        "script_name": "gps_maps_live_test_acc048.py",
        "run_id": _timestamp,
        "phone_id": PHONE_ID,
        "account_email": EMAIL,
        "business_id": mapping.get("business_id"),
        "attempt": 1,
        "started_ok": True,
        "overall_status": "GPS_MAPS_LIVE_TEST",
        "gps_test": {
            "target_lat": target_lat,
            "target_lng": target_lng,
            "mock_success": gps_ok,
            "dumpsys_mock_ok": mock_ok,
            "dumpsys_mock_info": mock_info,
            "maps_search_result": maps_result.get("maps_search_result"),
            "screenshot_path": str(maps_result.get("screenshot_path")) if maps_result.get("screenshot_path") else None,
        },
        "errors": [maps_result["error"]] if maps_result.get("error") else [],
    }
    write_jsonl(jsonl_record)

    _write("\n" + "=" * 70)
    _write("RESULTS")
    _write("=" * 70)
    _write(f"Phone:        {PHONE_ID}")
    _write(f"Account:      {EMAIL}")
    _write(f"Business:     {business_name}")
    _write(f"Nearby:       {target_lat}, {target_lng}")
    _write(f"GPS mock:     {gps_ok}")
    _write(f"Dumpsys:      ok={mock_ok} | {mock_info}")
    _write(f"Maps result:  {maps_result.get('maps_search_result')}")
    if maps_result.get("screenshot_path"):
        _write(f"Screenshot:   {maps_result['screenshot_path']}")
    if maps_result.get("error"):
        _write(f"Error:        {maps_result['error']}")
    _write(f"\nLog dir:  {_log_dir}")

    return 0 if gps_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
