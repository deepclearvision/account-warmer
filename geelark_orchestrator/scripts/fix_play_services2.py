"""Try more aggressive fix for Play Services update prompt."""
import sys
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")

from core.geelark_client import GeelarKClient, _post

PHONE_ID = "614216908010422642"

print("=== Press back to exit Play Store ===")
try:
    r = _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "input keyevent KEYCODE_BACK"})
    print(f"Output: {r.get('output', '')}")
except Exception as e:
    print(f"Error: {e}")

import time
time.sleep(2)

print("=== Press back again ===")
try:
    r = _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "input keyevent KEYCODE_BACK"})
    print(f"Output: {r.get('output', '')}")
except Exception as e:
    print(f"Error: {e}")

time.sleep(2)

print("=== Go home ===")
try:
    r = _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "input keyevent KEYCODE_HOME"})
    print(f"Output: {r.get('output', '')}")
except Exception as e:
    print(f"Error: {e}")

time.sleep(2)

print("=== Dismiss all notifications ===")
try:
    r = _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "service call statusbar 2"})
    print(f"Output: {r.get('output', '')}")
except Exception as e:
    print(f"Error: {e}")

time.sleep(2)

print("=== Check foreground activity ===")
try:
    r = _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "dumpsys window windows | grep -E 'mCurrentFocus|mFocusedApp'"})
    print(f"Output: {r.get('output', '')}")
except Exception as e:
    print(f"Error: {e}")

print()
print("=== Screenshot ===")
client = GeelarKClient()
data = client.take_screenshot(PHONE_ID, max_wait=30)
if data:
    from pathlib import Path
    p = Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator\logs\screenshots")
    p.mkdir(parents=True, exist_ok=True)
    path = p / "fix_play_services2.png"
    path.write_bytes(data)
    print(f"Screenshot saved: {path}")
