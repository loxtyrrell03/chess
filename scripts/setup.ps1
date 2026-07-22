[CmdletBinding()]
param(
    [switch]$SkipPython,
    [switch]$ForceEngineDownload
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$appDataRoot = Join-Path $env:LOCALAPPDATA 'ChessTrainer'
$venvPath = Join-Path $appDataRoot 'venv'
$pythonPath = Join-Path $venvPath 'Scripts\python.exe'
$engineDirectory = Join-Path $projectRoot 'vendor\stockfish-18'
$enginePath = Join-Path $engineDirectory 'stockfish-windows-x86-64-bmi2.exe'
$archivePath = Join-Path $env:TEMP 'stockfish-18-windows-x86-64-bmi2.zip'
$extractPath = Join-Path $env:TEMP 'chess-trainer-stockfish-18'
$downloadUrl = 'https://github.com/official-stockfish/Stockfish/releases/download/sf_18/stockfish-windows-x86-64-bmi2.zip'
$expectedSha256 = 'c0b06a547deb261bf35456773155354b00b228ef853c51dcedbbb7c580477ece'

if (-not $SkipPython) {
    New-Item -ItemType Directory -Path $appDataRoot -Force | Out-Null
    if (-not (Test-Path -LiteralPath $pythonPath)) {
        py -3.12 -m venv $venvPath
    }
    & $pythonPath -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed with exit code $LASTEXITCODE" }
    & $pythonPath -m pip install -e "$projectRoot[dev]"
    if ($LASTEXITCODE -ne 0) { throw "dependency installation failed with exit code $LASTEXITCODE" }
}

if ($ForceEngineDownload -or -not (Test-Path -LiteralPath $enginePath)) {
    Invoke-WebRequest -Uri $downloadUrl -OutFile $archivePath -UseBasicParsing
    $actualSha256 = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualSha256 -ne $expectedSha256) {
        throw "Stockfish archive checksum mismatch. Expected $expectedSha256, received $actualSha256."
    }

    if (Test-Path -LiteralPath $extractPath) {
        $resolvedExtract = [System.IO.Path]::GetFullPath($extractPath)
        $resolvedTemp = [System.IO.Path]::GetFullPath($env:TEMP)
        if (-not $resolvedExtract.StartsWith($resolvedTemp, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw 'Refusing to clear an extraction path outside the temporary directory.'
        }
        Remove-Item -LiteralPath $resolvedExtract -Recurse -Force
    }
    New-Item -ItemType Directory -Path $extractPath | Out-Null
    Expand-Archive -LiteralPath $archivePath -DestinationPath $extractPath -Force
    $downloadedEngine = Get-ChildItem -LiteralPath $extractPath -Recurse -File |
        Where-Object { $_.Name -eq 'stockfish-windows-x86-64-bmi2.exe' } |
        Select-Object -First 1
    if (-not $downloadedEngine) {
        throw 'The Stockfish executable was not present in the official archive.'
    }
    New-Item -ItemType Directory -Path $engineDirectory -Force | Out-Null
    Copy-Item -LiteralPath $downloadedEngine.FullName -Destination $enginePath -Force

    $license = Get-ChildItem -LiteralPath $extractPath -Recurse -File |
        Where-Object { $_.Name -in @('Copying.txt', 'COPYING') } |
        Select-Object -First 1
    if ($license) {
        Copy-Item -LiteralPath $license.FullName -Destination (Join-Path $engineDirectory 'COPYING.txt') -Force
    }
}

Write-Host "Chess Trainer setup complete."
Write-Host "Python: $pythonPath"
Write-Host "Stockfish: $enginePath"
