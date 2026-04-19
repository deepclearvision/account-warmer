"""
Diagnose Settings UI on GeelarK phone.
Opens Settings, takes screenshot, dumps UI hierarchy to understand element structure.
Run from: account-warmer/
"""
import os, sys, yaml, time, base64, json
from pathlib import Path

_env = Path('warmer.env')
for line in _env.read_text(encoding='utf-8').splitlines():
    line = line.strip()
    if line and not line.startswith('#') and '=' in line:
        k, _, v = line.partition('=')
        os.environ.setdefault(k.strip(), v.strip())

from activities.google_login_mobile import _shell
from core.geelark_client import GeelarKClient

client = GeelarKClient()
with open(r'C:\WarmingData\geelark_accounts.yaml', encoding='utf-8') as f:
    data = yaml.safe_load(f)
acc = data['accounts'][0]
phone_id = acc['geelark_phone_id']
print('Phone:', phone_id, '  Account:', acc.get('email'))

print('Starting phone...')
client.start_phone(phone_id)
for i in range(20):
    time.sleep(5)
    ok, out = _shell(phone_id, 'wm size')
    if ok and out.strip():
        print('Boot after %ds. Screen: %s' % ((i+1)*5, out.strip()))
        break
time.sleep(3)

# Open Settings
print()
print('=== Opening Settings ===')
ok, out = _shell(phone_id, 'am start -n com.android.settings/.Settings')
print('am start result:', ok, out[:100])
time.sleep(3)

# Take screenshot
print()
print('=== Taking screenshot (Settings main) ===')
shot = client.take_screenshot(phone_id)
if shot:
    out_path = r'C:\WarmingData\screenshots\settings_main.png'
    Path(out_path).parent.mkdir(exist_ok=True)
    with open(out_path, 'wb') as f:
        f.write(shot)
    print('Screenshot saved to', out_path)
else:
    print('Screenshot failed')

# Dump UI hierarchy
print()
print('=== UI Hierarchy (Settings main) ===')
ok, out = _shell(phone_id, 'uiautomator dump /sdcard/ui.xml && cat /sdcard/ui.xml')
if ok and out:
    # Save full dump
    dump_path = r'C:\WarmingData\screenshots\settings_main_ui.xml'
    with open(dump_path, 'w', encoding='utf-8') as f:
        f.write(out)
    print('Full UI dump saved to', dump_path)
    # Show text elements
    import re
    texts = re.findall(r'text="([^"]+)"', out)
    print('Visible text elements:')
    for t in texts[:50]:
        if t.strip():
            print(' ', repr(t))
else:
    print('uiautomator dump failed:', out[:200])

# Also check what the openApp RPA step would do with the uri parameter
# Try: am start -a android.settings.ADD_ACCOUNT_SETTINGS
print()
print('=== Testing am start -a android.settings.ADD_ACCOUNT_SETTINGS ===')
ok, out = _shell(phone_id, 'am start -a android.settings.ADD_ACCOUNT_SETTINGS')
print('Result:', ok, out[:200])
time.sleep(3)

# Screenshot of Add Account screen
print()
print('=== Taking screenshot (Add Account screen) ===')
shot2 = client.take_screenshot(phone_id)
if shot2:
    out_path2 = r'C:\WarmingData\screenshots\settings_add_account.png'
    with open(out_path2, 'wb') as f:
        f.write(shot2)
    print('Screenshot saved to', out_path2)

# UI dump of Add Account screen
print()
print('=== UI Hierarchy (Add Account screen) ===')
ok, out = _shell(phone_id, 'uiautomator dump /sdcard/ui2.xml && cat /sdcard/ui2.xml')
if ok and out:
    dump_path2 = r'C:\WarmingData\screenshots\settings_add_account_ui.xml'
    with open(dump_path2, 'w', encoding='utf-8') as f:
        f.write(out)
    print('Full UI dump saved to', dump_path2)
    import re
    texts = re.findall(r'text="([^"]+)"', out)
    print('Visible text elements:')
    for t in texts[:50]:
        if t.strip():
            print(' ', repr(t))
else:
    print('uiautomator dump failed:', out[:200])

# Get current focus
ok, focus_out = _shell(phone_id, 'dumpsys window | grep mCurrentFocus')
print()
print('Current focus:', focus_out.strip())

client.stop_phone(phone_id)
print()
print('Done. Phone stopped.')
print('Check screenshots in C:\\WarmingData\\screenshots\\')
