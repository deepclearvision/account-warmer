#!/usr/bin/env python3
"""
tiny_business_click_fix.py — Fix the business click by expanding Maps bottom sheet first.

Test: open Maps deeplink -> wait -> swipe up bottom sheet -> click business.
"""
import sys
import time
from pathlib import Path

_repo_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_repo_root / "account-warmer"))

from core.geelark_client import GeelarKClient

PHONE_ID = "614216908010422642"
SEARCH_TERM = "plumber sidcup"
BUSINESS_NAME = "Sidcup Plumbing"

client = GeelarKClient()

# Build flow: open Maps -> wait -> swipe up bottom sheet -> click business
from core.geelark_flow_builder import FlowBuilder
fb = FlowBuilder(
    title="Tiny business click fix",
    desc="Swipe up Maps bottom sheet then click business",
    timeout_minutes=5,
    error_type="skip",
)
fb.open_app("com.google.android.apps.maps", uri=f"geo:0,0?q={SEARCH_TERM.replace(' ', '+')}", remark="Open Maps search")
fb.wait(8000, "Wait for results")
# Swipe UP on bottom sheet (position near bottom center, scroll "bottom" pulls content up)
fb.scroll_down(min_px=900, max_px=1100, remark="Swipe up bottom sheet")
fb.wait(2000, "Wait after swipe")
# Try clicking business name with shorter match
fb.click("text", BUSINESS_NAME, search_time_ms=10_000, remark="Click business in list")
fb.wait(3000, "Wait for business panel")

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
    task_name="biz_click_fix_test",
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
    p = Path("biz_click_fix_screenshot.png")
    p.write_bytes(data)
    print(f"Screenshot saved: {p.resolve()}")
else:
    print("Screenshot returned None.")

print("Done.")
