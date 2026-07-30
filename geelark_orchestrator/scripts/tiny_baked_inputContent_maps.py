#!/usr/bin/env python3
"""
tiny_baked_inputContent_maps.py — Test baked-literal inputContent in Maps search box.

Sequence:
  1. Open Maps (plain openApp, no geo URI)
  2. Click search box
  3. inputContent with BAKED LITERAL "plumber sidcup"
  4. pressKey enter
  5. Wait + screenshot to verify real search results
"""
import json
import sys
import time
from pathlib import Path

_repo_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_repo_root / "account-warmer"))

from core.geelark_client import GeelarKClient

PHONE_ID = "614216908010422642"
SEARCH_TERM = "plumber sidcup"   # BAKED LITERAL, no {var}

gal_dict = {
    "title": "Tiny baked inputContent Maps test",
    "desc": "inputContent with literal string into Maps search box",
    "content": {
        "contentType": "phone",
        "errorType": "skip",
        "isDebug": False,
        "timeOut": "5",
        "contents": [
            {"type": "openApp", "config": {"packgename": "com.google.android.apps.maps", "timeout": 30000, "remark": "Open Google Maps"}},
            {"type": "waitTime", "config": {"timeout": 5000, "timeoutType": "fixedValue"}},
            # Click search box
            {"type": "click", "config": {"serial": 1, "useOffset": False, "searchTime": 10000, "serialType": "fixedValue", "doubleClick": False, "hiddenChildren": False, "randomDistance": 0,
                "filterCollection": [[{"type": "class", "content": "android.widget.TextView", "filterType": "equal"}, {"type": "text", "content": "Search here", "filterType": "equal"}]]}},
            {"type": "waitTime", "config": {"timeout": 1000, "timeoutType": "fixedValue"}},
            # inputContent with BAKED LITERAL
            {"type": "inputContent", "name": "Input search term", "config": {"clear": True, "serial": 1, "content": [SEARCH_TERM], "simulate": True, "waitTime": 300, "inputType": "taskOrder", "searchTime": 5000, "serialType": "fixedValue", "hiddenChildren": False,
                "filterCollection": [[{"type": "class", "content": "android.widget.EditText", "filterType": "equal"}]]}},
            {"type": "waitTime", "config": {"timeout": 1000, "timeoutType": "fixedValue"}},
            # Press Enter
            {"type": "pressKey", "config": {"key": "enter", "remark": "Submit search"}},
            {"type": "waitTime", "config": {"timeout": 8000, "timeoutType": "fixedValue"}},
        ],
    },
}

client = GeelarKClient()

print("Importing flow...")
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
            break

print("Dispatching...")
task_id = client.run_custom_flow(
    flow_id=flow_id,
    phone_id=PHONE_ID,
    param_map={},   # empty — literal is baked into the step
    task_name="baked_inputContent_maps_test",
    schedule_delay=5,
)
print(f"Task id: {task_id}")

print("Polling...")
deadline = time.time() + 300
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

print("Taking screenshot...")
time.sleep(2)
data = client.take_screenshot(PHONE_ID, max_wait=30)
if data:
    p = Path("baked_inputContent_maps_screenshot.png")
    p.write_bytes(data)
    print(f"Screenshot saved: {p.resolve()}")
else:
    print("Screenshot returned None.")

print("Done.")
