import sys
import time

sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")

from core.geelark_client import _post

PHONE_ID = "614216822245294147"
FAKE_GPS_PACKAGE = "com.theappninjas.fakegpsjoystick"

def shell(cmd):
    return _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": cmd})

print("========================================")
print("CORRECTED TAP COORDINATES (720x1440)")
print("========================================\n")

# Open app
print("--- Open Fake GPS ---")
try:
    r = shell(f"monkey -p {FAKE_GPS_PACKAGE} -c android.intent.category.LAUNCHER 1")
    print(r.get("output", ""))
except Exception as e:
    print(f"ERROR: {e}")

time.sleep(5)

# Dismiss ad
print("--- Dismiss ad if present ---")
try:
    r = shell("dumpsys activity activities | grep mResumedActivity")
    out = r.get("output", "").strip()
    if "AdActivity" in out:
        shell("input keyevent KEYCODE_BACK")
        time.sleep(2)
        print("Ad dismissed")
    else:
        print(f"Foreground: {out}")
except Exception as e:
    print(f"ERROR: {e}")

# Tap sequence with corrected 720x1440 coordinates
print("\n--- Tap sequence ---")
# Center map at y=600 (middle of upper portion)
sequence = [
    ("Center map (set pin)", 360, 600),
    ("Wait", None, None),
    ("Bottom-left button", 180, 1200),
    ("Wait", None, None),
    ("Bottom-center button", 360, 1200),
    ("Wait", None, None),
    ("Bottom-right button", 540, 1200),
    ("Wait", None, None),
    ("Lower-left (alt)", 180, 1300),
    ("Wait", None, None),
    ("Lower-center (alt)", 360, 1300),
    ("Wait", None, None),
    ("Lower-right (alt)", 540, 1300),
    ("Wait", None, None),
    ("Bottom edge", 360, 1380),
    ("Wait", None, None),
]

for label, x, y in sequence:
    if x is None:
        print(f"  {label}...")
        time.sleep(3)
    else:
        print(f"  {label}: ({x},{y})")
        shell(f"input tap {x} {y}")
        time.sleep(1)

# Check dumpsys
print("\n--- dumpsys after corrected taps ---")
try:
    r = shell("dumpsys location")
    out = r.get("output", "")
    for line in out.splitlines():
        if any(k in line.lower() for k in ["mock", "fakegps", "theappninjas", "controller", "extra package", "last known"]):
            print(line.strip())
except Exception as e:
    print(f"ERROR: {e}")

print("\n========================================")
print("DONE")
print("========================================")
