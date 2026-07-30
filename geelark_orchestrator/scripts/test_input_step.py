"""Test which step triggers Play Store: inputContent or keyOption."""
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

# Test A: open + coord click + inputContent only
fb_a = FlowBuilder("Test input only", "", timeout_minutes=3, error_type="skip")
fb_a.open_app(MAPS_PACKAGE, remark="Open Maps")
fb_a.wait(5000, "Wait")
fb_a.click_coord(540, 160, remark="Tap search")
fb_a.wait(1000, "Wait")
fb_a.add(_step_input_content(SEARCH_TERM, remark="Type search"))
fb_a.wait(3000, "Wait after input")

# Test B: open + coord click + inputContent + keyOption
fb_b = FlowBuilder("Test input + enter", "", timeout_minutes=3, error_type="skip")
fb_b.open_app(MAPS_PACKAGE, remark="Open Maps")
fb_b.wait(5000, "Wait")
fb_b.click_coord(540, 160, remark="Tap search")
fb_b.wait(1000, "Wait")
fb_b.add(_step_input_content(SEARCH_TERM, remark="Type search"))
fb_b.wait(1000, "Wait")
fb_b.key_option("enter", remark="Submit")
fb_b.wait(5000, "Wait after submit")

client = GeelarKClient()

for name, fb in [("input_only", fb_a), ("input_enter", fb_b)]:
    print(f"\n=== {name} ===")
    gal = fb.build_gal()
    flow_id = client.import_rpa_flow(json.dumps(gal, ensure_ascii=False))
    task_id = client.run_custom_flow(
        flow_id=flow_id,
        phone_id=PHONE_ID,
        param_map={},
        task_name=f"test_{name}",
        schedule_delay=5,
    )
    print(f"task_id = {task_id}")
    import time
    time.sleep(20)
    data = client.take_screenshot(PHONE_ID, max_wait=30)
    if data:
        from pathlib import Path
        p = Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator\logs\screenshots")
        path = p / f"test_{name}.png"
        path.write_bytes(data)
        print(f"Screenshot: {path}")
    time.sleep(5)
    items = client.query_tasks([task_id])
    if items:
        t = items[0]
        print(f"status={t.get('status')} cost={t.get('cost')}")
