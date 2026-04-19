$VPS_IP   = '103.170.154.139'
$VPS_USER = 'Administrator'
$VPS_PASS = 'NjlW42jk6dTmSxJe4PDR'
$pass = ConvertTo-SecureString $VPS_PASS -AsPlainText -Force
$cred = New-Object System.Management.Automation.PSCredential($VPS_USER, $pass)
$so   = New-PSSessionOption -SkipCACheck -SkipCNCheck -SkipRevocationCheck
$s    = New-PSSession -ComputerName $VPS_IP -Credential $cred -SessionOption $so -Authentication Negotiate

Invoke-Command -Session $s -ScriptBlock {
    cd "C:\Users\Administrator\Desktop\AccountWarmer-Deploy\account-warmer"
    python -c "
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

client.stop_phone(phone_id)
print()
print('Phone stopped.')
" 2>&1
}

Remove-PSSession $s
