import sys
import time
import re
sys.stdout.reconfigure(encoding='utf-8')

sys.path.insert(0, r"C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer")
from core.geelark_client import _post

PHONE_ID = "614216911852404803"
LAT, LNG = "51.5224", "-0.1026"


def dumpsys_mock_lines():
    r = _post('/open/v1/shell/execute', {'id': PHONE_ID, 'cmd': 'dumpsys location'})
    out = r.get('output', '')
    mock = [l.strip() for l in out.splitlines() if 'mock' in l.lower() and 'Location[' in l.lower()]
    return mock


def top_activity():
    r = _post('/open/v1/shell/execute', {'id': PHONE_ID, 'cmd': 'dumpsys activity activities | grep topResumedActivity'})
    return r.get('output', '').strip()


def dump_ui(filename):
    _post('/open/v1/shell/execute', {'id': PHONE_ID, 'cmd': f'uiautomator dump /sdcard/{filename}'})
    time.sleep(1)
    r = _post('/open/v1/shell/execute', {'id': PHONE_ID, 'cmd': f'cat /sdcard/{filename}'})
    return r.get('output', '')


def extract_text_bounds(xml):
    items = []
    for m in re.finditer(r'text="([^"]*)"', xml):
        t = m.group(1)
        bounds_match = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml[m.start():m.start()+500])
        if bounds_match:
            items.append((t, int(bounds_match.group(1)), int(bounds_match.group(2)),
                          int(bounds_match.group(3)), int(bounds_match.group(4))))
    return items


def tap_text(xml, target):
    for t, x1, y1, x2, y2 in extract_text_bounds(xml):
        if target.lower() in t.lower():
            cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
            _post('/open/v1/shell/execute', {'id': PHONE_ID, 'cmd': f'input tap {cx} {cy}'})
            return True
    return False


print("=== TEST 1: Overlay/Hide JoyStick mode ===")

# 1. Launch Fake GPS
print("\n[1] Launching Fake GPS via monkey...")
_post('/open/v1/shell/execute', {'id': PHONE_ID, 'cmd': 'monkey -p com.theappninjas.fakegpsjoystick -c android.intent.category.LAUNCHER 1'})
time.sleep(5)
print(f"    top: {top_activity()}")

# 2. Handle any initial dialogs
xml = dump_ui('test1_initial.xml')

# Privacy Accept
if tap_text(xml, 'ACCEPT'):
    print("    Tapped ACCEPT")
    time.sleep(2)
    xml = dump_ui('test1_after_accept.xml')

# Let's Go
if tap_text(xml, "Let's Go"):
    print("    Tapped Let's Go")
    time.sleep(3)
    xml = dump_ui('test1_after_go.xml')

# Skip notification step if present
if tap_text(xml, 'Skip this step'):
    print("    Tapped Skip this step")
    time.sleep(3)
    xml = dump_ui('test1_after_skip.xml')

# Grant location access if dialog
if tap_text(xml, 'WHILE USING THE APP'):
    print("    Tapped WHILE USING THE APP")
    time.sleep(3)
    xml = dump_ui('test1_after_perm.xml')

# "You're All Set" -> Start Using GPS JoyStick
if tap_text(xml, 'Start Using GPS JoyStick'):
    print("    Tapped Start Using GPS JoyStick")
    time.sleep(3)
    xml = dump_ui('test1_after_start.xml')

# Update dialog -> CANCEL
if tap_text(xml, 'CANCEL'):
    print("    Tapped CANCEL (update)")
    time.sleep(2)
    xml = dump_ui('test1_after_cancel.xml')

# What's New -> Done
if tap_text(xml, 'Done'):
    print("    Tapped Done")
    time.sleep(2)
    xml = dump_ui('test1_after_done.xml')

# GDPR consent if present
if tap_text(xml, 'Consent'):
    print("    Tapped Consent")
    time.sleep(2)
    xml = dump_ui('test1_after_consent.xml')

print(f"    top after setup: {top_activity()}")

# 3. Check if we're on main screen or map
items = extract_text_bounds(xml)
texts = [t for t, *_ in items]
print(f"    UI texts: {[t for t in texts if t][:15]}")

# 4. Send deep-link to set coords
print("\n[2] Sending deep-link...")
url = f'gpsjoystick://teleport?lat={LAT}&lng={LNG}'
_post('/open/v1/shell/execute', {'id': PHONE_ID, 'cmd': f"am start -a android.intent.action.VIEW -d '{url}' com.theappninjas.fakegpsjoystick"})
time.sleep(4)

# 5. Dismiss ad if present
xml = dump_ui('test1_after_deeplink.xml')
if tap_text(xml, 'Continue to app'):
    print("    Dismissed ad")
    time.sleep(2)
    xml = dump_ui('test1_after_ad.xml')

# 6. Tap map area to activate mock
print("\n[3] Tapping map area to activate mock...")
if not tap_text(xml, 'Click here to open the map'):
    # Fallback: tap center of map area if text not found
    _post('/open/v1/shell/execute', {'id': PHONE_ID, 'cmd': 'input tap 360 1168'})
time.sleep(4)

xml = dump_ui('test1_map.xml')
items = extract_text_bounds(xml)
texts = [t for t, *_ in items]
print(f"    Map UI texts: {[t for t in texts if t][:15]}")

# Check mock
mock = dumpsys_mock_lines()
print(f"    Mock after map tap: {mock[:2] if mock else 'NONE'}")

if not mock:
    print("\n>>> FAIL: Mock not active even on map view. TEST 1 cannot proceed.")
    sys.exit(1)

# 7. Find and enable Hide JoyStick / Hide Overlay
print("\n[4] Looking for Hide JoyStick / Hide Overlay...")
# First look in the UI for these toggles
found_hide = False
for t, *_ in items:
    if 'hide' in t.lower() and ('joystick' in t.lower() or 'overlay' in t.lower()):
        print(f"    Found: {t!r}")
        found_hide = True

# If not visible, try opening settings via side menu (hamburger/back + menu)
if not found_hide:
    print("    Not visible on map screen. Trying side menu...")
    _post('/open/v1/shell/execute', {'id': PHONE_ID, 'cmd': 'input tap 45 84'})
    time.sleep(2)
    xml = dump_ui('test1_menu.xml')
    items = extract_text_bounds(xml)
    for t, x1, y1, x2, y2 in items:
        if 'hide' in t.lower() and ('joystick' in t.lower() or 'overlay' in t.lower()):
            print(f"    Found in menu: {t!r}")
            cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
            _post('/open/v1/shell/execute', {'id': PHONE_ID, 'cmd': f'input tap {cx} {cy}'})
            found_hide = True
            time.sleep(1)
            break
        elif t.lower() == 'settings':
            print("    Tapping Settings...")
            cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
            _post('/open/v1/shell/execute', {'id': PHONE_ID, 'cmd': f'input tap {cx} {cy}'})
            time.sleep(2)
            xml = dump_ui('test1_settings.xml')
            settings_items = extract_text_bounds(xml)
            for st, sx1, sy1, sx2, sy2 in settings_items:
                if 'hide' in st.lower() and ('joystick' in st.lower() or 'overlay' in st.lower()):
                    print(f"    Found in Settings: {st!r}")
                    scx, scy = (sx1 + sx2) // 2, (sy1 + sy2) // 2
                    _post('/open/v1/shell/execute', {'id': PHONE_ID, 'cmd': f'input tap {scx} {scy}'})
                    found_hide = True
                    time.sleep(1)
                    break
            break

if not found_hide:
    print("\n>>> FAIL: Hide JoyStick / Hide Overlay not found anywhere in UI. TEST 1 cannot proceed.")
    sys.exit(1)

# 8. Background the app
print("\n[5] Backgrounding app (HOME)...")
_post('/open/v1/shell/execute', {'id': PHONE_ID, 'cmd': 'input keyevent KEYCODE_HOME'})
time.sleep(1)

# 9. Check mock at 0s, 5s, 15s, 30s
print("\n[6] Checking mock persistence...")
for delay in [0, 5, 15, 30]:
    if delay > 0:
        time.sleep(delay)
    mock = dumpsys_mock_lines()
    print(f"    t=+{delay}s: {mock[:2] if mock else 'NONE'}")

# 10. If mock persists, open Maps
mock = dumpsys_mock_lines()
if mock:
    print("\n[7] Mock persists — opening Maps to verify spoofed location...")
    _post('/open/v1/shell/execute', {'id': PHONE_ID, 'cmd': 'am force-stop com.google.android.apps.maps'})
    time.sleep(1)
    _post('/open/v1/shell/execute', {'id': PHONE_ID, 'cmd': 'am start -a android.intent.action.VIEW -d geo:0,0?q=plumber com.google.android.apps.maps'})
    time.sleep(8)

    # Check Maps focus
    top = top_activity()
    print(f"    Maps top: {top}")

    # Dump Maps UI to see if search shows London results
    xml = dump_ui('test1_maps.xml')
    items = extract_text_bounds(xml)
    texts = [t for t, *_ in items]
    london_refs = [t for t in texts if any(w in t.lower() for w in ['london', 'uk', 'united kingdom', 'england'])]
    print(f"    London references in Maps: {london_refs[:5] if london_refs else 'NONE'}")

    # Check mock one more time after Maps open
    mock = dumpsys_mock_lines()
    print(f"    Mock while Maps open: {mock[:2] if mock else 'NONE'}")

    if mock and london_refs:
        print("\n>>> TEST 1 PASS: Overlay mode keeps mock alive and Maps sees spoofed location.")
    elif mock:
        print("\n>>> TEST 1 PARTIAL: Mock persists but Maps results don't show London (may need more time).")
    else:
        print("\n>>> TEST 1 FAIL: Mock died when Maps opened.")
else:
    print("\n>>> TEST 1 FAIL: Mock did not persist after backgrounding.")
