"""Test coordinate-based click on search box to avoid Play Store interception."""
import json
import sys
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")

from core.geelark_client import GeelarKClient
from core.geelark_flow_builder import FlowBuilder

MAPS_PACKAGE = "com.google.android.apps.maps"
PHONE_ID = "614216908010422642"

fb = FlowBuilder(
    title="Test coord click",
    desc="Open Maps, click search box by coordinate (~540,130)",
    timeout_minutes=3,
    error_type="skip",
)
fb.open_app(MAPS_PACKAGE, remark="Open Maps")
fb.wait(5000, "Wait for Maps")
# Click at the search box area (top center, around y=130)
fb.click_coord(540, 130, remark="Tap search box by coord")
fb.wait(2000, "Wait after click")

client = GeelarKClient()
gal = fb.build_gal()
flow_id = client.import_rpa_flow(json.dumps(gal, ensure_ascii=False))
task_id = client.run_custom_flow(
    flow_id=flow_id,
    phone_id=PHONE_ID,
    param_map={},
    task_name="test_coord_click",
    schedule_delay=5,
)

import time
time.sleep(15)
data = client.take_screenshot(PHONE_ID, max_wait=30)
if data:
    from pathlib import Path
    p = Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator\logs\screenshots")
    p.mkdir(parents=True, exist_ok=True)
    path = p / "test_coord_click.png"
    path.write_bytes(data)
    print(f"Screenshot: {path}")

time.sleep(5)
items = client.query_tasks([task_id])
if items:
    t = items[0]
    print(f"status={t.get('status')} cost={t.get('cost')} failDesc={t.get('failDesc')}")
