$VPS_IP   = '103.170.154.139'
$VPS_USER = 'Administrator'
$VPS_PASS = 'NjlW42jk6dTmSxJe4PDR'
$pass = ConvertTo-SecureString $VPS_PASS -AsPlainText -Force
$cred = New-Object System.Management.Automation.PSCredential($VPS_USER, $pass)
$so   = New-PSSessionOption -SkipCACheck -SkipCNCheck -SkipRevocationCheck
$s    = New-PSSession -ComputerName $VPS_IP -Credential $cred -SessionOption $so -Authentication Negotiate

Write-Host "=== Stopping all GeelarK phones ==="
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
client = GeelarKClient()

with open(r'C:\WarmingData\geelark_accounts.yaml', encoding='utf-8') as f:
    data = yaml.safe_load(f)
accounts = data.get('accounts', [])
phone_ids = [a.get('geelark_phone_id') for a in accounts if a.get('geelark_phone_id')]

stopped = 0
errors  = 0
for pid in phone_ids:
    try:
        ok = client.stop_phone(pid)
        if ok:
            stopped += 1
        # small delay to avoid rate limiting
        time.sleep(0.3)
    except Exception as e:
        errors += 1

print('Stopped %d phones. Errors: %d' % (stopped, errors))
print('All login operations have been terminated.')
"
}

Remove-PSSession $s
