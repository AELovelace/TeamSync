<#
.SYNOPSIS
  Registers a Windows scheduled task that pushes new Box files to TeamSync.

.DESCRIPTION
  Writes a small wrapper .cmd in %LOCALAPPDATA%\TeamSync that holds the server URL and
  device token (restricted to the current user), then registers a task that runs it every
  N minutes. The task uses S4U logon, so it runs whether or not you are signed in and no
  password is stored. Box Drive itself only syncs while you are signed in, so a signed-out
  run can only ever see files that had already arrived.

.EXAMPLE
  .\tools\Register-PushTask.ps1 -Root "C:\Users\Edith\Box\Singh JE Reports" `
      -Server https://teamsync.eternalhotels.com -Token tsk_... -IntervalMinutes 15 -Insecure

.EXAMPLE
  # Re-register the task (new interval, logon type or script location) keeping the
  # existing wrapper and token:
  .\tools\Register-PushTask.ps1 -IntervalMinutes 10
#>
param(
  [string] $Root,
  [string] $Server,
  [string] $Token,
  [int] $IntervalMinutes = 15,
  [switch] $Insecure,
  [string] $CaBundle,
  [string] $TaskName = "TeamSync Box Push",
  [string] $Python = "python"
)

$ErrorActionPreference = "Stop"
$script = Join-Path $PSScriptRoot "push_from_box.py"
if (-not (Test-Path $script)) { throw "push_from_box.py not found next to this script" }

$appDir = Join-Path $env:LOCALAPPDATA "TeamSync"
New-Item -ItemType Directory -Force $appDir | Out-Null
$wrapper = Join-Path $appDir "push.cmd"
$log = Join-Path $appDir "push.log"

if ($Token) {
  if (-not ($Root -and $Server)) { throw "-Root and -Server are required when -Token is given" }
  $lines = @(
    "@echo off",
    "set TEAMSYNC_PUSH_ROOT=$Root",
    "set TEAMSYNC_PUSH_SERVER=$Server",
    "set TEAMSYNC_PUSH_TOKEN=$Token"
  )
  if ($Insecure) { $lines += "set TEAMSYNC_PUSH_INSECURE=1" }
  if ($CaBundle) { $lines += "set TEAMSYNC_PUSH_CA_BUNDLE=$CaBundle" }
  $lines += "`"$Python`" -I `"$script`" >> `"$log`" 2>&1"
  Set-Content -Path $wrapper -Value $lines -Encoding ASCII
  # Restrict the wrapper (it holds the token) to the current user.
  icacls $wrapper /inheritance:r /grant:r "$($env:USERNAME):(R,W)" | Out-Null
  Write-Host "Wrote $wrapper"
} elseif (Test-Path $wrapper) {
  if ($Root -or $Server -or $Insecure -or $CaBundle) {
    throw "Pass -Token together with -Root and -Server to rewrite $wrapper; without -Token the existing wrapper is kept as is"
  }
  Write-Host "Keeping existing $wrapper"
} else {
  throw "No wrapper at $wrapper yet: pass -Root, -Server and -Token"
}

$action = New-ScheduledTaskAction -Execute $wrapper
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
  -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
# S4U: runs as this user without an interactive session and without storing a password.
# Local files only (the Box folder is local), which is all the push needs.
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
Write-Host "Registered '$TaskName' every $IntervalMinutes minutes as $env:USERDOMAIN\$env:USERNAME (S4U, runs when signed out)."
Write-Host "Wrapper: $wrapper  Log: $log"
Write-Host "Run now with: Start-ScheduledTask -TaskName '$TaskName'"
