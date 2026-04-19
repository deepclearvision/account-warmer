$VPS_IP   = '103.170.154.139'
$VPS_USER = 'Administrator'
$VPS_PASS = 'NjlW42jk6dTmSxJe4PDR'
$pass = ConvertTo-SecureString $VPS_PASS -AsPlainText -Force
$cred = New-Object System.Management.Automation.PSCredential($VPS_USER, $pass)
$so   = New-PSSessionOption -SkipCACheck -SkipCNCheck -SkipRevocationCheck
$s    = New-PSSession -ComputerName $VPS_IP -Credential $cred -SessionOption $so -Authentication Negotiate

Write-Host "=== google_login_results.json ==="
Invoke-Command -Session $s -ScriptBlock {
    Get-Content "C:\WarmingData\logs\state\google_login_results.json" -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "=== Most recent gl_ login log ==="
Invoke-Command -Session $s -ScriptBlock {
    $latest = Get-ChildItem "C:\WarmingData\logs\state\gl_*.log" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($latest) {
        Write-Host "File: $($latest.FullName) (modified $($latest.LastWriteTime))"
        Get-Content $latest.FullName
    }
}

Write-Host ""
Write-Host "=== geelark_login_flow_id check on first account ==="
Invoke-Command -Session $s -ScriptBlock {
    cd "C:\Users\Administrator\Desktop\AccountWarmer-Deploy\account-warmer"
    python -c "
import yaml
with open(r'C:\WarmingData\geelark_accounts.yaml', encoding='utf-8') as f:
    data = yaml.safe_load(f)
a = data['accounts'][0]
print('id:', a.get('id'))
print('email:', a.get('email'))
print('phone_id:', a.get('geelark_phone_id'))
print('flow_id:', a.get('geelark_login_flow_id'))
print('login_verified:', a.get('login_verified'))
"
}

Remove-PSSession $s
