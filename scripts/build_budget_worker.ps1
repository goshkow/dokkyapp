param(
  [string]$OutputDir,
  [ValidateSet("msvc", "gcc")][string]$Compiler,
  [int]$Jobs
)
$ErrorActionPreference = "Stop"
$null = [Console]::ReadLine()
& (Join-Path $PSScriptRoot "build_nuitka.ps1") -OutputDir $OutputDir -Compiler $Compiler -Jobs $Jobs -SkipInstall
exit $LASTEXITCODE
