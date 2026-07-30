"""Clean test: packagename + click Search here, one task only."""
import json
import sys
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")

from core.geelark_client import GeelarKClient
from core.geelark_flow_builder import FlowBuilder, _step

MAPS_PACKAGE = "com.google.android.apps.maps"
PHONE_ID = "614216908010422642"

fb = FlowBuilder(
    title="Clean packagename click",
    desc="Use correct packagename spelling and click Search here",
    timeout_minutes=3,
    error_type="skip",
)
# Use correct spelling: packageName
fb.add(_step("openApp", remark="Open Maps (packagename)", packageName=MAPS_PACKAGE, timeout=30000))
fb.wait(5000, "Wait for Maps")
fb.click("text", "Search here", search_time_ms=10000, remark="Tap search box")
fb.wait(2000, "Wait after click")

client = GeelarKClient()
gal = fb.build_gal()
flow_id = client.import_rpa_flow(json.dumps(gal, ensure_ascii=False))
print(f"flow_id = {flow_id}")
task_id = client.run_custom_flow(
    flow_id=flow_id,
    phone_id=PHONE_ID,
    param_map={},
    task_name="clean_packagename_click",
    schedule_delay=5,
)
print(f"task_id = {task_id}")

import time
# Screenshot DURING execution (while click step should be running)
time.sleep(10)
data = client.take_screenshot(PHONE_ID, max_wait=30)
if data:
    from pathlib import Path
    p = Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator\logs\screenshots")
    p.mkdir(parents=True, exist_ok=True)
    path = p / "clean_packagename_during.png"
    path.write_bytes(data)
    print(f"During screenshot: {path}")

# Wait for completion
time.sleep(15)
items = client.query_tasks([task_id])
if items:
    t = items[0]
    print(f"status={t.get('status')} cost={t.get('cost')} failDesc={t.get('failDesc')}")

# Final screenshot
data = client.take_screenshot(PHONE_ID, max_wait=30)
if data:
    path = p / "clean_packagename_final.png"
    path.write_bytes(data)
    print(f"Final screenshot: {path}")
