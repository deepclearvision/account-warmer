"""
Diagnostic: Test which Android intent opens the native Google Add Account wizard.
Run from: account-warmer/

We test several intents to find one that:
  1. Opens a NATIVE (non-WebView) Google sign-in screen
  2. Shows android.widget.EditText in uiautomator dump
  3. Is navigable without Chrome WebView blindness

This tells us whether the pure-ADB login approach is viable on Android 10 (SM-G9650).
"""
import os, sys, time, json
from pathlib import Path

_env = Path('warmer.env')
for line in _env.read_text(encoding='utf-8').splitlines():
    line = line.strip()
    if line and not line.startswith('#') and '=' in line:
        k, _, v = line.partition('=')
        os.environ.setdefault(k.strip(), v.strip())

from activities.google_login_mobile import _shell, _get_window_focus
from core.geelark_client import GeelarKClient

import yaml
client = GeelarKClient()
with open(r'C:\WarmingData\geelark_accounts.yaml', encoding='utf-8') as f:
    data = yaml.safe_load(f)

# Use first account with a phone ID
acc = next(a for a in data['accounts'] if a.get('geelark_phone_id'))
phone_id = acc['geelark_phone_id']
print(f'Using phone: {phone_id}  ({acc.get("email","?")})')
print()

# Boot phone
print('Starting phone...')
viewer_url = client.start_phone(phone_id)
print('Viewer:', viewer_url)

# Boot poll
for i in range(24):
    time.sleep(5)
    ok, out = _shell(phone_id, 'wm size')
    if ok and out.strip():
        print(f'Phone ready after {(i+1)*5}s — screen: {out.strip()}')
        break
else:
    print('WARNING: phone not responding after 120s')
time.sleep(3)

# Screenshot helper
shot_dir = Path(r'C:\WarmingData\screenshots\adb_intent_test')
shot_dir.mkdir(parents=True, exist_ok=True)
shot_n = [0]

def take_shot(label):
    shot_n[0] += 1
    shot = client.take_screenshot(phone_id)
    if shot:
        p = shot_dir / f'{shot_n[0]:02d}_{label}.png'
        p.write_bytes(shot)
        print(f'  Screenshot: {p.name}')

def dump_ui(label):
    """Dump UI and return trimmed XML. Also print key element types."""
    ok, xml = _shell(phone_id, 'uiautomator dump /sdcard/ui.xml && cat /sdcard/ui.xml')
    if not xml:
        print(f'  [{label}] UI dump failed')
        return ''
    # Count key element types
    edit_count = xml.count('android.widget.EditText')
    webview_count = xml.count('android.webkit.WebView')
    focus = _get_window_focus(phone_id)
    print(f'  [{label}] EditText={edit_count}  WebView={webview_count}')
    print(f'  [{label}] Focus: {focus.strip()[-80:]}')
    # Save XML
    p = shot_dir / f'{shot_n[0]:02d}_{label}.xml'
    p.write_text(xml, encoding='utf-8', errors='replace')
    return xml

# ─────────────────────────────────────────────────────────────────────────────
# TEST 1: Standard Add Account settings intent (no specific account type)
# ─────────────────────────────────────────────────────────────────────────────
print()
print('='*60)
print('TEST 1: am start -a android.settings.ADD_ACCOUNT_SETTINGS')
print('='*60)
ok, out = _shell(phone_id, 'am start -a android.settings.ADD_ACCOUNT_SETTINGS')
print('Result:', ok, out[:100] if out else '(no output)')
time.sleep(3)
take_shot('test1_add_account_settings')
xml1 = dump_ui('test1')

# ─────────────────────────────────────────────────────────────────────────────
# TEST 2: Add Account with Google account type pre-selected
# ─────────────────────────────────────────────────────────────────────────────
print()
print('='*60)
print('TEST 2: ADD_ACCOUNT_SETTINGS with account type=com.google')
print('='*60)
ok, out = _shell(phone_id,
    'am start -a android.settings.ADD_ACCOUNT_SETTINGS '
    '--es "account_types" "com.google"')
print('Result:', ok, out[:100] if out else '(no output)')
time.sleep(3)
take_shot('test2_add_account_google_type')
xml2 = dump_ui('test2')

# ─────────────────────────────────────────────────────────────────────────────
# TEST 3: Direct GMS sign-in activity
# ─────────────────────────────────────────────────────────────────────────────
print()
print('='*60)
print('TEST 3: Direct GMS SignInActivity')
print('='*60)
# Go back to home first
_shell(phone_id, 'input keyevent KEYCODE_HOME')
time.sleep(1)
ok, out = _shell(phone_id,
    'am start -n com.google.android.gms/'
    'com.google.android.gms.auth.uiflows.signin.SignInActivity')
print('Result:', ok, out[:100] if out else '(no output)')
time.sleep(3)
take_shot('test3_gms_signin')
xml3 = dump_ui('test3')

# ─────────────────────────────────────────────────────────────────────────────
# TEST 4: GMS account setup via PICK action
# ─────────────────────────────────────────────────────────────────────────────
print()
print('='*60)
print('TEST 4: com.google.android.gms.auth.setup.devicesignals')
print('='*60)
_shell(phone_id, 'input keyevent KEYCODE_HOME')
time.sleep(1)
ok, out = _shell(phone_id,
    'am start -a com.google.android.gms.auth.GOOGLE_SIGN_IN '
    '-p com.google.android.gms')
print('Result:', ok, out[:100] if out else '(no output)')
time.sleep(3)
take_shot('test4_gms_pick')
xml4 = dump_ui('test4')

# ─────────────────────────────────────────────────────────────────────────────
# TEST 5: Settings explicit component
# ─────────────────────────────────────────────────────────────────────────────
print()
print('='*60)
print('TEST 5: Settings AddAccountSettings component')
print('='*60)
_shell(phone_id, 'input keyevent KEYCODE_HOME')
time.sleep(1)
ok, out = _shell(phone_id,
    'am start -n com.android.settings/.accounts.AddAccountSettings')
print('Result:', ok, out[:100] if out else '(no output)')
time.sleep(4)
take_shot('test5_settings_add_account')
xml5 = dump_ui('test5')

# If we see account-type chooser, tap Google
if xml5 and 'Google' in xml5:
    print('  → "Google" option visible! Tapping it...')
    import re as _re
    m = _re.search(r'text="Google"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml5)
    if not m:
        m = _re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"[^>]*text="Google"', xml5)
    if m:
        x = (int(m.group(1)) + int(m.group(3))) // 2
        y = (int(m.group(2)) + int(m.group(4))) // 2
        print(f'  Tapping Google at ({x},{y})')
        _shell(phone_id, f'input tap {x} {y}')
        time.sleep(4)
        take_shot('test5b_after_google_tap')
        xml5b = dump_ui('test5b')
        if 'android.widget.EditText' in (xml5b or ''):
            print()
            print('SUCCESS: Found EditText after tapping Google in Settings!')
            print('This confirms the Settings > Add Account > Google path works via ADB.')

# ─────────────────────────────────────────────────────────────────────────────
# SUMMARY
# ─────────────────────────────────────────────────────────────────────────────
print()
print('='*60)
print('SUMMARY')
print('='*60)

results = {
    'Test 1 (ADD_ACCOUNT_SETTINGS)': xml1,
    'Test 2 (ADD_ACCOUNT_SETTINGS+google type)': xml2,
    'Test 3 (GMS SignInActivity)': xml3,
    'Test 4 (GMS GOOGLE_SIGN_IN)': xml4,
    'Test 5 (Settings AddAccountSettings)': xml5,
}

for name, xml in results.items():
    if not xml:
        print(f'  {name}: NO XML (intent failed)')
        continue
    has_edit = 'android.widget.EditText' in xml
    has_web  = 'android.webkit.WebView' in xml
    has_google_option = 'Google' in xml
    print(f'  {name}:')
    print(f'    EditText visible: {has_edit}')
    print(f'    WebView present:  {has_web}')
    print(f'    "Google" option:  {has_google_option}')

print()
print(f'Screenshots saved to: {shot_dir}')

# Stop phone
client.stop_phone(phone_id)
print('Phone stopped.')
