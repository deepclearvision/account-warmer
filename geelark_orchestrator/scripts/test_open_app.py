"""Test openApp for Maps in isolation on acc_049."""
import json
import sys
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")

from core.geelark_client import GeelarKClient
from core.geelark_flow_builder import FlowBuilder

MAPS_PACKAGE = "com.google.android.apps.maps"
PHONE_ID = "614216908010422642"

fb = FlowBuilder(
    title="Test openApp Maps",
    desc="Minimal test: open Maps and wait",
    timeout_minutes=5,
    error_type="skip",
)
fb.open_app(MAPS_PACKAGE, remark="Open Google Maps")
fb.wait(5000, "Wait for Maps to load")

gal = fb.build_gal()
print(json.dumps(gal, indent=2, ensure_ascii=False))

client = GeelarKClient()
print("\nImporting flow...")
flow_id = client.import_rpa_flow(json.dumps(gal, ensure_ascii=False))
print(f"flow_id = {flow_id}")

print("Dispatching...")
task_id = client.run_custom_flow(
    flow_id=flow_id,
    phone_id=PHONE_ID,
    param_map={},
    task_name="test_openapp_maps",
    schedule_delay=5,
)
print(f"task_id = {task_id}")

import time
time.sleep(15)

print("Taking screenshot...")
data = client.take_screenshot(PHONE_ID, max_wait=30)
if data:
    from pathlib import Path
    p = Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator\logs\screenshots")
    p.mkdir(parents=True, exist_ok=True)
    path = p / "test_openapp_maps.png"
    path.write_bytes(data)
    print(f"Screenshot saved: {path}")
else:
    print("Screenshot failed")

print("Querying task...")
items = client.query_tasks([task_id])
if items:
    t = items[0]
    print(f"status={t.get('status')} cost={t.get('cost')} failDesc={t.get('failDesc')}")
