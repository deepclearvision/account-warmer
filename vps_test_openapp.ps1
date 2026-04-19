$VPS_IP   = '103.170.154.139'
$VPS_USER = 'Administrator'
$VPS_PASS = 'NjlW42jk6dTmSxJe4PDR'
$pass = ConvertTo-SecureString $VPS_PASS -AsPlainText -Force
$cred = New-Object System.Management.Automation.PSCredential($VPS_USER, $pass)
$so   = New-PSSessionOption -SkipCACheck -SkipCNCheck -SkipRevocationCheck
$s    = New-PSSession -ComputerName $VPS_IP -Credential $cred -SessionOption $so -Authentication Negotiate

Copy-Item -Path "C:\Users\Administrator\Desktop\AccountWarmer-Deploy\test_openapp.py" `
          -Destination "C:\Users\Administrator\Desktop\AccountWarmer-Deploy\account-warmer\test_openapp.py" `
          -ToSession $s

Write-Host "=== Testing openApp RPA step ==="
Invoke-Command -Session $s -ScriptBlock {
    cd "C:\Users\Administrator\Desktop\AccountWarmer-Deploy\account-warmer"
    python test_openapp.py 2>&1
} -ErrorAction Continue

Write-Host ""
Write-Host "=== Copying screenshots ==="
$localDir = "C:\Users\Administrator\Desktop\AccountWarmer-Deploy\openapp_screenshots"
New-Item -ItemType Directory -Force -Path $localDir | Out-Null
$files = Invoke-Command -Session $s -ScriptBlock {
    Get-ChildItem "C:\WarmingData\screenshots\openapp_test\*.png" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty FullName
}
foreach ($f in $files) {
    $name = Split-Path $f -Leaf
    Copy-Item -Path $f -Destination "$localDir\$name" -FromSession $s
    Write-Host "  $name"
}

Remove-PSSession $s
