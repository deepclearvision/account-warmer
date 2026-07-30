import sys
import time

sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")

from core.geelark_client import _post

PHONE_ID = "614216822245294147"
FAKE_GPS_PACKAGE = "com.theappninjas.fakegpsjoystick"
TARGET_LAT = "51.5013"
TARGET_LNG = "-0.0886"

def shell(cmd):
    return _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": cmd})

print("========================================")
print("TEST: Intent-based GPS coord injection")
print(f"Target: {TARGET_LAT}, {TARGET_LNG}")
print("========================================\n")

# First, stop any existing spoof and clear
try:
    shell("am force-stop com.theappninjas.fakegpsjoystick")
    print("Force-stopped Fake GPS")
except Exception as e:
    print(f"Force stop error: {e}")

time.sleep(2)

# Try various intent patterns
intents = [
    # Pattern 1: Common Tasker intent
    f"am broadcast -a com.theappninjas.fakegpsjoystick.UPDATE --ef lat {TARGET_LAT} --ef lng {TARGET_LNG} -n {FAKE_GPS_PACKAGE}/{FAKE_GPS_PACKAGE}.TaskerIntentReceiver",
    # Pattern 2: Direct broadcast to package
    f"am broadcast -a com.theappninjas.fakegpsjoystick.UPDATE --ef lat {TARGET_LAT} --ef lng {TARGET_LNG} -p {FAKE_GPS_PACKAGE}",
    # Pattern 3: Start service with extras
    f"am startservice -n {FAKE_GPS_PACKAGE}/{FAKE_GPS_PACKAGE}.MockLocationProviderService --es lat {TARGET_LAT} --es lng {TARGET_LNG}",
    # Pattern 4: Start main activity with extras
    f"am start -n {FAKE_GPS_PACKAGE}/com.theappninjas.fakegpsjoystick.ui.main.MainActivity --es lat {TARGET_LAT} --es lng {TARGET_LNG}",
    # Pattern 5: Generic set location intent
    f"am broadcast -a com.theappninjas.fakegpsjoystick.SET_LOCATION --ef lat {TARGET_LAT} --ef lng {TARGET_LNG} -p {FAKE_GPS_PACKAGE}",
]

for i, intent_cmd in enumerate(intents, 1):
    print(f"\n--- Attempt {i}: {intent_cmd[:80]}... ---")
    try:
        r = shell(intent_cmd)
        out = r.get("output", "").strip()
        err = r.get("error", "").strip()
        print(f"stdout: {out}")
        print(f"stderr: {err}")
    except Exception as e:
        print(f"ERROR: {e}")

    time.sleep(3)

    # Check dumpsys
    print("  dumpsys mock check:")
    try:
        r = shell("dumpsys location")
        out = r.get("output", "")
        found = False
        for line in out.splitlines():
            if "mock" in line.lower() and "Location[" in line:
                print(f"    {line.strip()}")
                found = True
        if not found:
            print("    (no mock location)")
    except Exception as e:
        print(f"    ERROR: {e}")

print("\n========================================")
print("TEST COMPLETE")
print("========================================")
