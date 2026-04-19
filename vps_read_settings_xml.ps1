$VPS_IP   = '103.170.154.139'
$VPS_USER = 'Administrator'
$VPS_PASS = 'NjlW42jk6dTmSxJe4PDR'
$pass = ConvertTo-SecureString $VPS_PASS -AsPlainText -Force
$cred = New-Object System.Management.Automation.PSCredential($VPS_USER, $pass)
$so   = New-PSSessionOption -SkipCACheck -SkipCNCheck -SkipRevocationCheck
$s    = New-PSSession -ComputerName $VPS_IP -Credential $cred -SessionOption $so -Authentication Negotiate

Copy-Item -Path "C:\Users\Administrator\Desktop\AccountWarmer-Deploy\read_settings_xml.py" `
          -Destination "C:\Users\Administrator\Desktop\AccountWarmer-Deploy\account-warmer\read_settings_xml.py" `
          -ToSession $s

Invoke-Command -Session $s -ScriptBlock {
    python "C:\Users\Administrator\Desktop\AccountWarmer-Deploy\account-warmer\read_settings_xml.py" 2>&1
}

Remove-PSSession $s
