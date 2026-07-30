#!/usr/bin/env python3
"""
tiny_baked_literal_test.py — Option (3): bake resolved value into inputContent at build time.

Manually constructs an inputContent step with a LITERAL string (not a placeholder)
and runs it on one phone. If this works, we have a deterministic path forward.
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

# Manually build a tiny flow with inputContent containing a LITERAL value
# Copied exact structure from exported Test A flow, only changing content[]
gal_dict = {
    "title": "Tiny baked literal test",
    "desc": "inputContent with literal string, no substitution",
    "content": {
        "contentType": "phone",
        "errorType": "skip",
        "isDebug": False,
        "timeOut": "5",
        "contents": [
            {
                "type": "openApp",
                "config": {
                    "packgename": "com.google.android.apps.maps",
                    "remark": "Open Google Maps",
                    "timeout": 30000,
                },
            },
            {
                "type": "waitTime",
                "config": {
                    "timeout": 5000,
                    "timeoutType": "fixedValue",
                },
            },
            {
                "type": "click",
                "config": {
                    "remark": "Tap search box",
                    "serial": 1,
                    "useOffset": False,
                    "searchTime": 10000,
                    "serialType": "fixedValue",
                    "doubleClick": False,
                    "hiddenChildren": False,
                    "randomDistance": 0,
                    "filterCollection": [
                        [
                            {"type": "class", "content": "android.widget.TextView", "filterType": "equal"},
                            {"type": "text", "content": "Search here", "filterType": "equal"},
                        ]
                    ],
                },
            },
            {
                "type": "waitTime",
                "config": {
                    "timeout": 1000,
                    "timeoutType": "fixedValue",
                },
            },
            {
                "type": "inputContent",
                "name": "Input Text",
                "config": {
                    "clear": True,
                    "serial": 1,
                    "content": [TEST_TERM],   # <-- LITERAL, no placeholder
                    "simulate": True,
                    "waitTime": 300,
                    "inputType": "taskOrder",
                    "searchTime": 3000,
                    "serialType": "fixedValue",
                    "hiddenChildren": False,
                    "filterCollection": [
                        [
                            {"type": "class", "content": "android.widget.EditText", "filterType": "equal"},
                            {"type": "text", "content": "Search here", "filterType": "equal"},
                        ]
                    ],
                },
            },
            {
                "type": "waitTime",
                "config": {
                    "timeout": 3000,
                    "timeoutType": "fixedValue",
                },
            },
        ],
    },
}

client = GeelarKClient()

print("Importing tiny baked-literal flow...")
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

print(f"Dispatching flow {flow_id} (NO paramMap needed)...")
task_id = client.run_custom_flow(
    flow_id=flow_id,
    phone_id=PHONE_ID,
    param_map={},   # empty — value is baked in
    task_name="baked_literal_test",
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
    if status == 3:
        print("Task completed.")
        break
    if status == 4:
        print(f"Task failed: {t.get('failDesc', t.get('failCode'))}")
        break
    if status == 7:
        print("Task cancelled.")
        break
else:
    print("Poll timed out.")

print("Taking screenshot...")
time.sleep(2)
data = client.take_screenshot(PHONE_ID, max_wait=30)
if data:
    p = Path("baked_literal_screenshot.png")
    p.write_bytes(data)
    print(f"Screenshot saved: {p.resolve()}")
else:
    print("Screenshot returned None.")

print("Done.")
