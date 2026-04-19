$VPS_IP   = '103.170.154.139'
$VPS_USER = 'Administrator'
$VPS_PASS = 'NjlW42jk6dTmSxJe4PDR'
$pass = ConvertTo-SecureString $VPS_PASS -AsPlainText -Force
$cred = New-Object System.Management.Automation.PSCredential($VPS_USER, $pass)
$so   = New-PSSessionOption -SkipCACheck -SkipCNCheck -SkipRevocationCheck
try {
    $s = New-PSSession -ComputerName $VPS_IP -Credential $cred -SessionOption $so -Authentication Negotiate -ErrorAction Stop
    Write-Host "Connected to VPS"
    $result = Invoke-Command -Session $s -ScriptBlock {
        Set-Location "C:\Users\Administrator\Desktop\AccountWarmer-Deploy\account-warmer"
        python -c @"
import yaml
with open(r'C:\WarmingData\geelark_accounts.yaml', encoding='utf-8') as f:
    data = yaml.safe_load(f)
accounts = data.get('accounts', [])
has_phone = [a for a in accounts if a.get('geelark_phone_id')]
no_phone  = [a for a in accounts if not a.get('geelark_phone_id')]
has_flow  = [a for a in accounts if a.get('geelark_login_flow_id')]
print(f'Total: {len(accounts)}, Has phone: {len(has_phone)}, No phone: {len(no_phone)}, Has flow: {len(has_flow)}')
for a in has_phone:
    fid = a.get('geelark_login_flow_id', 'NONE')
    print(f"{a['id']} | {a.get('email','')} | phone={str(a.get('geelark_phone_id',''))[:10]}... | flow={fid}")
"@
    }
    Write-Host $result
    Remove-PSSession $s
} catch {
    Write-Host "ERROR: $($_.Exception.Message)"
}
