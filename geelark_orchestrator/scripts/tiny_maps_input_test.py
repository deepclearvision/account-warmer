#!/usr/bin/env python3
"""
tiny_maps_input_test.py — Step 1: prove inputText + paramMap works in Maps search.

Builds a tiny flow via the proven geelark_flow_builder, imports it, runs on one
phone, and screenshots the result so we can see what was actually typed.
"""
import sys
import time
from pathlib import Path

_repo_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_repo_root / "account-warmer"))

from core.geelark_client import GeelarKClient
from core.geelark_flow_builder import FlowBuilder

PHONE_ID = "614216908010422642"   # acc_049
TEST_TERM = "plumber sidcup"

# ---------------------------------------------------------------------------
# 1. Build tiny flow
# ---------------------------------------------------------------------------
fb = FlowBuilder(
    title="Tiny Maps inputText test",
    desc="Type a param into Maps search and screenshot result",
    timeout_minutes=5,
    error_type="skip",
)
fb.open_app("com.google.android.apps.maps", remark="Open Google Maps")
fb.wait(5000, "Wait for Maps to load")
fb.click("text", "Search here", search_time_ms=10_000, remark="Tap search box")
fb.wait(1000, "Wait for search field focus")
fb.type_text(
    "{test_term}",
    selector_type="class",
    selector="android.widget.EditText",
    search_time_ms=5_000,
    remark="Type param value into search box",
)
fb.press_enter("Submit search")
fb.wait(8000, "Wait for results to load")
fb.click("text", "Search here", search_time_ms=10_000, remark="Tap search box again for screenshot")
fb.wait(2000, "Pause before screenshot")

print("Importing tiny test flow...")
client = GeelarKClient()
flow_id = fb.save()
print(f"Flow imported: {flow_id}")

# ---------------------------------------------------------------------------
# 2. Ensure phone running
# ---------------------------------------------------------------------------
print(f"Checking phone {PHONE_ID}...")
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

# ---------------------------------------------------------------------------
# 3. Dispatch with paramMap
# ---------------------------------------------------------------------------
print(f"Dispatching flow {flow_id} with paramMap: test_term={TEST_TERM!r}")
task_id = client.run_custom_flow(
    flow_id=flow_id,
    phone_id=PHONE_ID,
    param_map={"test_term": TEST_TERM},
    task_name="tiny_inputText_test",
    schedule_delay=5,
)
print(f"Task id: {task_id}")

# ---------------------------------------------------------------------------
# 4. Poll
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# 5. Screenshot
# ---------------------------------------------------------------------------
print("Taking screenshot...")
data = client.take_screenshot(PHONE_ID, max_wait=30)
if data:
    p = Path("tiny_test_screenshot.png")
    p.write_bytes(data)
    print(f"Screenshot saved: {p.resolve()}")
else:
    print("Screenshot returned None.")

print("Done.")
