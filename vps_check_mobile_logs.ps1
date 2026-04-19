$VPS_IP   = '103.170.154.139'
$VPS_USER = 'Administrator'
$VPS_PASS = 'NjlW42jk6dTmSxJe4PDR'
$pass = ConvertTo-SecureString $VPS_PASS -AsPlainText -Force
$cred = New-Object System.Management.Automation.PSCredential($VPS_USER, $pass)
$so   = New-PSSessionOption -SkipCACheck -SkipCNCheck -SkipRevocationCheck
$s    = New-PSSession -ComputerName $VPS_IP -Credential $cred -SessionOption $so -Authentication Negotiate

Write-Host "=== Log files in WarmingData ==="
Invoke-Command -Session $s -ScriptBlock {
    Get-ChildItem "C:\WarmingData\logs\" -Recurse -File | Sort-Object LastWriteTime -Descending | Select-Object FullName, LastWriteTime, Length | Format-Table -AutoSize
}

Write-Host "=== Searching all logs for mobile/geelark/login activity ==="
Invoke-Command -Session $s -ScriptBlock {
    Get-ChildItem "C:\WarmingData\logs\" -Recurse -Filter "*.log" | ForEach-Object {
        $matches = Select-String -Path $_.FullName -Pattern "geelark|mobile|Mode A|Mode B|flow_id|googleLogin|login_flow" -CaseSensitive:$false
        if ($matches) {
            Write-Host "--- $($_.FullName) ---"
            $matches | Select-Object -Last 20 | ForEach-Object { Write-Host $_.Line }
        }
    }
}

Remove-PSSession $s
