import sys
import time

sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")

from core.geelark_client import _post

PHONE_ID = "614216822245294147"
FAKE_GPS_PACKAGE = "com.theappninjas.fakegpsjoystick"

def shell(cmd):
    return _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": cmd})

print("--- App state ---")
try:
    r = shell("dumpsys activity activities | grep mResumedActivity")
    print(r.get("output", ""))
except Exception as e:
    print(f"ERROR: {e}")

# If not open, open it
if "fakegpsjoystick" not in r.get("output", "").lower():
    shell(f"am start -n {FAKE_GPS_PACKAGE}/com.theappninjas.fakegpsjoystick.ui.main.MainActivity")
    time.sleep(5)

# Dismiss ad
if "AdActivity" in r.get("output", ""):
    shell("input keyevent KEYCODE_BACK")
    time.sleep(2)

print("\n--- Desperate tap grid (covering whole bottom half) ---")
# Cover bottom half in a grid pattern
for y in range(700, 1441, 100):
    for x in range(60, 661, 150):
        print(f"  tap ({x},{y})")
        shell(f"input tap {x} {y}")
        time.sleep(1)

print("\nWaiting 5s...")
time.sleep(5)

print("\n--- dumpsys ---")
try:
    r = shell("dumpsys location")
    out = r.get("output", "")
    for line in out.splitlines():
        if any(k in line.lower() for k in ["mock", "fakegps", "theappninjas", "controller", "extra package", "last known"]):
            print(line.strip())
except Exception as e:
    print(f"ERROR: {e}")
