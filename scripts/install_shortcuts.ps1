[CmdletBinding()]
param(
    [switch]$DesktopShortcut
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$builtExe = Join-Path $projectRoot 'dist\ChessTrainer\ChessTrainer.exe'
$sourcePython = Join-Path $env:LOCALAPPDATA 'ChessTrainer\venv\Scripts\pythonw.exe'
$sourceEntry = Join-Path $projectRoot 'main.py'
$icon = Join-Path $projectRoot 'chess_trainer\resources\knight.ico'

if (Test-Path -LiteralPath $builtExe) {
    $target = $builtExe
    $arguments = ''
    $workingDirectory = Split-Path -Parent $builtExe
    $shortcutIcon = $builtExe
} elseif (Test-Path -LiteralPath $sourcePython) {
    $target = $sourcePython
    $arguments = "`"$sourceEntry`""
    $workingDirectory = $projectRoot
    $shortcutIcon = $icon
} else {
    throw 'Run scripts\setup.ps1 first.'
}

$startMenu = [Environment]::GetFolderPath('Programs')
$shortcutPath = Join-Path $startMenu 'Chess Trainer.lnk'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $target
$shortcut.Arguments = $arguments
$shortcut.WorkingDirectory = $workingDirectory
$shortcut.IconLocation = "$shortcutIcon,0"
$shortcut.Description = 'Open the Chess Trainer Control Centre'
$shortcut.Save()

if ($DesktopShortcut) {
    $desktopPath = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Chess Trainer.lnk'
    Copy-Item -LiteralPath $shortcutPath -Destination $desktopPath -Force
}

Write-Host "Start menu shortcut installed: $shortcutPath"
Write-Host 'Open it once to place the tray icon in the taskbar notification area. You can also right-click the Start shortcut and choose Pin to taskbar.'
