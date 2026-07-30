import sys
import time

sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")

from core.geelark_client import _post

PHONE_ID = "614216822245294147"
FAKE_GPS_PACKAGE = "com.theappninjas.fakegpsjoystick"

def shell(cmd):
    return _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": cmd})

print("========================================")
print("ACTIVATE MOCK LOCATION — FAKE GPS")
print("========================================\n")

# Ensure app is foreground
print("--- Ensure Fake GPS is foreground ---")
try:
    r = shell(f"monkey -p {FAKE_GPS_PACKAGE} -c android.intent.category.LAUNCHER 1")
    print(f"monkey: {r.get('output', '')}")
except Exception as e:
    print(f"ERROR: {e}")

time.sleep(5)

# Verify foreground
try:
    r = shell("dumpsys activity activities | grep mResumedActivity")
    out = r.get("output", "").strip()
    print(f"Foreground: {out}")
except Exception as e:
    print(f"ERROR: {e}")

# ---------------------------------------------------------------------------
# Tap sequence to set location and start mock
# Fake GPS Joystick layout (typical, 720x1280 screen):
#   Map area: top ~70% of screen
#   Controls: bottom ~30%
#   "Set Location" button: usually bottom-left or center-bottom
#   "Start" button: usually bottom-right or center-bottom
#   First: tap center of map to drop pin
# ---------------------------------------------------------------------------

print("\n--- Tap sequence to activate mock ---")

sequence = [
    ("Center map (drop pin)", 360, 500),
    ("Wait", None, None),
    ("Bottom-left control", 180, 1050),
    ("Wait", None, None),
    ("Bottom-center control", 360, 1050),
    ("Wait", None, None),
    ("Bottom-right control", 540, 1050),
    ("Wait", None, None),
    ("Lower-center (Start?)", 360, 1150),
    ("Wait", None, None),
    ("Lower-left (Set Location?)", 180, 1150),
    ("Wait", None, None),
    ("Lower-right (Start?)", 540, 1150),
    ("Wait", None, None),
    ("Bottom edge center", 360, 1200),
    ("Wait", None, None),
]

for label, x, y in sequence:
    if x is None:
        print(f"  {label}...")
        time.sleep(3)
    else:
        print(f"  {label}: tap ({x},{y})")
        shell(f"input tap {x} {y}")
        time.sleep(1)

# ---------------------------------------------------------------------------
# Check dumpsys after tap sequence
# ---------------------------------------------------------------------------
print("\n--- dumpsys location after activation ---")
try:
    r = shell("dumpsys location")
    out = r.get("output", "")
    relevant = False
    for line in out.splitlines():
        low = line.lower()
        if any(k in low for k in ["mock", "fakegps", "theappninjas", "controller", "extra package", "last known"]):
            print(line.strip())
            relevant = True
    if not relevant:
        print("(no mock-related lines found in dumpsys)")
except Exception as e:
    print(f"ERROR: {e}")

# ---------------------------------------------------------------------------
# Alternative: try using the app's known deeplink or broadcast
# ---------------------------------------------------------------------------
print("\n--- Try app broadcast/deeplink ---")
try:
    # Some Fake GPS versions support broadcast to start mock
    r = shell(f"am broadcast -a android.intent.action.MAIN -n {FAKE_GPS_PACKAGE}/com.theappninjas.fakegpsjoystick.ui.main.MainActivity")
    print(f"broadcast: {r.get('output', '')}")
except Exception as e:
    print(f"ERROR: {e}")

time.sleep(3)

print("\n--- dumpsys after broadcast ---")
try:
    r = shell("dumpsys location")
    out = r.get("output", "")
    for line in out.splitlines():
        if any(k in line.lower() for k in ["mock", "fakegps", "theappninjas", "controller", "extra package", "last known"]):
            print(line.strip())
except Exception as e:
    print(f"ERROR: {e}")

print("\n========================================")
print("ACTIVATION ATTEMPT COMPLETE")
print("========================================")
