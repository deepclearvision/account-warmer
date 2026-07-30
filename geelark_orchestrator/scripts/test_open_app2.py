"""Test openApp variants for Maps on acc_049."""
import json
import sys
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")

from core.geelark_client import GeelarKClient
from core.geelark_flow_builder import FlowBuilder, _step

MAPS_PACKAGE = "com.google.android.apps.maps"
PHONE_ID = "614216908010422642"

# Variant 1: packgename (current builder spelling)
fb1 = FlowBuilder("Test packgename", "", timeout_minutes=3, error_type="skip")
fb1.open_app(MAPS_PACKAGE, remark="packgename variant")
fb1.wait(5000, "Wait")

# Variant 2: packagename (correct spelling)
fb2 = FlowBuilder("Test packagename", "", timeout_minutes=3, error_type="skip")
fb2.add(_step("openApp", remark="packagename variant", packageName=MAPS_PACKAGE, timeout=30000))
fb2.wait(5000, "Wait")

# Variant 3: click Maps icon on home screen (coord based on screenshot ~220,350)
fb3 = FlowBuilder("Test click icon", "", timeout_minutes=3, error_type="skip")
fb3.click_coord(220, 350, remark="Click Maps icon")
fb3.wait(5000, "Wait")

# Variant 4: click Maps by text
fb4 = FlowBuilder("Test click text", "", timeout_minutes=3, error_type="skip")
fb4.click("text", "Maps", remark="Click Maps text")
fb4.wait(5000, "Wait")

client = GeelarKClient()

variants = [
    ("packgename", fb1),
    ("packagename", fb2),
    ("click_coord", fb3),
    ("click_text", fb4),
]

for name, fb in variants:
    print(f"\n=== Variant: {name} ===")
    gal = fb.build_gal()
    flow_id = client.import_rpa_flow(json.dumps(gal, ensure_ascii=False))
    print(f"flow_id = {flow_id}")
    task_id = client.run_custom_flow(
        flow_id=flow_id,
        phone_id=PHONE_ID,
        param_map={},
        task_name=f"test_{name}",
        schedule_delay=5,
    )
    print(f"task_id = {task_id}")
    import time
    time.sleep(12)
    data = client.take_screenshot(PHONE_ID, max_wait=30)
    if data:
        from pathlib import Path
        p = Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator\logs\screenshots")
        path = p / f"test_{name}.png"
        path.write_bytes(data)
        print(f"Screenshot saved: {path}")
    time.sleep(3)
    items = client.query_tasks([task_id])
    if items:
        t = items[0]
        print(f"status={t.get('status')} cost={t.get('cost')}")
