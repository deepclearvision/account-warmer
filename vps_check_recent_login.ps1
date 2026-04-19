$VPS_IP   = '103.170.154.139'
$VPS_USER = 'Administrator'
$VPS_PASS = 'NjlW42jk6dTmSxJe4PDR'
$pass = ConvertTo-SecureString $VPS_PASS -AsPlainText -Force
$cred = New-Object System.Management.Automation.PSCredential($VPS_USER, $pass)
$so   = New-PSSessionOption -SkipCACheck -SkipCNCheck -SkipRevocationCheck
$s    = New-PSSession -ComputerName $VPS_IP -Credential $cred -SessionOption $so -Authentication Negotiate

Write-Host "=== Most recent gl_ login logs ==="
Invoke-Command -Session $s -ScriptBlock {
    $files = Get-ChildItem "C:\WarmingData\logs\state\gl_*.log" | Sort-Object LastWriteTime -Descending | Select-Object -First 5
    foreach ($f in $files) {
        Write-Host "--- $($f.Name) (modified $($f.LastWriteTime)) ---"
        Get-Content $f.FullName
        Write-Host ""
    }
}

Write-Host "=== Last 60 lines of combined log ==="
Invoke-Command -Session $s -ScriptBlock {
    Get-Content "C:\WarmingData\logs\combined.log" -Tail 60
}

Remove-PSSession $s
