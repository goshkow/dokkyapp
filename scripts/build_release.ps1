param(
  [string]$OutputDir = "dist_nuitka",
  [ValidateRange(0, 64)]
  [int]$Jobs = 0,
  [switch]$OneFile
)

$ErrorActionPreference = "Stop"
./scripts/build_portable.ps1 -OutputDir $OutputDir -Jobs $Jobs -OneFile:$OneFile
