$VPS_IP   = '103.170.154.139'
$VPS_USER = 'Administrator'
$VPS_PASS = 'NjlW42jk6dTmSxJe4PDR'
$pass = ConvertTo-SecureString $VPS_PASS -AsPlainText -Force
$cred = New-Object System.Management.Automation.PSCredential($VPS_USER, $pass)
$so   = New-PSSessionOption -SkipCACheck -SkipCNCheck -SkipRevocationCheck
$s    = New-PSSession -ComputerName $VPS_IP -Credential $cred -SessionOption $so -Authentication Negotiate

Write-Host "=== Account credential status ==="
Invoke-Command -Session $s -ScriptBlock {
    cd "C:\Users\Administrator\Desktop\AccountWarmer-Deploy\account-warmer"
    python -c "
import yaml
with open(r'C:\WarmingData\geelark_accounts.yaml', encoding='utf-8') as f:
    data = yaml.safe_load(f)
accounts = data.get('accounts', [])
no_pass  = [a for a in accounts if not a.get('password')]
no_totp  = [a for a in accounts if not a.get('totp_secret')]
no_phone = [a for a in accounts if not a.get('geelark_phone_id')]
print('Total accounts:     %d' % len(accounts))
print('Missing password:   %d' % len(no_pass))
print('Missing totp_secret:%d' % len(no_totp))
print('Missing phone_id:   %d' % len(no_phone))
if no_totp:
    print()
    print('Accounts with no totp_secret:')
    for a in no_totp:
        print('  %s | %s' % (a.get('id'), a.get('email')))
if no_pass:
    print()
    print('Accounts with no password:')
    for a in no_pass:
        print('  %s | %s' % (a.get('id'), a.get('email')))
"
}

Write-Host ""
Write-Host "=== Test: submit one login task manually and check result ==="
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

# Use first account with phone + flow + password + totp
acc = None
for a in accounts:
    if a.get('geelark_phone_id') and a.get('geelark_login_flow_id') and a.get('password') and a.get('totp_secret'):
        acc = a
        break

if not acc:
    print('ERROR: No account has all required fields (phone_id, flow_id, password, totp_secret)')
else:
    print('Testing with: %s (%s)' % (acc.get('id'), acc.get('email')))
    print('  phone_id:  %s' % acc.get('geelark_phone_id'))
    print('  flow_id:   %s' % acc.get('geelark_login_flow_id'))
    print('  password:  %s' % ('set (%d chars)' % len(acc.get('password','')) if acc.get('password') else 'MISSING'))
    print('  totp:      %s' % ('set (%d chars)' % len(acc.get('totp_secret','')) if acc.get('totp_secret') else 'MISSING'))

    # Try starting phone
    print()
    print('Starting phone...')
    try:
        url = client.start_phone(acc.get('geelark_phone_id'))
        print('Phone started. Viewer: %s' % url[:60])
        time.sleep(15)

        # Submit the flow task
        param_map = {
            'email':    acc.get('email'),
            'password': acc.get('password'),
        }
        if acc.get('totp_secret'):
            param_map['totp_secret'] = acc.get('totp_secret')
        print('Submitting custom flow task...')
        task_id = client.run_custom_flow(
            flow_id   = acc.get('geelark_login_flow_id'),
            phone_id  = acc.get('geelark_phone_id'),
            param_map = param_map,
            task_name = 'Diagnostic test login',
        )
        print('Task submitted: %s' % task_id)

        # Poll 3 times (24s) to see initial status
        for i in range(3):
            time.sleep(8)
            tasks = client.query_tasks([task_id])
            if tasks:
                t = tasks[0]
                status_name = {1:'Waiting',2:'InProgress',3:'Completed',4:'Failed',7:'Cancelled'}.get(t.get('status'), str(t.get('status')))
                print('  [%ds] status=%s failCode=%s failDesc=%s' % (
                    (i+1)*8, status_name,
                    t.get('failCode',''), t.get('failDesc','')))
                if t.get('status') in (3,4,7):
                    break

        # Stop phone
        client.stop_phone(acc.get('geelark_phone_id'))
        print('Phone stopped.')
    except Exception as e:
        print('ERROR: %s' % e)
"
}

Remove-PSSession $s
