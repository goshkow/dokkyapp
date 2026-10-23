param(
  [string]$ExePath = "dist_nuitka\web_host.dist\DokkyApp.exe",
  [int]$Port = 8876,
  [int]$TimeoutSeconds = 60
)

$ErrorActionPreference = "Stop"
if (!(Test-Path $ExePath)) {
  throw "Executable not found: $ExePath"
}

$env:KODDI_PORT = "$Port"
$env:KODDI_DATA_DIR = Join-Path ([System.IO.Path]::GetTempPath()) ("koddi-exe-smoke-" + [guid]::NewGuid().ToString("N"))
Remove-Item Env:KODDI_PORTABLE -ErrorAction SilentlyContinue
$env:PYTHONUNBUFFERED = "1"
$resolvedExe = Resolve-Path $ExePath
$workDir = Split-Path $resolvedExe
$stdout = Join-Path ([System.IO.Path]::GetTempPath()) "koddi-smoke-$Port.out.log"
$stderr = Join-Path ([System.IO.Path]::GetTempPath()) "koddi-smoke-$Port.err.log"
Remove-Item $stdout,$stderr -ErrorAction SilentlyContinue
$process = Start-Process -FilePath $resolvedExe -WorkingDirectory $workDir -PassThru -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr
try {
  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  $health = $null
  do {
    Start-Sleep -Milliseconds 750
    if ($process.HasExited) {
      throw "Koddi exited early with code $($process.ExitCode)"
    }
    try {
      $health = Invoke-RestMethod "http://127.0.0.1:$Port/api/health" -TimeoutSec 2
    }
    catch {
      $health = $null
    }
  } while (($null -eq $health) -and ((Get-Date) -lt $deadline))

  if ($null -eq $health) {
    $outText = if (Test-Path $stdout) { Get-Content $stdout -Raw } else { "" }
    $errText = if (Test-Path $stderr) { Get-Content $stderr -Raw } else { "" }
    throw "Health endpoint did not respond in $TimeoutSeconds seconds`nSTDOUT:`n$outText`nSTDERR:`n$errText"
  }
  if (!$health.ok) { throw "Health check failed" }
  Write-Host "Packaged app smoke passed"
}
finally {
  if (!$process.HasExited) { Stop-Process -Id $process.Id -Force }
}
