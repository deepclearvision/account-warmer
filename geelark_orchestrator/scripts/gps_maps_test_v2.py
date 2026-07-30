#!/usr/bin/env python3
"""
GPS + Maps test v2 — uses proven _activate_gps_android13() from run_one_phone.py.
Single-phone-at-a-time. Logs to JSON + cumulative JSONL.
"""
import argparse
import json
import random
import sys
import time
import urllib.parse
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

# Import the proven A13 GPS activation
from run_one_phone import _activate_gps_android13, _ui_clear_storage

START_TIMEOUT = 360
MAX_ATTEMPTS = 3
INTER_PHONE_DELAY = 30

_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
_log_file = _repo_root / "geelark_orchestrator" / "scripts" / f"gps_maps_test_{_timestamp}.json"
_text_log = _repo_root / "geelark_orchestrator" / "scripts" / f"gps_maps_test_{_timestamp}.txt"
_screenshot_dir = _repo_root / "geelark_orchestrator" / "scripts" / f"screenshots_{_timestamp}"
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
    with open(_data_dir / "accounts_business_mapping.json", encoding="utf-8") as f:
        return json.load(f)


def _get_running_phones(client: GeelarKClient) -> list[dict]:
    try:
        return [p for p in client.list_phones(page_size=100) if p.get("status") == 0]
    except Exception:
        return []


def _global_cleanup(client: GeelarKClient) -> None:
    _write("=== Pre-run global cleanup ===")
    running = _get_running_phones(client)
    if not running:
        _write("  No phones running.")
        return
    for p in running:
        try:
            client.stop_phone(p["id"])
        except Exception:
            pass
    for _ in range(12):
        time.sleep(5)
        if len(_get_running_phones(client)) == 0:
            _write("  All stopped.")
            return


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


def ensure_running(client: GeelarKClient, phone_id: str) -> bool:
    try:
        s = client.get_phone_status([phone_id])[0].get("status", -1)
    except Exception:
        s = -1
    if s == 0:
        return True
    _write(f"  Starting phone {phone_id}...")
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

        # Check business name in UI dump
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
    ap = argparse.ArgumentParser()
    ap.add_argument("--live-debug", action="store_true", help="Pause for live view")
    args = ap.parse_args()

    client = GeelarKClient()

    _write("=" * 70)
    _write(f"GPS + MAPS TEST V2 — {_timestamp}")
    _write("=" * 70)

    # Load mapping
    mapping = load_mapping()

    # Select random provisioned phone that has mapping data
    provisioned_ids = [
        "614216915744719218", "614216911852404803", "614216908010422642",
        "614216903581237315", "614216899286270322", "614216895242960963",
        "614216890796998723", "614216886971793475", "614216883146588530",
        "614216879120056690", "614216875076747634", "614216871117324355",
        "614216867258564675", "614216863500468594", "614216858970620274",
        "614216854994420082", "614216851202768963", "614216847293677635",
        "614216843351031875", "614216838938624067", "614216835079864690",
        "614216831053332547", "614216826674479171", "614216822245294147",
        "614216818470420547", "614216814192230770", "614216810182475843",
        "614216806256607602", "614216802213298243", "614216798086103107",
        "614216794093125699", "614216790150480242", "614216786023284803",
        "614216782181302642", "614216777836003395", "614216773910135154",
        "614216769833271363", "614216763072053315", "613438578466226595",
        "613354607392850339", "613286814907629987", "613135440043573595",
        "613135434842636540"
    ]

    candidates = []
    for email, data in mapping.items():
        pid = data.get("geelark_phone_id", "")
        if pid in provisioned_ids:
            candidates.append({"phone_id": pid, "email": email, "data": data})

    _write(f"Mapped provisioned phones: {len(candidates)}")
    selected = random.choice(candidates)
    phone_id = selected["phone_id"]
    email = selected["email"]
    data = selected["data"]
    business_name = data["business_name"]

    _write(f"\nSelected: {phone_id} ({email})")
    _write(f"Business: {business_name}")

    # Pick random nearby point
    nearby = data.get("nearby_points", [])
    if not nearby:
        _write("ERROR: No nearby coordinates!")
        return 1
    target = random.choice(nearby)
    target_lat, target_lng = target[0], target[1]
    _write(f"Target nearby: {target_lat}, {target_lng}")

    # Cleanup
    _global_cleanup(client)

    # Start phone
    start_ok = False
    for attempt in range(1, MAX_ATTEMPTS + 1):
        _write(f"\n=== Attempt {attempt}/{MAX_ATTEMPTS} ===")
        start_ok = ensure_running(client, phone_id)
        if start_ok:
            break
        if attempt < MAX_ATTEMPTS:
            time.sleep(INTER_PHONE_DELAY)

    if not start_ok:
        _write("FAILED: Phone could not start.")
        return 1

    # Live debug mode
    if args.live_debug:
        dashboard_url = f"https://agent.geelark.com/phone/{phone_id}"
        _write(f"\n*** LIVE DEBUG MODE ***")
        _write(f"Open this URL to watch the phone: {dashboard_url}")
        _write("Press Enter in the terminal to continue...")
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            _write("Continuing without waiting...")

    # Pre-GPS: UI Clear Storage for clean state
    _write("\nPre-GPS: UI Clear Storage...")
    try:
        _ui_clear_storage(phone_id)
    except Exception as e:
        _write(f"  UI Clear Storage warning: {e}")

    # GPS activation using proven A13 method
    _write("\nActivating GPS via _activate_gps_android13()...")
    try:
        gps_ok = _activate_gps_android13(phone_id, float(target_lat), float(target_lng))
    except Exception as e:
        _write(f"  GPS activation error: {e}")
        gps_ok = False

    _write(f"GPS activation result: {gps_ok}")

    # Wait 5s
    time.sleep(5)

    # Maps search
    maps_result = run_maps_search(client, phone_id, business_name)

    # Stop phone
    _write("Stopping phone...")
    _confirm_stop(client, phone_id)

    # Build final log
    final_log = {
        "timestamp": datetime.now().isoformat(),
        "phone_id": phone_id,
        "account_email": email,
        "business_name": business_name,
        "business_lat": data.get("business_lat"),
        "business_lng": data.get("business_lng"),
        "target_nearby_lat": target_lat,
        "target_nearby_lng": target_lng,
        "mock_set_success": gps_ok,
        "maps_search_result": maps_result.get("maps_search_result"),
        "business_found_text": maps_result.get("business_found_text"),
        "screenshot_path": maps_result.get("screenshot_path"),
        "ui_dump_path": maps_result.get("ui_dump_path"),
        "error": maps_result.get("error"),
    }

    with open(_log_file, "w", encoding="utf-8") as f:
        json.dump(final_log, f, indent=2)

    # Append to JSONL
    jsonl_record = {
        "timestamp": final_log["timestamp"],
        "script_name": "gps_maps_test_v2.py",
        "run_id": _timestamp,
        "phone_id": phone_id,
        "account_email": email,
        "business_id": data.get("business_id"),
        "attempt": 1,
        "started_ok": True,
        "overall_status": "GPS_MAPS_TEST",
        "gps_test": {
            "target_lat": target_lat,
            "target_lng": target_lng,
            "mock_success": gps_ok,
            "maps_search_result": maps_result.get("maps_search_result"),
            "screenshot_path": maps_result.get("screenshot_path"),
        },
        "errors": [maps_result["error"]] if maps_result.get("error") else [],
    }
    write_jsonl(jsonl_record)

    # Report
    _write("\n" + "=" * 70)
    _write("RESULTS")
    _write("=" * 70)
    _write(f"Phone:        {phone_id}")
    _write(f"Account:      {email}")
    _write(f"Business:     {business_name}")
    _write(f"Nearby:       {target_lat}, {target_lng}")
    _write(f"GPS mock:     {gps_ok}")
    _write(f"Maps result:  {maps_result.get('maps_search_result')}")
    if maps_result.get("screenshot_path"):
        _write(f"Screenshot:   {maps_result['screenshot_path']}")
    if maps_result.get("error"):
        _write(f"Error:        {maps_result['error']}")
    _write(f"\nJSON:  {_log_file}")
    _write(f"Text:  {_text_log}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
