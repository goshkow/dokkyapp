param(
  [string]$Python = "python",
  [string]$OutputDir = "dist_nuitka",
  [ValidateSet("msvc", "gcc")]
  [string]$Compiler = "gcc",
  [ValidateRange(0, 64)]
  [int]$Jobs = 0,
  [switch]$SkipInstall,
  [switch]$Clean,
  [switch]$OneFile
)

$ErrorActionPreference = "Stop"
# название задано кодами символов: Windows PowerShell читает файл без BOM в ANSI, и кириллица в тексте испортилась бы
$ProductName = -join ([char]0x0414, [char]0x043E, [char]0x043A, [char]0x043A, [char]0x0438)
$Jobs = if ($Jobs -gt 0) { $Jobs } else {
  [Math]::Max(1, [Math]::Floor([Environment]::ProcessorCount * 0.75))
}
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root
$OutputDir = [System.IO.Path]::GetFullPath((Join-Path $Root $OutputDir))
$rootPrefix = [System.IO.Path]::GetFullPath("$Root").TrimEnd('\') + '\'
if (!$OutputDir.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
  throw "Папка сборки должна находиться внутри проекта"
}

if ($Clean) {
  Remove-Item -Recurse -Force (Join-Path $OutputDir "web_host.build") -ErrorAction SilentlyContinue
}
Remove-Item -Recurse -Force (Join-Path $OutputDir "web_host.dist") -ErrorAction SilentlyContinue
Remove-Item -Force (Join-Path $OutputDir "DokkyApp.exe") -ErrorAction SilentlyContinue

# версия одна на всё - из pyproject.toml; в ресурс exe идёт числовая часть без -beta.N
# читаем без python -c: Windows PowerShell 5.1 теряет кавычки в аргументах программ
$VersionMatch = Select-String -Path (Join-Path $Root "pyproject.toml") -Pattern '^version\s*=\s*"([^"]+)"' | Select-Object -First 1
if (!$VersionMatch) { throw "Не удалось прочитать версию из pyproject.toml" }
$Version = $VersionMatch.Matches[0].Groups[1].Value
$FileVersion = ($Version -split "-")[0] + ".0"
$env:NUITKA_CACHE_DIR = Join-Path $env:TEMP "koddi-nuitka-cache"
$env:PIP_PREFER_BINARY = "1"

if (!$SkipInstall) {
  & $Python -m pip install --upgrade pip
  & $Python -m pip install -e ".[dev]"
}

$modeArgs = @("--standalone")
if ($OneFile) {
  $modeArgs = @("--onefile")
}

$compilerArgs = @()
if ($Compiler -eq "msvc") {
  $compilerArgs = @("--msvc=latest")
} else {
  $compilerArgs = @("--mingw64")
}

& $Python -m nuitka `
  @modeArgs `
  @compilerArgs `
  --assume-yes-for-downloads `
  --enable-plugin=pyside6 `
  "--jobs=$Jobs" `
  --output-dir="$OutputDir" `
  --output-filename=DokkyApp.exe `
  --windows-icon-from-ico=assets/DokkyApp.ico `
  --company-name=Goshkow `
  "--product-name=$ProductName" `
  "--file-description=$ProductName" `
  "--file-version=$FileVersion" `
  "--product-version=$FileVersion" `
  --windows-console-mode=disable `
  --include-package=koddi `
  --include-package-data=certifi `
  --include-module=_socket `
  --include-module=_ssl `
  --include-module=select `
  --include-module=unicodedata `
  --include-data-dir=backend/koddi/computer_use/native=koddi/computer_use/native `
  --include-data-dir=backend/koddi/licenses=koddi/licenses `
  --include-data-dir=prompts=prompts `
  --include-data-dir=locales=locales `
  --include-data-dir=backend/koddi/desktop/web=koddi/desktop/web `
  --include-data-dir=backend/koddi/desktop/web-next=koddi/desktop/web-next `
  --include-data-dir=backend/koddi/storage/migrations=koddi/storage/migrations `
  backend/koddi/desktop/web_host.py

if ($LASTEXITCODE -ne 0) {
  throw "Nuitka build failed with exit code $LASTEXITCODE"
}

$standaloneExe = Get-ChildItem -Path $OutputDir -Filter "DokkyApp.exe" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty FullName
$oneFileExe = Join-Path $OutputDir "DokkyApp.exe"
if ($standaloneExe -or (Test-Path $oneFileExe)) {
  if ($standaloneExe -and !$OneFile) {
    & $Python (Join-Path $PSScriptRoot "bundle_browser.py") (Split-Path -Parent $standaloneExe)
    if ($LASTEXITCODE -ne 0) { throw "Не удалось включить браузер агента" }
  }
  Write-Host "Koddi Nuitka build ready:"
  if ($standaloneExe) { Write-Host "  $standaloneExe" }
  if (Test-Path $oneFileExe) { Write-Host "  $oneFileExe" }
  exit 0
}

throw "Nuitka build finished but DokkyApp.exe was not produced"
