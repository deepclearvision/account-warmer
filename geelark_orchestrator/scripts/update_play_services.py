"""Try to update Play Services by clicking the Update button."""
import sys
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")

from core.geelark_client import GeelarKClient, _post
import time

PHONE_ID = "614216908010422642"
client = GeelarKClient()

# First, open Maps via ADB to trigger the update prompt
print("=== Open Maps via ADB ===")
try:
    r = _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "am start -n com.google.android.apps.maps/com.google.android.maps.MapsActivity"})
    print(f"Output: {r.get('output', '')}")
except Exception as e:
    print(f"Error: {e}")

time.sleep(5)

# Screenshot to see state
data = client.take_screenshot(PHONE_ID, max_wait=30)
if data:
    from pathlib import Path
    p = Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator\logs\screenshots")
    p.mkdir(parents=True, exist_ok=True)
    path = p / "update_ps_step1.png"
    path.write_bytes(data)
    print(f"Step 1 screenshot: {path}")

# If there's an update prompt, click Update
print("=== Try clicking Update button (coord ~540,440) ===")
try:
    r = _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "input tap 540 440"})
    print(f"Output: {r.get('output', '')}")
except Exception as e:
    print(f"Error: {e}")

time.sleep(10)

# Screenshot after clicking update
data = client.take_screenshot(PHONE_ID, max_wait=30)
if data:
    path = p / "update_ps_step2.png"
    path.write_bytes(data)
    print(f"Step 2 screenshot: {path}")

# Wait for update to progress
time.sleep(30)

# Screenshot after waiting
data = client.take_screenshot(PHONE_ID, max_wait=30)
if data:
    path = p / "update_ps_step3.png"
    path.write_bytes(data)
    print(f"Step 3 screenshot: {path}")

print("Done")
