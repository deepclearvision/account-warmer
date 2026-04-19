$VPS_IP   = '103.170.154.139'
$VPS_USER = 'Administrator'
$VPS_PASS = 'NjlW42jk6dTmSxJe4PDR'
$pass = ConvertTo-SecureString $VPS_PASS -AsPlainText -Force
$cred = New-Object System.Management.Automation.PSCredential($VPS_USER, $pass)
$so   = New-PSSessionOption -SkipCACheck -SkipCNCheck -SkipRevocationCheck
$s    = New-PSSession -ComputerName $VPS_IP -Credential $cred -SessionOption $so -Authentication Negotiate

Write-Host "=== Deploying pure ADB login to VPS ==="
Copy-Item -Path "C:\Users\Administrator\Desktop\AccountWarmer-Deploy\account-warmer\activities\google_login_mobile.py" `
          -Destination "C:\Users\Administrator\Desktop\AccountWarmer-Deploy\account-warmer\activities\google_login_mobile.py" `
          -ToSession $s

Write-Host "=== Running login test on first account ==="
Invoke-Command -Session $s -ScriptBlock {
    cd "C:\Users\Administrator\Desktop\AccountWarmer-Deploy\account-warmer"
    python -c "
import os, yaml
from pathlib import Path
_env = Path('warmer.env')
for line in _env.read_text(encoding='utf-8').splitlines():
    line = line.strip()
    if line and not line.startswith('#') and '=' in line:
        k, _, v = line.partition('=')
        os.environ.setdefault(k.strip(), v.strip())

import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

from activities.google_login_mobile import run_google_login

with open(r'C:\WarmingData\geelark_accounts.yaml', encoding='utf-8') as f:
    data = yaml.safe_load(f)

# Use first account that has all required fields
acc = None
for a in data['accounts']:
    if a.get('geelark_phone_id') and a.get('email') and a.get('password') and a.get('totp_secret'):
        acc = a
        break

if not acc:
    print('ERROR: No account with all required fields found')
    exit(1)

print('Testing login for:', acc['email'])
print('Phone ID:', acc['geelark_phone_id'])
print()

result = run_google_login(acc, stop_phone_on_success=True)
print()
print('=== RESULT ===')
print('Success:', result['success'])
print('Diagnosis:', result['diagnosis'])
print('Attempts:', result['attempts'])
if result.get('error'):
    print('Error:', result['error'])
if result.get('needs_user_input'):
    print('Needs user input: YES')
" 2>&1
} -ErrorAction Continue

Remove-PSSession $s
