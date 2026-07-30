import sys
import time

sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")

from core.geelark_client import _post

PHONE_ID = "614216822245294147"

def shell(cmd):
    return _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": cmd})

print("========================================")
print("FINAL VERIFICATION — PHONE 24")
print("========================================\n")

# Check 1: dumpsys location for mock fix
print("--- CHECK 1: dumpsys location (mock evidence) ---")
try:
    r = shell("dumpsys location")
    out = r.get("output", "")
    mock_found = False
    for line in out.splitlines():
        if "mock" in line.lower() and "Location[" in line:
            print(line.strip())
            mock_found = True
    if not mock_found:
        print("(no mock location found in dumpsys)")
except Exception as e:
    print(f"ERROR: {e}")

# Check 2: Open Maps, verify foreground
print("\n--- CHECK 2: Open Maps, verify foreground ---")
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
    if "com.google.android.apps.maps" in out:
        print("Maps is foreground — PASS")
    elif "com.android.vending" in out:
        print("Play Store is foreground — FAIL")
    else:
        print(f"Unexpected foreground: {out}")
except Exception as e:
    print(f"ERROR: {e}")

# Check 3: Is the mock location still active while Maps is open?
print("\n--- CHECK 3: dumpsys while Maps open ---")
try:
    r = shell("dumpsys location")
    out = r.get("output", "")
    mock_found = False
    for line in out.splitlines():
        if "mock" in line.lower() and "Location[" in line:
            print(line.strip())
            mock_found = True
    if not mock_found:
        print("(no mock location found)")
except Exception as e:
    print(f"ERROR: {e}")

print("\n========================================")
print("VERIFICATION COMPLETE")
print("========================================")
