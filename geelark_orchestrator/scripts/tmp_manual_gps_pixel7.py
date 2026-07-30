import sys
import time
sys.stdout.reconfigure(encoding='utf-8')

sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")
from core.geelark_client import _post

PHONE_ID = "614216911852404803"

print("=== Manual GPS activation on Pixel 7 ===")

# Start overlay service
r = _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "am startservice -n com.theappninjas.fakegpsjoystick/.service.OverlayService"})
print("OverlayService:", r.get("output", "").strip()[:200])
time.sleep(3)

# Open app
r = _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "am start -n com.theappninjas.fakegpsjoystick/com.theappninjas.fakegpsjoystick.MainActivity"})
print("App start:", r.get("output", "").strip()[:200])
time.sleep(5)

# Send deep-link while app is foreground
url = "gpsjoystick://teleport?lat=51.5224&lng=-0.1026"
r = _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": f"am start -a android.intent.action.VIEW -d '{url}' com.theappninjas.fakegpsjoystick"})
print("Deep-link:", r.get("output", "").strip()[:200])
time.sleep(8)

# Check dumpsys
r = _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "dumpsys location"})
out = r.get("output", "")
mock = [l.strip() for l in out.splitlines() if "mock" in l.lower() and "Location[" in l.lower()]
print("Mock:", mock[:2] if mock else "NONE")

# Check focus
r = _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "dumpsys activity activities | grep mResumedActivity"})
print("Focus:", r.get("output", "").strip())

# If still no mock, try UI taps
if not mock:
    print("\nTrying UI taps...")
    _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "input tap 360 720"})
    time.sleep(2)
    _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "input tap 360 1150"})
    time.sleep(3)

    r = _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "dumpsys location"})
    out = r.get("output", "")
    mock = [l.strip() for l in out.splitlines() if "mock" in l.lower() and "Location[" in l.lower()]
    print("Mock after taps:", mock[:2] if mock else "NONE")
