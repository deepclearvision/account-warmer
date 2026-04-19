$VPS_IP   = '103.170.154.139'
$VPS_USER = 'Administrator'
$VPS_PASS = 'NjlW42jk6dTmSxJe4PDR'
$pass = ConvertTo-SecureString $VPS_PASS -AsPlainText -Force
$cred = New-Object System.Management.Automation.PSCredential($VPS_USER, $pass)
$so   = New-PSSessionOption -SkipCACheck -SkipCNCheck -SkipRevocationCheck
$s    = New-PSSession -ComputerName $VPS_IP -Credential $cred -SessionOption $so -Authentication Negotiate

Write-Host "=== Confirming flow ID set on all accounts ==="
Invoke-Command -Session $s -ScriptBlock {
    cd "C:\Users\Administrator\Desktop\AccountWarmer-Deploy\account-warmer"
    python -c "
import yaml
with open(r'C:\WarmingData\geelark_accounts.yaml', encoding='utf-8') as f:
    data = yaml.safe_load(f)
accounts = data['accounts']
with_flow = [a for a in accounts if a.get('geelark_login_flow_id')]
without   = [a for a in accounts if not a.get('geelark_login_flow_id')]
print('Accounts with flow_id set:', len(with_flow))
print('Accounts WITHOUT flow_id:', len(without))
if without:
    for a in without:
        print(' MISSING:', a.get('id'), a.get('email'))
"
}

Remove-PSSession $s
