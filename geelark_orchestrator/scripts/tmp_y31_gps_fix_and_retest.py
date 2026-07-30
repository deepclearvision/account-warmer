import sys, time
sys.stdout.reconfigure(encoding='utf-8')

sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator\src")
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator")
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")

from run_one_phone import _activate_gps_android13, _ui_clear_storage
from core.geelark_client import _post

PHONE_ID = '614216886971793475'
MODEL = 'Y31'
LAT = 51.5224
LNG = -0.1026

print(f"=== Step 1: Apply fix on {MODEL} ({PHONE_ID}) ===")

fix_cmds = [
    ('appops set MOCK_LOCATION allow', 'appops set com.theappninjas.fakegpsjoystick MOCK_LOCATION allow'),
    ('deviceidle whitelist', 'dumpsys deviceidle whitelist +com.theappninjas.fakegpsjoystick'),
]

for label, cmd in fix_cmds:
    print(f"\n--- {label} ---")
    try:
        r = _post('/open/v1/shell/execute', {'id': PHONE_ID, 'cmd': cmd})
        out = r.get('output', '').strip()
        if out:
            print(out)
        else:
            print("(silent success)")
    except Exception as e:
        print(f"ERROR: {e}")

print(f"\n=== Step 2: Pre-GPS UI Clear Storage ===")
try:
    _ui_clear_storage(PHONE_ID)
    print("  UI Clear Storage done.")
except Exception as e:
    print(f"  UI Clear Storage warning: {e}")

print(f"\n=== Step 3: Re-run A13 GPS activation flow ===")
print(f"Target coords: {LAT},{LNG}")

mock_ok = _activate_gps_android13(PHONE_ID, LAT, LNG)
print(f"\nFlow returned: mock_ok={mock_ok}")

print(f"\n=== Step 3: Verify mock state ===")
try:
    r = _post('/open/v1/shell/execute', {'id': PHONE_ID, 'cmd': 'dumpsys location | grep mock'})
    out = r.get('output', '').strip()
    if out:
        print(out)
    else:
        print("(no output — mock not active)")
except Exception as e:
    print(f"ERROR: {e}")

print(f"\n=== Step 4: Verify appops ===")
try:
    r = _post('/open/v1/shell/execute', {'id': PHONE_ID, 'cmd': 'appops get com.theappninjas.fakegpsjoystick | grep -i mock'})
    out = r.get('output', '').strip()
    if out:
        print(out)
    else:
        print("(no output)")
except Exception as e:
    print(f"ERROR: {e}")
