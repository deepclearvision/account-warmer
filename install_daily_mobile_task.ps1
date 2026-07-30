<#
.SYNOPSIS
    Install (or update) the AccountWarmerDailyMobile Windows Scheduled Task.

.DESCRIPTION
    Creates a daily scheduled task that runs the mobile warm-up batch runner
    at a random time between 09:00 and 17:00 each day.  The task launches
    daily_mobile_batch_runner.py --daemon which will run one warm-up session
    per account and then sleep until the next day.

    If the task already exists, the trigger time is updated rather than
    creating a duplicate.

    If the Account Warmer tray app is currently managing the batch runner,
    a warning is printed — the scheduled task can still be created but may
    conflict.

.PARAMETER RandomHour
    Override the random start hour (0-23).  If not specified, a random hour
    between 9 and 17 is picked.

.PARAMETER RandomMinute
    Override the random start minute (0-59).  If not specified, a random
    minute is picked.

.EXAMPLE
    .\install_daily_mobile_task.ps1
    Creates the task with a random daily trigger between 09:00 and 17:00.

.EXAMPLE
    .\install_daily_mobile_task.ps1 -RandomHour 10 -RandomMinute 30
    Creates the task to run daily at 10:30.
#>

param(
    [int]$RandomHour = -1,
    [int]$RandomMinute = -1
)

$ErrorActionPreference = "Stop"

# ── Constants ──────────────────────────────────────────────────────────────────

$TaskName       = "AccountWarmerDailyMobile"
$ProjectRoot    = "C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced"
$PythonExe      = "python"
$RunnerScript   = "daily_mobile_batch_runner.py"
$TaskDescription = "Runs one mobile warm-up session per GeelarK account each day. Launches daily_mobile_batch_runner.py --daemon which warms all enabled accounts sequentially, then sleeps until the next day's window."

# ── Pre-flight checks ──────────────────────────────────────────────────────────

Write-Host "=== Account Warmer — Daily Mobile Batch Task Installer ===" -ForegroundColor Cyan
Write-Host ""

if (-not (Test-Path $ProjectRoot)) {
    Write-Error "Project root not found: $ProjectRoot"
    exit 1
}

$runnerPath = Join-Path $ProjectRoot $RunnerScript
if (-not (Test-Path $runnerPath)) {
    Write-Error "Runner script not found: $runnerPath"
    exit 1
}

# ── Warn if tray app is likely managing the batch ──────────────────────────────

$trayRunning = Get-Process -Name "python" -ErrorAction SilentlyContinue |
    Where-Object { $_.MainWindowTitle -match "tray" -or $_.CommandLine -match "tray\.py" }

if ($trayRunning) {
    Write-Warning "The Account Warmer tray app appears to be running."
    Write-Warning "The tray app can also manage the daily mobile batch via its right-click menu."
    Write-Warning "If you use the tray's 'Start Daily Mobile Batch', disable this scheduled task"
    Write-Warning "to avoid two batch runners starting simultaneously."
    Write-Host ""
}

# ── Pick random time ───────────────────────────────────────────────────────────

if ($RandomHour -lt 0) {
    $RandomHour = Get-Random -Minimum 9 -Maximum 18    # 9..17 inclusive
}
if ($RandomMinute -lt 0) {
    $RandomMinute = Get-Random -Minimum 0 -Maximum 60  # 0..59 inclusive
}

$triggerTime = "{0:D2}:{1:D2}" -f $RandomHour, $RandomMinute
Write-Host "Daily trigger time: $triggerTime (local time)" -ForegroundColor Green
Write-Host ""

# ── Build the scheduled task action ────────────────────────────────────────────

$Action = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument "$RunnerScript --daemon" `
    -WorkingDirectory $ProjectRoot

# ── Build the trigger (daily at the chosen time) ───────────────────────────────

$Trigger = New-ScheduledTaskTrigger `
    -Daily `
    -At $triggerTime

# ── Build principal (current user, only when logged on, highest privileges) ────

$Principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive `
    -RunLevel Highest

# ── Build settings ─────────────────────────────────────────────────────────────

$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -Compatibility Win8

# ── Register (or update) the task ──────────────────────────────────────────────

$existingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue

if ($existingTask) {
    Write-Host "Task '$TaskName' already exists — updating trigger to daily at $triggerTime ..." -ForegroundColor Yellow

    # Unregister and re-register (cleanest way to update the trigger)
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false

    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $Action `
        -Trigger $Trigger `
        -Principal $Principal `
        -Settings $Settings `
        -Description $TaskDescription `
        -Force | Out-Null

    Write-Host "Task '$TaskName' updated successfully." -ForegroundColor Green
} else {
    Write-Host "Creating new scheduled task '$TaskName' ..." -ForegroundColor Yellow

    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $Action `
        -Trigger $Trigger `
        -Principal $Principal `
        -Settings $Settings `
        -Description $TaskDescription | Out-Null

    Write-Host "Task '$TaskName' created successfully." -ForegroundColor Green
}

# ── Summary ────────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "=== Installation Summary ===" -ForegroundColor Cyan
Write-Host "  Task name:       $TaskName"
Write-Host "  Runs daily at:   $triggerTime"
Write-Host "  Working dir:     $ProjectRoot"
Write-Host "  Command:         $PythonExe $RunnerScript --daemon"
Write-Host "  Run as:          $env:USERNAME (interactive, highest privileges)"
Write-Host ""
Write-Host "To view the task:   taskschd.msc" -ForegroundColor Gray
Write-Host "To remove the task: Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false" -ForegroundColor Gray
Write-Host "To run now (test):  Start-ScheduledTask -TaskName '$TaskName'" -ForegroundColor Gray
Write-Host ""
Write-Host "Done." -ForegroundColor Green
