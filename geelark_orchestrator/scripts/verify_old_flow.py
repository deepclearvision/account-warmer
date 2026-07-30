#!/usr/bin/env python3
"""
verify_old_flow.py — Test if the OLD Test A Maps flow substitutes paramMap correctly.
"""
import sys
from pathlib import Path

_repo_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_repo_root / "account-warmer"))

from core.geelark_client import GeelarKClient

PHONE_ID = "614216908010422642"
OLD_TEST_A_FLOW = "620084214733209921"

client = GeelarKClient()

# Start phone if needed
statuses = client.get_phone_status([PHONE_ID])
s = statuses[0].get("status", -1) if statuses else -1
if s != 0:
    print("Starting phone...")
    client.start_phone(PHONE_ID)
    import time
    deadline = time.time() + 120
    while time.time() < deadline:
        time.sleep(5)
        statuses = client.get_phone_status([PHONE_ID])
        s = statuses[0].get("status", -1) if statuses else -1
        if s == 0:
            break

params = {
    "business_name": "Sidcup Plumbing & Heating",
    "search_term": "plumber sidcup",
    "nearby_lat": "51.420702",
    "nearby_lng": "0.088121",
    "home_lat": "51.436536",
    "home_lng": "0.101544",
    "business_lat": "51.427684",
    "business_lng": "0.101638",
    "work_lat": "",
    "work_lng": "",
    "account_email": "eddievilla9987@gmail.com",
}

print(f"Dispatching OLD Test A flow {OLD_TEST_A_FLOW} with paramMap...")
task_id = client.run_custom_flow(
    flow_id=OLD_TEST_A_FLOW,
    phone_id=PHONE_ID,
    param_map=params,
    task_name="verify_old_test_a",
    schedule_delay=5,
)
print(f"Task id: {task_id}")

print("Polling...")
import time
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
    p = Path("verify_old_flow_screenshot.png")
    p.write_bytes(data)
    print(f"Screenshot saved: {p.resolve()}")
else:
    print("Screenshot returned None.")

print("Done.")
