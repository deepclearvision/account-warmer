import time
import sys
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")
from core.geelark_client import _post

PHONE_ID = "614216903581237315"
FAKE_GPS_PACKAGE = "com.theappninjas.fakegpsjoystick"

def shell(cmd):
    return _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": cmd})

print("Opening Fake GPS...")
shell(f"am start -n {FAKE_GPS_PACKAGE}/com.theappninjas.fakegpsjoystick.MainActivity")
time.sleep(8)

print("Starting overlay service...")
shell(f"am startservice -n {FAKE_GPS_PACKAGE}/.service.OverlayService")
time.sleep(5)

print("Sending deep-link...")
url = "gpsjoystick://teleport?lat=51.5224&lng=-0.1026"
r = shell(f"am start -a android.intent.action.VIEW -d '{url}' {FAKE_GPS_PACKAGE}")
print("result:", r.get("output", "").strip()[:200])
time.sleep(10)

print("Checking dumpsys...")
r = shell("dumpsys location")
out = r.get("output", "")
mock = [l.strip() for l in out.splitlines() if "mock" in l.lower() and "Location[" in l.lower()]
print("Mock lines:", mock[:3] if mock else "NONE")

# If still no mock, try UI taps on the app
if not mock:
    print("\nNo mock from deep-link. Trying UI taps...")
    shell("input tap 360 720")  # center - set pin
    time.sleep(2)
    shell("input tap 360 1150")  # start button area
    time.sleep(3)
    shell("input tap 600 1200")  # alternative start area
    time.sleep(3)

    r = shell("dumpsys location")
    out = r.get("output", "")
    mock = [l.strip() for l in out.splitlines() if "mock" in l.lower() and "Location[" in l.lower()]
    print("Mock lines after taps:", mock[:3] if mock else "NONE")

# Return home
shell("input keyevent KEYCODE_HOME")
