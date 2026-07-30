import sys
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")

from core.geelark_client import _post

PHONE_ID = "614216822245294147"
FAKE_GPS_PACKAGE = "com.theappninjas.fakegpsjoystick"

def shell(cmd):
    return _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": cmd})

print("--- Verify settings after provisioning attempt ---")

checks = [
    ("mock_location_app (secure)", f"settings get secure mock_location_app"),
    ("mock_location (secure)", "settings get secure mock_location"),
    ("location_mode (secure)", "settings get secure location_mode"),
    ("appops mock_location", f"appops get {FAKE_GPS_PACKAGE} android:mock_location"),
    ("Fake GPS running processes", f"ps -A | grep {FAKE_GPS_PACKAGE}"),
    ("Fake GPS activity", f"dumpsys activity | grep -i fakegps | head -5"),
]

for label, cmd in checks:
    print(f"\n{label}:")
    try:
        r = shell(cmd)
        out = r.get("output", "").strip()
        print(out if out else "(empty/no output)")
    except Exception as e:
        print(f"ERROR: {e}")

print("\n--- dumpsys location focused grep ---")
try:
    r = shell("dumpsys location")
    out = r.get("output", "")
    for line in out.splitlines():
        if any(k in line.lower() for k in ["mock", "fakegps", "theappninjas", "controller", "extra package"]):
            print(line.strip())
except Exception as e:
    print(f"ERROR: {e}")
