$VPS_IP   = '103.170.154.139'
$VPS_USER = 'Administrator'
$VPS_PASS = 'NjlW42jk6dTmSxJe4PDR'
$pass = ConvertTo-SecureString $VPS_PASS -AsPlainText -Force
$cred = New-Object System.Management.Automation.PSCredential($VPS_USER, $pass)
$so   = New-PSSessionOption -SkipCACheck -SkipCNCheck -SkipRevocationCheck
$s    = New-PSSession -ComputerName $VPS_IP -Credential $cred -SessionOption $so -Authentication Negotiate

Write-Host "=== Check if Chrome is on acc_004 phone ==="
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

from activities.google_login_mobile import _shell
from core.geelark_client import GeelarKClient

client = GeelarKClient()
with open(r'C:\WarmingData\geelark_accounts.yaml', encoding='utf-8') as f:
    data = yaml.safe_load(f)
acc = data['accounts'][0]
phone_id = acc['geelark_phone_id']

# Start phone briefly
print('Starting phone...')
client.start_phone(phone_id)
time.sleep(15)

# Check for Chrome
print()
print('=== Checking installed packages ===')
ok, out = _shell(phone_id, 'pm list packages | grep -i chrome')
print('Chrome packages:', out.strip() or 'NONE FOUND')

ok, out = _shell(phone_id, 'pm list packages | grep -i browser')
print('Browser packages:', out.strip() or 'NONE FOUND')

ok, out = _shell(phone_id, 'pm list packages | grep -i google')
print('Google packages:', out.strip() or 'NONE')

# Try to find what the default browser is
ok, out = _shell(phone_id, 'pm resolve-activity --brief -a android.intent.action.VIEW -d http://google.com')
print()
print('Default browser for http://:', out.strip())

# Stop phone
client.stop_phone(phone_id)
print('Phone stopped.')
"
}

Remove-PSSession $s
