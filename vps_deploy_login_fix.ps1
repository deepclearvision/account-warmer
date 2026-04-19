$VPS_IP   = '103.170.154.139'
$VPS_USER = 'Administrator'
$VPS_PASS = 'NjlW42jk6dTmSxJe4PDR'
$pass = ConvertTo-SecureString $VPS_PASS -AsPlainText -Force
$cred = New-Object System.Management.Automation.PSCredential($VPS_USER, $pass)
$so   = New-PSSessionOption -SkipCACheck -SkipCNCheck -SkipRevocationCheck
$s    = New-PSSession -ComputerName $VPS_IP -Credential $cred -SessionOption $so -Authentication Negotiate

$src = "C:\Users\Administrator\Desktop\AccountWarmer-Deploy\account-warmer"
$dst = "C:\Users\Administrator\Desktop\AccountWarmer-Deploy\account-warmer"

Write-Host "Deploying api/routers/mobile.py ..."
Copy-Item "$src\api\routers\mobile.py" -Destination "$dst\api\routers\mobile.py" -ToSession $s -Force

Write-Host "Deploying static/index.html ..."
Copy-Item "$src\static\index.html" -Destination "$dst\static\index.html" -ToSession $s -Force

Write-Host ""
Write-Host "Deploy complete. Restart the warmer on VPS to pick up the server change." -ForegroundColor Green

Remove-PSSession $s
