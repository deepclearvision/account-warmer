#!/usr/bin/env python3
"""
tiny_maps_inputText_test.py — Step 1: prove inputText + {var} works in Maps search.

Targets Maps search with inputText (not inputContent). Screenshots BEFORE pressing
enter so we can see what was actually typed.
"""
import sys
import time
from pathlib import Path

_repo_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_repo_root / "account-warmer"))

from core.geelark_client import GeelarKClient
from core.geelark_flow_builder import FlowBuilder

PHONE_ID = "614216908010422642"
TEST_TERM = "plumber sidcup"

client = GeelarKClient()

# Build flow using inputText (the builder's type_text uses inputText internally)
fb = FlowBuilder(
    title="Tiny inputText Maps test",
    desc="Type {test_term} into Maps search via inputText and screenshot before enter",
    timeout_minutes=5,
    error_type="skip",
)
fb.open_app("com.google.android.apps.maps", remark="Open Google Maps")
fb.wait(5000, "Wait for Maps")
# Click search box (TextView with text "Search here")
fb.click("text", "Search here", search_time_ms=10_000, remark="Tap search box")
fb.wait(1000, "Wait for focus")
# Use inputText with the Maps search box selector
fb.type_text(
    "{test_term}",
    selector_type="class",
    selector="android.widget.EditText",
    search_time_ms=5_000,
    remark="Type param into search field",
)
fb.wait(3000, "Pause to read what was typed")
# Screenshot step: go home then reopen so we can see state? No — just wait.
# Actually we want to screenshot BEFORE enter, but the flow will end here.

print("Importing flow...")
flow_id = fb.save()
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
    param_map={"test_term": TEST_TERM},
    task_name="inputText_maps_test",
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

# Screenshot immediately after flow ends
print("Taking screenshot...")
time.sleep(2)
data = client.take_screenshot(PHONE_ID, max_wait=30)
if data:
    p = Path("inputText_test_screenshot.png")
    p.write_bytes(data)
    print(f"Screenshot saved: {p.resolve()}")
else:
    print("Screenshot returned None.")

print("Done.")
