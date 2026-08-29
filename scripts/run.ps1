$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$pythonPath = Join-Path $env:LOCALAPPDATA 'ChessTrainer\venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Run scripts\setup.ps1 first.'
}
& $pythonPath (Join-Path $projectRoot 'main.py')

