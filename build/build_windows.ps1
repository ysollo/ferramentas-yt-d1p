param(
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$project = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $project

$ffmpeg = (Get-Command ffmpeg -ErrorAction Stop).Source
$ffprobe = (Get-Command ffprobe -ErrorAction Stop).Source
$deno = (Get-Command deno -ErrorAction Stop).Source

if ($Clean) {
    Remove-Item -LiteralPath (Join-Path $project "build\pyinstaller") -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Join-Path $project "build\pyinstaller-updater") -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Join-Path $project "dist\YTD1P") -Recurse -Force -ErrorAction SilentlyContinue
}

$pyinstallerArgs = @(
    "--noconfirm", "--clean", "--windowed", "--name", "YTD1P",
    "--hidden-import", "yt_dlp_plugins.extractor.getpot_wpc",
    "--add-binary", "$ffmpeg;runtime",
    "--add-binary", "$ffprobe;runtime",
    "--add-binary", "$deno;runtime",
    "--add-data", "$project\VERSION;.",
    "--distpath", "dist", "--workpath", "build\pyinstaller", "--specpath", "build",
    "src\app.py"
)

python -m PyInstaller @pyinstallerArgs

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller falhou com código $LASTEXITCODE."
}

$updaterArgs = @(
    "--noconfirm", "--clean", "--onedir", "--console", "--name", "YTD1P-Updater",
    "--distpath", "dist\YTD1P\updater", "--workpath", "build\pyinstaller-updater", "--specpath", "build",
    "src\updater_helper.py"
)

python -m PyInstaller @updaterArgs

if ($LASTEXITCODE -ne 0) {
    throw "Build do auxiliar de atualização falhou com código $LASTEXITCODE."
}

$output = Join-Path $project "dist\YTD1P"
Write-Host "Distribuicao criada em: $output"
Get-ChildItem -LiteralPath $output -Recurse -File |
    Measure-Object -Property Length -Sum |
    Select-Object Count, @{Name="Bytes"; Expression={$_.Sum}}
