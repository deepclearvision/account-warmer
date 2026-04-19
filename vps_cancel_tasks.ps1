$VPS_IP   = '103.170.154.139'
$VPS_USER = 'Administrator'
$VPS_PASS = 'NjlW42jk6dTmSxJe4PDR'
$pass = ConvertTo-SecureString $VPS_PASS -AsPlainText -Force
$cred = New-Object System.Management.Automation.PSCredential($VPS_USER, $pass)
$so   = New-PSSessionOption -SkipCACheck -SkipCNCheck -SkipRevocationCheck
$s    = New-PSSession -ComputerName $VPS_IP -Credential $cred -SessionOption $so -Authentication Negotiate

Write-Host "=== Cancelling all running GeelarK tasks ==="
Invoke-Command -Session $s -ScriptBlock {
    cd "C:\Users\Administrator\Desktop\AccountWarmer-Deploy\account-warmer"
    python -c "
import os, sys
from pathlib import Path

_env = Path('warmer.env')
for line in _env.read_text(encoding='utf-8').splitlines():
    line = line.strip()
    if line and not line.startswith('#') and '=' in line:
        k, _, v = line.partition('=')
        os.environ.setdefault(k.strip(), v.strip())

from core.geelark_client import GeelarKClient
client = GeelarKClient()

# Query all tasks that are waiting or in-progress
import requests, json, time
headers = {
    'Content-Type': 'application/json',
    'accessKey': os.environ.get('GEELARK_API_KEY', '')
}
base = 'https://openapi.geelark.com'

# Get running tasks - query each phone's tasks
import yaml
with open(r'C:\WarmingData\geelark_accounts.yaml', encoding='utf-8') as f:
    data = yaml.safe_load(f)
accounts = data.get('accounts', [])

phone_ids = [a.get('geelark_phone_id') for a in accounts if a.get('geelark_phone_id')]
print('Checking tasks for', len(phone_ids), 'phones...')

# Cancel all in-progress/waiting tasks via task cancel endpoint
cancelled = 0
failed = 0
for phone_id in phone_ids:
    try:
        r = requests.post(base + '/open/v1/task/rpa/cancel',
                         json={'id': phone_id},
                         headers=headers, timeout=10)
        resp = r.json()
        if resp.get('code') == 0:
            cancelled += 1
            print('Cancelled tasks for phone', str(phone_id)[:10], '...')
        else:
            code = resp.get('code')
            msg  = resp.get('msg', '')
            if code not in (42001, 42002):  # 42001=no task, 42002=already done
                print('  Note phone', str(phone_id)[:10], '->', code, msg)
    except Exception as e:
        failed += 1
        print('  Error for', str(phone_id)[:10], ':', e)

print()
print('Done. Cancellation requests sent for all phones.')
print('Also stop and restart the warmer on the VPS to kill background login threads.')
"
}

Remove-PSSession $s
