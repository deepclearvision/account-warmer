#!/usr/bin/env python3
"""
tiny_branded_click_test.py — Test click mechanism with branded keyword where
business DEFINITELY appears. Proves click works independently of ranking question.
"""
import json
import sys
import time
from pathlib import Path

_repo_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_repo_root / "account-warmer"))

from core.geelark_client import GeelarKClient

PHONE_ID = "614216908010422642"
BRANDED_TERM = "Sidcup Plumbing and Heating"
BUSINESS_NAME = "Sidcup Plumbing and Heating"

gal_dict = {
    "title": "Tiny branded click test",
    "desc": "Baked branded keyword into Maps search + click business",
    "content": {
        "contentType": "phone",
        "errorType": "skip",
        "isDebug": False,
        "timeOut": "8",
        "contents": [
            {"type": "openApp", "config": {"packgename": "com.google.android.apps.maps", "timeout": 30000, "remark": "Open Google Maps"}},
            {"type": "waitTime", "config": {"timeout": 5000, "timeoutType": "fixedValue"}},
            # Click search box
            {"type": "click", "config": {"serial": 1, "useOffset": False, "searchTime": 10000, "serialType": "fixedValue", "doubleClick": False, "hiddenChildren": False, "randomDistance": 0,
                "filterCollection": [[{"type": "class", "content": "android.widget.TextView", "filterType": "equal"}, {"type": "text", "content": "Search here", "filterType": "equal"}]]}},
            {"type": "waitTime", "config": {"timeout": 1000, "timeoutType": "fixedValue"}},
            # inputContent with BAKED BRANDED keyword
            {"type": "inputContent", "name": "Input branded term", "config": {"clear": True, "serial": 1, "content": [BRANDED_TERM], "simulate": True, "waitTime": 300, "inputType": "taskOrder", "searchTime": 5000, "serialType": "fixedValue", "hiddenChildren": False,
                "filterCollection": [[{"type": "class", "content": "android.widget.EditText", "filterType": "equal"}]]}},
            {"type": "waitTime", "config": {"timeout": 1000, "timeoutType": "fixedValue"}},
            # Submit search (visual-editor style — keyOption, NOT pressKey)
            {"type": "keyOption", "config": {"keyType": "enter", "remark": "Submit search via keyOption"}},
            {"type": "waitTime", "config": {"timeout": 6000, "timeoutType": "fixedValue"}},
            # Scroll results (capped)
            {"type": "scrollPage", "config": {"direction": "bottom", "distanceMin": 500, "distanceMax": 700, "position": [360, 960], "randomWheelSleepTime": [300, 500]}},
            {"type": "waitTime", "config": {"timeout": 2000, "timeoutType": "fixedValue"}},
            # Click business in submitted results list
            {"type": "click", "config": {"serial": 1, "useOffset": False, "searchTime": 8000, "serialType": "fixedValue", "doubleClick": False, "hiddenChildren": False, "randomDistance": 0,
                "filterCollection": [[{"type": "text", "content": BUSINESS_NAME, "filterType": "contain"}]]}},
            {"type": "waitTime", "config": {"timeout": 4000, "timeoutType": "fixedValue"}},
        ],
    },
}

client = GeelarKClient()

print("Importing flow...")
gal_json = json.dumps(gal_dict, ensure_ascii=False)
flow_id = client.import_rpa_flow(gal_json)
print(f"Flow imported: {flow_id}")

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
    param_map={},
    task_name="branded_click_test",
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

print("Taking screenshots...")
time.sleep(2)

# After search results
data = client.take_screenshot(PHONE_ID, max_wait=30)
if data:
    p = Path("branded_click_results.png")
    p.write_bytes(data)
    print(f"Results screenshot: {p.resolve()}")

# After business click
time.sleep(3)
data = client.take_screenshot(PHONE_ID, max_wait=30)
if data:
    p = Path("branded_click_business.png")
    p.write_bytes(data)
    print(f"Business screenshot: {p.resolve()}")

print("Done.")
