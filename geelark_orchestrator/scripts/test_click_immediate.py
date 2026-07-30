"""Test click Search here and screenshot immediately after."""
import json
import sys
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")

from core.geelark_client import GeelarKClient
from core.geelark_flow_builder import FlowBuilder

MAPS_PACKAGE = "com.google.android.apps.maps"
PHONE_ID = "614216908010422642"

fb = FlowBuilder(
    title="Test click immediate",
    desc="Open Maps, click Search here, screenshot immediately",
    timeout_minutes=3,
    error_type="skip",
)
fb.open_app(MAPS_PACKAGE, remark="Open Maps")
fb.wait(5000, "Wait for Maps")
fb.click("text", "Search here", search_time_ms=10000, remark="Tap search box")
# No wait after click - take screenshot via API immediately

client = GeelarKClient()
gal = fb.build_gal()
flow_id = client.import_rpa_flow(json.dumps(gal, ensure_ascii=False))
task_id = client.run_custom_flow(
    flow_id=flow_id,
    phone_id=PHONE_ID,
    param_map={},
    task_name="test_click_immediate",
    schedule_delay=5,
)

import time
time.sleep(12)
# Screenshot during flow execution
data = client.take_screenshot(PHONE_ID, max_wait=30)
if data:
    from pathlib import Path
    p = Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator\logs\screenshots")
    path = p / "test_click_immediate_during.png"
    path.write_bytes(data)
    print(f"During screenshot saved: {path}")

time.sleep(10)
items = client.query_tasks([task_id])
if items:
    t = items[0]
    print(f"status={t.get('status')} cost={t.get('cost')} failDesc={t.get('failDesc')}")

data = client.take_screenshot(PHONE_ID, max_wait=30)
if data:
    path = p / "test_click_immediate_final.png"
    path.write_bytes(data)
    print(f"Final screenshot saved: {path}")
