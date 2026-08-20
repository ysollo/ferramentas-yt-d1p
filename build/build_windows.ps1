param(
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$project = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $project

$ffmpeg = (Get-Command ffmpeg -ErrorAction Stop).Source
$ffprobe = (Get-Command ffprobe -ErrorAction Stop).Source
$deno = (Get-Command deno -ErrorAction Stop).Source
$pluginPath = Join-Path $project "vendor\pot-wpc"

if ($Clean) {
    Remove-Item -LiteralPath (Join-Path $project "build\pyinstaller") -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Join-Path $project "dist\YTD1P") -Recurse -Force -ErrorAction SilentlyContinue
}

$pyinstallerArgs = @(
    "--noconfirm", "--clean", "--windowed", "--name", "YTD1P",
    "--add-binary", "$ffmpeg;runtime",
    "--add-binary", "$ffprobe;runtime",
    "--add-binary", "$deno;runtime",
    "--distpath", "dist", "--workpath", "build\pyinstaller", "--specpath", "build",
    "src\app.py"
)

if (Test-Path -LiteralPath $pluginPath) {
    $plugin = (Resolve-Path $pluginPath).Path
    $pyinstallerArgs = @(
        "--paths", $plugin,
        "--collect-submodules", "yt_dlp_plugins",
        "--add-data", "$plugin;vendor\pot-wpc"
    ) + $pyinstallerArgs
    Write-Host "Plugin PO Token encontrado; incluindo no pacote."
} else {
    Write-Warning "Plugin PO Token não encontrado; o modo de compatibilidade ficará indisponível nesta build."
}

python -m PyInstaller @pyinstallerArgs

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller falhou com código $LASTEXITCODE."
}

$output = Join-Path $project "dist\YTD1P"
Write-Host "Distribuicao criada em: $output"
Get-ChildItem -LiteralPath $output -Recurse -File |
    Measure-Object -Property Length -Sum |
    Select-Object Count, @{Name="Bytes"; Expression={$_.Sum}}
