$VPS_IP   = '103.170.154.139'
$VPS_USER = 'Administrator'
$VPS_PASS = 'NjlW42jk6dTmSxJe4PDR'
$pass = ConvertTo-SecureString $VPS_PASS -AsPlainText -Force
$cred = New-Object System.Management.Automation.PSCredential($VPS_USER, $pass)
$so   = New-PSSessionOption -SkipCACheck -SkipCNCheck -SkipRevocationCheck
$s    = New-PSSession -ComputerName $VPS_IP -Credential $cred -SessionOption $so -Authentication Negotiate

Copy-Item -Path "C:\Users\Administrator\Desktop\AccountWarmer-Deploy\diagnose_settings_ui.py" `
          -Destination "C:\Users\Administrator\Desktop\AccountWarmer-Deploy\account-warmer\diagnose_settings_ui.py" `
          -ToSession $s

Write-Host "=== Running Settings UI diagnostic ==="
Invoke-Command -Session $s -ScriptBlock {
    cd "C:\Users\Administrator\Desktop\AccountWarmer-Deploy\account-warmer"
    python diagnose_settings_ui.py 2>&1
} -ErrorAction Continue

# Copy screenshots back
Write-Host ""
Write-Host "=== Copying screenshots back ==="
try {
    Copy-Item -Path "C:\WarmingData\screenshots\settings_main.png" `
              -Destination "C:\Users\Administrator\Desktop\AccountWarmer-Deploy\settings_main.png" `
              -FromSession $s -ErrorAction SilentlyContinue
    Write-Host "  settings_main.png copied"
} catch { Write-Host "  settings_main.png not found" }

try {
    Copy-Item -Path "C:\WarmingData\screenshots\settings_add_account.png" `
              -Destination "C:\Users\Administrator\Desktop\AccountWarmer-Deploy\settings_add_account.png" `
              -FromSession $s -ErrorAction SilentlyContinue
    Write-Host "  settings_add_account.png copied"
} catch { Write-Host "  settings_add_account.png not found" }

Remove-PSSession $s
