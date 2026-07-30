"""Test Maps idle - does it auto-navigate to Play Store?"""
import json
import sys
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")

from core.geelark_client import GeelarKClient
from core.geelark_flow_builder import FlowBuilder

MAPS_PACKAGE = "com.google.android.apps.maps"
PHONE_ID = "614216908010422642"

fb = FlowBuilder(
    title="Test Maps idle",
    desc="Open Maps, wait 15s, screenshot",
    timeout_minutes=3,
    error_type="skip",
)
fb.open_app(MAPS_PACKAGE, remark="Open Maps")
fb.wait(15000, "Wait 15s idle")

client = GeelarKClient()
gal = fb.build_gal()
flow_id = client.import_rpa_flow(json.dumps(gal, ensure_ascii=False))
task_id = client.run_custom_flow(
    flow_id=flow_id,
    phone_id=PHONE_ID,
    param_map={},
    task_name="test_maps_idle",
    schedule_delay=5,
)

import time
time.sleep(25)
data = client.take_screenshot(PHONE_ID, max_wait=30)
if data:
    from pathlib import Path
    p = Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator\logs\screenshots")
    path = p / "test_maps_idle.png"
    path.write_bytes(data)
    print(f"Screenshot saved: {path}")

items = client.query_tasks([task_id])
if items:
    t = items[0]
    print(f"status={t.get('status')} cost={t.get('cost')} failDesc={t.get('failDesc')}")
