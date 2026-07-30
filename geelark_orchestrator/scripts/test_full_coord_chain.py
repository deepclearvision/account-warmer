"""Test full Maps chain with coordinate-based search box click."""
import json
import sys
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")

from core.geelark_client import GeelarKClient
from core.geelark_flow_builder import FlowBuilder

MAPS_PACKAGE = "com.google.android.apps.maps"
PHONE_ID = "614216908010422642"
SEARCH_TERM = "plumber sidcup"

def _step_input_content(text: str, remark: str = "") -> dict:
    return {
        "type": "inputContent",
        "name": remark or "Input content",
        "config": {
            "clear": True,
            "serial": 1,
            "content": [text],
            "simulate": True,
            "waitTime": 300,
            "inputType": "taskOrder",
            "searchTime": 5000,
            "serialType": "fixedValue",
            "hiddenChildren": False,
            "filterCollection": [
                [{"type": "class", "content": "android.widget.EditText", "filterType": "equal"}]
            ],
        },
    }

fb = FlowBuilder(
    title="Test full coord chain",
    desc="Open Maps, coord-click search, type, submit, screenshot",
    timeout_minutes=5,
    error_type="skip",
)
fb.open_app(MAPS_PACKAGE, remark="Open Maps")
fb.wait(5000, "Wait for Maps")
# Use coordinate click to avoid Play Services prompt interception
fb.click_coord(540, 130, remark="Tap search box by coord")
fb.wait(1000, "Wait after click")
fb.add(_step_input_content(SEARCH_TERM, remark=f"Search: {SEARCH_TERM}"))
fb.wait(1000, "Wait after typing")
fb.key_option("enter", remark="Submit search")
fb.wait(8000, "Wait for results")

client = GeelarKClient()
gal = fb.build_gal()
flow_id = client.import_rpa_flow(json.dumps(gal, ensure_ascii=False))
print(f"flow_id = {flow_id}")
task_id = client.run_custom_flow(
    flow_id=flow_id,
    phone_id=PHONE_ID,
    param_map={},
    task_name="test_full_coord_chain",
    schedule_delay=5,
)
print(f"task_id = {task_id}")

import time
time.sleep(25)

# Screenshot during results phase
data = client.take_screenshot(PHONE_ID, max_wait=30)
if data:
    from pathlib import Path
    p = Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator\logs\screenshots")
    p.mkdir(parents=True, exist_ok=True)
    path = p / "test_full_coord_chain_mid.png"
    path.write_bytes(data)
    print(f"Mid screenshot: {path}")

time.sleep(10)
items = client.query_tasks([task_id])
if items:
    t = items[0]
    print(f"status={t.get('status')} cost={t.get('cost')} failDesc={t.get('failDesc')}")

# Final screenshot
data = client.take_screenshot(PHONE_ID, max_wait=30)
if data:
    path = p / "test_full_coord_chain_final.png"
    path.write_bytes(data)
    print(f"Final screenshot: {path}")
