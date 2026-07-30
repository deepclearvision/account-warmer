#!/usr/bin/env python3
"""
GPS + Maps single-phone test.
Sets mock location near business, opens Maps, searches, captures evidence.
"""
import json
import random
import re
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

START_TIMEOUT = 360
MAX_ATTEMPTS = 3
INTER_DELAY = 30
FAKE_GPS_PACKAGE = "com.theappninjas.fakegpsjoystick"

_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
_log_file = _repo_root / "geelark_orchestrator" / "scripts" / f"gps_maps_test_{_timestamp}.json"
_text_log = _repo_root / "geelark_orchestrator" / "scripts" / f"gps_maps_test_{_timestamp}.txt"
_screenshot_dir = _repo_root / "geelark_orchestrator" / "scripts" / f"screenshots_{_timestamp}"
_screenshot_dir.mkdir(parents=True, exist_ok=True)


def _write(line: str) -> None:
    print(line)
    with open(_text_log, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_mapping() -> dict:
    with open(_data_dir / "accounts_business_mapping.json", encoding="utf-8") as f:
        return json.load(f)


def select_random_phone(mapping: dict, provisioned_ids: list[str]) -> dict:
    mapped = []
    for email, data in mapping.items():
        pid = data.get("geelark_phone_id", "")
        if pid in provisioned_ids:
            mapped.append({"phone_id": pid, "email": email, "data": data})
    _write(f"Mapped phones: {len(mapped)}")
    selected = random.choice(mapped)
    return selected


def get_nearby_coords(data: dict) -> list[tuple[str, str]]:
    coords = []
    for i in range(1, 21):
        lat = data.get(f"nearby_{i}_lat", "").strip()
        lng = data.get(f"nearby_{i}_lng", "").strip()
        if lat and lng:
            coords.append((lat, lng))
    return coords


# =====================================================================
# Hardened phone control (from check_fleet_provisioning.py)
# =====================================================================

def _get_running_phones(client: GeelarKClient) -> list[dict]:
    try:
        phones = client.list_phones(page_size=100)
        return [p for p in phones if p.get("status") == 0]
    except Exception:
        return []


def _global_cleanup(client: GeelarKClient) -> None:
    _write("=== Pre-run global cleanup ===")
    running = _get_running_phones(client)
    if not running:
        _write("  No phones running.")
        return
    _write(f"  Stopping {len(running)} phones...")
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
    for attempt in range(1, 4):
        try:
            client.stop_phone(phone_id)
        except Exception:
            pass
        for _ in range(3):
            time.sleep(5)
            try:
                statuses = client.get_phone_status([phone_id])
                if statuses[0].get("status") != 0:
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
            s = client.get_phone_status([phone_id])[0].get("status", -1)
            if s == 0:
                _write("  Phone running.")
                return True
        except Exception:
            pass
    _write("  ERROR: phone did not start within 360s.")
    return False


# =====================================================================
# GPS mock location
# =====================================================================

def set_mock_location(phone_id: str, lat: str, lng: str) -> tuple[bool, str]:
    """Set Fake GPS mock location and verify."""
    _write(f"  Setting mock location to {lat},{lng} ...")

    # 1. Ensure permissions
    _post("/open/v1/shell/execute",
          {"id": phone_id, "cmd": f"appops set {FAKE_GPS_PACKAGE} android:mock_location allow"})
    _post("/open/v1/shell/execute",
          {"id": phone_id, "cmd": f"settings put secure mock_location_app {FAKE_GPS_PACKAGE}"})
    _post("/open/v1/shell/execute",
          {"id": phone_id, "cmd": "settings put secure mock_location 1"})
    time.sleep(1)

    # 2. Start OverlayService
    _post("/open/v1/shell/execute",
          {"id": phone_id, "cmd": f"am startservice -n {FAKE_GPS_PACKAGE}/.service.OverlayService"})
    time.sleep(3)

    # 3. Open app MainActivity first
    _post("/open/v1/shell/execute",
          {"id": phone_id,
           "cmd": f"am start -n {FAKE_GPS_PACKAGE}/{FAKE_GPS_PACKAGE}.MainActivity"})
    time.sleep(4)

    # 4. Deep-link to set coords
    url = f"gpsjoystick://teleport?lat={lat}&lng={lng}"
    _write(f"  Sending deep-link: {url}")
    _post("/open/v1/shell/execute",
          {"id": phone_id,
           "cmd": f"am start -a android.intent.action.VIEW -d '{url}' {FAKE_GPS_PACKAGE}"})
    time.sleep(8)

    # 5. Dismiss any dialog
    _post("/open/v1/shell/execute",
          {"id": phone_id, "cmd": "uiautomator dump /sdcard/gps_dialog.xml"})
    time.sleep(1)
    r = _post("/open/v1/shell/execute",
              {"id": phone_id, "cmd": "cat /sdcard/gps_dialog.xml"})
    xml = r.get("output", "")
    for text in ("Continue to app", "Got it", "OK", "Allow", "Accept", "Dismiss"):
        if text.lower() in xml.lower():
            _write(f"    Dismissing dialog '{text}'")
            # Find approximate center of screen and tap
            _post("/open/v1/shell/execute",
                  {"id": phone_id, "cmd": "input tap 360 900"})
            time.sleep(2)
            break

    # 6. Verify mock in dumpsys
    time.sleep(3)
    return verify_mock_location(phone_id, lat, lng)


def verify_mock_location(phone_id: str, expected_lat: str, expected_lng: str) -> tuple[bool, str]:
    """Check dumpsys location for mock coordinates matching target."""
    try:
        r = _post("/open/v1/shell/execute", {"id": phone_id, "cmd": "dumpsys location"})
        out = r.get("output", "")
        for line in out.splitlines():
            if "mock" in line.lower() and "Location[" in line:
                m = re.search(r"(-?\d+\.\d+),(-?\d+\.\d+)", line)
                if m:
                    found_lat, found_lng = m.group(1), m.group(2)
                    # Check if close enough (0.0002 tolerance)
                    try:
                        match = abs(float(found_lat) - float(expected_lat)) <= 0.0002 and \
                                abs(float(found_lng) - float(expected_lng)) <= 0.0002
                    except Exception:
                        match = False
                    return match, f"{found_lat},{found_lng}"
        return False, "no mock location in dumpsys"
    except Exception as e:
        return False, str(e)


# =====================================================================
# Maps search + evidence capture
# =====================================================================

def run_maps_search(phone_id: str, business_name: str) -> dict:
    result = {
        "maps_search_result": "UNKNOWN",
        "business_found_text": None,
        "screenshot_path": None,
        "ui_dump_path": None,
        "error": None,
    }

    _write("  Opening Google Maps...")
    encoded_name = urllib.parse.quote(business_name)
    geo_url = f"geo:0,0?q={encoded_name}"

    try:
        _post("/open/v1/shell/execute",
              {"id": phone_id,
               "cmd": f"am start -a android.intent.action.VIEW -d '{geo_url}' com.google.android.apps.maps"})
    except Exception as e:
        result["error"] = f"Maps start failed: {e}"
        return result

    _write("  Waiting 8s for Maps to load...")
    time.sleep(8)

    # Take UI dump
    dump_path_local = str(_screenshot_dir / f"{phone_id}_maps_ui.xml")
    screenshot_path_local = str(_screenshot_dir / f"{phone_id}_maps_screen.png")

    try:
        _post("/open/v1/shell/execute",
              {"id": phone_id, "cmd": "uiautomator dump /sdcard/maps_ui.xml"})
        time.sleep(2)
        _post("/open/v1/shell/execute",
              {"id": phone_id, "cmd": "screencap -p /sdcard/maps_screen.png"})
        time.sleep(2)

        # Read UI dump content via shell
        r = _post("/open/v1/shell/execute",
                  {"id": phone_id, "cmd": "cat /sdcard/maps_ui.xml"})
        xml_content = r.get("output", "")

        # Save locally
        with open(dump_path_local, "w", encoding="utf-8") as f:
            f.write(xml_content)
        result["ui_dump_path"] = dump_path_local

        # Check if business name appears in text attributes
        if business_name in xml_content:
            result["maps_search_result"] = "FOUND"
            result["business_found_text"] = business_name
        else:
            # Try partial match (first 3 words)
            words = business_name.split()[:3]
            partial = " ".join(words)
            if partial in xml_content:
                result["maps_search_result"] = "FOUND"
                result["business_found_text"] = partial
            else:
                result["maps_search_result"] = "NOT_FOUND"

        _write(f"  Maps search: {result['maps_search_result']}")

        # Pull screenshot (via Geelark screenshot API if available, else try shell)
        try:
            # Try Geelark screenshot API
            data = client.take_screenshot(phone_id, max_wait=30)
            if data:
                with open(screenshot_path_local, "wb") as f:
                    f.write(data)
                result["screenshot_path"] = screenshot_path_local
                _write(f"  Screenshot saved: {screenshot_path_local}")
        except Exception as e:
            _write(f"  Screenshot via API failed: {e}")
            # Try shell pull
            try:
                r = _post("/open/v1/shell/execute",
                          {"id": phone_id, "cmd": "cat /sdcard/maps_screen.png | base64"})
                b64 = r.get("output", "")
                if b64:
                    import base64
                    img = base64.b64decode(b64)
                    with open(screenshot_path_local, "wb") as f:
                        f.write(img)
                    result["screenshot_path"] = screenshot_path_local
                    _write(f"  Screenshot saved via shell: {screenshot_path_local}")
            except Exception as e2:
                _write(f"  Screenshot shell fallback failed: {e2}")

    except Exception as e:
        result["error"] = str(e)
        _write(f"  ERROR during Maps capture: {e}")

    return result


# =====================================================================
# MAIN
# =====================================================================

def main() -> int:
    global client
    client = GeelarKClient()

    _write("=" * 70)
    _write(f"GPS + MAPS SINGLE-PHONE TEST — {_timestamp}")
    _write("=" * 70)

    # 1. Load mapping
    mapping = load_mapping()

    # 2. Select random provisioned phone
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

    selected = select_random_phone(mapping, provisioned_ids)
    phone_id = selected["phone_id"]
    email = selected["email"]
    data = selected["data"]
    business_name = data["business_name"]

    _write(f"\nSelected phone: {phone_id} ({email})")
    _write(f"Business: {business_name}")

    # 3. Pick random nearby coordinate
    nearby_coords = get_nearby_coords(data)
    if not nearby_coords:
        _write("ERROR: No nearby coordinates available!")
        return 1
    target_lat, target_lng = random.choice(nearby_coords)
    _write(f"Target nearby coords: {target_lat}, {target_lng}")

    # 4. Pre-run cleanup
    _global_cleanup(client)

    # 5. Start phone with retry
    start_ok = False
    for attempt in range(1, MAX_ATTEMPTS + 1):
        _write(f"\n=== Attempt {attempt}/{MAX_ATTEMPTS} to start phone ===")
        start_ok = ensure_running(client, phone_id)
        if start_ok:
            break
        if attempt < MAX_ATTEMPTS:
            time.sleep(INTER_DELAY)

    if not start_ok:
        _write("FAILED: Phone could not be started.")
        return 1

    # 6. Set mock location
    mock_ok, mock_info = set_mock_location(phone_id, target_lat, target_lng)
    _write(f"Mock location result: {mock_ok} | {mock_info}")

    # 7. Wait 5s for GPS fix
    _write("Waiting 5s for GPS fix...")
    time.sleep(5)

    # 8. Maps search
    maps_result = run_maps_search(phone_id, business_name)

    # 9. Stop phone
    _write("Stopping phone...")
    _confirm_stop(client, phone_id)

    # 10. Build final log
    final_log = {
        "timestamp": datetime.now().isoformat(),
        "phone_id": phone_id,
        "account_email": email,
        "business_name": business_name,
        "business_lat": data.get("business_lat"),
        "business_lng": data.get("business_lng"),
        "target_nearby_lat": target_lat,
        "target_nearby_lng": target_lng,
        "mock_set_success": mock_ok,
        "mock_verification_detail": mock_info,
        "maps_search_result": maps_result.get("maps_search_result"),
        "business_found_text": maps_result.get("business_found_text"),
        "screenshot_path": maps_result.get("screenshot_path"),
        "ui_dump_path": maps_result.get("ui_dump_path"),
        "error": maps_result.get("error"),
    }

    with open(_log_file, "w", encoding="utf-8") as f:
        json.dump(final_log, f, indent=2)

    _write("\n" + "=" * 70)
    _write("RESULTS")
    _write("=" * 70)
    _write(f"Phone:        {phone_id}")
    _write(f"Account:      {email}")
    _write(f"Business:     {business_name}")
    _write(f"Nearby coords: {target_lat}, {target_lng}")
    _write(f"Mock success: {mock_ok}")
    _write(f"Maps result:  {maps_result.get('maps_search_result')}")
    if maps_result.get("screenshot_path"):
        _write(f"Screenshot:   {maps_result['screenshot_path']}")
    if maps_result.get("error"):
        _write(f"Error:        {maps_result['error']}")
    _write(f"\nJSON log: {_log_file}")
    _write(f"Text log: {_text_log}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
