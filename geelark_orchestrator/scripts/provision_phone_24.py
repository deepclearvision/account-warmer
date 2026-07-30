import sys
import time
from pathlib import Path

sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")

from core.geelark_client import GeelarKClient, _post

PHONE_ID = "614216822245294147"
PROFILE_KEY = "serial_24"
FAKE_GPS_PACKAGE = "com.theappninjas.fakegpsjoystick"

def shell(cmd):
    return _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": cmd})

print("========================================")
print("PHONE RE-PROVISIONING SCRIPT")
print(f"Phone ID: {PHONE_ID}")
print(f"Target mock app: {FAKE_GPS_PACKAGE}")
print("========================================\n")

# ---------------------------------------------------------------------------
# BASELINE: dumpsys BEFORE any changes
# ---------------------------------------------------------------------------
print("--- BASELINE: dumpsys location (before) ---")
try:
    r = shell("dumpsys location | head -n 40")
    print(r.get("output", "(empty)"))
except Exception as e:
    print(f"ERROR: {e}")

# ---------------------------------------------------------------------------
# STEP 1: Enable mock location via appops + settings
# ---------------------------------------------------------------------------
print("\n--- STEP 1: Enable mock location via shell ---")

cmds = [
    f"appops set {FAKE_GPS_PACKAGE} android:mock_location allow",
    f"settings put secure mock_location_app {FAKE_GPS_PACKAGE}",
    "settings put secure mock_location 1",
    "settings put secure location_mode 3",
]

for cmd in cmds:
    print(f"  Running: {cmd}")
    try:
        r = shell(cmd)
        out = r.get("output", "").strip()
        err = r.get("error", "").strip()
        if out:
            print(f"    OUT: {out}")
        if err:
            print(f"    ERR: {err}")
    except Exception as e:
        print(f"    ERROR: {e}")
    time.sleep(1)

# ---------------------------------------------------------------------------
# STEP 2: Verify Fake GPS is installed
# ---------------------------------------------------------------------------
print("\n--- STEP 2: Check Fake GPS installed ---")
try:
    r = shell(f"pm list packages | grep {FAKE_GPS_PACKAGE}")
    out = r.get("output", "").strip()
    print(out if out else "(not found)")
except Exception as e:
    print(f"ERROR: {e}")

# ---------------------------------------------------------------------------
# STEP 3: Open Fake GPS and trigger location
# ---------------------------------------------------------------------------
print("\n--- STEP 3: Open Fake GPS Joystick ---")
try:
    r = shell(f"am start -n {FAKE_GPS_PACKAGE}/com.theappninjas.fakegpsjoystick.MainActivity")
    out = r.get("output", "").strip()
    print(out if out else "(no output)")
except Exception as e:
    print(f"ERROR: {e}")

print("Waiting 5s for app to open...")
time.sleep(5)

print("\n--- STEP 3b: Set a test location via Fake GPS ---")
try:
    # Try to find the set-location button via UI and tap it, or use known coordinates
    # Fake GPS Joystick typically has a map interface. Let's try tapping center to set a pin
    r = shell("dumpsys window windows | grep mCurrentFocus")
    out = r.get("output", "").strip()
    print(f"Current focus: {out}")
except Exception as e:
    print(f"ERROR: {e}")

# Try tapping center of screen (common for setting location on Fake GPS map)
print("  Tapping center of screen to set pin...")
shell("input tap 360 720")
time.sleep(2)

# Try tapping the "Set Location" button if it exists
print("  Tapping known Set Location button area...")
shell("input tap 360 1150")
time.sleep(2)

# Try tapping START button area
print("  Tapping START button area...")
shell("input tap 540 1200")
time.sleep(2)

# ---------------------------------------------------------------------------
# STEP 4: POST-check dumpsys location
# ---------------------------------------------------------------------------
print("\n--- STEP 4: dumpsys location (after setup) ---")
try:
    r = shell("dumpsys location | head -n 60")
    out = r.get("output", "")
    print(out if out else "(empty)")
except Exception as e:
    print(f"ERROR: {e}")

print("\n--- STEP 4b: Specific mock provider grep ---")
try:
    r = shell(r"dumpsys location | grep -i 'mock\|fakegps\|theappninjas'")
    out = r.get("output", "")
    print(out if out else "(no mock provider found)")
except Exception as e:
    print(f"ERROR: {e}")

# ---------------------------------------------------------------------------
# STEP 5: Open Maps and check foreground
# ---------------------------------------------------------------------------
print("\n--- STEP 5: Open Maps and verify foreground ---")
try:
    r = shell("am start -n com.google.android.apps.maps/com.google.android.maps.MapsActivity")
    print(r.get("output", ""))
except Exception as e:
    print(f"ERROR: {e}")

print("Waiting 7s...")
time.sleep(7)

try:
    r = shell("dumpsys activity activities | grep mResumedActivity")
    out = r.get("output", "").strip()
    print(f"Foreground: {out}")
except Exception as e:
    print(f"ERROR: {e}")

# ---------------------------------------------------------------------------
# STEP 6: Screenshot for record
# ---------------------------------------------------------------------------
print("\n--- STEP 6: Screenshot ---")
client = GeelarKClient()
try:
    data = client.take_screenshot(PHONE_ID, max_wait=30)
    if data:
        p = Path(r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator\logs\screenshots")
        p.mkdir(parents=True, exist_ok=True)
        path = p / f"provision_{PROFILE_KEY}_after.png"
        path.write_bytes(data)
        print(f"Screenshot saved: {path}")
    else:
        print("Screenshot: no data")
except Exception as e:
    print(f"Screenshot error: {e}")

print("\n========================================")
print("RE-PROVISIONING COMPLETE")
print("========================================")
