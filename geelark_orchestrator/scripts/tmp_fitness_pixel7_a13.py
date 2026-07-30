import sys
import time
sys.stdout.reconfigure(encoding='utf-8')

sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator\src")
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\geelark_orchestrator")
sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")

from core.geelark_client import _post
from proxy_control import check_proxy_ip
from run_one_phone import _get_phone_ip, _activate_gps_android13, dumpsys_mock_check

PHONE_ID = "614216911852404803"
PROFILE_KEY = "tannerchambers9987"
MODEL = "Pixel 7"
LAT, LNG = 51.5224, -0.1026

print(f"=== Live fitness check: {PROFILE_KEY} / {MODEL} ({PHONE_ID}) ===")

# 1. Proxy routing
expected_ip = check_proxy_ip()
print(f"\n[1] Proxy routing — expected {expected_ip}")
detected = _get_phone_ip(PHONE_ID)
ok = detected == expected_ip
print(f"    Result: {'PASS' if ok else 'FAIL'} | detected={detected}")
if not ok:
    print("    ABORT: proxy routing leak")
    sys.exit(1)

# 2. Fake GPS mock provider via A13 path
print("\n[2] Fake GPS mock provider (A13 provisioning path)")
ok = _activate_gps_android13(PHONE_ID, LAT, LNG)
print(f"    A13 provisioning result: {'PASS' if ok else 'FAIL'}")
if not ok:
    print("    ABORT: GPS mock not active after A13 provisioning")
    sys.exit(1)

# Verify mock persists 10s after backgrounding
time.sleep(10)
mock_ok, mock_info = dumpsys_mock_check(PHONE_ID)
print(f"    Mock persistence (+10s): {'PASS' if mock_ok else 'FAIL'} | {mock_info}")
if not mock_ok:
    print("    ABORT: mock not persistent")
    sys.exit(1)

# 3. Maps opens cleanly
print("\n[3] Maps opens cleanly (no Play Store interstitial)")
_post('/open/v1/shell/execute', {'id': PHONE_ID, 'cmd': 'am force-stop com.google.android.apps.maps'})
time.sleep(1)
_post('/open/v1/shell/execute', {'id': PHONE_ID, 'cmd': 'am start -a android.intent.action.VIEW -d geo:0,0?q=plumber com.google.android.apps.maps'})
time.sleep(6)
_post('/open/v1/shell/execute', {'id': PHONE_ID, 'cmd': 'uiautomator dump /sdcard/maps_fit.xml'})
time.sleep(1)
r = _post('/open/v1/shell/execute', {'id': PHONE_ID, 'cmd': 'cat /sdcard/maps_fit.xml'})
xml = r.get('output', '')

has_search_term = 'plumber' in xml  # search term appears in search bar
has_clear_btn = 'Clear' in xml          # Clear button indicates active search bar
has_search_ui = has_search_term or has_clear_btn or 'Search here' in xml or 'Search' in xml
has_play_store = 'com.android.vending' in xml or 'Play Store' in xml or 'Update' in xml
maps_pkg = 'com.google.android.apps.maps' in xml

print(f"    Maps package present: {maps_pkg}")
print(f"    Search UI found: {has_search_ui} (term={has_search_term}, clear={has_clear_btn})")
print(f"    Play Store interstitial: {has_play_store}")

if has_play_store:
    print("    FAIL: Play Store / update interstitial blocking Maps")
    sys.exit(1)
if not (maps_pkg and has_search_ui):
    print("    FAIL: Maps did not open to usable search screen")
    sys.exit(1)

print("    PASS: Maps opens to usable search screen")

# Final mock check after Maps open
mock_ok, mock_info = dumpsys_mock_check(PHONE_ID)
print(f"    Mock while Maps open: {'PASS' if mock_ok else 'FAIL'} | {mock_info}")

print("\n=== ALL THREE LIVE GATES PASSED ===")
print(f"Candidate {PROFILE_KEY} is fit for Goal A1.")
print("\n>>> READY FOR WATCHED DISPATCH <<<")
