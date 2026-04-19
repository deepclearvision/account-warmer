$VPS_IP   = '103.170.154.139'
$VPS_USER = 'Administrator'
$VPS_PASS = 'NjlW42jk6dTmSxJe4PDR'
$pass = ConvertTo-SecureString $VPS_PASS -AsPlainText -Force
$cred = New-Object System.Management.Automation.PSCredential($VPS_USER, $pass)
$so   = New-PSSessionOption -SkipCACheck -SkipCNCheck -SkipRevocationCheck
$s    = New-PSSession -ComputerName $VPS_IP -Credential $cred -SessionOption $so -Authentication Negotiate

Copy-Item -Path "C:\Users\Administrator\Desktop\AccountWarmer-Deploy\check_phone_model.py" `
          -Destination "C:\Users\Administrator\Desktop\AccountWarmer-Deploy\account-warmer\check_phone_model.py" `
          -ToSession $s

Invoke-Command -Session $s -ScriptBlock {
    cd "C:\Users\Administrator\Desktop\AccountWarmer-Deploy\account-warmer"
    python check_phone_model.py 2>&1
} -ErrorAction Continue

Remove-PSSession $s
