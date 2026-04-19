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
import os, yaml, time
from pathlib import Path
_env = Path('warmer.env')
for line in _env.read_text(encoding='utf-8').splitlines():
    line = line.strip()
    if line and not line.startswith('#') and '=' in line:
        k, _, v = line.partition('=')
        os.environ.setdefault(k.strip(), v.strip())

from core.geelark_client import GeelarKClient
from activities.google_login_mobile import _shell

client = GeelarKClient()
with open(r'C:\WarmingData\geelark_accounts.yaml', encoding='utf-8') as f:
    data = yaml.safe_load(f)

acc = next(a for a in data['accounts'] if a.get('id') == 'acc_032')
phone_id = acc['geelark_phone_id']

# Take screenshot and save
screenshot = client.take_screenshot(phone_id)
if screenshot:
    p = r'C:\WarmingData\screenshots\totp_debug\live_totp_now.png'
    import pathlib
    pathlib.Path(p).parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(p).write_bytes(screenshot)
    print(f'Screenshot saved: {p} ({len(screenshot)} bytes)')
else:
    print('No screenshot returned')

# Also dump XML
ok, xml = _shell(phone_id, 'uiautomator dump /sdcard/ui.xml && cat /sdcard/ui.xml')
if xml:
    p2 = r'C:\WarmingData\screenshots\totp_debug\live_totp_now.xml'
    pathlib.Path(p2).write_text(xml, encoding='utf-8', errors='replace')
    print(f'XML saved: {p2}')
    # Show EditText details
    import re
    for m in re.finditer(r'class=\"android\.widget\.EditText\"[^>]*>', xml):
        print('EditText:', m.group(0)[:200])
" 2>&1
} -ErrorAction Continue

# Copy screenshot locally
$localDir = "C:\Users\Administrator\Desktop\AccountWarmer-Deploy\totp_debug_screenshots"
New-Item -ItemType Directory -Force -Path $localDir | Out-Null
Copy-Item -Path "C:\WarmingData\screenshots\totp_debug\live_totp_now.png" `
          -Destination "$localDir\live_totp_now.png" -FromSession $s -ErrorAction SilentlyContinue
Write-Host "Screenshot copied to: $localDir\live_totp_now.png"

Remove-PSSession $s
