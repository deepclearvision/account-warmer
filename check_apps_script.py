import os, sys, yaml, time
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

# Wait for boot — poll wm size until it responds
print('Waiting for boot...')
for i in range(20):
    time.sleep(5)
    ok, out = _shell(phone_id, 'wm size')
    if ok and out.strip():
        print('Phone ready after %ds. Screen: %s' % ((i+1)*5, out.strip()))
        break
else:
    print('Phone did not respond in 100s — aborting')
    client.stop_phone(phone_id)
    sys.exit(1)

time.sleep(3)

# Check packages
print()
ok, out = _shell(phone_id, 'pm list packages')
packages = [line.replace('package:', '').strip() for line in out.splitlines() if line.startswith('package:')]
print('Total packages installed:', len(packages))
print()

chrome_pkgs = [p for p in packages if 'chrome' in p.lower()]
browser_pkgs = [p for p in packages if 'browser' in p.lower()]
google_pkgs = [p for p in packages if 'google' in p.lower()]
maps_pkg = [p for p in packages if 'maps' in p.lower()]
gmail_pkg = [p for p in packages if 'gmail' in p.lower() or p == 'com.google.android.gm']
yt_pkg = [p for p in packages if 'youtube' in p.lower()]

print('Chrome:   ', chrome_pkgs or 'NOT INSTALLED')
print('Browser:  ', browser_pkgs or 'NOT INSTALLED')
print('Maps:     ', maps_pkg or 'NOT INSTALLED')
print('Gmail:    ', gmail_pkg or 'NOT INSTALLED')
print('YouTube:  ', yt_pkg or 'NOT INSTALLED')
print()
print('All Google packages:')
for p in sorted(google_pkgs):
    print(' ', p)

# Default browser
ok, out = _shell(phone_id, 'pm resolve-activity --brief -a android.intent.action.VIEW -d http://google.com')
print()
print('Default browser:', out.strip() or 'unknown')

# Also check AccountManager
ok, out = _shell(phone_id, 'dumpsys account')
google_lines = [l for l in out.splitlines() if 'google' in l.lower() or 'gmail' in l.lower()]
print()
print('AccountManager (google entries):')
for l in google_lines[:20]:
    print(' ', l)

client.stop_phone(phone_id)
print()
print('Phone stopped.')
