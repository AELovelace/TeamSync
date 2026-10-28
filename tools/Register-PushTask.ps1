<#
.SYNOPSIS
  Registers a Windows scheduled task that pushes new Box files to TeamSync.

.EXAMPLE
  .\tools\Register-PushTask.ps1 -Root "C:\Users\Edith\Box\Singh JE Reports" `
      -Server https://teamsync.eternalhotels.com -Token tsk_... -IntervalMinutes 15 -Insecure

  The token is stored in the task's environment through a small wrapper .cmd in
  %LOCALAPPDATA%\TeamSync so it never appears in the task's command line.
#>
param(
  [Parameter(Mandatory)] [string] $Root,
  [Parameter(Mandatory)] [string] $Server,
  [Parameter(Mandatory)] [string] $Token,
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

$action = New-ScheduledTaskAction -Execute $wrapper
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
  -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Write-Host "Registered '$TaskName' every $IntervalMinutes minutes. Wrapper: $wrapper  Log: $log"
Write-Host "Run now with: Start-ScheduledTask -TaskName '$TaskName'"
