$VPS_IP   = '103.170.154.139'
$VPS_USER = 'Administrator'
$VPS_PASS = 'NjlW42jk6dTmSxJe4PDR'
$pass = ConvertTo-SecureString $VPS_PASS -AsPlainText -Force
$cred = New-Object System.Management.Automation.PSCredential($VPS_USER, $pass)
$so   = New-PSSessionOption -SkipCACheck -SkipCNCheck -SkipRevocationCheck
$s    = New-PSSession -ComputerName $VPS_IP -Credential $cred -SessionOption $so -Authentication Negotiate

Write-Host "=== Last 80 lines of combined log ==="
Invoke-Command -Session $s -ScriptBlock {
    $log = "C:\WarmingData\logs\combined.log"
    if (Test-Path $log) {
        Get-Content $log -Tail 80
    } else {
        Write-Host "No combined.log found"
        Get-ChildItem "C:\WarmingData\logs\" -ErrorAction SilentlyContinue
    }
}

Remove-PSSession $s
