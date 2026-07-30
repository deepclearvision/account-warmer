import sys
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")

from core.geelark_client import _post

PHONE_ID = "614216822245294147"
FAKE_GPS_PACKAGE = "com.theappninjas.fakegpsjoystick"

def shell(cmd):
    return _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": cmd})

print("--- Package info ---")
try:
    r = shell(f"pm list packages {FAKE_GPS_PACKAGE}")
    print(r.get("output", "(not installed)"))
except Exception as e:
    print(f"ERROR: {e}")

print("\n--- Package path ---")
try:
    r = shell(f"pm path {FAKE_GPS_PACKAGE}")
    print(r.get("output", "(no path)"))
except Exception as e:
    print(f"ERROR: {e}")

print("\n--- Is package enabled? ---")
try:
    r = shell(f"pm dump {FAKE_GPS_PACKAGE} | grep -i 'enabled\|disabled'")
    print(r.get("output", "(empty)"))
except Exception as e:
    print(f"ERROR: {e}")

print("\n--- Launch via explicit intent ---")
try:
    r = shell(f"am start -n {FAKE_GPS_PACKAGE}/com.theappninjas.fakegpsjoystick.ui.main.MainActivity")
    print(f"stdout: {r.get('output', '')}")
    print(f"stderr: {r.get('error', '')}")
except Exception as e:
    print(f"ERROR: {e}")

import time
time.sleep(5)

print("\n--- Foreground after explicit intent ---")
try:
    r = shell("dumpsys activity activities | grep mResumedActivity")
    print(r.get("output", ""))
except Exception as e:
    print(f"ERROR: {e}")

print("\n--- Clear Play Store from recent and try again ---")
shell("input keyevent KEYCODE_HOME")
time.sleep(1)
shell("input keyevent KEYCODE_APP_SWITCH")
time.sleep(1)
shell("input swipe 360 600 360 100 200")  # swipe up to dismiss recent app
time.sleep(1)
shell("input keyevent KEYCODE_HOME")
time.sleep(1)

print("\n--- Try launching Fake GPS again ---")
try:
    r = shell(f"am start -n {FAKE_GPS_PACKAGE}/com.theappninjas.fakegpsjoystick.ui.main.MainActivity")
    print(f"stdout: {r.get('output', '')}")
    print(f"stderr: {r.get('error', '')}")
except Exception as e:
    print(f"ERROR: {e}")

time.sleep(5)

print("\n--- Foreground after second attempt ---")
try:
    r = shell("dumpsys activity activities | grep mResumedActivity")
    print(r.get("output", ""))
except Exception as e:
    print(f"ERROR: {e}")
