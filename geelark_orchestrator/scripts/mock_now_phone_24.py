import sys
import time

sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")

from core.geelark_client import _post

PHONE_ID = "614216822245294147"
FAKE_GPS_PACKAGE = "com.theappninjas.fakegpsjoystick"

def shell(cmd):
    return _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": cmd})

print("--- Verify Fake GPS is foreground ---")
try:
    r = shell("dumpsys activity activities | grep mResumedActivity")
    out = r.get("output", "").strip()
    print(out)
except Exception as e:
    print(f"ERROR: {e}")

# If not foreground, open it
if "fakegpsjoystick" not in out.lower():
    print("\n--- Opening Fake GPS ---")
    shell(f"am start -n {FAKE_GPS_PACKAGE}/com.theappninjas.fakegpsjoystick.ui.main.MainActivity")
    time.sleep(5)

# Dismiss ad if present
try:
    r = shell("dumpsys activity activities | grep mResumedActivity")
    out = r.get("output", "").strip()
    if "AdActivity" in out:
        shell("input keyevent KEYCODE_BACK")
        time.sleep(2)
        print("Ad dismissed")
except Exception as e:
    print(f"ERROR: {e}")

print("\n--- Tapping to set location and start mock ---")
# Tap center of map (set pin)
shell("input tap 360 600")
time.sleep(2)

# Try bottom buttons
for x, y in [(180, 1200), (360, 1200), (540, 1200), (180, 1300), (360, 1300), (540, 1300), (360, 1380)]:
    print(f"  tap ({x},{y})")
    shell(f"input tap {x} {y}")
    time.sleep(2)

print("\nWaiting 5s...")
time.sleep(5)

print("\n--- dumpsys after taps ---")
try:
    r = shell("dumpsys location")
    out = r.get("output", "")
    for line in out.splitlines():
        if any(k in line.lower() for k in ["mock", "fakegps", "theappninjas", "controller", "extra package", "last known"]):
            print(line.strip())
except Exception as e:
    print(f"ERROR: {e}")
