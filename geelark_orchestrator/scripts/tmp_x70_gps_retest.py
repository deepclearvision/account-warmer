import sys, time
sys.stdout.reconfigure(encoding='utf-8')

sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator\src")
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator")
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")

from run_one_phone import _activate_gps_android13, _ui_clear_storage
from core.geelark_client import _post

PHONE_ID = '614216895242960963'
MODEL = 'X70'
LAT = 51.5224
LNG = -0.1026

print(f"=== Re-running A13 GPS activation on {MODEL} ({PHONE_ID}) ===")
print(f"Target coords: {LAT},{LNG}")
print()

# Pre-GPS: UI Clear Storage
try:
    _ui_clear_storage(PHONE_ID)
except Exception as e:
    print(f"  UI Clear Storage warning: {e}")

# Run the same flow that worked on Pixel 7
mock_ok = _activate_gps_android13(PHONE_ID, LAT, LNG)

print(f"\n=== Flow returned: mock_ok={mock_ok} ===")

# Immediate dumpsys check
print("\n=== Post-flow dumpsys location | grep mock ===")
try:
    r = _post('/open/v1/shell/execute', {'id': PHONE_ID, 'cmd': 'dumpsys location | grep mock'})
    out = r.get('output', '').strip()
    if out:
        print(out)
    else:
        print("(no output — mock not active)")
except Exception as e:
    print(f"ERROR: {e}")

# Also check appops to confirm still allow
print("\n=== Post-flow appops MOCK_LOCATION ===")
try:
    r = _post('/open/v1/shell/execute', {'id': PHONE_ID, 'cmd': 'appops get com.theappninjas.fakegpsjoystick | grep -i mock'})
    out = r.get('output', '').strip()
    if out:
        print(out)
    else:
        print("(no output)")
except Exception as e:
    print(f"ERROR: {e}")
