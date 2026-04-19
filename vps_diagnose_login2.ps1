$VPS_IP   = '103.170.154.139'
$VPS_USER = 'Administrator'
$VPS_PASS = 'NjlW42jk6dTmSxJe4PDR'
$pass = ConvertTo-SecureString $VPS_PASS -AsPlainText -Force
$cred = New-Object System.Management.Automation.PSCredential($VPS_USER, $pass)
$so   = New-PSSessionOption -SkipCACheck -SkipCNCheck -SkipRevocationCheck
$s    = New-PSSession -ComputerName $VPS_IP -Credential $cred -SessionOption $so -Authentication Negotiate

Write-Host "=== Check for NEW gl_ log files today ==="
Invoke-Command -Session $s -ScriptBlock {
    Get-ChildItem "C:\WarmingData\logs\state\gl_*.log" | Where-Object { $_.LastWriteTime -gt (Get-Date).AddHours(-3) } | Sort-Object LastWriteTime -Descending | ForEach-Object {
        Write-Host "--- $($_.Name) ($($_.LastWriteTime)) ---"
        Get-Content $_.FullName
    }
    if (-not (Get-ChildItem "C:\WarmingData\logs\state\gl_*.log" | Where-Object { $_.LastWriteTime -gt (Get-Date).AddHours(-3) })) {
        Write-Host "No new gl_ logs in the last 3 hours"
    }
}

Write-Host ""
Write-Host "=== Run actual run_google_login for one account (wait full cycle) ==="
Invoke-Command -Session $s -ScriptBlock {
    cd "C:\Users\Administrator\Desktop\AccountWarmer-Deploy\account-warmer"
    python -c "
import os, sys, logging, yaml, time
from pathlib import Path

# Setup logging to stdout so we can see it
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s %(name)s %(levelname)s %(message)s',
    stream=sys.stdout
)

_env = Path('warmer.env')
for line in _env.read_text(encoding='utf-8').splitlines():
    line = line.strip()
    if line and not line.startswith('#') and '=' in line:
        k, _, v = line.partition('=')
        os.environ.setdefault(k.strip(), v.strip())

with open(r'C:\WarmingData\geelark_accounts.yaml', encoding='utf-8') as f:
    data = yaml.safe_load(f)
accounts = data.get('accounts', [])

# First account with all credentials
acc = next((a for a in accounts if a.get('geelark_phone_id') and a.get('geelark_login_flow_id') and a.get('password') and a.get('totp_secret')), None)
if not acc:
    print('ERROR: No qualifying account found')
    sys.exit(1)

print('=== Testing login for: %s ===' % acc.get('email'))

from activities.google_login_mobile import run_google_login
result = run_google_login(acc, stop_phone_on_success=True)
print()
print('=== RESULT ===')
for k, v in result.items():
    if k != 'last_screenshot_b64':
        print('  %s: %s' % (k, v))
" 2>&1
} -ErrorAction Continue

Remove-PSSession $s
