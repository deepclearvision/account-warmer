#!/usr/bin/env python3
"""
tiny_baked_full_chain.py — Prove the full chain: Maps deeplink -> click business name.
Bakes the search term and business name as literals into a per-run flow.
"""
import json
import sys
import time
from pathlib import Path

_repo_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_repo_root / "account-warmer"))

from core.geelark_client import GeelarKClient

PHONE_ID = "614216908010422642"
SEARCH_TERM = "plumber sidcup"
BUSINESS_NAME = "Sidcup Plumber"   # visible in search results

gal_dict = {
    "title": "Tiny baked full chain",
    "desc": "Maps deeplink + business click with literal values",
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
                    "uri": f"geo:0,0?q={SEARCH_TERM.replace(' ', '+')}",
                    "timeout": 30000,
                },
            },
            {"type": "waitTime", "config": {"timeout": 8000, "timeoutType": "fixedValue"}},
            # Scroll down to find business listing
            {"type": "scrollPage", "config": {"direction": "bottom", "distanceMin": 500, "distanceMax": 700, "position": [360, 960], "randomWheelSleepTime": [300, 500]}},
            {"type": "waitTime", "config": {"timeout": 2000, "timeoutType": "fixedValue"}},
            # Click business name
            {
                "type": "click",
                "config": {
                    "serial": 1,
                    "useOffset": False,
                    "searchTime": 8000,
                    "serialType": "fixedValue",
                    "doubleClick": False,
                    "hiddenChildren": False,
                    "randomDistance": 0,
                    "filterCollection": [
                        [
                            {"type": "text", "content": BUSINESS_NAME, "filterType": "contain"}
                        ]
                    ],
                },
            },
            {"type": "waitTime", "config": {"timeout": 3000, "timeoutType": "fixedValue"}},
            # Scroll down inside business panel
            {"type": "scrollPage", "config": {"direction": "bottom", "distanceMin": 500, "distanceMax": 700, "position": [360, 960], "randomWheelSleepTime": [300, 500]}},
            {"type": "waitTime", "config": {"timeout": 2000, "timeoutType": "fixedValue"}},
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
    task_name="baked_full_chain_test",
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
    p = Path("baked_full_chain_screenshot.png")
    p.write_bytes(data)
    print(f"Screenshot saved: {p.resolve()}")
else:
    print("Screenshot returned None.")

print("Done.")
