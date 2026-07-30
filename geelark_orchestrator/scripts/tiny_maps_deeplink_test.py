#!/usr/bin/env python3
"""
tiny_maps_deeplink_test.py — Test opening Maps directly to a search URI.
If this works, we can bake the search term into the URI at build time (Option 3).
"""
import sys
import time
from pathlib import Path

_repo_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_repo_root / "account-warmer"))

from core.geelark_client import GeelarKClient

PHONE_ID = "614216908010422642"
SEARCH_TERM = "plumber sidcup"

client = GeelarKClient()

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

# Build tiny flow: open Maps with search URI, wait, screenshot
from core.geelark_flow_builder import FlowBuilder
fb = FlowBuilder(
    title="Tiny Maps deeplink test",
    desc="Open Maps directly to search results via URI",
    timeout_minutes=5,
    error_type="skip",
)
# Try geo: URI first
geo_uri = f"geo:0,0?q={SEARCH_TERM.replace(' ', '+')}"
fb.open_app("com.google.android.apps.maps", uri=geo_uri, remark=f"Open Maps search: {SEARCH_TERM}")
fb.wait(8000, "Wait for results")

print("Importing flow...")
flow_id = fb.save()
print(f"Flow imported: {flow_id}")

print("Dispatching...")
task_id = client.run_custom_flow(
    flow_id=flow_id,
    phone_id=PHONE_ID,
    param_map={},
    task_name="maps_deeplink_test",
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
    p = Path("maps_deeplink_screenshot.png")
    p.write_bytes(data)
    print(f"Screenshot saved: {p.resolve()}")
else:
    print("Screenshot returned None.")

print("Done.")
