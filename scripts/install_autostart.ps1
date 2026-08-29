[CmdletBinding()]
param(
    [switch]$Remove
)

$ErrorActionPreference = 'Stop'
$taskName = 'ChessTrainerControlCentre'

if ($Remove) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Removed scheduled task: $taskName"
    exit 0
}

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$pythonPath = Join-Path $env:LOCALAPPDATA 'ChessTrainer\venv\Scripts\pythonw.exe'
$entryPoint = Join-Path $projectRoot 'main.py'

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Run scripts\setup.ps1 before installing autostart.'
}
if (-not (Test-Path -LiteralPath $entryPoint)) {
    throw "Chess Trainer entry point not found: $entryPoint"
}

$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction `
    -Execute $pythonPath `
    -Argument "`"$entryPoint`"" `
    -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity
$trigger.Delay = 'PT10S'
$principal = New-ScheduledTaskPrincipal `
    -UserId $identity `
    -LogonType Interactive `
    -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 10 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description 'Starts the lightweight local Chess Trainer bridge after sign-in; engines start only while the extension is in use.' `
    -Force | Out-Null

Write-Host "Installed scheduled task: $taskName"
Write-Host "It starts the Chess Trainer bridge 10 seconds after $identity signs in and retries failures up to 10 times. Engines remain request-started."
