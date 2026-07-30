import sys
import time

sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator\src")
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator")
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")

from core.geelark_client import GeelarKClient, _post
from run_one_phone import dumpsys_mock_check

PHONE_ID = "614216822245294147"
FAKE_GPS_PACKAGE = "com.theappninjas.fakegpsjoystick"

print(f"=== Test: No force-stop on serial_24 ({PHONE_ID}) ===")

# Activate via deep-link WITHOUT force-stop
print("\n[1] Starting overlay service...")
_post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": f"am startservice -n {FAKE_GPS_PACKAGE}/.service.OverlayService"})
time.sleep(3)

print("\n[2] Sending deep-link (NO force-stop)...")
url = "gpsjoystick://teleport?lat=51.5224&lng=-0.1026"
r = _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": f"am start -a android.intent.action.VIEW -d '{url}' {FAKE_GPS_PACKAGE}"})
print(f"    result: {r.get('output', '').strip()[:120]}")
time.sleep(5)

# Check mock persistence
print("\n[3] Checking mock persistence (polling 30s)...")
for i in range(6):
    time.sleep(5)
    mock_ok, mock_info = dumpsys_mock_check(PHONE_ID)
    print(f"    T+{i*5}s: mock={mock_ok} | {mock_info}")

# Check foreground
print("\n[4] Foreground check...")
r = _post("/open/v1/shell/execute", {"id": PHONE_ID, "cmd": "dumpsys activity activities | grep mResumedActivity"})
print(f"    {r.get('output', '').strip()}")

print("\n=== Test complete ===")
