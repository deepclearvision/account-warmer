$VPS_IP   = '103.170.154.139'
$VPS_USER = 'Administrator'
$VPS_PASS = 'NjlW42jk6dTmSxJe4PDR'
$pass = ConvertTo-SecureString $VPS_PASS -AsPlainText -Force
$cred = New-Object System.Management.Automation.PSCredential($VPS_USER, $pass)
$so   = New-PSSessionOption -SkipCACheck -SkipCNCheck -SkipRevocationCheck
$s    = New-PSSession -ComputerName $VPS_IP -Credential $cred -SessionOption $so -Authentication Negotiate

Copy-Item -Path "C:\Users\Administrator\Desktop\AccountWarmer-Deploy\diagnose_flow_screenshots.py" `
          -Destination "C:\Users\Administrator\Desktop\AccountWarmer-Deploy\account-warmer\diagnose_flow_screenshots.py" `
          -ToSession $s

Write-Host "=== Running screenshot diagnostic ==="
Invoke-Command -Session $s -ScriptBlock {
    cd "C:\Users\Administrator\Desktop\AccountWarmer-Deploy\account-warmer"
    python diagnose_flow_screenshots.py 2>&1
} -ErrorAction Continue

# Copy screenshots back
Write-Host ""
Write-Host "=== Copying screenshots back ==="
$remoteDir = "C:\WarmingData\screenshots\flow_run"
$localDir  = "C:\Users\Administrator\Desktop\AccountWarmer-Deploy\flow_screenshots"
New-Item -ItemType Directory -Force -Path $localDir | Out-Null

$files = Invoke-Command -Session $s -ScriptBlock {
    Get-ChildItem "C:\WarmingData\screenshots\flow_run\*.png" | Select-Object -ExpandProperty FullName
}
foreach ($f in $files) {
    $name = Split-Path $f -Leaf
    Copy-Item -Path $f -Destination "$localDir\$name" -FromSession $s
    Write-Host "  Copied $name"
}

Remove-PSSession $s
Write-Host ""
Write-Host "Screenshots saved to: $localDir"
