import sys
import time

sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")

from core.geelark_client import _post

PHONE_ID = "614216822245294147"
FAKE_GPS_PACKAGE = "com.theappninjas.fakegpsjoystick"

def shell(cmd):
    return _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": cmd})

print("--- Check notifications for Fake GPS ---")
try:
    r = shell("dumpsys notification")
    out = r.get("output", "")
    in_package = False
    for line in out.splitlines():
        if FAKE_GPS_PACKAGE in line:
            in_package = True
        if in_package:
            print(line)
            if line.strip() == "" or line.startswith("  NotificationRecord"):
                pass
            if "---" in line and FAKE_GPS_PACKAGE not in line:
                in_package = False
except Exception as e:
    print(f"ERROR: {e}")

print("\n--- Try DPAD center / Enter on Fake GPS ---")
try:
    r = shell(f"monkey -p {FAKE_GPS_PACKAGE} -c android.intent.category.LAUNCHER 1")
    print(f"monkey: {r.get('output', '')}")
except Exception as e:
    print(f"ERROR: {e}")

time.sleep(5)

# Dismiss ad if present
try:
    r = shell("dumpsys activity activities | grep mResumedActivity")
    out = r.get("output", "").strip()
    if "AdActivity" in out:
        shell("input keyevent KEYCODE_BACK")
        time.sleep(2)
        print("Ad dismissed via BACK")
except Exception as e:
    print(f"ERROR: {e}")

# Try DPAD_CENTER in case the Start button has focus
print("\n--- Try DPAD_CENTER / ENTER ---")
shell("input keyevent KEYCODE_DPAD_CENTER")
time.sleep(2)
shell("input keyevent KEYCODE_ENTER")
time.sleep(2)

# Check dumpsys
print("\n--- dumpsys after DPAD_CENTER ---")
try:
    r = shell("dumpsys location")
    out = r.get("output", "")
    for line in out.splitlines():
        if any(k in line.lower() for k in ["mock", "fakegps", "theappninjas", "controller", "extra package", "last known"]):
            print(line.strip())
except Exception as e:
    print(f"ERROR: {e}")

print("\n--- Try swiping down (notification shade) ---")
shell("input swipe 360 50 360 300 200")
time.sleep(2)

# Look for Fake GPS notification and tap it
print("\n--- Check notification shade content ---")
try:
    r = shell("uiautomator dump /sdcard/notif_shade.xml")
    r2 = shell("cat /sdcard/notif_shade.xml")
    xml = r2.get("output", "")
    if xml and "fakegps" in xml.lower():
        print("Fake GPS found in notification shade!")
    else:
        print("No Fake GPS in notification shade (or dump failed)")
except Exception as e:
    print(f"ERROR: {e}")

# Swipe up to dismiss
shell("input swipe 360 300 360 50 200")
time.sleep(1)

print("\nDONE")
