import sys
import time

sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")

from core.geelark_client import _post

PHONE_ID = "614216903581237315"
PROFILE_KEY = "oakleighoneal9987"
FAKE_GPS_PACKAGE = "com.theappninjas.fakegpsjoystick"

def shell(cmd):
    return _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": cmd})

print(f"=== Provisioning {PROFILE_KEY} ({PHONE_ID}) ===")

# Step 1: Enable mock location via appops + settings
print("\n[1] Enable mock location via shell ...")
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

# Step 2: Check Fake GPS installed
print("\n[2] Check Fake GPS installed ...")
r = shell(f"pm list packages | grep {FAKE_GPS_PACKAGE}")
out = r.get("output", "").strip()
print(f"  {out if out else 'NOT FOUND'}")

# Step 3: Activate via deep-link (same method as run_one_phone.py)
print("\n[3] Activate GPS via deep-link ...")
r = shell(f"am startservice -n {FAKE_GPS_PACKAGE}/.service.OverlayService")
time.sleep(3)

url = "gpsjoystick://teleport?lat=51.5224&lng=-0.1026"
r = shell(f"am start -a android.intent.action.VIEW -d '{url}' {FAKE_GPS_PACKAGE}")
print(f"  deep-link: {r.get('output', '').strip()[:120]}")
time.sleep(5)

# Step 4: Force-stop so it doesn't stay foreground
shell(f"am force-stop {FAKE_GPS_PACKAGE}")
time.sleep(2)

# Step 5: Verify dumpsys
print("\n[4] Verify dumpsys mock location ...")
time.sleep(3)
r = shell("dumpsys location")
out = r.get("output", "")
mock_lines = [l.strip() for l in out.splitlines() if "mock" in l.lower() and "Location[" in l.lower()]
if mock_lines:
    for ml in mock_lines[:3]:
        print(f"  PASS: {ml}")
else:
    print("  FAIL: no mock location found")

print("\n=== Provisioning complete ===")
