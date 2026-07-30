#!/usr/bin/env python3
"""
tiny_business_geo_test.py — Open Maps centered on business coordinates with business name.
If this opens the business card directly, the click problem is solved.
"""
import sys
import time
from pathlib import Path

_repo_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_repo_root / "account-warmer"))

from core.geelark_client import GeelarKClient
from core.geelark_flow_builder import FlowBuilder

PHONE_ID = "614216908010422642"
BUSINESS_LAT = "51.427684"
BUSINESS_LNG = "0.101638"
BUSINESS_NAME = "Sidcup Plumbing and Heating"

client = GeelarKClient()

fb = FlowBuilder(
    title="Tiny business geo test",
    desc="Open Maps centered on business coords with business name query",
    timeout_minutes=5,
    error_type="skip",
)
# Open Maps directly centered on business location
fb.open_app("com.google.android.apps.maps", uri=f"geo:{BUSINESS_LAT},{BUSINESS_LNG}?q={BUSINESS_NAME.replace(' ', '+')}", remark="Open Maps at business location")
fb.wait(8000, "Wait for Maps")
# Small scroll to ensure panel is visible
fb.scroll_down(min_px=200, max_px=400, remark="Minor scroll")
fb.wait(2000, "Pause")

print("Importing flow...")
flow_id = fb.save()
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
    task_name="business_geo_test",
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
    p = Path("business_geo_screenshot.png")
    p.write_bytes(data)
    print(f"Screenshot saved: {p.resolve()}")
else:
    print("Screenshot returned None.")

print("Done.")
