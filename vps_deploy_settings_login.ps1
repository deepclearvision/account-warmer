$VPS_IP   = '103.170.154.139'
$VPS_USER = 'Administrator'
$VPS_PASS = 'NjlW42jk6dTmSxJe4PDR'
$pass = ConvertTo-SecureString $VPS_PASS -AsPlainText -Force
$cred = New-Object System.Management.Automation.PSCredential($VPS_USER, $pass)
$so   = New-PSSessionOption -SkipCACheck -SkipCNCheck -SkipRevocationCheck
$s    = New-PSSession -ComputerName $VPS_IP -Credential $cred -SessionOption $so -Authentication Negotiate

$localBase  = "C:\Users\Administrator\Desktop\AccountWarmer-Deploy\account-warmer"
$remoteBase = "C:\Users\Administrator\Desktop\AccountWarmer-Deploy\account-warmer"

Write-Host "=== Copying updated files to VPS ==="

# Core files changed
Copy-Item -Path "$localBase\core\geelark_flow_builder.py" `
          -Destination "$remoteBase\core\geelark_flow_builder.py" `
          -ToSession $s
Write-Host "  Copied geelark_flow_builder.py"

Copy-Item -Path "$localBase\activities\google_login_mobile.py" `
          -Destination "$remoteBase\activities\google_login_mobile.py" `
          -ToSession $s
Write-Host "  Copied google_login_mobile.py"

# Update script
Copy-Item -Path "C:\Users\Administrator\Desktop\AccountWarmer-Deploy\update_login_flow.py" `
          -Destination "$remoteBase\update_login_flow.py" `
          -ToSession $s
Write-Host "  Copied update_login_flow.py"

Write-Host ""
Write-Host "=== Updating GeelarK flow on server ==="
Invoke-Command -Session $s -ScriptBlock {
    cd "C:\Users\Administrator\Desktop\AccountWarmer-Deploy\account-warmer"
    python update_login_flow.py 2>&1
}

Remove-PSSession $s
Write-Host ""
Write-Host "=== Done ==="
Write-Host "The GeelarK flow has been updated to use Settings-based account addition."
Write-Host "Next login attempt will use the new flow."
