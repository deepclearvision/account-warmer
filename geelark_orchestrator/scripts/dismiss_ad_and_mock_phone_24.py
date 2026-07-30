import sys
import time

sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")

from core.geelark_client import _post

PHONE_ID = "614216822245294147"
FAKE_GPS_PACKAGE = "com.theappninjas.fakegpsjoystick"

def shell(cmd):
    return _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": cmd})

print("========================================")
print("DISMISS AD + ACTIVATE MOCK")
print("========================================\n")

# Open app
print("--- Open Fake GPS ---")
try:
    r = shell(f"monkey -p {FAKE_GPS_PACKAGE} -c android.intent.category.LAUNCHER 1")
    print(r.get("output", ""))
except Exception as e:
    print(f"ERROR: {e}")

time.sleep(5)

# Check if ad is showing
print("\n--- Check foreground ---")
try:
    r = shell("dumpsys activity activities | grep mResumedActivity")
    out = r.get("output", "").strip()
    print(f"Foreground: {out}")
except Exception as e:
    print(f"ERROR: {e}")

# Dismiss ad with BACK key
if "AdActivity" in out or "ads" in out.lower():
    print("\n--- Ad detected. Pressing BACK to dismiss ---")
    shell("input keyevent KEYCODE_BACK")
    time.sleep(2)

    # Check again
    try:
        r = shell("dumpsys activity activities | grep mResumedActivity")
        out = r.get("output", "").strip()
        print(f"Foreground after BACK: {out}")
    except Exception as e:
        print(f"ERROR: {e}")

# If still ad, try tapping top-right X (common ad close button)
if "AdActivity" in out or "ads" in out.lower():
    print("\n--- Tap top-right corner (ad close X) ---")
    shell("input tap 650 100")
    time.sleep(2)
    try:
        r = shell("dumpsys activity activities | grep mResumedActivity")
        out = r.get("output", "").strip()
        print(f"Foreground after tap X: {out}")
    except Exception as e:
        print(f"ERROR: {e}")

# Now do the tap sequence on the real app
if "AdActivity" not in out:
    print("\n--- Real app is foreground. Activating mock ---")

    # Tap center of map to set pin
    print("  Tapping center map...")
    shell("input tap 360 500")
    time.sleep(2)

    # Tap bottom area buttons
    for label, x, y in [
        ("Bottom-left", 180, 1050),
        ("Bottom-center", 360, 1050),
        ("Bottom-right", 540, 1050),
        ("Lower-center", 360, 1150),
    ]:
        print(f"  {label} ({x},{y})")
        shell(f"input tap {x} {y}")
        time.sleep(2)

    print("\nWaiting 5s for mock to activate...")
    time.sleep(5)
else:
    print("\n--- Ad still showing. Mock activation skipped ---")

# Final dumpsys
print("\n--- Final dumpsys ---")
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
