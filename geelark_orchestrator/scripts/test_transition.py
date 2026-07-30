"""Test Fake GPS -> Maps transition with Privacy Policy screen."""
import json
import sys
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")

from core.geelark_client import GeelarKClient
from core.geelark_flow_builder import FlowBuilder

FAKE_GPS_PACKAGE = "com.theappninjas.fakegpsjoystick"
MAPS_PACKAGE = "com.google.android.apps.maps"
PHONE_ID = "614216908010422642"

fb = FlowBuilder(
    title="Test transition",
    desc="Open Fake GPS, then open Maps",
    timeout_minutes=5,
    error_type="skip",
)
fb.open_app(FAKE_GPS_PACKAGE, remark="Open Fake GPS")
fb.wait(5000, "Wait for Fake GPS")
fb.open_app(MAPS_PACKAGE, remark="Open Maps from Fake GPS")
fb.wait(5000, "Wait for Maps")

client = GeelarKClient()
gal = fb.build_gal()
flow_id = client.import_rpa_flow(json.dumps(gal, ensure_ascii=False))
print(f"flow_id = {flow_id}")
task_id = client.run_custom_flow(
    flow_id=flow_id,
    phone_id=PHONE_ID,
    param_map={},
    task_name="test_transition",
    schedule_delay=5,
)
print(f"task_id = {task_id}")

import time
time.sleep(20)
data = client.take_screenshot(PHONE_ID, max_wait=30)
if data:
    from pathlib import Path
    p = Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator\logs\screenshots")
    path = p / "test_transition.png"
    path.write_bytes(data)
    print(f"Screenshot saved: {path}")

time.sleep(5)
items = client.query_tasks([task_id])
if items:
    t = items[0]
    print(f"status={t.get('status')} cost={t.get('cost')} failDesc={t.get('failDesc')}")
