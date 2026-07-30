#!/usr/bin/env python3
"""
tiny_coord_input_test.py — Test inputText with inputMode:coord on Maps search bar.
Known screen: 720x1440. Search bar is near top center (~x=360, y=130).
"""
import json
import sys
import time
from pathlib import Path

_repo_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_repo_root / "account-warmer"))

from core.geelark_client import GeelarKClient

PHONE_ID = "614216908010422642"
TEST_TERM = "plumber sidcup"

client = GeelarKClient()

# Build flow: open Maps -> wait -> click coord on search bar -> inputText coord with literal
gal_dict = {
    "title": "Tiny coord input test",
    "desc": "inputText coord mode on Maps search bar",
    "content": {
        "contentType": "phone",
        "errorType": "skip",
        "isDebug": False,
        "timeOut": "5",
        "contents": [
            {
                "type": "openApp",
                "config": {"packgename": "com.google.android.apps.maps", "timeout": 30000},
            },
            {"type": "waitTime", "config": {"timeout": 5000, "timeoutType": "fixedValue"}},
            # Click search bar area by coordinate (top center-ish)
            {"type": "clickCoord", "config": {"x": 360, "y": 130, "remark": "Tap search bar"}},
            {"type": "waitTime", "config": {"timeout": 1000, "timeoutType": "fixedValue"}},
            # Type literal via coord mode
            {
                "type": "inputText",
                "config": {
                    "x": 360, "y": 130,
                    "text": TEST_TERM,
                    "clearText": True,
                    "inputMode": "coord",
                    "remark": "Type literal at coord",
                },
            },
            {"type": "waitTime", "config": {"timeout": 3000, "timeoutType": "fixedValue"}},
        ],
    },
}

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
    task_name="coord_input_test",
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
    p = Path("coord_input_screenshot.png")
    p.write_bytes(data)
    print(f"Screenshot saved: {p.resolve()}")
else:
    print("Screenshot returned None.")

print("Done.")
