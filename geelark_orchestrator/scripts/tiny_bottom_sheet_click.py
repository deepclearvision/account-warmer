#!/usr/bin/env python3
"""
tiny_bottom_sheet_click.py — Expand Maps bottom sheet and click first result by coord.
"""
import sys
import time
from pathlib import Path

_repo_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_repo_root / "account-warmer"))

from core.geelark_client import GeelarKClient
from core.geelark_flow_builder import FlowBuilder

PHONE_ID = "614216908010422642"
SEARCH_TERM = "plumber sidcup"

client = GeelarKClient()

fb = FlowBuilder(
    title="Tiny bottom sheet click",
    desc="Expand sheet and click first result by coordinate",
    timeout_minutes=5,
    error_type="skip",
)
fb.open_app("com.google.android.apps.maps", uri=f"geo:0,0?q={SEARCH_TERM.replace(' ', '+')}", remark="Open Maps search")
fb.wait(8000, "Wait for results")
# Multiple swipes to expand bottom sheet fully
fb.scroll_down(min_px=900, max_px=1100, remark="Swipe 1")
fb.wait(1000, "Pause")
fb.scroll_down(min_px=900, max_px=1100, remark="Swipe 2")
fb.wait(1000, "Pause")
# Click at coordinate in bottom sheet where first result card appears
fb.click_coord(x=360, y=1050, remark="Click first result in sheet")
fb.wait(4000, "Wait for business panel")

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
    task_name="bottom_sheet_click_test",
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
    p = Path("bottom_sheet_click_screenshot.png")
    p.write_bytes(data)
    print(f"Screenshot saved: {p.resolve()}")
else:
    print("Screenshot returned None.")

print("Done.")
