"""Dismiss the full-screen dialog."""
import sys
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")

from core.geelark_client import GeelarKClient, _post
import time

PHONE_ID = "614216908010422642"

print("=== Press back to dismiss ===")
try:
    r = _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "input keyevent KEYCODE_BACK"})
except Exception as e:
    print(f"Error: {e}")

time.sleep(2)

print("=== Press back again ===")
try:
    r = _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "input keyevent KEYCODE_BACK"})
except Exception as e:
    print(f"Error: {e}")

time.sleep(2)

print("=== Go home ===")
try:
    r = _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "input keyevent KEYCODE_HOME"})
except Exception as e:
    print(f"Error: {e}")

time.sleep(2)

client = GeelarKClient()
data = client.take_screenshot(PHONE_ID, max_wait=30)
if data:
    from pathlib import Path
    p = Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator\logs\screenshots")
    path = p / "dismiss_fullscreen.png"
    path.write_bytes(data)
    print(f"Screenshot: {path}")
