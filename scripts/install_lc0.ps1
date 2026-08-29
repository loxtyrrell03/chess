[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$downloadRoot = Join-Path $env:LOCALAPPDATA 'ChessTrainer\downloads'
$engineRoot = Join-Path $env:LOCALAPPDATA 'ChessTrainer\engines\lc0-v0.32.1-fresh'
$queenRoot = Join-Path $engineRoot 'queen-odds'
New-Item -ItemType Directory -Force -Path $downloadRoot, $engineRoot, $queenRoot | Out-Null

function Install-VerifiedFile {
    param(
        [Parameter(Mandatory)] [string] $Url,
        [Parameter(Mandatory)] [string] $DownloadName,
        [Parameter(Mandatory)] [string] $Sha256,
        [Parameter(Mandatory)] [string] $Destination
    )
    $downloadPath = Join-Path $downloadRoot $DownloadName
    if (-not (Test-Path -LiteralPath $downloadPath) -or (Get-FileHash -LiteralPath $downloadPath -Algorithm SHA256).Hash -ne $Sha256) {
        Invoke-WebRequest -Uri $Url -OutFile $downloadPath
    }
    $actual = (Get-FileHash -LiteralPath $downloadPath -Algorithm SHA256).Hash
    if ($actual -ne $Sha256) { throw "SHA-256 mismatch for $DownloadName (got $actual)" }
    if ([System.IO.Path]::GetFullPath($downloadPath) -ne [System.IO.Path]::GetFullPath($Destination)) {
        Copy-Item -LiteralPath $downloadPath -Destination $Destination -Force
    }
    return $downloadPath
}

$releaseZip = Install-VerifiedFile `
    -Url 'https://github.com/LeelaChessZero/lc0/releases/download/v0.32.1/lc0-v0.32.1-windows-gpu-nvidia-cuda12.zip' `
    -DownloadName 'lc0-v0.32.1-cuda12.zip' `
    -Sha256 '8D0CE17676EB15E303BEA9E790742D31C94CE5D24107F6187ADC58E820F6D2F7' `
    -Destination (Join-Path $downloadRoot 'lc0-v0.32.1-cuda12.zip')

tar -xf $releaseZip -C $engineRoot
if ($LASTEXITCODE -ne 0) { throw "Could not extract $releaseZip" }

Install-VerifiedFile `
    -Url 'https://storage.lczero.org/files/networks-contrib/BT4-1024x15x32h-swa-6147500-policytune-332.pb.gz' `
    -DownloadName 'BT4-it332.pb.gz' `
    -Sha256 'E6ADA9D6C4A769BFAB3AA0848D82CAEB809AA45F83E6C605FC58A31D21BDD618' `
    -Destination (Join-Path $engineRoot 'BT4-it332.pb.gz') | Out-Null
Install-VerifiedFile `
    -Url 'https://storage.lczero.org/files/networks-contrib/t1-512x15x8h-distilled-swa-3395000.pb.gz' `
    -DownloadName 'T1-odds.pb.gz' `
    -Sha256 '1FDB1519E5B02E03F1D9201EC8EB95F640E32CC6459A4F14C0EAB6890DC097E8' `
    -Destination (Join-Path $engineRoot 'T1-odds.pb.gz') | Out-Null
Install-VerifiedFile `
    -Url 'https://github.com/notune/LeelaQueenOdds/releases/download/v2/lqo_v2.pb.gz' `
    -DownloadName 'lqo_v2.pb.gz' `
    -Sha256 '063697BA38E8263D68C87A7F2963BB5966D1537D0181102972E832BC55D6E64F' `
    -Destination (Join-Path $queenRoot 'lqo_v2.pb.gz') | Out-Null

Write-Host "Installed fresh LCZero v0.32.1 and verified networks in $engineRoot"
