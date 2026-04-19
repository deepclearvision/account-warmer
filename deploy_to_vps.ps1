# deploy_to_vps.ps1
# Deploys latest code from this machine to the VPS AccountWarmer-Deploy folder.
# Copies every file individually to avoid Copy-Item -Recurse silent-skip bugs.
# Auto-restarts the VPS server after deploy.
#
# Usage:  Right-click → Run with PowerShell
#         OR from terminal: powershell -ExecutionPolicy Bypass -File deploy_to_vps.ps1

$VPS_IP    = "103.170.154.139"
$VPS_USER  = "Administrator"
$VPS_PASS  = "NjlW42jk6dTmSxJe4PDR"
$VPS_DEST  = "C:\Users\Administrator\Desktop\AccountWarmer-Deploy\account-warmer"
$LOCAL_SRC = "C:\Users\Administrator\Desktop\AccountWarmer-Deploy\account-warmer"

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Account Warmer — Deploy to VPS" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Source : $LOCAL_SRC"
Write-Host "  Target : $VPS_IP $VPS_DEST"
Write-Host ""

$pass = ConvertTo-SecureString $VPS_PASS -AsPlainText -Force
$cred = New-Object System.Management.Automation.PSCredential($VPS_USER, $pass)
$so   = New-PSSessionOption -SkipCACheck -SkipCNCheck -SkipRevocationCheck

Write-Host "Connecting to VPS..." -ForegroundColor Yellow
try {
    $session = New-PSSession -ComputerName $VPS_IP -Credential $cred -SessionOption $so -Authentication Negotiate -ErrorAction Stop
    Write-Host "Connected." -ForegroundColor Green
} catch {
    Write-Host "ERROR: Could not connect to VPS — $($_.Exception.Message)" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

# ── Ensure destination subdirectories exist on VPS ───────────────────────────
$foldersToCreate = @('activities', 'api', 'api\routers', 'core', 'data', 'static', 'config')
Invoke-Command -Session $session -ScriptBlock {
    param($dest, $folders)
    foreach ($f in $folders) {
        $p = Join-Path $dest $f
        if (-not (Test-Path $p)) { New-Item -ItemType Directory -Path $p -Force | Out-Null }
    }
} -ArgumentList $VPS_DEST, $foldersToCreate

# ── Copy every .py / .yaml / .html / .js / .css / .json file individually ───
$skipNames  = @('warmer.env')
$skipExt    = @('.png', '.jpg', '.jpeg', '.gif', '.bak')
$skipFolders = @('__pycache__', '.git', 'logs', 'WarmingData')

$allFiles = Get-ChildItem $LOCAL_SRC -Recurse -File | Where-Object {
    $skipNames  -notcontains $_.Name -and
    $skipExt    -notcontains $_.Extension.ToLower() -and
    # skip any file whose path contains a skip-folder segment
    ($skipFolders | ForEach-Object { $_.FullName -like "*\$_\*" } | Where-Object { $_ }) -eq $null
}

$total   = $allFiles.Count
$current = 0
foreach ($file in $allFiles) {
    $current++
    $rel  = $file.FullName.Substring($LOCAL_SRC.Length).TrimStart('\')
    $dest = Join-Path $VPS_DEST $rel
    $destDir = Split-Path $dest -Parent

    # Ensure parent directory exists on VPS
    Invoke-Command -Session $session -ScriptBlock {
        param($d) if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d -Force | Out-Null }
    } -ArgumentList $destDir

    Write-Host ("  [{0}/{1}] {2}" -f $current, $total, $rel) -ForegroundColor Gray
    Copy-Item -Path $file.FullName -Destination $dest -Force -ToSession $session
}

# ── Stop old server process on VPS, start new one ────────────────────────────
Write-Host ""
Write-Host "Restarting VPS server..." -ForegroundColor Yellow
Invoke-Command -Session $session -ScriptBlock {
    param($dest)
    # Kill existing python server
    $procs = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*api*main.py*' }
    foreach ($p in $procs) {
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep 1

    # Start new server
    $py   = (Get-Command python -ErrorAction SilentlyContinue).Source
    if (-not $py) { $py = 'python' }
    $proc = Start-Process -FilePath $py -ArgumentList 'api/main.py' -WorkingDirectory $dest -PassThru -WindowStyle Hidden
    Start-Sleep 8

    $listening = netstat -ano | Select-String ":8000.*LISTENING"
    if ($listening) {
        Write-Host "Server running (PID $($proc.Id)) — port 8000 LISTENING" -ForegroundColor Green
    } else {
        Write-Host "WARNING: port 8000 not listening yet — may still be starting up" -ForegroundColor Yellow
    }
} -ArgumentList $VPS_DEST

Remove-PSSession $session

Write-Host ""
Write-Host "Deploy complete." -ForegroundColor Green
Write-Host "NOTE: warmer.env on the VPS was NOT overwritten." -ForegroundColor Yellow
Write-Host ""
Read-Host "Press Enter to exit"
