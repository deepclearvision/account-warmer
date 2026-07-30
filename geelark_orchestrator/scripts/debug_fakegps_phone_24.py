import sys
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")

from core.geelark_client import _post

PHONE_ID = "614216822245294147"
FAKE_GPS_PACKAGE = "com.theappninjas.fakegpsjoystick"

def shell(cmd):
    return _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": cmd})

print("--- Debug Fake GPS state ---")

# Check if package exists and is enabled
print("\n1. Package state:")
try:
    r = shell(f"pm dump {FAKE_GPS_PACKAGE} | head -n 20")
    print(r.get("output", "(empty)")[:500])
except Exception as e:
    print(f"ERROR: {e}")

# Check if the app is running
print("\n2. Running processes:")
try:
    r = shell("ps -A")
    out = r.get("output", "")
    for line in out.splitlines():
        if FAKE_GPS_PACKAGE in line or "fakegps" in line.lower():
            print(line)
except Exception as e:
    print(f"ERROR: {e}")

# Check uiautomator dump file
print("\n3. UI dump file:")
try:
    r = shell("ls -la /sdcard/fakegps_ui.xml")
    print(r.get("output", "(empty)"))
except Exception as e:
    print(f"ERROR: {e}")

try:
    r = shell("head -c 200 /sdcard/fakegps_ui.xml")
    out = r.get("output", "")
    print(f"First 200 chars: {repr(out)}")
except Exception as e:
    print(f"ERROR: {e}")

# Try raw uiautomator dump without parsing
print("\n4. Re-run uiautomator dump:")
try:
    r = shell("uiautomator dump /sdcard/fakegps_ui2.xml")
    print(f"dump result: {r.get('output', '')} {r.get('error', '')}")
    r2 = shell("head -c 200 /sdcard/fakegps_ui2.xml")
    print(f"file head: {repr(r2.get('output', ''))}")
except Exception as e:
    print(f"ERROR: {e}")

# Grant all possible permissions
print("\n5. Grant permissions:")
perms = [
    "android.permission.ACCESS_FINE_LOCATION",
    "android.permission.ACCESS_COARSE_LOCATION",
    "android.permission.ACCESS_MOCK_LOCATION",
    "android.permission.INTERNET",
    "android.permission.ACCESS_NETWORK_STATE",
]
for perm in perms:
    try:
        r = shell(f"pm grant {FAKE_GPS_PACKAGE} {perm}")
        out = r.get("output", "").strip()
        err = r.get("error", "").strip()
        print(f"  {perm}: {out or err or 'ok'}")
    except Exception as e:
        print(f"  {perm}: ERROR {e}")

# Try opening with monkey instead of explicit intent
print("\n6. Open via monkey:")
try:
    r = shell(f"monkey -p {FAKE_GPS_PACKAGE} -c android.intent.category.LAUNCHER 1")
    print(r.get("output", ""))
except Exception as e:
    print(f"ERROR: {e}")

import time
time.sleep(5)

# Check foreground
print("\n7. Foreground activity:")
try:
    r = shell("dumpsys activity activities | grep mResumedActivity")
    print(r.get("output", ""))
except Exception as e:
    print(f"ERROR: {e}")

# Final dumpsys mock grep
print("\n8. dumpsys mock provider:")
try:
    r = shell("dumpsys location")
    out = r.get("output", "")
    for line in out.splitlines():
        if any(k in line.lower() for k in ["mock", "fakegps", "theappninjas", "controller", "extra package", "last known"]):
            print(line.strip())
except Exception as e:
    print(f"ERROR: {e}")
