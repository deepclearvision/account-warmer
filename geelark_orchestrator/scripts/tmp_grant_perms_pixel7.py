import sys
import time
sys.stdout.reconfigure(encoding='utf-8')

sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")
from core.geelark_client import _post

PHONE_ID = "614216911852404803"

print("=== Granting permissions on Pixel 7 ===")
for cmd in [
    "appops set com.theappninjas.fakegpsjoystick MOCK_LOCATION allow",
    "appops set com.theappninjas.fakegpsjoystick SYSTEM_ALERT_WINDOW allow",
    "appops set com.theappninjas.fakegpsjoystick FINE_LOCATION allow",
    "appops set com.theappninjas.fakegpsjoystick COARSE_LOCATION allow",
]:
    r = _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": cmd})
    print(f"{cmd}: {r.get('output', '').strip() or 'OK'}")
    time.sleep(0.5)

print("\nVerifying...")
r = _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "appops get com.theappninjas.fakegpsjoystick"})
print(r.get("output", "").strip())
