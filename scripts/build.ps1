[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$pythonPath = Join-Path $env:LOCALAPPDATA 'ChessTrainer\venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Run scripts\setup.ps1 before building.'
}

& $pythonPath (Join-Path $PSScriptRoot 'make_icon.py')
$iconPath = Join-Path $projectRoot 'chess_trainer\resources\knight.ico'
$resourceSeparator = ';'
Push-Location $projectRoot
try {
    & $pythonPath -m PyInstaller `
        --noconfirm `
        --clean `
        --windowed `
        --onedir `
        --name 'ChessTrainer' `
        --icon $iconPath `
        --add-data "chess_trainer/resources/knight.svg${resourceSeparator}chess_trainer/resources" `
        --add-data "extension${resourceSeparator}extension" `
        --add-data "vendor/stockfish-18/stockfish-windows-x86-64-bmi2.exe${resourceSeparator}engine" `
        --add-data "vendor/stockfish-18/COPYING.txt${resourceSeparator}engine" `
        main.py
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }
} finally {
    Pop-Location
}
Write-Host "Built: $(Join-Path $projectRoot 'dist\ChessTrainer\ChessTrainer.exe')"
