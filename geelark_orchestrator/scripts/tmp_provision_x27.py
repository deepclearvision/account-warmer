import time
import sys
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")
from core.geelark_client import _post

PHONE_ID = "614216890796998723"
FAKE_GPS_PACKAGE = "com.theappninjas.fakegpsjoystick"

def shell(cmd):
    return _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": cmd})

print(f"=== Provisioning X27 ({PHONE_ID}) ===")

for cmd in [
    f"appops set {FAKE_GPS_PACKAGE} android:mock_location allow",
    f"settings put secure mock_location_app {FAKE_GPS_PACKAGE}",
    "settings put secure mock_location 1",
    "settings put secure location_mode 3",
]:
    try:
        r = shell(cmd)
        print(f"  {cmd} -> {r.get('output', '').strip() or 'OK'}")
    except Exception as e:
        print(f"  ERROR: {e}")
    time.sleep(0.5)

print("\nOpening Fake GPS...")
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

if not mock:
    print("\nTrying UI taps...")
    shell("input tap 360 720")
    time.sleep(2)
    shell("input tap 360 1150")
    time.sleep(3)
    shell("input tap 600 1200")
    time.sleep(3)
    r = shell("dumpsys location")
    out = r.get("output", "")
    mock = [l.strip() for l in out.splitlines() if "mock" in l.lower() and "Location[" in l.lower()]
    print("Mock lines after taps:", mock[:3] if mock else "NONE")

shell("input keyevent KEYCODE_HOME")
print("\n=== Done ===")
