#!/usr/bin/env python3
"""
tiny_combined_chain_test.py — Combined tiny test: Fake GPS (baked literals) -> wait ->
Maps deeplink (baked URI) -> business click (baked name), with per-stage evidence.

Per-stage evidence:
  1. After GPS Start:   dumpsys location + screenshot (verify spoof active)
  2. After deep-link:   screenshot (verify Sidcup results, not Plumstead)
  3. After biz click:   screenshot (verify correct business open)
"""
import json
import sys
import time
from pathlib import Path

_repo_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_repo_root / "account-warmer"))

from core.geelark_client import GeelarKClient, _post

PHONE_ID = "614216908010422642"

# Baked resolved values from a real RunPlan for acc_049
HOME_LAT = "51.436536"
HOME_LNG = "0.101544"
NEARBY_LAT = "51.420702"
NEARBY_LNG = "0.088121"
SEARCH_TERM = "plumber sidcup"
BUSINESS_NAME = "Sidcup Plumbing & Heating"

PKG = "com.theappninjas.fakegpsjoystick"
CONTROL_ID = f"{PKG}:id/control"
SET_BTN_ID = f"{PKG}:id/set_location_button"
START_BTN_ID = f"{PKG}:id/start_button"


def _screenshot(name: str) -> str | None:
    data = client.take_screenshot(PHONE_ID, max_wait=30)
    if not data:
        return None
    p = Path(f"chain_test_{name}.png")
    p.write_bytes(data)
    return str(p.resolve())


def _dumpsys_location() -> str:
    try:
        r = _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "dumpsys location | head -25"}, timeout=30)
        return r.get("output", "(no output)")
    except Exception as e:
        return f"(error: {e})"


# ---------------------------------------------------------------------------
# Build flow with baked literals
# ---------------------------------------------------------------------------
steps = [
    # 0. Open Fake GPS
    {"type": "openApp", "config": {"packgename": PKG, "timeout": 30000, "remark": "Open Fake GPS Joystick"}},
    {"type": "waitTime", "config": {"timeout": 4000, "timeoutType": "fixedValue"}},
    # First-time onboarding guards (skip-tolerant)
    {"type": "click", "config": {"serial": 1, "useOffset": False, "searchTime": 3000, "serialType": "fixedValue", "doubleClick": False, "hiddenChildren": False, "randomDistance": 0,
        "filterCollection": [[{"type": "id", "content": f"{PKG}:id/accept_button", "filterType": "equal"}]]}},
    {"type": "waitTime", "config": {"timeout": 3000, "timeoutType": "fixedValue"}},
    {"type": "click", "config": {"serial": 1, "useOffset": False, "searchTime": 3000, "serialType": "fixedValue", "doubleClick": False, "hiddenChildren": False, "randomDistance": 0,
        "filterCollection": [[{"type": "class", "content": "android.widget.TextView", "filterType": "contain"}, {"type": "text", "content": "Let's Go", "filterType": "contain"}]]}},
    {"type": "waitTime", "config": {"timeout": 3000, "timeoutType": "fixedValue"}},
    {"type": "click", "config": {"serial": 1, "useOffset": False, "searchTime": 3000, "serialType": "fixedValue", "doubleClick": False, "hiddenChildren": False, "randomDistance": 0,
        "filterCollection": [[{"type": "class", "content": "android.widget.TextView", "filterType": "equal"}, {"type": "text", "content": "Allow Notifications", "filterType": "equal"}]]}},
    {"type": "waitTime", "config": {"timeout": 3000, "timeoutType": "fixedValue"}},
    {"type": "click", "config": {"serial": 1, "useOffset": False, "searchTime": 3000, "serialType": "fixedValue", "doubleClick": False, "hiddenChildren": False, "randomDistance": 0,
        "filterCollection": [[{"type": "text", "content": "ALLOW", "filterType": "equal"}]]}},
    {"type": "click", "config": {"serial": 1, "useOffset": False, "searchTime": 3000, "serialType": "fixedValue", "doubleClick": False, "hiddenChildren": False, "randomDistance": 0,
        "filterCollection": [[{"type": "class", "content": "android.widget.TextView", "filterType": "equal"}, {"type": "text", "content": "Grant Location Access", "filterType": "equal"}]]}},
    {"type": "waitTime", "config": {"timeout": 3000, "timeoutType": "fixedValue"}},
    {"type": "click", "config": {"serial": 1, "useOffset": False, "searchTime": 3000, "serialType": "fixedValue", "doubleClick": False, "hiddenChildren": False, "randomDistance": 0,
        "filterCollection": [[{"type": "text", "content": "WHILE USING THE APP", "filterType": "equal"}]]}},
    {"type": "waitTime", "config": {"timeout": 3000, "timeoutType": "fixedValue"}},
    {"type": "click", "config": {"serial": 1, "useOffset": False, "searchTime": 3000, "serialType": "fixedValue", "doubleClick": False, "hiddenChildren": False, "randomDistance": 0,
        "filterCollection": [[{"type": "class", "content": "android.widget.TextView", "filterType": "equal"}, {"type": "text", "content": "Start Using GPS JoyStick", "filterType": "equal"}]]}},
    {"type": "waitTime", "config": {"timeout": 3000, "timeoutType": "fixedValue"}},
    {"type": "click", "config": {"serial": 1, "useOffset": False, "searchTime": 3000, "serialType": "fixedValue", "doubleClick": False, "hiddenChildren": False, "randomDistance": 0,
        "filterCollection": [[{"type": "class", "content": "android.widget.TextView", "filterType": "contain"}, {"type": "text", "content": "Done", "filterType": "contain"}]]}},
    # Click Set Location (home)
    {"type": "click", "config": {"serial": 1, "useOffset": False, "searchTime": 8000, "serialType": "fixedValue", "doubleClick": False, "hiddenChildren": False, "randomDistance": 0,
        "filterCollection": [[{"type": "id", "content": SET_BTN_ID, "filterType": "equal"}]]}},
    # Input home coordinates (BAKED LITERAL via inputContent — known to work in this field)
    {"type": "inputContent", "name": "Input home", "config": {"clear": True, "serial": 1, "content": [f"{HOME_LAT}, {HOME_LNG}"], "simulate": True, "waitTime": 300, "inputType": "taskOrder", "searchTime": 3000, "serialType": "fixedValue", "hiddenChildren": False,
        "filterCollection": [[{"type": "id", "content": CONTROL_ID, "filterType": "equal"}, {"type": "class", "content": "android.widget.EditText", "filterType": "equal"}, {"type": "text", "content": "Latitude, Longitude", "filterType": "equal"}]]}},
    {"type": "waitTime", "config": {"timeout": 3000, "timeoutType": "fixedValue"}},
    {"type": "scrollPage", "config": {"direction": "top", "distanceMin": 500, "distanceMax": 700, "position": [360, 960], "randomWheelSleepTime": [300, 500]}},
    {"type": "waitTime", "config": {"timeout": 2000, "timeoutType": "fixedValue"}},
    # Input altitude
    {"type": "inputContent", "name": "Input altitude", "config": {"clear": True, "serial": 1, "content": ["4"], "simulate": True, "waitTime": 300, "inputType": "taskOrder", "searchTime": 3000, "serialType": "fixedValue", "hiddenChildren": False,
        "filterCollection": [[{"type": "id", "content": CONTROL_ID, "filterType": "equal"}, {"type": "class", "content": "android.widget.EditText", "filterType": "equal"}, {"type": "text", "content": "Altitude", "filterType": "equal"}]]}},
    {"type": "scrollPage", "config": {"direction": "top", "distanceMin": 500, "distanceMax": 700, "position": [360, 960], "randomWheelSleepTime": [300, 500]}},
    {"type": "waitTime", "config": {"timeout": 2000, "timeoutType": "fixedValue"}},
    # Consent / continue guards
    {"type": "click", "config": {"serial": 1, "useOffset": False, "searchTime": 3000, "serialType": "fixedValue", "doubleClick": False, "hiddenChildren": False, "randomDistance": 0,
        "filterCollection": [[{"type": "text", "content": "Consent", "filterType": "equal"}]]}},
    {"type": "click", "config": {"serial": 1, "useOffset": False, "searchTime": 3000, "serialType": "fixedValue", "doubleClick": False, "hiddenChildren": False, "randomDistance": 0,
        "filterCollection": [[{"type": "text", "content": "Continue to app", "filterType": "equal"}]]}},
    {"type": "click", "config": {"serial": 1, "useOffset": False, "searchTime": 3000, "serialType": "fixedValue", "doubleClick": False, "hiddenChildren": False, "randomDistance": 0,
        "filterCollection": [[{"type": "text", "content": "Consent", "filterType": "equal"}]]}},
    {"type": "waitTime", "config": {"timeout": 2000, "timeoutType": "fixedValue"}},
    # Click Set Location (nearby)
    {"type": "click", "config": {"serial": 1, "useOffset": False, "searchTime": 5000, "serialType": "fixedValue", "doubleClick": False, "hiddenChildren": False, "randomDistance": 0,
        "filterCollection": [[{"type": "id", "content": SET_BTN_ID, "filterType": "equal"}]]}},
    # Input nearby coordinates (BAKED LITERAL)
    {"type": "inputContent", "name": "Input nearby", "config": {"clear": True, "serial": 1, "content": [f"{NEARBY_LAT}, {NEARBY_LNG}"], "simulate": True, "waitTime": 300, "inputType": "taskOrder", "searchTime": 3000, "serialType": "fixedValue", "hiddenChildren": False,
        "filterCollection": [[{"type": "id", "content": CONTROL_ID, "filterType": "contain"}, {"type": "text", "content": "Latitude, Longitude", "filterType": "contain"}]]}},
    {"type": "waitTime", "config": {"timeout": 2000, "timeoutType": "fixedValue"}},
    # Input altitude
    {"type": "inputContent", "name": "Input altitude2", "config": {"clear": True, "serial": 1, "content": ["10"], "simulate": True, "waitTime": 300, "inputType": "taskOrder", "searchTime": 3000, "serialType": "fixedValue", "hiddenChildren": False,
        "filterCollection": [[{"type": "id", "content": CONTROL_ID, "filterType": "equal"}, {"type": "text", "content": "Altitude", "filterType": "equal"}]]}},
    {"type": "waitTime", "config": {"timeout": 2000, "timeoutType": "fixedValue"}},
    # Continue / consent guards
    {"type": "click", "config": {"serial": 1, "useOffset": False, "searchTime": 3000, "serialType": "fixedValue", "doubleClick": False, "hiddenChildren": False, "randomDistance": 0,
        "filterCollection": [[{"type": "text", "content": "Continue to app", "filterType": "equal"}]]}},
    {"type": "waitTime", "config": {"timeout": 2000, "timeoutType": "fixedValue"}},
    # Click START
    {"type": "click", "config": {"serial": 1, "useOffset": False, "searchTime": 5000, "serialType": "fixedValue", "doubleClick": False, "hiddenChildren": False, "randomDistance": 0,
        "filterCollection": [[{"type": "id", "content": START_BTN_ID, "filterType": "contain"}, {"type": "text", "content": "START", "filterType": "contain"}]]}},
    {"type": "waitTime", "config": {"timeout": 3000, "timeoutType": "fixedValue"}},
    {"type": "click", "config": {"serial": 1, "useOffset": False, "searchTime": 3000, "serialType": "fixedValue", "doubleClick": False, "hiddenChildren": False, "randomDistance": 0,
        "filterCollection": [[{"type": "text", "content": "Continue to app", "filterType": "equal"}]]}},
    {"type": "waitTime", "config": {"timeout": 2000, "timeoutType": "fixedValue"}},
    {"type": "click", "config": {"serial": 1, "useOffset": False, "searchTime": 3000, "serialType": "fixedValue", "doubleClick": False, "hiddenChildren": False, "randomDistance": 0,
        "filterCollection": [[{"type": "class", "content": "android.widget.Button", "filterType": "equal"}, {"type": "text", "content": "Consent", "filterType": "equal"}]]}},
    {"type": "click", "config": {"serial": 1, "useOffset": False, "searchTime": 5000, "serialType": "fixedValue", "doubleClick": False, "hiddenChildren": False, "randomDistance": 0,
        "filterCollection": [[{"type": "id", "content": START_BTN_ID, "filterType": "contain"}, {"type": "text", "content": "START", "filterType": "contain"}]]}},
    # Explicit 5-8s wait after START before Maps (Master Reference timing rule)
    {"type": "waitTime", "config": {"timeout": 7000, "timeoutType": "fixedValue"}},
    # Maps deep-link with baked search term
    {"type": "openApp", "config": {"packgename": "com.google.android.apps.maps", "uri": f"geo:0,0?q={SEARCH_TERM.replace(' ', '+')}", "timeout": 30000, "remark": f"Maps search: {SEARCH_TERM}"}},
    {"type": "waitTime", "config": {"timeout": 8000, "timeoutType": "fixedValue"}},
    # Scroll to find business
    {"type": "scrollPage", "config": {"direction": "bottom", "distanceMin": 500, "distanceMax": 700, "position": [360, 960], "randomWheelSleepTime": [300, 500]}},
    {"type": "waitTime", "config": {"timeout": 2000, "timeoutType": "fixedValue"}},
    # Click business name (BAKED LITERAL)
    {"type": "click", "config": {"serial": 1, "useOffset": False, "searchTime": 8000, "serialType": "fixedValue", "doubleClick": False, "hiddenChildren": False, "randomDistance": 0,
        "filterCollection": [[{"type": "text", "content": BUSINESS_NAME, "filterType": "contain"}]]}},
    {"type": "waitTime", "config": {"timeout": 4000, "timeoutType": "fixedValue"}},
]

gal_dict = {
    "title": "Tiny combined chain test",
    "desc": "Fake GPS baked literals + Maps geo deeplink + business click",
    "content": {
        "contentType": "phone",
        "errorType": "skip",
        "isDebug": False,
        "timeOut": "15",
        "contents": steps,
    },
}

client = GeelarKClient()

print("Importing combined chain test flow...")
gal_json = json.dumps(gal_dict, ensure_ascii=False)
flow_id = client.import_rpa_flow(gal_json)
print(f"Flow imported: {flow_id}")

# Ensure phone running
statuses = client.get_phone_status([PHONE_ID])
s = statuses[0].get("status", -1) if statuses else -1
if s != 0:
    print("Starting phone...")
    client.start_phone(PHONE_ID)
    deadline = time.time() + 120
    while time.time() < deadline:
        time.sleep(5)
        statuses = client.get_phone_status([PHONE_ID])
        s = statuses[0].get("status", -1) if statuses else -1
        if s == 0:
            print("Phone running.")
            break
    else:
        print("ERROR: phone did not start.")
        sys.exit(1)
else:
    print("Phone already running.")

print("Dispatching...")
task_id = client.run_custom_flow(
    flow_id=flow_id,
    phone_id=PHONE_ID,
    param_map={},
    task_name="combined_chain_test",
    schedule_delay=5,
)
print(f"Task id: {task_id}")

print("Polling...")
deadline = time.time() + 900
while time.time() < deadline:
    time.sleep(10)
    items = client.query_tasks([task_id])
    if not items:
        continue
    t = items[0]
    status = t.get("status")
    if status in (3, 4, 7):
        print(f"Terminal status: {status}")
        break
else:
    print("Poll timed out.")

# ---------------------------------------------------------------------------
# Per-stage evidence
# ---------------------------------------------------------------------------
print("\n=== STAGE 1: Verify GPS spoof active ===")
loc = _dumpsys_location()
print(loc)
p = _screenshot("after_gps")
print(f"Screenshot: {p}")

print("\n=== STAGE 2: Verify Maps results ===")
p = _screenshot("after_maps")
print(f"Screenshot: {p}")

print("\n=== STAGE 3: Verify business open ===")
p = _screenshot("after_business")
print(f"Screenshot: {p}")

print("\nDone.")
