param(
  [int]$Port = 8765,
  [switch]$Portable
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

if ($Portable) {
  $env:KODDI_PORTABLE = "1"
}
$env:KODDI_PORT = "$Port"
$env:PYTHONPATH = (Join-Path $Root "backend")

python -m pip install -e ".[dev]"
python -m koddi.desktop.web_host
