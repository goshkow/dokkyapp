param(
  [string]$OutputDir = "dist_nuitka",
  [ValidateRange(0, 64)]
  [int]$Jobs = 0,
  [switch]$OneFile
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

./scripts/build_nuitka.ps1 -OutputDir $OutputDir -Jobs $Jobs -OneFile:$OneFile

Write-Host "Portable app:"
if ($OneFile) {
  Write-Host "  $(Join-Path $OutputDir 'DokkyApp.exe')"
} else {
  Write-Host "  $(Join-Path $OutputDir 'web_host.dist\DokkyApp.exe')"
}
