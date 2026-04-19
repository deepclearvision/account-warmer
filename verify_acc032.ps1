$VPS_IP   = '103.170.154.139'
$VPS_USER = 'Administrator'
$VPS_PASS = 'NjlW42jk6dTmSxJe4PDR'
$pass = ConvertTo-SecureString $VPS_PASS -AsPlainText -Force
$cred = New-Object System.Management.Automation.PSCredential($VPS_USER, $pass)
$so   = New-PSSessionOption -SkipCACheck -SkipCNCheck -SkipRevocationCheck
$s    = New-PSSession -ComputerName $VPS_IP -Credential $cred -SessionOption $so -Authentication Negotiate

Write-Host "=== Verifying acc_032 (cleoperry9987@gmail.com) AccountManager ==="
Invoke-Command -Session $s -ScriptBlock {
    cd "C:\Users\Administrator\Desktop\AccountWarmer-Deploy\account-warmer"
    python -c "
import os, yaml, time, logging
from pathlib import Path
_env = Path('warmer.env')
for line in _env.read_text(encoding='utf-8').splitlines():
    line = line.strip()
    if line and not line.startswith('#') and '=' in line:
        k, _, v = line.partition('=')
        os.environ.setdefault(k.strip(), v.strip())

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
from core.geelark_client import GeelarKClient
from activities.google_login_mobile import _shell

client = GeelarKClient()
with open(r'C:\WarmingData\geelark_accounts.yaml', encoding='utf-8') as f:
    data = yaml.safe_load(f)

acc = next(a for a in data['accounts'] if a.get('id') == 'acc_032')
phone_id = acc['geelark_phone_id']
email = acc['email']
print(f'Account: {email}')
print(f'Phone:   {phone_id}')
print()

# Start phone
print('Starting phone...')
client.start_phone(phone_id)
for i in range(30):
    time.sleep(5)
    ok, out = _shell(phone_id, 'wm size')
    if ok and out.strip():
        print(f'Phone ready after {(i+1)*5}s')
        break
else:
    print('Phone did not become ready in 150s')
    exit(1)

time.sleep(3)

# Check AccountManager
print()
print('Checking AccountManager...')
ok, out = _shell(phone_id, 'dumpsys account')
if not ok:
    print(f'dumpsys failed: {out}')
else:
    if email in out:
        print(f'SUCCESS: {email} IS in AccountManager!')
        # Find the relevant lines
        for line in out.splitlines():
            if email in line or 'Account {' in line:
                print(f'  {line.strip()}')
    else:
        print(f'FAIL: {email} NOT found in AccountManager')
        # Show what accounts are there
        import re
        accounts = re.findall(r'Account \{[^}]+\}', out)
        if accounts:
            print('Accounts found on phone:')
            for a in accounts:
                print(f'  {a}')
        else:
            print('No accounts found at all')

client.stop_phone(phone_id)
print()
print('Done.')
" 2>&1
} -ErrorAction Continue

Remove-PSSession $s
