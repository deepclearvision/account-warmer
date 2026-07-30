import sys, time
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")
from core.geelark_client import _post

PHONE_ID = "613286814907629987"
FAKE_GPS_PACKAGE = "com.theappninjas.fakegpsjoystick"

def shell(cmd):
    return _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": cmd})

print("=== Y77 GPS Diagnostic ===")

r = shell("getprop ro.build.version.release")
print(f"Android version: {r.get('output','').strip()}")

r = shell(f"pm list packages | grep {FAKE_GPS_PACKAGE}")
print(f"Package: {r.get('output','').strip()}")

r = shell("dumpsys location")
out = r.get("output", "")
print("\n--- dumpsys location (BEFORE activation) ---")
for line in out.splitlines():
    if "Location[" in line or "mock" in line.lower() or "provider" in line.lower():
        print(line.strip())

print("\n--- Provisioning ---")
for cmd in [
    f"appops set {FAKE_GPS_PACKAGE} android:mock_location allow",
    f"settings put secure mock_location_app {FAKE_GPS_PACKAGE}",
    f"settings put secure mock_location 1",
    f"settings put secure location_mode 3",
]:
    r = shell(cmd)
    print(f"  {cmd}: {r.get('output','').strip() or 'OK'}")
    time.sleep(0.5)

print("\n--- Activation ---")
shell(f"am start -n {FAKE_GPS_PACKAGE}/com.theappninjas.fakegpsjoystick.MainActivity")
time.sleep(5)
shell(f"am startservice -n {FAKE_GPS_PACKAGE}/.service.OverlayService")
time.sleep(3)
url = "gpsjoystick://teleport?lat=51.5224&lng=-0.1026"
r = shell(f"am start -a android.intent.action.VIEW -d '{url}' {FAKE_GPS_PACKAGE}")
print(f"deep-link: {r.get('output','').strip()[:120]}")
time.sleep(10)

r = shell("dumpsys location")
out = r.get("output", "")
print("\n--- dumpsys location (AFTER activation) ---")
for line in out.splitlines():
    if "Location[" in line or "mock" in line.lower() or "provider" in line.lower():
        print(line.strip())

mock_lines = [l.strip() for l in out.splitlines() if "mock" in l.lower() and "Location[" in l.lower()]
print(f"\nMock lines found: {len(mock_lines)}")
for ml in mock_lines[:5]:
    print(f"  {ml}")

if not any("51.5224" in ml for ml in mock_lines):
    print("\nNo correct mock found. Trying UI taps...")
    shell("input tap 360 720")
    time.sleep(2)
    shell("input tap 360 1150")
    time.sleep(3)
    shell("input tap 600 1200")
    time.sleep(3)

    r = shell("dumpsys location")
    out = r.get("output", "")
    print("\n--- dumpsys location (after UI taps) ---")
    mock_lines2 = [l.strip() for l in out.splitlines() if "mock" in l.lower() and "Location[" in l.lower()]
    for ml in mock_lines2[:5]:
        print(f"  {ml}")

print("\n=== Diagnostic complete ===")
