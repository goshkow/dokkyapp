param(
  [Parameter(Mandatory = $true)]
  [string[]]$Files
)

# подпись Authenticode для CI: сертификат PFX в base64 из WINDOWS_SIGNING_CERT, пароль из WINDOWS_SIGNING_PASSWORD.
# в файле есть BOM: Windows PowerShell 5.1 без него читает кириллицу как ANSI
$ErrorActionPreference = "Stop"

if (-not $env:WINDOWS_SIGNING_CERT) {
  throw "Нет сертификата в WINDOWS_SIGNING_CERT"
}
$signtool = Get-ChildItem -Path "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\signtool.exe" -ErrorAction SilentlyContinue |
  Sort-Object FullName -Descending | Select-Object -First 1 -ExpandProperty FullName
if (-not $signtool) {
  throw "Не нашёл signtool.exe из Windows SDK"
}

$temp = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { [System.IO.Path]::GetTempPath() }
$pfx = Join-Path $temp ("sign-" + [guid]::NewGuid().ToString("N") + ".pfx")
try {
  [System.IO.File]::WriteAllBytes($pfx, [Convert]::FromBase64String($env:WINDOWS_SIGNING_CERT))
  foreach ($file in $Files) {
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) {
      throw "Нет файла $file"
    }
    # метка времени: подпись остаётся действительной после окончания срока сертификата
    & $signtool sign /fd SHA256 /td SHA256 /tr "http://timestamp.digicert.com" /f $pfx /p "$env:WINDOWS_SIGNING_PASSWORD" /d "Dokky" $file
    if ($LASTEXITCODE -ne 0) { throw "signtool sign: код $LASTEXITCODE для $file" }
    & $signtool verify /pa /q $file
    if ($LASTEXITCODE -ne 0) { throw "signtool verify: код $LASTEXITCODE для $file" }
    Write-Host "Подписан: $file"
  }
} finally {
  Remove-Item -LiteralPath $pfx -Force -ErrorAction SilentlyContinue
}
